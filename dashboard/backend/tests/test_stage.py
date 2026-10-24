import json
import os
import time
from pathlib import Path

import pytest

from stagewatch import procinfo
from stagewatch.models import Finding, Review, SessionDetail, Worker
from stagewatch.stage import (
    ModelIdentity,
    StageParser,
    _findings_by_priority,
    engine_from_evidence,
    parse_brief,
    parse_findings,
    parse_milestones,
    parse_model_identity,
    parse_summary,
    summary_of,
    worker_log_sizes,
    worker_stems,
)


def _jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(record) for record in records) + "\n"


CLAUDE_INIT = {
    "type": "system",
    "subtype": "init",
    "model": "claude-x",
    "tools": ["Bash"],
    "session_id": "s",
}
CLAUDE_TEXT = {
    "type": "assistant",
    "message": {
        "id": "msg_1",
        "model": "claude-x",
        "content": [{"type": "text", "text": "working"}],
    },
    "session_id": "s",
}
CURSOR_INIT = {
    "type": "system",
    "subtype": "init",
    "apiKeySource": "login",
    "session_id": "s",
    "model": "Composer",
}
CURSOR_TOOL = {
    "type": "tool_call",
    "subtype": "started",
    "call_id": "1",
    "tool_call": {"readToolCall": {"args": {"path": "a.ts"}}},
    "session_id": "s",
}
OPENCODE_TEXT = {
    "type": "text",
    "sessionID": "ses_1",
    "part": {"type": "text", "text": "reading"},
}


def _build(stage: Path) -> tuple[StageParser, SessionDetail]:
    parser = StageParser(stage)
    scan = parser.scan()
    return parser, parser.build("sid", stage.parent.name, scan)


def _worker(detail: SessionDetail, name: str) -> Worker:
    return next(worker for worker in detail.workers if worker.name == name)


def _stage(tmp_path: Path, project: str = "proj") -> Path:
    stage = tmp_path / project / ".stage"
    stage.mkdir(parents=True)
    return stage


# --------------------------------------------------------------------------- #
# Worker identification
# --------------------------------------------------------------------------- #
def test_worker_identification_excludes_reviews_and_extras(make_stage: Path) -> None:
    parser = StageParser(make_stage)
    scan = parser.scan()

    stems = worker_stems(scan)

    assert stems == {"live", "dead", "prose", "empty", "done", "blank"}
    assert "review-name" not in stems
    assert "final-test" not in stems
    # The nested `.stage/w` must not surface as a worker.
    assert "w" not in stems


def test_review_and_final_stems_are_never_workers(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "review-x.log").write_text("- [P1] finding\n")
    (stage / "final-test.log").write_text("1 passed\n")
    (stage / "briefs").mkdir()
    (stage / "briefs" / "review-x.md").write_text("# Review brief\n")

    _, detail = _build(stage)

    assert detail.workers == []
    assert [review.name for review in detail.reviews] == ["review-x"]
    assert [extra.name for extra in detail.extra_logs] == ["final-test"]


# --------------------------------------------------------------------------- #
# Status table
# --------------------------------------------------------------------------- #
def test_status_table_rows(make_stage: Path) -> None:
    _, detail = _build(make_stage)

    assert _worker(detail, "live").status == "running"
    assert _worker(detail, "dead").status == "exited"
    assert _worker(detail, "done").status == "finished"
    assert _worker(detail, "blank").status == "unknown"
    assert _worker(detail, "empty").status == "unknown"
    assert _worker(detail, "prose").status == "unknown"


