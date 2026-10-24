"""Per-step tool, duration and outcome (ported from the depth slice 4 branch, parser half).

The parser tests feed the three shapes that do carry a step's own start and end: an opencode tool
part, a claude/cursor tool call paired with its result, and a codex command with its exit line.
Everything else has no such data, and must stay empty so the row can show a dash.

The s4 rendering half of that file is not ported: it tested ``render._rd_g_step_line`` and
``UIState.gopen``, both of which belong to the renderer slices on this branch.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from logparse import STEP_FAILED, STEP_OK, parse_log
from model import LogState

CODEX_HEAD = [
    "Reading additional input from stdin...",
    "OpenAI Codex v0.161.0",
    "--------",
    "workdir: /work/.stage/probe",
    "model: gpt-6-luna",
    "reasoning effort: medium",
    "--------",
]


def _oc(**obj) -> str:
    return json.dumps(obj)


class StepLogCase(unittest.TestCase):
    """A temp directory to parse logs from."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def path(self, lines: list[str], name: str = "worker.log") -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return path

    def parse(self, lines: list[str], name: str = "worker.log") -> LogState:
        return parse_log(self.path(lines, name), now=1007.0)


class OpencodeStepDetail(StepLogCase):
    """An opencode tool part carries its own start and end times and its status."""

    def _tool(self, status: str, timing: dict[str, int] | None) -> str:
        state: dict[str, object] = {"status": status, "input": {"filePath": "/a.txt"}}
        if timing is not None:
            state["time"] = timing
        return _oc(type="tool_use", timestamp=1_001_000, part={"tool": "read", "state": state})

    def test_duration_comes_from_the_part_timestamps(self) -> None:
        state = self.parse([self._tool("completed", {"start": 1_000_750, "end": 1_001_000})])
        step = state.steps[-1]
        self.assertEqual(step.tool, "read")
        self.assertEqual(step.outcome, STEP_OK)
        self.assertAlmostEqual(step.dur or 0.0, 0.25, places=3)

    def test_a_running_part_has_no_end_time_so_no_duration_and_no_outcome(self) -> None:
        state = self.parse([self._tool("running", {"start": 1_000_750})])
        step = state.steps[-1]
        self.assertIsNone(step.dur)
        self.assertEqual(step.outcome, "")

    def test_a_part_without_timing_or_status_stays_missing(self) -> None:
        state = self.parse([_oc(type="tool_use", timestamp=1_001_000, part={"tool": "shell", "state": {}})])
        step = state.steps[-1]
        self.assertIsNone(step.dur)
        self.assertEqual(step.outcome, "")

    def test_an_error_part_is_a_failed_step_with_its_duration(self) -> None:
        state = self.parse([self._tool("error", {"start": 1_000_000, "end": 1_002_500})])
        step = state.steps[-1]
        self.assertEqual(step.outcome, STEP_FAILED)
        self.assertAlmostEqual(step.dur or 0.0, 2.5, places=3)

    def test_start_after_end_gives_no_duration(self) -> None:
        state = self.parse([self._tool("completed", {"start": 1_002_000, "end": 1_001_000})])
        self.assertIsNone(state.steps[-1].dur)

    def test_a_zero_length_part_keeps_zero_not_none(self) -> None:
        state = self.parse([self._tool("completed", {"start": 1_001_000, "end": 1_001_000})])
        self.assertEqual(state.steps[-1].dur, 0.0)

    def test_the_harness_error_event_is_a_failure(self) -> None:
        state = self.parse([_oc(type="error", timestamp=1_006_000, error={"message": "boom"})])
        step = state.steps[-1]
        self.assertEqual(step.kind, "error")
        self.assertEqual(step.outcome, STEP_FAILED)
        self.assertIsNone(step.dur)


