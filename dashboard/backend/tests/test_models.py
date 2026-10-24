"""Contract checks for the project plan fields added to the API models."""

import pytest

from stagewatch.models import (
    ActivitySample,
    Artifact,
    Counts,
    CycleEvent,
    CycleProgress,
    LatFacts,
    LeadIdentity,
    LogChunk,
    LogIndexEntry,
    NowLine,
    PickCandidate,
    PickRecord,
    PickResult,
    QuotaUsage,
    SessionDetail,
    SessionSummary,
    Worker,
    WorkerDetail,
    WorkerPreview,
)

OLD_SESSION_SUMMARY_FIELDS = {
    "id",
    "project",
    "run",
    "path",
    "rev",
    "status",
    "counts",
    "last_activity_at",
    "latest_milestone",
    "findings_total",
    "running_workers",
}
NEW_SESSION_SUMMARY_FIELDS = {
    "tokens_total",
    "cost_total",
    "findings_by_priority",
    "lead",
    "stream",
    "cycle_progress",
    "source_warnings",
    "worker_previews",
    "worker_total",
    "activity_series",
    "lat",
}


def _worker(**overrides: object) -> Worker:
    base: dict[str, object] = {
        "name": "w1",
        "status": "running",
        "pid": None,
        "model": None,
        "title": None,
        "goal": None,
        "has_brief": False,
        "has_report": False,
        "started_at": None,
        "finished_at": None,
        "last_activity_at": None,
        "now": None,
        "summary": [],
        "log_stem": None,
        "log_size": 0,
        "log_format": "missing",
        "flags": [],
    }
    return Worker(**(base | overrides))


def _bare_detail() -> SessionDetail:
    return SessionDetail(
        id="s",
        project="p",
        path="/p",
        rev=1,
        status="idle",
        counts=Counts(running=0, finished=0, exited=0, unknown=0),
        last_activity_at=None,
        latest_milestone=None,
        findings_total=0,
        running_workers=[],
        workers=[_worker()],
        milestones=[],
        reviews=[],
        extra_logs=[],
    )


@pytest.fixture
def full_detail() -> SessionDetail:
    """Every new nullable field set to a non-null value."""
    artifact = Artifact(
        name="brief.md",
        kind="brief",
        size=10,
        mtime=1.0,
        previewable=True,
        preview_url="/api/sessions/s/workers/w1/brief",
    )
    worker = _worker(
        model="gpt",
        engine="codex",
        started_at=1.0,
        finished_at=3.0,
        last_activity_at=2.0,
        duration_s=2.0,
        log_mtime=2.5,
        artifact_count=1,
        log_stem="w1",
        log_size=5,
        log_format="text",
        now=NowLine(kind="text", text="working", tool=None),
    )
    return SessionDetail(
        id="s",
        project="p",
        run="r",
        path="/p",
        rev=2,
        status="active",
        counts=Counts(running=1, finished=0, exited=0, unknown=0),
        last_activity_at=2.0,
        latest_milestone="M1",
        findings_total=1,
        running_workers=[],
        workers=[worker],
        milestones=["M1"],
        reviews=[],
        extra_logs=[],
        lat=LatFacts(section="active", level_tag="LEAD", project="alpha"),
        lead=LeadIdentity(
            label="Lead",
            model="claude",
            confidence="explicit-stream",
            source_path="docs/streams/x.md",
            source_line=4,
            evidence="owner: Lead",
            conflicts=["progress says otherwise"],
        ),
        stream="redesign-v4",
        cycle_progress=CycleProgress(
            completed=1,
            current=2,
            phase="build",
            events=[
                CycleEvent(
                    phase="plan",
                    cycle=1,
                    label="plan done",
                    at=1.0,
                    source_path="PROGRESS.md",
                    source_line=1,
                )
            ],
            over_policy=True,
        ),
        source_warnings=["stale STATE.md"],
        worker_previews=[
            WorkerPreview(
                name="w1",
                model="gpt",
                engine="codex",
                started_at=1.0,
                now=NowLine(kind="tool", text="grep", tool="grep"),
                status="running",
                pid=42,
                title="Build",
                goal="Ship it",
                finished_at=None,
                last_activity_at=2.0,
                summary=["step"],
                has_brief=True,
                has_report=False,
                log_stem="w1",
                log_size=5,
                log_format="codex-text",
                flags=["stale"],
                engine_family="codex",
                account=None,
                effort="medium",
            )
        ],
        worker_total=3,
        tokens_total=1200,
        cost_total=0.25,
        findings_by_priority={"P1": 1, "P2": 0, "P3": 0},
        activity_series=[ActivitySample(at=1.0, count=4)],
        history=[CycleEvent(phase="review", cycle=2, at=2.0)],
        artifacts=[artifact],
        log_index=[
            LogIndexEntry(
                session_id="s",
                worker_name="w1",
                artifact_name="brief.md",
                kind="brief",
                mtime=1.0,
                size=10,
                preview_url="/api/sessions/s/workers/w1/brief",
            )
        ],
    )


