"""REST routes over a FakeStore: shapes, 404s, stem containment, tail_kb clamping, SPA."""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fake_store import FakeStore, make_session, make_worker
from fastapi.testclient import TestClient
from test_quota import PICK_BUILD_SAMPLE, USAGE_SAMPLE

from stagewatch import __version__, auth, main
from stagewatch.api import (
    LOG_INDEX_MAX,
    QUOTA_CACHE_S,
    SseTiming,
    _event_stream,
    _log_events,
    create_app,
)
from stagewatch.config import AuthConfig, Config
from stagewatch.models import (
    LogIndexEntry,
    LogSizeSample,
    PickCandidate,
    PickRecord,
    PickResult,
    QuotaUsage,
    WorkerEvent,
)
from stagewatch.quota import QuotaClient

LOG_TEXT = "first line\nsecond line\n"


@pytest.fixture
def stage(tmp_path: Path) -> Path:
    stage = tmp_path / "proj" / ".stage"
    (stage / "briefs").mkdir(parents=True)
    (stage / "w1.log").write_text(LOG_TEXT, encoding="utf-8")
    (stage / "notes.txt").write_text("notes\n", encoding="utf-8")
    return stage


@pytest.fixture
def store(stage: Path) -> FakeStore:
    fake = FakeStore({"proj": stage})
    fake.set_session("proj", make_session("proj", stage, running=2))
    fake.pending.clear()
    return fake


@pytest.fixture
def client(store: FakeStore) -> TestClient:
    return TestClient(create_app(store, Config()))


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "version": __version__}


def test_sessions_list_shape(client: TestClient) -> None:
    body = client.get("/api/sessions").json()
    assert set(body) == {"generated_at", "sessions", "monitor_online"}
    assert body["monitor_online"] is False
    assert isinstance(body["generated_at"], float)
    assert [s["id"] for s in body["sessions"]] == ["proj"]
    assert body["sessions"][0]["status"] == "active"
    assert "workers" not in body["sessions"][0]


def test_session_detail_and_unknown_sid(client: TestClient) -> None:
    resp = client.get("/api/sessions/proj")
    assert resp.status_code == 200
    assert [w["name"] for w in resp.json()["workers"]] == ["w0", "w1"]

    missing = client.get("/api/sessions/nope")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "session not found"}


def test_worker_detail_and_404(client: TestClient) -> None:
    resp = client.get("/api/sessions/proj/workers/w0")
    assert resp.status_code == 200
    assert resp.json()["worker"]["name"] == "w0"
    assert resp.json()["brief_md"] is None

    assert client.get("/api/sessions/proj/workers/nope").status_code == 404
    assert client.get("/api/sessions/proj/workers/..%2Fx").status_code == 404


def test_log_chunk_shape(client: TestClient) -> None:
    resp = client.get("/api/sessions/proj/logs/w1?tail_kb=64")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"stem", "format", "size", "offset", "truncated", "lines"}
    assert body["stem"] == "w1"
    assert body["format"] == "text"
    assert body["size"] == len(LOG_TEXT.encode())
    assert body["offset"] == body["size"]
    assert body["truncated"] is False
    assert [line["text"] for line in body["lines"]] == ["first line", "second line"]
    assert body["lines"][0]["kind"] == "raw"


def test_log_notxt_extension_is_served(client: TestClient) -> None:
    resp = client.get("/api/sessions/proj/logs/notes")
    assert resp.status_code == 200
    assert resp.json()["lines"][0]["text"] == "notes"


@pytest.mark.parametrize(
    "url",
    [
        "/api/sessions/nope/logs/w1",  # unknown sid
        "/api/sessions/proj/logs/missing",  # unknown stem
        "/api/sessions/proj/logs/w1.log",  # stem that resolves to w1.log.log
        "/api/sessions/proj/logs/..%2Fx",  # traversal stem
        "/api/sessions/proj/logs/..%2F..%2Fetc%2Fpasswd",
        "/api/sessions/proj/logs/bad%20name",  # outside the allowed charset
        "/api/sessions/nope/logs/w1/stream",
        "/api/sessions/proj/logs/missing/stream",
        "/api/sessions/proj/logs/..%2Fx/stream",
    ],
)
def test_bad_log_requests_are_404(client: TestClient, url: str) -> None:
    assert client.get(url).status_code == 404