class StreamStepDetail(StepLogCase):
    """Claude and cursor streams pair a tool call with the result that answers it."""

    def _init(self) -> str:
        return json.dumps({"type": "system", "subtype": "init", "claude_code_version": "2.0", "model": "claude-x"})

    def _tool_use(self, call_id: str, ts: str) -> str:
        return json.dumps({"type": "assistant", "timestamp": ts, "message": {"content": [
            {"type": "tool_use", "id": call_id, "name": "Read", "input": {"file_path": "/work/a.txt"}}]}})

    def _result(self, call_id: str, ts: str, error: bool = False) -> str:
        block: dict[str, object] = {"type": "tool_result", "tool_use_id": call_id, "content": "ok"}
        if error:
            block["is_error"] = True
        return json.dumps({"type": "user", "timestamp": ts, "message": {"content": [block]}})

    def test_a_result_gives_the_step_its_duration_and_an_ok_outcome(self) -> None:
        state = self.parse([
            self._init(),
            self._tool_use("t1", "2026-10-24T10:00:00.100Z"),
            self._result("t1", "2026-10-24T10:00:00.900Z"),
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.outcome, STEP_OK)
        self.assertAlmostEqual(step.dur or 0.0, 0.8, places=3)

    def test_a_failed_result_is_a_failed_step_and_still_an_error_step(self) -> None:
        state = self.parse([
            self._init(),
            self._tool_use("t1", "2026-10-24T10:00:00.000Z"),
            self._result("t1", "2026-10-24T10:00:00.500Z", error=True),
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.outcome, STEP_FAILED)
        self.assertAlmostEqual(step.dur or 0.0, 0.5, places=3)
        self.assertEqual(state.steps[-1].kind, "error")       # unchanged: the error step is still made
        self.assertEqual(state.steps[-1].outcome, STEP_FAILED)
        self.assertEqual(state.errors, 1)

    def test_an_unanswered_tool_call_keeps_both_fields_missing(self) -> None:
        state = self.parse([
            self._init(),
            self._tool_use("t1", "2026-10-24T10:00:00.100Z"),
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertIsNone(step.dur)
        self.assertEqual(step.outcome, "")

    def test_a_result_in_the_next_read_closes_the_step(self) -> None:
        path = self.path([self._init(), self._tool_use("t1", "2026-10-24T10:00:00.100Z")])
        state = parse_log(path, now=1007.0)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(self._result("t1", "2026-10-24T10:00:01.100Z") + "\n")
        state = parse_log(path, prev=state, now=1008.0)
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.outcome, STEP_OK)
        self.assertAlmostEqual(step.dur or 0.0, 1.0, places=3)
        self.assertEqual(sum(s.kind == "tool" for s in state.steps), 1)

    def test_events_without_timestamps_get_no_duration(self) -> None:
        state = self.parse([
            self._init(),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/a"}}]}}),
            json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}),
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertIsNone(step.dur)
        self.assertEqual(step.outcome, STEP_OK)


class CodexStepDetail(StepLogCase):
    """A codex result line gives the command step both its outcome and the duration it prints."""

    def _codex(self, exit_line: str) -> LogState:
        return self.parse(CODEX_HEAD + ["codex", "Running it.", "exec", '/usr/bin/bash -lc "ls" in /x', exit_line])

    def test_a_command_that_succeeded_is_ok_with_the_duration_it_printed(self) -> None:
        step = next(s for s in self._codex(" succeeded in 1.2s:").steps if s.kind == "tool")
        self.assertEqual(step.outcome, STEP_OK)
        self.assertAlmostEqual(step.dur or 0.0, 1.2, places=3)

    def test_a_command_that_failed_is_failed_and_keeps_its_duration(self) -> None:
        state = self._codex(" exited 2 in 0.4s:")
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.outcome, STEP_FAILED)
        self.assertAlmostEqual(step.dur or 0.0, 0.4, places=3)
        self.assertEqual(state.steps[-1].kind, "error")
        self.assertEqual(state.steps[-1].outcome, STEP_FAILED)

    def test_a_millisecond_result_line_is_read_as_seconds(self) -> None:
        step = next(s for s in self._codex(" succeeded in 812ms:").steps if s.kind == "tool")
        self.assertAlmostEqual(step.dur or 0.0, 0.812, places=3)

    def test_a_result_line_without_a_known_duration_leaves_it_missing(self) -> None:
        step = next(s for s in self._codex(" succeeded in a while:").steps if s.kind == "tool")
        self.assertEqual(step.outcome, STEP_OK)
        self.assertIsNone(step.dur)


if __name__ == "__main__":
    unittest.main()