@pytest.fixture
def bare_detail() -> SessionDetail:
    """Only the original required fields; every new field left at its default."""
    return _bare_detail()


def test_full_fixture_dumps_new_fields_non_null(full_detail: SessionDetail) -> None:
    data = full_detail.model_dump(mode="json")

    for key in NEW_SESSION_SUMMARY_FIELDS | {"history", "artifacts", "log_index"}:
        assert key in data
        assert data[key] not in (None, [])
    assert data["lead"]["confidence"] == "explicit-stream"
    assert data["cycle_progress"]["current"] == 2
    assert data["cycle_progress"]["events"][0]["phase"] == "plan"
    assert data["worker_previews"][0]["now"]["kind"] == "tool"
    assert data["worker_previews"][0]["engine_family"] == "codex"
    assert data["activity_series"] == [{"at": 1.0, "count": 4}]
    assert data["workers"][0]["duration_s"] == 2.0
    assert data["workers"][0]["artifact_count"] == 1
    assert data["log_index"][0]["kind"] == "brief"


def test_bare_fixture_dumps_new_fields_at_defaults(bare_detail: SessionDetail) -> None:
    data = bare_detail.model_dump(mode="json")

    assert data["lead"] is None
    assert data["lat"] is None
    assert data["stream"] is None
    assert data["cycle_progress"] is None
    assert data["source_warnings"] == []
    assert data["worker_previews"] == []
    assert data["worker_total"] == 0
    assert data["activity_series"] == []
    assert data["history"] == []
    assert data["artifacts"] == []
    assert data["log_index"] is None
    assert data["workers"][0]["duration_s"] is None
    assert data["workers"][0]["log_mtime"] is None
    assert data["workers"][0]["artifact_count"] == 0
    worker = data["workers"][0]
    nullable = (
        "engine_family", "account", "effort", "mode", "access", "access_source",
        "job", "tokens", "tokens_source",
    )
    for key in nullable:
        assert worker[key] is None
    for key in ("worker_events", "log_series", "step_events"):
        assert worker[key] == []
    assert data["pick_records"] == []


def test_worker_preview_identity_fields_default_to_null() -> None:
    preview = WorkerPreview(name="w1")
    data = preview.model_dump(mode="json")

    assert data["engine_family"] is None
    assert data["account"] is None
    assert data["effort"] is None


def test_monitor_facts_are_optional() -> None:
    assert LatFacts().model_dump(mode="json")["lead_state"] is None


