"""Shared fixtures: a synthetic `.stage` directory covering the file kinds in project plan §1."""

import json
import os
from pathlib import Path

import pytest

OPENCODE_LOG = (
    "\n".join(
        [
            json.dumps({"type": "step_start", "sessionID": "ses_abc123", "part": {}}),
            json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_abc123",
                    "part": {"type": "text", "text": "Reading the brief now."},
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "sessionID": "ses_abc123",
                    "part": {
                        "type": "tool",
                        "tool": "bash",
                        "state": {"status": "completed", "input": {"command": "ls -la"}},
                    },
                }
            ),
            json.dumps({"type": "step_finish", "sessionID": "ses_abc123", "part": {}}),
        ]
    )
    + "\n"
)

CODEX_LOG = "OpenAI Codex v0.9.1\nmodel: gpt-6-luna\nreasoning effort: high\n\nRunning tests\n"

PROSE_LOG = "Starting the work.\nChecked the parser.\nAll green so far, writing the report.\n"

REVIEW_LOG = (
    "- [P1] Null deref: crashes on empty input\n"
    "- [P2] Slow path on large batches\n"
    "- [P1] Null deref: crashes on empty input\n"
)

DEAD_PID = "999999999"


@pytest.fixture
def make_stage(tmp_path: Path) -> Path:
    """Build a synthetic `<tmp>/myproj/.stage` directory and return it."""
    stage = tmp_path / "myproj" / ".stage"
    stage.mkdir(parents=True)

    def write(rel: str, text: str = "") -> None:
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    # Live worker: OpenCode JSONL log, no .model file.
    write("live.pid", str(os.getpid()))
    write("live.log", OPENCODE_LOG)
    write("briefs/live.md", "# Live worker title\n\nGoal: keep the loop going\n")

    # Dead worker: Codex text log and a .model file.
    write("dead.pid", DEAD_PID)
    write("dead.model", "gpt-6-luna\n")
    write("dead.log", CODEX_LOG)

    # Prose worker identified by brief plus log.
    write("prose.log", PROSE_LOG)
    write("briefs/prose.md", "# Prose task\n\nGoal: write prose\n")

    # Zero-byte log.
    write("empty.model", "claude-x\n")
    write("empty.log", "")

    # Finished worker, report only.
    write("done/REPORT.md", "## Summary\n- Finished the thing\n")

    # Empty report: the worker exists but has no finished status.
    write("blank/REPORT.md", "")

    # Review and extra logs.
    write("review-name.log", REVIEW_LOG)
    write("final-test.log", "12 passed\n")

    # Distractions that discovery must not follow: nested stage and dependencies.
    write("w/.stage/nested.pid", "1")
    write("w/node_modules/pkg/index.js", "module.exports = 1;\n")

    return stage


@pytest.fixture(autouse=True)
def _stage_roots_include_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Stage dirs in tests live under the pytest temp root, not the home dir."""
    from pathlib import Path

    from stagewatch import snapshot

    root = tmp_path_factory.getbasetemp()
    monkeypatch.setattr(snapshot, "allowed_roots", lambda: [Path.home(), root])