def test_symlink_escaping_the_stage_dir_is_404(
    client: TestClient, stage: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "secret.log"
    outside.write_text("do not serve\n", encoding="utf-8")
    (stage / "escape.log").symlink_to(outside)
    assert client.get("/api/sessions/proj/logs/escape").status_code == 404
    assert client.get("/api/sessions/proj/logs/escape/stream").status_code == 404


def test_tail_kb_is_clamped_to_1_and_1024(client: TestClient, stage: Path) -> None:
    big = "".join(f"line {i:06d} {'x' * 60}\n" for i in range(40_000))  # about 2.6 MB
    (stage / "big.log").write_text(big, encoding="utf-8")

    def get(tail_kb: int) -> dict:
        return client.get(f"/api/sessions/proj/logs/big?tail_kb={tail_kb}").json()

    low, one = get(0), get(1)
    assert low == one
    assert one["truncated"] is True
    assert len(one["lines"]) < 40_000

    high, capped = get(5000), get(1024)
    assert high == capped
    assert high["truncated"] is True
    assert len(high["lines"]) > len(one["lines"])


def test_tail_kb_non_integer_is_rejected(client: TestClient) -> None:
    assert client.get("/api/sessions/proj/logs/w1?tail_kb=lots").status_code == 422


def test_log_stream_reports_deletion_and_recovers(stage: Path) -> None:
    async def exercise() -> list[str]:
        stream = _log_events("w1", stage / "w1.log", 64 * 1024, SseTiming(poll_s=0.001))
        events = [await asyncio.wait_for(anext(stream), 1)]
        (stage / "w1.log").unlink()
        events.append(await asyncio.wait_for(anext(stream), 1))
        (stage / "w1.log").write_text("replacement line\n", encoding="utf-8")
        events.extend(
            [await asyncio.wait_for(anext(stream), 1), await asyncio.wait_for(anext(stream), 1)]
        )
        await stream.aclose()
        return events

    events = asyncio.run(exercise())
    assert 'event: init' in events[0]
    assert 'event: error' in events[1]
    assert 'event: reset' in events[2]
    assert 'event: init' in events[3]
    assert 'replacement line' in events[3]


def test_unknown_api_path_is_404(client: TestClient) -> None:
    assert client.get("/api/nope").status_code == 404


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return dist


def test_spa_fallback_serves_index_and_assets(
    store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "FRONTEND_DIST", _dist(tmp_path))
    client = TestClient(main.create_app(store, Config()))

    assert client.get("/").text == "<html>spa</html>"
    assert client.get("/sessions/proj/some/route").text == "<html>spa</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/sessions/proj").status_code == 200
    assert client.get("/api/unknown").status_code == 404


def test_no_spa_without_dist(
    store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path / "missing")
    client = TestClient(main.create_app(store, Config()))
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200


# --------------------------------------------------------------------------- #
# Nested stations
# --------------------------------------------------------------------------- #
RUN_SID = "run:3JvbnJhbQ"


def _nested_client(tmp_path: Path) -> tuple[TestClient, Path]:
    rundir = tmp_path / "proj" / ".stage" / "ram"
    rundir.mkdir(parents=True)
    (rundir / "w0.log").write_text("nested log\n", encoding="utf-8")
    fake = FakeStore({RUN_SID: rundir})
    fake.set_session(RUN_SID, make_session(RUN_SID, rundir, running=1))
    fake.pending.clear()
    return TestClient(create_app(fake, Config())), rundir


def test_nested_station_detail_worker_and_log_routes(tmp_path: Path) -> None:
    client, rundir = _nested_client(tmp_path)

    detail = client.get(f"/api/sessions/{RUN_SID}")
    assert detail.status_code == 200
    assert detail.json()["id"] == RUN_SID
    assert "run" in detail.json()

    worker = client.get(f"/api/sessions/{RUN_SID}/workers/w0")
    assert worker.status_code == 200
    assert worker.json()["worker"]["name"] == "w0"
    assert "engine" in worker.json()["worker"]

    log = client.get(f"/api/sessions/{RUN_SID}/logs/w0")
    assert log.status_code == 200
    assert log.json()["lines"][0]["text"] == "nested log"

    assert client.get("/api/sessions/run:missing").status_code == 404