def test_running_guard_rejects_pid_reuse(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    pid_file = stage / "w.pid"
    pid_file.write_text(str(os.getpid()))
    (stage / "w.log").write_text("working\n")

    # A pid file older than the process start cannot belong to it.
    old = time.time() - 1000
    os.utime(pid_file, (old, old))

    _, detail = _build(stage)

    assert _worker(detail, "w").status == "exited"


def test_running_status_when_alive_with_fresh_pid_file(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    pid_file = stage / "w.pid"
    pid_file.write_text(str(os.getpid()))
    (stage / "w.log").write_text("working\n")

    # Anchor the pid file mtime to this process's own start so the test does
    # not depend on how long pytest has already been running.
    started = procinfo.start_time(os.getpid())
    assert started is not None
    os.utime(pid_file, (started, started))

    _, detail = _build(stage)

    worker = _worker(detail, "w")
    assert worker.status == "running"
    assert worker.started_at is not None
    assert worker.started_at <= time.time() + 1


def test_running_status_when_process_started_60s_before_pid_file(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    pid_file = stage / "w.pid"
    pid_file.write_text(str(os.getpid()))
    (stage / "w.log").write_text("working\n")

    # A launcher may write the pid file well after the process is up. Within
    # the 120s reuse window the worker is still the one that wrote the file.
    started = procinfo.start_time(os.getpid())
    assert started is not None
    written = started + 60
    os.utime(pid_file, (written, written))

    _, detail = _build(stage)

    assert _worker(detail, "w").status == "running"


def test_running_rejects_recycled_pid_started_before_pid_file(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    pid_file = stage / "w.pid"
    pid_file.write_text(str(os.getpid()))
    (stage / "w.log").write_text("working\n")

    # The pid file claims a start well after this process began, so it cannot
    # have been written by this process at startup: the pid has been recycled.
    # Only the file mtime changes; size and content are untouched.
    future = time.time() + 3600
    os.utime(pid_file, (future, future))

    _, detail = _build(stage)

    assert _worker(detail, "w").status == "exited"


# --------------------------------------------------------------------------- #
# Flags
# --------------------------------------------------------------------------- #
def test_flags_empty_report_and_no_log(make_stage: Path) -> None:
    _, detail = _build(make_stage)

    assert set(_worker(detail, "blank").flags) == {"empty_report", "no_log"}
    assert set(_worker(detail, "done").flags) == {"no_log"}
    assert set(_worker(detail, "empty").flags) == {"empty_log"}
    assert _worker(detail, "live").flags == []


def test_stdin_hang_flag(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "h.pid").write_text("999999999")
    (stage / "h.log").write_text("\nReading additional input from stdin... waiting\n")

    _, detail = _build(stage)

    assert "stdin_hang" in _worker(detail, "h").flags


def test_stale_flag_on_running_worker_with_old_log(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "w.pid").write_text(str(os.getpid()))
    log = stage / "w.log"
    log.write_text("still going\n")
    old = time.time() - 1000
    os.utime(log, (old, old))

    _, detail = _build(stage)

    worker = _worker(detail, "w")
    assert worker.status == "running"
    assert "stale" in worker.flags


# --------------------------------------------------------------------------- #
# Field sources
# --------------------------------------------------------------------------- #
def test_model_from_model_file(make_stage: Path) -> None:
    _, detail = _build(make_stage)

    assert _worker(detail, "dead").model == "gpt-6-luna"
    assert _worker(detail, "empty").model == "claude-x"


def test_brief_title_and_goal(make_stage: Path) -> None:
    _, detail = _build(make_stage)

    live = _worker(detail, "live")
    assert (live.title, live.goal, live.has_brief) == (
        "Live worker title",
        "keep the loop going",
        True,
    )
    prose = _worker(detail, "prose")
    assert (prose.title, prose.goal) == ("Prose task", "write prose")


def test_brief_exact_match_preferred_over_retry(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "briefs").mkdir()
    (stage / "foo.pid").write_text("999999999")
    exact = stage / "briefs" / "foo.md"
    exact.write_text("# Exact\n\nGoal: exact goal\n")
    (stage / "briefs" / "foo-retry.md").write_text("# Retry\n\nGoal: retry goal\n")

    parser, detail = _build(stage)
    assert _worker(detail, "foo").title == "Exact"

    exact.unlink()
    scan = parser.scan()
    detail = parser.build("sid", "proj", scan)
    assert _worker(detail, "foo").title == "Retry"


def test_reviews_and_findings_dedupe(make_stage: Path) -> None:
    _, detail = _build(make_stage)

    assert [review.name for review in detail.reviews] == ["review-name"]
    review = detail.reviews[0]
    assert [(finding.priority, finding.text) for finding in review.findings] == [
        ("P1", "Null deref: crashes on empty input"),
        ("P2", "Slow path on large batches"),
    ]
    assert detail.findings_total == 2


def test_extra_log_kind_and_last_line(make_stage: Path) -> None:
    _, detail = _build(make_stage)

    assert len(detail.extra_logs) == 1
    extra = detail.extra_logs[0]
    assert (extra.name, extra.kind, extra.last_line) == ("final-test", "check", "12 passed")


def test_log_role_change_is_not_served_from_stale_cache(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "foo.log").write_text("plain line\n")
    parser, detail = _build(stage)
    assert detail.workers == []
    assert [extra.name for extra in detail.extra_logs] == ["foo"]

    # The log itself is untouched; only a pid file appears, making it a worker.
    (stage / "foo.pid").write_text("999999999")
    scan = parser.scan()
    detail = parser.build("sid", "proj", scan)

    worker = _worker(detail, "foo")
    assert worker.status == "exited"
    assert worker.log_format == "text"


def test_milestones_newest_first(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "PROGRESS.md").write_text("first\n\nsecond\nthird\n")

    _, detail = _build(stage)

    assert detail.milestones == ["third", "second", "first"]


# --------------------------------------------------------------------------- #
# Pure parsers
# --------------------------------------------------------------------------- #
def test_parse_brief_title_and_goal_continuation() -> None:
    text = "# Title here\n\nGoal: first line\n  second line\nthird line\n\n# Later\nignored\n"

    title, goal = parse_brief(text)

    assert title == "Title here"
    assert goal == "first line second line third line"


def test_parse_brief_goal_capped_at_400() -> None:
    _, goal = parse_brief("Goal: " + "x" * 500)

    assert goal is not None
    assert len(goal) == 400


def test_parse_brief_missing_fields() -> None:
    assert parse_brief("no title no goal\n") == (None, None)


def test_parse_summary_takes_at_most_four() -> None:
    text = "## Summary\na\n\nb\nc\nd\ne\n\n## Next\nf\n"

    assert parse_summary(text) == ["a", "b", "c", "d"]


def test_parse_summary_falls_back_to_not_met() -> None:
    text = "## Plan requirements not met\n- item one\n- item two\n- item three\n"

    assert parse_summary(text) == ["not met: - item one", "not met: - item two"]


def test_parse_findings_dedupes_before_em_dash() -> None:
    lines = [
        "- [P1] Alpha \u2014 one detail",
        "- [P2] Alpha \u2014 another detail",
        "not a finding",
        "- [P1] Beta",
    ]

    findings = parse_findings(lines)

    assert [(finding.priority, finding.text) for finding in findings] == [
        ("P1", "Alpha \u2014 one detail"),
        ("P1", "Beta"),
    ]


def test_parse_milestones_drops_blank_lines() -> None:
    assert parse_milestones("one\n\n  \ntwo\n") == ["two", "one"]


def test_review_prefixed_stem_with_pid_is_a_worker(tmp_path: Path) -> None:
    # A live review agent owns .pid/.model/.log/briefs and a REPORT: it is a
    # worker that stage marker counts, not a review artifact.
    stage = _stage(tmp_path)
    (stage / "review-live.pid").write_text("999999999\n")
    (stage / "review-live.model").write_text("cursor-grok-4.6-medium (review)\n")
    (stage / "review-live.log").write_text("reviewing\n")
    (stage / "briefs").mkdir(exist_ok=True)
    (stage / "briefs" / "review-live.md").write_text(
        "# Review live\n\nGoal: check.\n\nDelegation: none\n"
    )

    parser, detail = _build(stage)
    stems = worker_stems(parser.scan())

    assert "review-live" in stems
    assert [review.name for review in detail.reviews] == []


# --------------------------------------------------------------------------- #
# Engine classification
# --------------------------------------------------------------------------- #
def _add_worker(stage: Path, stem: str, log: str, model: str | None = None) -> None:
    (stage / f"{stem}.pid").write_text("999999999\n")
    (stage / f"{stem}.log").write_text(log)
    if model is not None:
        (stage / f"{stem}.model").write_text(model + "\n")


def test_engine_classification_for_all_four_and_unknown(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "op", _jsonl(OPENCODE_TEXT))
    _add_worker(stage, "cx", "OpenAI Codex v1\nmodel: gpt-x\n")
    _add_worker(stage, "cl", _jsonl(CLAUDE_INIT, CLAUDE_TEXT))
    _add_worker(stage, "cu", _jsonl(CURSOR_INIT, CURSOR_TOOL))
    _add_worker(stage, "un", "plain prose with no signature\n")

    _, detail = _build(stage)

    assert _worker(detail, "op").engine == "opencode"
    assert _worker(detail, "cx").engine == "codex"
    assert _worker(detail, "cl").engine == "claude"
    assert _worker(detail, "cu").engine == "cursor"
    assert _worker(detail, "un").engine == "unknown"


def test_log_signature_is_not_overridden_by_a_claude_model_name(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "op", _jsonl(OPENCODE_TEXT), model="claude-haiku-5-5")
    _add_worker(stage, "cu", _jsonl(CURSOR_TOOL), model="claude-haiku-5-5")

    _, detail = _build(stage)

    assert _worker(detail, "op").engine == "opencode"
    assert _worker(detail, "cu").engine == "cursor"


def test_engine_from_model_evidence_for_a_plain_text_log(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    models = {
        "cl": "claude-haiku-5-5 high (work)",
        "cx": "codex gpt-6-luna medium",
        "op": "opencode-go/deepseek-v4.1-flash",
        "cu": "cursor-grok-4.6-medium",
        "un": "mystery-model",
    }
    for stem, model in models.items():
        _add_worker(stage, stem, "plain prose\n", model=model)

    _, detail = _build(stage)

    assert _worker(detail, "cl").engine == "claude"
    assert _worker(detail, "cx").engine == "codex"
    assert _worker(detail, "op").engine == "opencode"
    assert _worker(detail, "cu").engine == "cursor"
    assert _worker(detail, "un").engine == "unknown"


def test_running_worker_carries_its_engine(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "w", _jsonl(OPENCODE_TEXT))
    (stage / "w.pid").write_text(str(os.getpid()))

    _, detail = _build(stage)

    assert _worker(detail, "w").status == "running"
    assert detail.running_workers[0].engine == "opencode"


def test_engine_from_evidence_prefers_the_log_signature() -> None:
    assert engine_from_evidence("opencode-jsonl", "claude-x") == "opencode"
    assert engine_from_evidence("cursor-jsonl", "claude-x") == "cursor"
    assert engine_from_evidence("cursor-text", "codex gpt-6") == "cursor"
    assert engine_from_evidence("claude-stream-json", "cursor-x") == "claude"
    assert engine_from_evidence("codex-text", "claude-x") == "codex"
    assert engine_from_evidence("text", "claude-haiku-5-5") == "claude"
    assert engine_from_evidence("text", None) == "unknown"


# --------------------------------------------------------------------------- #
# Contract population: lead, previews, timestamps, artifacts
# --------------------------------------------------------------------------- #
def test_stream_heading_fills_lead_and_cycle_progress(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.md").write_text("## Build — cycle 2 — 2026-10-09 12:30 UTC\n")

    _, detail = _build(stage)

    assert detail.lead is not None
    assert (detail.lead.confidence, detail.lead.evidence) == ("explicit-stream", "build cycle 2")
    assert detail.lead.source_path == "stream.md"
    assert detail.stream == "stream.md"
    assert detail.cycle_progress is not None
    assert (detail.cycle_progress.phase, detail.cycle_progress.current) == ("build", 2)
    assert detail.cycle_progress.over_policy is False
    assert [(event.phase, event.cycle, event.label) for event in detail.history] == [
        ("build", 2, "2026-10-09 12:30 UTC")
    ]


def test_lead_json_fills_lead_and_cycle_progress(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "lead.json").write_text(
        json.dumps({"schema": 1, "lead_label": "Lead", "phase": "review", "cycle": 1})
    )

    _, detail = _build(stage)

    assert detail.lead is not None
    assert (detail.lead.label, detail.lead.confidence) == ("Lead", "explicit-stream")
    assert detail.lead.source_path == "lead.json"
    assert detail.cycle_progress is not None
    assert (detail.cycle_progress.phase, detail.cycle_progress.current) == ("review", 1)


def test_progress_supplies_lead_when_stream_has_no_heading(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.md").write_text("Notes with no phase heading\n")
    (stage / "PROGRESS.md").write_text("10:00 build cycle 2 started\n")

    _, detail = _build(stage)

    assert detail.lead is not None
    assert (detail.lead.confidence, detail.lead.evidence) == ("explicit-progress", "build cycle 2")
    assert detail.lead.source_path == "PROGRESS.md"
    assert detail.cycle_progress is not None
    assert (detail.cycle_progress.phase, detail.cycle_progress.current) == ("build", 2)


def test_oversized_stream_heading_does_not_override_progress(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.md").write_bytes(b"## Build cycle 2\n" + b"x" * (64 * 1024))
    (stage / "PROGRESS.md").write_text("10:00 review cycle 1 started\n")

    _, detail = _build(stage)

    assert detail.lead is not None
    assert (detail.lead.confidence, detail.lead.evidence) == (
        "explicit-progress",
        "review cycle 1",
    )
    assert detail.lead.source_path == "PROGRESS.md"


def test_no_lead_evidence_is_missing(tmp_path: Path) -> None:
    _, detail = _build(_stage(tmp_path))

    assert detail.lead is not None
    assert detail.lead.confidence == "missing"
    assert detail.cycle_progress is None
    assert detail.source_warnings == []


def test_conflicting_lead_evidence_is_unresolved(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.md").write_text("## Build cycle 1\n")
    (stage / "PROGRESS.md").write_text("10:00 review cycle 1 started\n")

    _, detail = _build(stage)

    assert detail.lead is not None
    assert detail.lead.confidence == "unresolved"
    assert detail.cycle_progress is None
    assert len(detail.source_warnings) == 1


def test_non_running_workers_are_previewed(make_stage: Path) -> None:
    _, detail = _build(make_stage)

    previews = {preview.name: preview for preview in detail.worker_previews}
    assert set(previews) == {worker.name for worker in detail.workers}
    assert detail.worker_total == len(detail.workers) == 6
    assert previews["done"].status == "finished"
    assert previews["dead"].status == "exited"
    assert previews["dead"].model == "gpt-6-luna"
    assert summary_of(detail).worker_total == 6


def test_duration_and_log_mtime_from_timestamps(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "fin.pid").write_text("999999999\n")
    (stage / "fin.log").write_text("done\n")
    (stage / "fin").mkdir()
    (stage / "fin" / "REPORT.md").write_text("## Summary\n- ok\n")
    base = 1_700_000_000
    os.utime(stage / "fin.pid", (base, base))
    os.utime(stage / "fin.log", (base + 90, base + 90))
    os.utime(stage / "fin" / "REPORT.md", (base + 100, base + 100))

    _, detail = _build(stage)

    worker = _worker(detail, "fin")
    assert worker.status == "finished"
    assert worker.duration_s == pytest.approx(100.0)
    assert worker.log_mtime == pytest.approx(base + 90)


def test_worker_artifacts_and_log_index(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "fin.pid").write_text("999999999\n")
    (stage / "fin.log").write_text("done\n")
    (stage / "fin.patch").write_text("--- a\n+++ b\n")
    (stage / "briefs").mkdir()
    (stage / "briefs" / "fin.md").write_text("# Fin\n\nGoal: ship\n")
    (stage / "fin").mkdir()
    (stage / "fin" / "REPORT.md").write_text("## Summary\n- ok\n")

    _, detail = _build(stage)

    kinds = {artifact.name: artifact.kind for artifact in detail.artifacts}
    assert kinds == {
        "briefs/fin.md": "brief",
        "fin/REPORT.md": "report",
        "fin.log": "log",
        "fin.patch": "patch",
    }
    assert all(artifact.preview_url is None for artifact in detail.artifacts)
    assert _worker(detail, "fin").artifact_count == 4
    assert detail.log_index is not None
    assert [
        (entry.session_id, entry.worker_name, entry.artifact_name, entry.kind)
        for entry in detail.log_index
    ] == [("sid", "fin", "fin.log", "log")]
    assert all(entry.preview_url is None for entry in detail.log_index)


def test_symlinks_and_escaping_paths_are_not_artifacts(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside\n")
    (outside / "REPORT.md").write_text("## Summary\n- leaked\n")
    # A worker whose log and patch are symlinks out of the stage.
    (stage / "leak.pid").write_text("999999999\n")
    (stage / "leak.log").symlink_to(outside / "secret.txt")
    (stage / "leak.patch").symlink_to(outside / "secret.txt")
    (stage / "briefs").mkdir()
    (stage / "briefs" / "leak.md").symlink_to(outside / "secret.txt")
    # A worker whose whole directory is a symlink out of the stage.
    (stage / "esc.pid").write_text("999999999\n")
    (stage / "esc").symlink_to(outside, target_is_directory=True)

    _, detail = _build(stage)

    names = {artifact.name for artifact in detail.artifacts}
    assert not names & {"leak.log", "leak.patch", "briefs/leak.md", "esc/REPORT.md"}
    assert _worker(detail, "leak").artifact_count == 0
    assert _worker(detail, "esc").artifact_count == 0
    assert detail.log_index is None or all(
        entry.worker_name not in {"leak", "esc"} for entry in detail.log_index
    )


def test_secret_like_names_are_not_artifacts(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "auth.pid").write_text("999999999\n")
    (stage / "auth.log").write_text("ok\n")
    (stage / "auth.json").write_text('{"token": "not-real"}\n')

    _, detail = _build(stage)

    assert {artifact.name for artifact in detail.artifacts} == {"auth.log"}
    assert detail.log_index is not None
    assert [entry.artifact_name for entry in detail.log_index] == ["auth.log"]


# --------------------------------------------------------------------------- #
# Contract fields D1-D8
# --------------------------------------------------------------------------- #
CODEX_LOG = (
    "OpenAI Codex v0.161.0\n"
    "--------\n"
    "workdir: /tmp/work\n"
    "model: gpt-6-luna\n"
    "provider: openai\n"
    "approval: never\n"
    "sandbox: {sandbox}\n"
    "reasoning effort: medium\n"
    "user\n"
    "do the thing\n"
    "codex\n"
    "exec\n"
    "bash -lc 'ls'\n"
    " succeeded in 3992ms:\n"
    "file.txt\n"
    "tokens used\n"
    "12,345\n"
    "## Summary\n"
    "- done\n"
)


@pytest.mark.parametrize(
    ("sandbox", "access"),
    [("read-only", "ro"), ("workspace-write", "rw"), ("something-new", None)],
)
def test_codex_log_header_gives_access_steps_and_tokens(
    tmp_path: Path, sandbox: str, access: str | None
) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "cx", CODEX_LOG.format(sandbox=sandbox), model="codex gpt-6-luna medium")

    _, detail = _build(stage)

    worker = _worker(detail, "cx")
    assert worker.engine_family == "codex"
    assert worker.effort == "medium"
    assert worker.access == access
    assert worker.access_source == ("codex_log_sandbox" if access else None)
    assert worker.tokens == 12345
    assert worker.tokens_source == "codex_exit"
    assert len(worker.step_events) == 1
    step = worker.step_events[0]
    assert (step.seq, step.kind, step.text, step.duration_ms, step.ok, step.at) == (
        1,
        "exec",
        "bash -lc 'ls'",
        3992,
        True,
        None,
    )


def test_opencode_json_gives_steps_and_summed_tokens(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    tool = {
        "type": "tool_use",
        "timestamp": 1791495539874,
        "sessionID": "ses_1",
        "part": {
            "type": "tool",
            "tool": "shell",
            "state": {"status": "completed", "input": {"command": "ls"}},
        },
    }
    finish = {
        "type": "step_finish",
        "sessionID": "ses_1",
        "reason": "tool-calls",
        "tokens": {"input": 10, "output": 5},
    }
    _add_worker(
        stage, "op", _jsonl(tool, finish), model="opencode-go/deepseek-v4.1-flash"
    )

    _, detail = _build(stage)

    worker = _worker(detail, "op")
    assert worker.engine_family == "opencode"
    assert worker.access is None
    assert worker.access_source is None
    assert worker.tokens == 15
    assert worker.tokens_source == "opencode_step_finish"
    assert [(step.kind, step.text, step.ok, step.at) for step in worker.step_events] == [
        ("shell", "ls", True, 1791495539874)
    ]


def test_prose_and_unknown_logs_leave_derived_fields_empty(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    # A `sandbox:` line without the Codex banner is prose, not a header.
    _add_worker(stage, "pr", "notes\nsandbox: read-only\n", model="mystery-model")
    _add_worker(stage, "nm", "plain prose only\n")

    _, detail = _build(stage)

    prose = _worker(detail, "pr")
    assert prose.engine_family == "unknown"
    assert prose.access is None
    assert prose.tokens is None
    assert prose.tokens_source is None
    assert prose.step_events == []
    assert prose.worker_events == []
    bare = _worker(detail, "nm")
    assert bare.engine_family is None
    assert bare.access is None


def test_worker_name_never_decides_the_engine_family(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "codex-named", "prose\n", model="claude-haiku-5-5")

    _, detail = _build(stage)

    worker = _worker(detail, "codex-named")
    assert worker.engine_family == "claude"
    assert worker.engine == "claude"


def test_worker_preview_copies_identity_from_worker(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "cw", "prose\n", model="claude-haiku-5-5 high (work)")
    _add_worker(stage, "cx", "prose\n", model="codex gpt-6-luna medium")

    _, detail = _build(stage)

    previews = {preview.name: preview for preview in detail.worker_previews}
    assert (
        previews["cw"].engine_family,
        previews["cw"].account,
        previews["cw"].effort,
    ) == ("claude-work", "work", "high")
    assert (
        previews["cx"].engine_family,
        previews["cx"].account,
        previews["cx"].effort,
    ) == ("codex", None, "medium")


def test_cursor_ask_mode_is_read_only(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "cu", "prose\n", model="composer-2.5 (mode ask)")

    _, detail = _build(stage)

    worker = _worker(detail, "cu")
    assert worker.engine_family == "cursor"
    assert worker.mode == "ask"
    assert worker.access == "ro"
    assert worker.access_source == "cursor_model_mode"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, ModelIdentity()),
        ("", ModelIdentity()),
        ("claude-haiku-5-5 (medium)", ModelIdentity("claude", None, "medium", None)),
        ("claude-haiku-5-5 high (work)", ModelIdentity("claude-work", "work", "high", None)),
        ("claude-haiku-5-5@work (medium)", ModelIdentity("claude-work", "work", "medium", None)),
        (
            "claude claude-sonnet-5-5 medium (work)",
            ModelIdentity("claude-work", "work", "medium", None),
        ),
        ("codex gpt-6-sol medium", ModelIdentity("codex", None, "medium", None)),
        ("opencode-go/deepseek-v4.1-flash", ModelIdentity("opencode", None, None, None)),
        ("cursor-grok-4.6-high", ModelIdentity("cursor", None, None, None)),
        ("mystery-model", ModelIdentity("unknown", None, None, None)),
    ],
)
def test_parse_model_identity(text: str | None, expected: ModelIdentity) -> None:
    assert parse_model_identity(text) == expected


def test_quota_pick_blocks_map_field_by_field(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.log").write_text(
        "job=build tierUsed=haiku\n"
        "haiku@high ok group: fast\n"
        "sonnet@medium runs-out\n"
        "assign: c2=haiku\n"
        "skipped: c3 over cap\n"
    )

    _, detail = _build(stage)

    assert [record.model_dump() for record in detail.pick_records] == [
        {
            "at": None,
            "job": "build",
            "slices": [],
            "tier": "haiku",
            "candidates": [
                {"model": "haiku", "effort": "high", "group": "fast", "reason": "ok"},
                {"model": "sonnet", "effort": "medium", "group": None, "reason": "runs-out"},
            ],
            "assigned": ["c2=haiku"],
            "skipped": ["c3 over cap"],
            "is_repick": False,
        }
    ]


def test_lead_json_picks_map_field_by_field(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    lead = {
        "schema": 1,
        "picks": [
            {
                "at": "2026-10-09T10:00Z",
                "job": "review",
                "slices": ["a"],
                "tier": "sonnet",
                "candidates": [{"model": "m", "effort": "low", "group": "g", "reason": "ok"}],
                "assigned": ["w=m"],
                "skipped": [],
                "is_repick": True,
            }
        ],
    }
    (stage / "lead.json").write_text(json.dumps(lead))

    _, detail = _build(stage)

    [record] = detail.pick_records
    assert record.at == "2026-10-09T10:00Z"
    assert record.job == "review"
    assert record.slices == ["a"]
    assert record.candidates[0].model == "m"
    assert record.assigned == ["w=m"]
    assert record.is_repick is True
    assert "source" not in record.model_dump()


@pytest.mark.parametrize("make_unreadable", [False, True])
def test_missing_or_unreadable_picks_give_no_records(tmp_path: Path, make_unreadable: bool) -> None:
    stage = _stage(tmp_path)
    if make_unreadable:
        # A directory where stream.log should be is not a regular file.
        (stage / "stream.log").mkdir()

    _, detail = _build(stage)

    assert detail.pick_records == []


def test_job_comes_from_a_pick_that_names_the_worker(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "c2", "prose\n")
    (stage / "stream.log").write_text(
        "job=build tierUsed=haiku\nassign: c2=haiku\n"
    )

    _, detail = _build(stage)

    assert _worker(detail, "c2").job == "build"


def test_job_is_review_for_a_review_named_worker(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "review-a", "prose\n")

    _, detail = _build(stage)

    assert _worker(detail, "review-a").job == "review"


def test_job_from_a_brief_first_line_and_none_otherwise(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "c1", "prose\n")
    _add_worker(stage, "c4", "prose\n")
    (stage / "briefs").mkdir()
    (stage / "briefs" / "c1.md").write_text("Job: build\n# C1 title\nGoal: do it\n")
    (stage / "briefs" / "c4.md").write_text("# C4 title\nJob: build\n")

    _, detail = _build(stage)

    assert _worker(detail, "c1").job == "build"
    assert _worker(detail, "c4").job is None


def test_retry_events_come_from_attempt_siblings_in_order(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "w", "prose\n")
    (stage / "w.attempt2.log").write_text("second\n")
    (stage / "w.attempt1.log").write_text("first\n")

    _, detail = _build(stage)

    events = _worker(detail, "w").worker_events
    assert [(event.kind, event.text) for event in events] == [
        ("retry", "attempt 1"),
        ("retry", "attempt 2"),
    ]
    assert all(isinstance(event.at, int) for event in events)


def test_stdin_hang_is_a_worker_event(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "h.pid").write_text("999999999")
    (stage / "h.log").write_text("Reading additional input from stdin...\n")

    _, detail = _build(stage)

    assert [event.kind for event in _worker(detail, "h").worker_events] == ["stdin_hang"]


def test_idle_event_for_a_quiet_running_log(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "w.pid").write_text(str(os.getpid()))
    log = stage / "w.log"
    log.write_text("still going\n")
    old = time.time() - 1000
    os.utime(log, (old, old))

    _, detail = _build(stage)

    assert [event.kind for event in _worker(detail, "w").worker_events] == ["idle"]


def test_empty_quiet_log_is_never_idle(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "w.pid").write_text(str(os.getpid()))
    log = stage / "w.log"
    log.write_text("")
    old = time.time() - 1000
    os.utime(log, (old, old))

    _, detail = _build(stage)

    worker = _worker(detail, "w")
    assert "stale" in worker.flags
    assert worker.worker_events == []


def test_worker_log_sizes_come_from_the_scan_only(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "w", "twelve bytes")
    (stage / "review-a.pid").write_text("999999999")
    (stage / "review-a.log").write_text("review\n")
    (stage / "nolog.pid").write_text("999999999")

    sizes = worker_log_sizes(StageParser(stage).scan())

    assert sizes == {"w": 12, "review-a": 7}


def test_parser_leaves_log_series_empty(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "w", "prose\n")

    _, detail = _build(stage)

    assert _worker(detail, "w").log_series == []


def test_worker_tokens_and_cost_reach_the_preview(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    finish = {
        "type": "step_finish",
        "sessionID": "ses_1",
        "tokens": {"input": 10, "output": 5},
        "part": {"cost": 0.4},
    }
    result = {"type": "result", "subtype": "success", "total_cost_usd": 1.25}
    _add_worker(stage, "op", _jsonl(finish), model="opencode-go/deepseek-v4.1-flash")
    _add_worker(stage, "cl", _jsonl(CLAUDE_INIT, result))
    _add_worker(stage, "pr", "prose\n")

    _, detail = _build(stage)

    previews = {preview.name: preview for preview in detail.worker_previews}
    assert (previews["op"].tokens, previews["op"].cost) == (15, pytest.approx(0.4))
    assert (previews["cl"].tokens, previews["cl"].cost) == (None, 1.25)
    assert (previews["pr"].tokens, previews["pr"].cost) == (None, None)


def test_session_totals_sum_known_values_only(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    finish = {"type": "step_finish", "tokens": {"input": 10, "output": 5}, "part": {"cost": 0.4}}
    result = {"type": "result", "subtype": "success", "total_cost_usd": 1.25}
    _add_worker(stage, "op", _jsonl(finish))
    _add_worker(stage, "cl", _jsonl(CLAUDE_INIT, result))
    _add_worker(stage, "pr", "prose\n")

    _, detail = _build(stage)
    summary = summary_of(detail)

    assert summary.tokens_total == 15
    assert summary.cost_total == pytest.approx(1.65)


def test_session_totals_are_none_when_no_worker_has_a_value(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "pr", "prose\n")

    _, detail = _build(stage)
    summary = summary_of(detail)

    assert (summary.tokens_total, summary.cost_total) == (None, None)


def test_findings_by_priority_always_has_three_keys(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    _add_worker(stage, "pr", "prose\n")

    _, detail = _build(stage)

    assert summary_of(detail).findings_by_priority == {"P1": 0, "P2": 0, "P3": 0}


def test_findings_by_priority_counts_reviews(make_stage: Path) -> None:
    # The shared review fixture holds one P1 and one P2 after dedupe.
    _, detail = _build(make_stage)

    assert summary_of(detail).findings_by_priority == {"P1": 1, "P2": 1, "P3": 0}


def test_findings_by_priority_folds_p0_into_p1_and_p4_into_p3() -> None:
    reviews = [
        Review(
            name="review-a",
            mtime=0.0,
            size=0,
            model=None,
            findings=[
                Finding(priority="P0", text="a"),
                Finding(priority="P1", text="b"),
                Finding(priority="P3", text="c"),
                Finding(priority="P4", text="d"),
            ],
        )
    ]

    assert _findings_by_priority(reviews) == {"P1": 2, "P2": 0, "P3": 2}
