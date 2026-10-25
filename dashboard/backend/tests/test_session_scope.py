"""`GET /api/sessions/{sid}?lead_session=` scopes a shared `.stage` to one lead session."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.api import create_app
from stagewatch.config import Config
from stagewatch.snapshot import SnapshotStore
from stagewatch.stage import session_lead_matches

LEAD = "8081aaaa-0000-4000-8000-000000000001"
OTHER = "8081bbbb-0000-4000-8000-000000000002"
ALL_WORKERS = {"alpha", "beta", "gamma", "delta", "epsilon"}


def _worker(stage: Path, stem: str) -> None:
    """A worker is any stem with a `.model` file; the brief and log feed its artifacts."""
    (stage / f"{stem}.model").write_text("opus\n")
    (stage / f"{stem}.log").write_text("hello\n")
    (stage / "briefs" / f"{stem}.md").write_text(f"# {stem}\n")


def _session(stage: Path, stem: str, lead: str | None, root: str | None) -> None:
    body = {"schema": 2, "lead_session": lead, "root_session": root, "parent_session": None}
    (stage / f"{stem}.session").write_text(json.dumps(body))


@pytest.fixture
def stage(tmp_path: Path) -> Path:
    stage = tmp_path / "proj" / ".stage"
    (stage / "briefs").mkdir(parents=True)
    for stem in sorted(ALL_WORKERS):
        _worker(stage, stem)
    (stage / "alpha").mkdir()
    (stage / "alpha" / "REPORT.md").write_text("# done\n")
    (stage / "gamma").mkdir()
    (stage / "gamma" / "REPORT.md").write_text("# other\n")
    # alpha: lead and root are LEAD. beta: lead OTHER, root LEAD. gamma: both OTHER.
    _session(stage, "alpha", LEAD, LEAD)
    _session(stage, "beta", OTHER, LEAD)
    _session(stage, "gamma", OTHER, OTHER)
    # delta has no `.session`; epsilon's is cut off mid-object.
    (stage / "epsilon.session").write_text('{"lead_session": "' + LEAD + '",')
    # Reviews are never workers, so their findings do not depend on the scope.
    (stage / "review-a.log").write_text("- [P2] off by one\n")
    return stage


@pytest.fixture
def store(stage: Path) -> SnapshotStore:
    def fetch_state() -> dict:
        return {"stages": [{"path": str(stage)}]}

    store = SnapshotStore(Config(), state_fetcher=fetch_state)
    store.discover()
    store.refresh()
    return store


@pytest.fixture
def sid(store: SnapshotStore) -> str:
    return store.summaries()[0].id


@pytest.fixture
def client(store: SnapshotStore) -> TestClient:
    return TestClient(create_app(store, Config()))


def _names(body: dict) -> set[str]:
    return {worker["name"] for worker in body["workers"]}


def test_without_lead_session_every_worker_is_listed(client: TestClient, sid: str) -> None:
    body = client.get(f"/api/sessions/{sid}").json()
    assert _names(body) == ALL_WORKERS
    assert body["worker_total"] == 5
    assert sum(body["counts"].values()) == 5


def test_lead_session_keeps_lead_and_root_matches(client: TestClient, sid: str) -> None:
    body = client.get(f"/api/sessions/{sid}", params={"lead_session": LEAD}).json()
    assert _names(body) == {"alpha", "beta"}
    assert body["worker_total"] == 2
    assert {preview["name"] for preview in body["worker_previews"]} == {"alpha", "beta"}
    assert sum(body["counts"].values()) == 2
    assert body["running_workers"] == []
    assert body["status"] == "idle"


def test_root_session_match_includes_worker_whose_lead_differs(
    client: TestClient, sid: str
) -> None:
    body = client.get(f"/api/sessions/{sid}", params={"lead_session": LEAD}).json()
    assert "beta" in _names(body)


def test_lead_session_match_for_the_other_lead(client: TestClient, sid: str) -> None:
    body = client.get(f"/api/sessions/{sid}", params={"lead_session": OTHER}).json()
    assert _names(body) == {"beta", "gamma"}
    assert body["worker_total"] == 2


def test_worker_without_session_file_is_excluded_only_when_scoped(
    client: TestClient, sid: str
) -> None:
    unscoped = client.get(f"/api/sessions/{sid}").json()
    scoped = client.get(f"/api/sessions/{sid}", params={"lead_session": LEAD}).json()
    assert "delta" in _names(unscoped)
    assert "delta" not in _names(scoped)


def test_malformed_session_file_is_ignored_and_worker_excluded(
    client: TestClient, sid: str
) -> None:
    unscoped = client.get(f"/api/sessions/{sid}").json()
    scoped = client.get(f"/api/sessions/{sid}", params={"lead_session": LEAD}).json()
    assert "epsilon" in _names(unscoped)
    assert "epsilon" not in _names(scoped)


def test_unknown_lead_session_gives_an_empty_scope(client: TestClient, sid: str) -> None:
    body = client.get(f"/api/sessions/{sid}", params={"lead_session": "nobody"}).json()
    assert body["workers"] == []
    assert body["worker_total"] == 0
    assert body["worker_previews"] == []
    assert body["counts"] == {"running": 0, "finished": 0, "exited": 0, "unknown": 0}
    assert body["tokens_total"] is None
    assert body["cost_total"] is None
    assert body["log_index"] is None
    assert body["status"] == "idle"


def test_artifacts_and_log_index_follow_the_scope(client: TestClient, sid: str) -> None:
    unscoped = client.get(f"/api/sessions/{sid}").json()
    scoped = client.get(f"/api/sessions/{sid}", params={"lead_session": LEAD}).json()

    unscoped_names = {item["name"] for item in unscoped["artifacts"]}
    assert {"alpha/REPORT.md", "gamma/REPORT.md", "briefs/gamma.md"} <= unscoped_names

    scoped_names = {item["name"] for item in scoped["artifacts"]}
    assert {"alpha/REPORT.md", "briefs/alpha.md", "briefs/beta.md"} <= scoped_names
    assert not any("gamma" in name or "delta" in name for name in scoped_names)
    assert {entry["worker_name"] for entry in scoped["log_index"]} <= {"alpha", "beta"}


def test_findings_come_from_reviews_and_stay_the_same(client: TestClient, sid: str) -> None:
    unscoped = client.get(f"/api/sessions/{sid}").json()
    scoped = client.get(f"/api/sessions/{sid}", params={"lead_session": LEAD}).json()
    assert unscoped["findings_total"] == 1
    assert scoped["findings_total"] == 1
    assert scoped["findings_by_priority"] == unscoped["findings_by_priority"]
    assert scoped["findings_by_priority"]["P2"] == 1


def test_empty_lead_session_is_rejected(client: TestClient, sid: str) -> None:
    resp = client.get(f"/api/sessions/{sid}", params={"lead_session": ""})
    assert resp.status_code == 422


def test_worker_route_follows_the_scope(client: TestClient, sid: str) -> None:
    assert client.get(f"/api/sessions/{sid}/workers/gamma").status_code == 200
    scoped = client.get(f"/api/sessions/{sid}/workers/alpha", params={"lead_session": LEAD})
    assert scoped.status_code == 200
    assert scoped.json()["worker"]["name"] == "alpha"
    out_of_scope = client.get(f"/api/sessions/{sid}/workers/gamma", params={"lead_session": LEAD})
    assert out_of_scope.status_code == 404
    unlisted = client.get(f"/api/sessions/{sid}/workers/delta", params={"lead_session": LEAD})
    assert unlisted.status_code == 404


def test_session_lead_matches_reads_lead_or_root(stage: Path) -> None:
    assert session_lead_matches(stage, "alpha", LEAD) is True
    assert session_lead_matches(stage, "beta", LEAD) is True
    assert session_lead_matches(stage, "gamma", LEAD) is False


@pytest.mark.parametrize(
    "body",
    [
        "",
        "not json",
        '{"lead_session": "' + LEAD + '",',
        '["' + LEAD + '"]',
        '"' + LEAD + '"',
        "[" * 60000,
        '{"lead_session": "' + LEAD + '", "pad": "' + "x" * 70000 + '"}',
    ],
)
def test_unusable_session_files_never_match(stage: Path, body: str) -> None:
    (stage / "zeta.session").write_text(body)
    assert session_lead_matches(stage, "zeta", LEAD) is False


def test_missing_session_file_never_matches(stage: Path) -> None:
    assert session_lead_matches(stage, "delta", LEAD) is False


def test_symlinked_session_file_is_ignored(stage: Path, tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    target.write_text(json.dumps({"lead_session": LEAD}))
    os.symlink(target, stage / "link.session")
    assert session_lead_matches(stage, "link", LEAD) is False


def _frame_body(frame: str) -> dict:
    data = next(line for line in frame.splitlines() if line.startswith("data: "))
    return json.loads(data[len("data: ") :])


def test_session_frame_scopes_the_live_stream(store: SnapshotStore, sid: str) -> None:
    # The run page reads `/api/events?session=&lead_session=`, so its frame is scoped too.
    from stagewatch.api import _session_frame

    assert _names(_frame_body(_session_frame(store, sid))) == ALL_WORKERS
    assert _names(_frame_body(_session_frame(store, sid, LEAD))) == {"alpha", "beta"}