def test_worker_and_session_detail_d1_d8_round_trip() -> None:
    detail = _bare_detail()
    worker = Worker.model_validate(detail.workers[0].model_dump() | {
        "engine_family": "claude-work",
        "account": "work",
        "effort": "high",
        "mode": "interactive",
        "access": "rw",
        "access_source": "model-file",
        "job": "build",
        "worker_events": [{"kind": "stdin_hang", "at": 5, "text": "stdin blocked"}],
        "log_series": [{"at": 6.0, "size": 512}],
        "step_events": [{
            "seq": 1, "kind": "exec", "text": "uv run", "duration_ms": 20,
            "ok": True, "at": 7,
        }],
        "tokens": 123,
        "tokens_source": "codex_exit",
    })
    detail.workers = [worker]
    detail.pick_records = [PickRecord(
        at="2026-10-09T12:00:00Z", job="build", slices=["C1"], tier="standard",
        candidates=[PickCandidate(model="claude", effort="high", group="opus", reason="ok")],
        assigned=["C1=claude"], skipped=[], is_repick=False,
    )]

    dumped = detail.model_dump(mode="json")
    assert dumped["workers"][0]["worker_events"][0] == {
        "kind": "stdin_hang", "at": 5, "text": "stdin blocked",
    }
    restored = SessionDetail.model_validate(dumped)

    assert restored.model_dump(mode="json") == dumped


def test_session_summary_keeps_original_names() -> None:
    summary = SessionSummary(
        id="s",
        project="p",
        path="/p",
        rev=1,
        status="idle",
        counts=Counts(running=0, finished=0, exited=0, unknown=0),
        last_activity_at=None,
        latest_milestone=None,
        findings_total=0,
        running_workers=[],
    )
    keys = set(summary.model_dump(mode="json"))

    assert OLD_SESSION_SUMMARY_FIELDS <= keys
    assert keys == OLD_SESSION_SUMMARY_FIELDS | NEW_SESSION_SUMMARY_FIELDS


def test_worker_detail_and_log_chunk_defaults() -> None:
    detail = WorkerDetail(worker=_worker(), brief_md=None, report_md=None)
    data = detail.model_dump(mode="json")

    assert data["artifacts"] == []
    assert data["brief_truncated"] is False
    assert data["report_truncated"] is False

    chunk = LogChunk(
        stem="w1", format="text", size=0, offset=0, truncated=False, lines=[]
    )
    assert "error" not in chunk.model_dump(mode="json")

    failed = chunk.model_copy(update={"error": "unreadable"})
    assert failed.model_dump(mode="json")["error"] == "unreadable"


def test_log_chunk_omits_null_error_key() -> None:
    chunk = LogChunk(
        stem="w1", format="text", size=0, offset=0, truncated=False, lines=[]
    )
    data = chunk.model_dump(mode="json")

    assert set(data) == {"stem", "format", "size", "offset", "truncated", "lines"}
    assert "error" not in chunk.model_dump_json()


def test_log_chunk_keeps_non_null_error() -> None:
    chunk = LogChunk(
        stem="w1",
        format="text",
        size=0,
        offset=0,
        truncated=False,
        lines=[],
        error="unreadable",
    )
    data = chunk.model_dump(mode="json")

    assert set(data) == {"stem", "format", "size", "offset", "truncated", "lines", "error"}
    assert data["error"] == "unreadable"


def test_quota_models_expose_no_secret_fields() -> None:
    usage = QuotaUsage(offline=True, generated_at=1.0, fetched_at=2.0, error_code="timeout")
    pick = PickResult(job="build", tier_used="small", stretched=False)

    assert set(usage.model_dump(mode="json")) == {
        "offline",
        "generated_at",
        "fetched_at",
        "error_code",
        "subscriptions",
        "credits",
    }
    assert set(pick.model_dump(mode="json")) == {
        "job",
        "generated_at",
        "tier_used",
        "stretched",
        "lead",
        "fallback",
        "candidates",
        "assignment",
        "skipped",
        "warnings",
    }
    assert set(QuotaUsage().model_dump(mode="json")) == set(usage.model_dump(mode="json"))


def test_quota_scalars_default_to_null_and_collections_to_empty() -> None:
    usage = QuotaUsage().model_dump(mode="json")
    pick = PickResult().model_dump(mode="json")

    assert usage == {
        "offline": None,
        "generated_at": None,
        "fetched_at": None,
        "error_code": None,
        "subscriptions": [],
        "credits": [],
    }
    assert {key: value for key, value in pick.items() if value is not None and value != []} == {}
    assert pick["candidates"] == [] and pick["assignment"] == [] and pick["warnings"] == []