def test_sessions_payload_includes_the_run_field(tmp_path: Path) -> None:
    client, rundir = _nested_client(tmp_path)

    body = client.get("/api/sessions").json()

    session = next(item for item in body["sessions"] if item["id"] == RUN_SID)
    assert session["run"] is None  # make_session leaves it unset

    fake = FakeStore({RUN_SID: rundir})
    fake.set_session(RUN_SID, make_session(RUN_SID, rundir).model_copy(update={"run": "ram"}))
    fake.pending.clear()
    nested = TestClient(create_app(fake, Config())).get("/api/sessions").json()

    assert nested["sessions"][0]["run"] == "ram"


# --------------------------------------------------------------------------- #
# Quota proxy (quota_service via app.state.quota_client)
# --------------------------------------------------------------------------- #
TOKEN = "tw-bearer-for-tests"
QUOTA_SERVICE = "http://127.0.0.1:8800"
PICK = "/api/quota/pick?job=build&slices=2&lead=lead-a&builder=builder-b"


def _upstream(
    *,
    status: int = 200,
    body: object = None,
    content: bytes | None = None,
    error: Exception | None = None,
) -> tuple[QuotaClient, list[httpx.Request]]:
    """A QuotaClient on an in-memory transport. The returned list records each upstream call."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if error is not None:
            raise error
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body)

    return QuotaClient(QUOTA_SERVICE, TOKEN, transport=httpx.MockTransport(handler)), seen


def _quota_app(store: FakeStore, client: QuotaClient | None) -> TestClient:
    app = create_app(store, Config())
    app.state.quota_client = client
    return TestClient(app)


def test_quota_routes_are_offline_without_a_client(store: FakeStore) -> None:
    client = _quota_app(store, None)

    usage = client.get("/api/quota/usage")
    assert usage.status_code == 503
    assert usage.json() == {
        "offline": True,
        "error_code": "offline",
        "generated_at": None,
        "fetched_at": None,
        "subscriptions": [],
        "credits": [],
    }

    pick = client.get(PICK)
    assert pick.status_code == 503
    assert pick.json() == {"detail": "quota offline"}


@pytest.mark.parametrize(
    "query",
    [
        "",  # everything missing
        "slices=2&lead=a&builder=b",  # job missing
        "job=%20&slices=2&lead=a&builder=b",  # blank job
        "job=build&slices=0&lead=a&builder=b",  # below 1
        "job=build&slices=two&lead=a&builder=b",  # not an integer
        "job=build&slices=&lead=a&builder=b",  # empty slices
        "job=build&slices=2&builder=b",  # lead missing
        "job=build&slices=2&lead=a",  # builder missing
    ],
)
def test_pick_rejects_bad_params_before_any_upstream_call(store: FakeStore, query: str) -> None:
    upstream, seen = _upstream(body={})
    response = _quota_app(store, upstream).get(f"/api/quota/pick?{query}")
    assert response.status_code == 400
    assert "detail" in response.json()
    assert seen == []


def test_usage_maps_the_online_payload_and_never_echoes_upstream(store: FakeStore) -> None:
    upstream, seen = _upstream(body={**USAGE_SAMPLE, "token": TOKEN, "headers": {"x": TOKEN}})
    response = _quota_app(store, upstream).get("/api/quota/usage")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(QuotaUsage.model_fields)
    assert body["offline"] is False
    assert body["generated_at"] == 1791511190896.0  # upstream ms, passed through unchanged
    assert isinstance(body["fetched_at"], float)  # dashboard s
    assert body["error_code"] is None
    assert [sub["id"] for sub in body["subscriptions"]][:2] == ["codex", "claude"]
    assert body["subscriptions"][1]["limits"][1]["runs_out_at"] == 1791633767085.3389
    assert TOKEN not in response.text
    assert seen[0].url.path == "/api/pick/usage"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_pick_maps_the_online_payload_into_snake_case(store: FakeStore) -> None:
    upstream, seen = _upstream(body={**PICK_BUILD_SAMPLE, "token": TOKEN})
    response = _quota_app(store, upstream).get(PICK)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(PickResult.model_fields)
    assert body["job"] == "build"
    assert body["generated_at"] == 1791511194672.0
    assert body["tier_used"] == "small"
    assert [c["model"] for c in body["candidates"]] == ["haiku-work", "luna", "haiku-cursor"]
    assert body["candidates"][0]["shortest"]["headroom_pct"] == 69.0
    assert body["candidates"][0]["longest"]["projected_pct"] is None
    assert body["fallback"] == {
        "model": "deepseek",
        "effort": "default",
        "subscription": "opencode-go",
    }
    assert body["lead"] is None
    assert body["assignment"] == [
        {"model": "haiku-work", "effort": "medium", "slices": 1},
        {"model": "luna", "effort": "medium", "slices": 1},
    ]
    assert TOKEN not in response.text
    assert dict(seen[0].url.params) == {
        "job": "build",
        "slices": "2",
        "lead": "lead-a",
        "builder": "builder-b",
    }


HUGE = b"1" * 400  # an integer float() cannot represent: OverflowError, not a 500


@pytest.mark.parametrize(
    ("raw", "score", "runway", "shortest_used", "longest_used"),
    [
        (
            b'{"candidates": [{"model": "m", "score": 0.5, "shortest": {"usedPct": '
            + HUGE
            + b'}, "longest": {"usedPct": 9}}]}',
            0.5,
            None,
            None,
            9.0,
        ),
        (
            b'{"candidates": [{"model": "m", "score": NaN, "runway": Infinity, '
            b'"shortest": null, "longest": {"usedPct": -Infinity}}]}',
            None,
            None,
            None,
            None,
        ),
        (
            b'{"candidates": [{"model": "m", "score": ' + HUGE + b', "runway": true}]}',
            None,
            None,
            None,
            None,
        ),
    ],
)
def test_pick_turns_huge_and_non_finite_numbers_into_null(
    store: FakeStore,
    raw: bytes,
    score: float | None,
    runway: float | None,
    shortest_used: float | None,
    longest_used: float | None,
) -> None:
    upstream, _ = _upstream(content=raw)
    response = _quota_app(store, upstream).get(PICK)

    assert response.status_code == 200
    [candidate] = response.json()["candidates"]
    assert (candidate["score"], candidate["runway"]) == (score, runway)
    shortest, longest = candidate["shortest"], candidate["longest"]
    assert (shortest and shortest["used_pct"], longest and longest["used_pct"]) == (
        shortest_used,
        longest_used,
    )


@pytest.mark.parametrize("raw", [b'{"generatedAt": ' + HUGE + b"}", b'{"generatedAt": NaN}'])
def test_usage_turns_huge_and_non_finite_generated_at_into_null(
    store: FakeStore, raw: bytes
) -> None:
    upstream, _ = _upstream(content=raw)
    response = _quota_app(store, upstream).get("/api/quota/usage")

    assert response.status_code == 200
    assert response.json()["generated_at"] is None
    assert response.json()["offline"] is False


@pytest.mark.parametrize(
    ("upstream_kwargs", "error_code"),
    [
        ({"status": 503, "body": {}}, "unavailable"),
        ({"status": 400, "body": {}}, "bad_request"),  # reported as 503: our query was valid
        ({"status": 500, "body": {}}, "error"),
        ({"error": httpx.ConnectError("refused")}, "offline"),
        ({"error": httpx.ReadTimeout("slow")}, "timeout"),
        ({"content": b"not json"}, "error"),
        ({"content": b"[1, 2]"}, "error"),  # valid JSON, not an object
        ({"content": b" " * (256 * 1024 + 1)}, "error"),  # over the byte cap
    ],
)
def test_upstream_failures_map_to_503_with_no_invented_metrics(
    store: FakeStore, upstream_kwargs: dict, error_code: str
) -> None:
    upstream, _ = _upstream(**upstream_kwargs)
    client = _quota_app(store, upstream)

    usage = client.get("/api/quota/usage")
    assert usage.status_code == 503
    assert usage.json() == {
        "offline": True,
        "error_code": error_code,
        "generated_at": None,
        "fetched_at": None,
        "subscriptions": [],
        "credits": [],
    }

    pick = client.get(PICK)
    assert pick.status_code == 503
    assert set(pick.json()) == {"detail"}
    assert "score" not in pick.text
    assert TOKEN not in usage.text + pick.text


def test_quota_results_are_cached_for_ten_seconds(store: FakeStore) -> None:
    upstream, seen = _upstream(body={"generated_at": 1.0})
    client = _quota_app(store, upstream)
    now = [1000.0]
    client.app.state.quota_cache.clock = lambda: now[0]

    client.get("/api/quota/usage")
    now[0] += QUOTA_CACHE_S - 1
    client.get("/api/quota/usage")
    assert len(seen) == 1

    now[0] += 2  # 10 s after the first upstream call
    client.get("/api/quota/usage")
    assert len(seen) == 2

    client.get(PICK)
    client.get(PICK)
    assert len(seen) == 3
    client.get(PICK.replace("job=build", "job=review"))  # a different query is its own entry
    assert len(seen) == 4


# --------------------------------------------------------------------------- #
# Logs index (from SessionDetail.log_index only)
# --------------------------------------------------------------------------- #
def test_logs_index_merges_sessions_newest_first_and_fills_session_id(tmp_path: Path) -> None:
    # The stage dirs do not exist: the index must come from the store, not from disk.
    a_stage, b_stage = tmp_path / "a", tmp_path / "b"
    fake = FakeStore({"a": a_stage, "b": b_stage})
    fake.set_session(
        "a",
        make_session("a", a_stage).model_copy(
            update={
                "log_index": [
                    LogIndexEntry(
                        worker_name="w1", artifact_name="w1.log", kind="log", mtime=10.0, size=5
                    ),
                    LogIndexEntry(
                        worker_name="w2", artifact_name="w2.log", kind="log", mtime=None, size=1
                    ),
                ]
            }
        ),
    )
    fake.set_session(
        "b",
        make_session("b", b_stage).model_copy(
            update={
                "log_index": [
                    LogIndexEntry(
                        session_id="b",
                        worker_name="w9",
                        artifact_name="w9.log",
                        kind="log",
                        mtime=30.0,
                        size=9,
                    ),
                    LogIndexEntry(
                        worker_name="w3", artifact_name="w3.log", kind="log", mtime=20.0, size=3
                    ),
                ]
            }
        ),
    )
    client = TestClient(create_app(fake, Config()))

    body = client.get("/api/logs").json()
    assert body["truncated"] is False
    assert [(e["session_id"], e["artifact_name"]) for e in body["entries"]] == [
        ("b", "w9.log"),
        ("b", "w3.log"),
        ("a", "w1.log"),
        ("a", "w2.log"),  # no mtime sorts last
    ]
    assert all(e["preview_url"] is None for e in body["entries"])

    only_a = client.get("/api/logs?sid=a").json()
    assert [e["artifact_name"] for e in only_a["entries"]] == ["w1.log", "w2.log"]
    assert client.get("/api/logs?sid=nope").status_code == 404


def test_logs_index_is_capped_and_says_so(tmp_path: Path) -> None:
    stage = tmp_path / "proj"
    entries = [
        LogIndexEntry(
            worker_name=f"w{i}", artifact_name=f"w{i}.log", kind="log", mtime=float(i), size=1
        )
        for i in range(LOG_INDEX_MAX + 5)
    ]
    fake = FakeStore({"proj": stage})
    fake.set_session("proj", make_session("proj", stage).model_copy(update={"log_index": entries}))

    body = TestClient(create_app(fake, Config())).get("/api/logs").json()
    assert len(body["entries"]) == LOG_INDEX_MAX
    assert body["truncated"] is True
    assert body["entries"][0]["mtime"] == float(LOG_INDEX_MAX + 4)  # newest entries are kept


# --------------------------------------------------------------------------- #
# D1-D8 fields flow through session, worker and SSE payloads unchanged
# --------------------------------------------------------------------------- #
def _assert_worker_d_fields(worker: dict) -> None:
    assert worker["engine_family"] == "codex"
    assert worker["worker_events"] == [{"kind": "retry", "at": 1700000000, "text": "retry 2"}]
    assert worker["log_series"] == [{"at": 1.0, "size": 12}]


def test_d_fields_reach_session_worker_and_sse_payloads(tmp_path: Path) -> None:
    stage = tmp_path / "proj"
    worker = make_worker("w0").model_copy(
        update={
            "engine_family": "codex",
            "worker_events": [WorkerEvent(kind="retry", at=1700000000, text="retry 2")],
            "log_series": [LogSizeSample(at=1.0, size=12)],
        }
    )
    record = PickRecord(
        job="build",
        slices=["s1"],
        candidates=[PickCandidate(model="m", effort="high", group="g", reason="ok")],
        assigned=["s1"],
    )
    fake = FakeStore({"proj": stage})
    fake.set_session(
        "proj",
        make_session("proj", stage).model_copy(
            update={"workers": [worker], "pick_records": [record]}
        ),
    )
    app = create_app(fake, Config())
    client = TestClient(app)

    session = client.get("/api/sessions/proj").json()
    _assert_worker_d_fields(session["workers"][0])
    assert session["pick_records"][0]["job"] == "build"
    assert session["pick_records"][0]["candidates"][0]["model"] == "m"

    _assert_worker_d_fields(client.get("/api/sessions/proj/workers/w0").json()["worker"])

    async def first_frames() -> list[str]:
        stream = _event_stream(fake, app.state.scanner, "proj", SseTiming(poll_s=0.001))
        frames = [await asyncio.wait_for(anext(stream), 1) for _ in range(3)]
        await stream.aclose()
        return frames

    retry, sessions, session_frame = asyncio.run(first_frames())
    assert retry.startswith("retry:")
    assert "event: sessions" in sessions
    assert "event: session\n" in session_frame
    data = next(line for line in session_frame.splitlines() if line.startswith("data: "))
    streamed = json.loads(data.removeprefix("data: "))
    _assert_worker_d_fields(streamed["workers"][0])
    assert streamed["pick_records"][0]["job"] == "build"


# --------------------------------------------------------------------------- #
# Public requests stay behind the sign-in gate on every new route
# --------------------------------------------------------------------------- #
OWNER = "owner@example.com"
PUBLIC = {"cf-connecting-ip": "203.0.113.7"}
BASE = "https://testserver"
NEW_ROUTES = [
    "/api/quota/usage",
    "/api/quota/pick?job=build&slices=2&lead=a&builder=b",
    "/api/logs",
    "/api/logs?sid=proj",
    "/api/monitor",
    "/api/monitor/stage?path=proj/build",
]


@pytest.fixture
def public_cfg(tmp_path: Path) -> Config:
    (tmp_path / "session-secret").write_text("s3ssion-secret-for-tests\n", encoding="utf-8")
    (tmp_path / "google-secret").write_text("g00gle-secret-for-tests\n", encoding="utf-8")
    return Config(
        auth=AuthConfig(
            google_client_id="client-id.apps.googleusercontent.com",
            google_client_secret_file=tmp_path / "google-secret",
            session_secret_file=tmp_path / "session-secret",
            allowed_email=OWNER.upper(),
            public_base_url="https://dashboard.example.com",
        )
    )


@pytest.mark.parametrize("url", NEW_ROUTES)
def test_new_routes_are_401_for_public_requests_without_a_session(
    public_cfg: Config, url: str
) -> None:
    client = TestClient(main.create_app(FakeStore({}), public_cfg), base_url=BASE)
    response = client.get(url, headers=PUBLIC)
    assert response.status_code == 401
    assert response.json() == {"detail": "sign-in required"}


def test_new_routes_are_not_gated_for_local_requests(public_cfg: Config) -> None:
    client = TestClient(main.create_app(FakeStore({}), public_cfg), base_url=BASE)
    assert client.get("/api/logs").status_code == 200
    assert client.get("/api/quota/usage").status_code == 503  # no quota_service configured


def test_new_routes_pass_the_gate_with_a_valid_session(public_cfg: Config, tmp_path: Path) -> None:
    secret = (tmp_path / "session-secret").read_text(encoding="utf-8").strip().encode()
    client = TestClient(main.create_app(FakeStore({}), public_cfg), base_url=BASE)
    client.cookies.set(
        auth.SESSION_COOKIE, auth.sign({"email": OWNER, "exp": time.time() + 3600}, secret)
    )
    assert client.get("/api/logs", headers=PUBLIC).status_code == 200
    assert client.get("/api/quota/usage", headers=PUBLIC).status_code == 503  # offline, not 401
