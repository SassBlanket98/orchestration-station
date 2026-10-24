"""Logparse: event time on steps, the 30-minute tool ring, tool_rate and the context window.

Fixtures are tiny inline lines with synthetic times and paths; no real log content.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from logparse import parse_log, tool_rate
from model import TOOL_RING_MAX, TOOL_RING_S, LogState

T0 = "2026-10-24T10:00:00.000Z"


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _claude_init() -> str:
    return json.dumps({"type": "system", "subtype": "init", "claude_code_version": "2.1.0", "model": "claude-x"})


def _tool_use(call_id: str, ts: str | None, name: str = "Read") -> str:
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": call_id, "name": name, "input": {"file_path": "/work/a.txt"}}]}}
    if ts is not None:
        obj["timestamp"] = ts
    return json.dumps(obj)


def _result(call_id: str, ts: str | None, duration_ms: int | None = None, error: bool = False) -> str:
    block: dict[str, object] = {"type": "tool_result", "tool_use_id": call_id, "content": "ok"}
    if error:
        block["is_error"] = True
    obj: dict[str, object] = {"type": "user", "message": {"content": [block]}}
    if ts is not None:
        obj["timestamp"] = ts
    if duration_ms is not None:
        obj["tool_use_result"] = {"durationMs": duration_ms}
    return json.dumps(obj)


def _rollout(ts: str, kind: str, payload: dict) -> str:
    return json.dumps({"timestamp": ts, "type": kind, "payload": payload})


def _session_meta() -> str:
    return _rollout(T0, "session_meta", {"session_id": "s", "cwd": "/work"})


class LogCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def path(self, lines: list[str], name: str = "worker.log") -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return path

    def parse(self, lines: list[str], name: str = "worker.log", now: float = 1_800_000_000.0) -> LogState:
        return parse_log(self.path(lines, name), now=now)


class EventTime(LogCase):
    """A step is stamped with the event's own time when the log carries one."""

    def test_a_claude_step_takes_the_event_timestamp(self) -> None:
        state = self.parse([_claude_init(), _tool_use("t1", T0)])
        self.assertAlmostEqual(state.steps[-1].ts, epoch(T0), places=3)
        self.assertAlmostEqual(state.first_ts, epoch(T0), places=3)

    def test_a_claude_text_step_takes_it_too(self) -> None:
        line = json.dumps({"type": "assistant", "timestamp": T0,
                           "message": {"content": [{"type": "text", "text": "hello"}]}})
        state = self.parse([_claude_init(), line])
        self.assertAlmostEqual(state.steps[-1].ts, epoch(T0), places=3)

    def test_an_event_without_a_timestamp_falls_back_to_the_parse_time(self) -> None:
        path = self.path([_claude_init(), _tool_use("t1", None)])
        state = parse_log(path, now=1_800_000_000.0)
        self.assertEqual(state.steps[-1].ts, os.stat(path).st_mtime)

    def test_the_fallback_on_a_later_read_is_the_read_time(self) -> None:
        path = self.path([_claude_init()])
        state = parse_log(path, now=1007.0)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(_tool_use("t1", None) + "\n")
        state = parse_log(path, prev=state, now=1008.0)
        self.assertEqual(state.steps[-1].ts, 1008.0)

    def test_a_codex_rollout_item_carries_its_own_timestamp(self) -> None:
        state = self.parse([
            _session_meta(),
            _rollout(T0, "response_item", {"type": "custom_tool_call", "name": "exec",
                                           "call_id": "c1", "input": "ls"}),
        ])
        self.assertEqual(state.harness, "codex")
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.tool, "exec")
        self.assertAlmostEqual(step.ts, epoch(T0), places=3)

    def test_a_codex_rollout_output_gives_the_call_its_duration(self) -> None:
        state = self.parse([
            _session_meta(),
            _rollout(T0, "response_item", {"type": "custom_tool_call", "name": "exec",
                                           "call_id": "c1", "input": "ls"}),
            _rollout("2026-10-24T10:00:02.500Z", "response_item",
                     {"type": "custom_tool_call_output", "call_id": "c1", "output": "done"}),
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertAlmostEqual(step.dur or 0.0, 2.5, places=3)
        self.assertEqual(step.outcome, "")     # the output does not say whether it succeeded

    def test_opencode_keeps_its_millisecond_event_time(self) -> None:
        line = json.dumps({"type": "tool_use", "timestamp": 1_001_500,
                           "part": {"tool": "read", "state": {"status": "completed", "input": {"path": "/a"}}}})
        state = self.parse([line])
        self.assertEqual(state.steps[-1].ts, 1001.5)


class ToolRing(LogCase):
    """LogState.tool_pulses: tool calls at event second, last 30 minutes, merged per second."""

    def ring(self, lines: list[str], age: float = 10.0) -> LogState:
        """Parse at 10 s after the fixture's first event, so the ring is only asked to prune real expiry."""
        return self.parse(lines, now=epoch(T0) + age)

    def test_calls_in_the_same_second_merge(self) -> None:
        lines = [
            _claude_init(),
            _tool_use("t1", "2026-10-24T10:00:00.100Z"),
            _tool_use("t2", "2026-10-24T10:00:00.900Z"),
            _tool_use("t3", "2026-10-24T10:00:05.000Z"),
        ]
        state = self.ring(lines)
        self.assertEqual(state.tool_pulses,
                         [(int(epoch("2026-10-24T10:00:00Z")), 2), (int(epoch("2026-10-24T10:00:05Z")), 1)])

    def test_only_tool_steps_count(self) -> None:
        text = json.dumps({"type": "assistant", "timestamp": T0,
                           "message": {"content": [{"type": "text", "text": "words"}]}})
        state = self.ring([_claude_init(), text, _tool_use("t1", T0)])
        self.assertEqual(state.tool_pulses, [(int(epoch(T0)), 1)])

    def test_an_untimed_tool_step_adds_no_ring_entry(self) -> None:
        line = json.dumps({"type": "tool_use", "timestamp": float("nan"),
                           "part": {"tool": "read", "state": {"status": "completed", "input": {"path": "/a"}}}})
        state = self.parse([line])
        self.assertEqual(state.steps[-1].kind, "tool")
        self.assertEqual(state.tool_pulses, [])

    def test_calls_older_than_the_ring_are_pruned_against_now(self) -> None:
        old = epoch(T0)
        lines = [
            _claude_init(),
            _tool_use("t1", "2026-10-24T10:00:00.000Z"),   # 2000 s before the parse time
            _tool_use("t2", "2026-10-24T10:30:00.000Z"),
            _tool_use("t3", "2026-10-24T10:33:20.000Z"),
        ]
        state = self.ring(lines, age=2001)
        self.assertEqual([second for second, _ in state.tool_pulses],
                         [int(old + 1800), int(old + 2000)])

    def test_the_ring_is_capped(self) -> None:
        lines = [_claude_init()] + [
            _tool_use(f"t{i}", f"2026-10-24T10:00:0{i}.000Z") for i in range(5)
        ]
        with patch("logparse.TOOL_RING_MAX", 3):
            state = self.ring(lines)
        self.assertEqual(len(state.tool_pulses), 3)
        self.assertEqual(state.tool_pulses[-1][0], int(epoch("2026-10-24T10:00:04Z")))

    def test_the_ring_survives_incremental_reads(self) -> None:
        path = self.path([_claude_init(), _tool_use("t1", "2026-10-24T10:00:00.000Z")])
        state = parse_log(path, now=epoch(T0) + 1)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(_tool_use("t2", "2026-10-24T10:00:00.500Z") + "\n")
        state = parse_log(path, prev=state, now=epoch(T0) + 2)
        self.assertEqual(state.tool_pulses, [(int(epoch("2026-10-24T10:00:00Z")), 2)])

    def test_an_unchanged_log_still_prunes_its_expired_calls(self) -> None:
        path = self.path([_claude_init(), _tool_use("t1", "2026-10-24T10:00:00.000Z")])
        state = parse_log(path, now=epoch(T0) + 1)
        self.assertEqual(len(state.tool_pulses), 1)
        state = parse_log(path, prev=state, now=epoch(T0) + TOOL_RING_S + 1)
        self.assertEqual(state.tool_pulses, [])

    def test_an_event_ahead_of_now_stays_in_the_ring(self) -> None:
        lines = [_claude_init(), _tool_use("t1", "2026-10-24T10:00:00.000Z")]
        state = self.ring(lines, age=-5)
        self.assertEqual(state.tool_pulses, [(int(epoch(T0)), 1)])

    def test_the_ring_keeps_a_record_split_across_reads(self) -> None:
        path = os.path.join(self.dir, "split.log")
        lines = [_claude_init(), _tool_use("t1", T0)]
        text = ("\n".join(lines) + "\n").encode()
        cut = len(text) - 30
        with open(path, "wb") as handle:
            handle.write(text[:cut])
        state = parse_log(path, now=epoch(T0) + 1)
        self.assertEqual(state.tool_pulses, [])
        with open(path, "ab") as handle:
            handle.write(text[cut:])
        state = parse_log(path, prev=state, now=epoch(T0) + 2)
        self.assertEqual(state.tool_pulses, [(int(epoch(T0)), 1)])


class PartialRecord(LogCase):
    """An incomplete JSON record bigger than the old 64 KiB carry limit is kept, not lost."""

    def huge_tool_use(self, size: int = 70_000) -> str:
        return json.dumps({"type": "assistant", "timestamp": T0, "message": {"content": [
            {"type": "tool_use", "id": "big", "name": "Read",
             "input": {"file_path": "/work/" + "a" * size}}]}})

    def test_a_seventy_kilobyte_record_split_across_reads_is_parsed(self) -> None:
        path = os.path.join(self.dir, "big.log")
        record = self.huge_tool_use()
        self.assertGreater(len(record), 65_536)
        head = (_claude_init() + "\n").encode()
        with open(path, "wb") as handle:
            handle.write(head + record[:68_000].encode())
        state = parse_log(path, now=epoch(T0) + 1)
        self.assertEqual([step.kind for step in state.steps], [])
        with open(path, "ab") as handle:
            handle.write(record[68_000:].encode() + b"\n")
        state = parse_log(path, prev=state, now=epoch(T0) + 2)
        (step,) = state.steps
        self.assertEqual((step.kind, step.tool, step.outcome), ("tool", "read", ""))
        self.assertEqual(len(step.text), 400)   # the step text is still cut to one line
        self.assertNotIn("truncated", state._lp_private)

    def test_a_record_over_the_cap_is_skipped_and_the_next_one_parsed(self) -> None:
        path = os.path.join(self.dir, "cap.log")
        with patch("logparse._LP_MAX_JSON_CARRY", 200):
            with open(path, "wb") as handle:
                handle.write((_claude_init() + "\n").encode())
                handle.write(b'{"type":"assistant","message":{"content":[{"type":"text","text":"' + b"x" * 400)
            state = parse_log(path, now=epoch(T0) + 1)
            self.assertEqual(state.steps, [])
            with open(path, "ab") as handle:
                handle.write(b'"}]}}\n' + (_tool_use("t1", T0) + "\n").encode())
            state = parse_log(path, prev=state, now=epoch(T0) + 2)
        self.assertEqual(state._lp_private.get("truncated"), 1)
        self.assertEqual([step.tool for step in state.steps if step.kind == "tool"], ["read"])


    def test_the_cap_constant_is_the_model_one(self) -> None:
        self.assertEqual(TOOL_RING_MAX, 2000)


class ToolRate(unittest.TestCase):
    """tool_rate bins the ring for the TOOLS/MIN sparkline: one number per cell, oldest first."""

    def test_cells_are_oldest_first(self) -> None:
        state = LogState(tool_pulses=[(1000.0, 2), (1600.0, 1)])
        self.assertEqual(tool_rate(state, 1800.0, cells=3, span=1800), [0, 2, 1])

    def test_entries_outside_the_span_are_ignored(self) -> None:
        state = LogState(tool_pulses=[(0.0, 5), (1799.0, 1)])
        self.assertEqual(tool_rate(state, 1800.0, cells=2, span=1800), [0, 1])

    def test_an_empty_ring_is_all_zeroes(self) -> None:
        self.assertEqual(tool_rate(LogState(), 1800.0), [0] * 15)

    def test_a_future_entry_lands_in_the_newest_cell(self) -> None:
        state = LogState(tool_pulses=[(1900.0, 1)])
        self.assertEqual(tool_rate(state, 1800.0, cells=2, span=1800), [0, 1])

    def test_zero_cells_is_empty(self) -> None:
        self.assertEqual(tool_rate(LogState(tool_pulses=[(1000.0, 1)]), 1800.0, cells=0), [])


class ContextWindow(LogCase):
    """LogState.ctx_window comes from the log itself, never from a guess."""

    def test_a_claude_result_gives_the_model_usage_window(self) -> None:
        line = json.dumps({"type": "result", "total_cost_usd": 0.1, "usage": {},
                           "modelUsage": {"claude-x": {"contextWindow": 200_000, "inputTokens": 5}}})
        state = self.parse([_claude_init(), line])
        self.assertEqual(state.ctx_window, 200_000)

    def test_the_largest_window_wins_when_a_result_lists_two_models(self) -> None:
        line = json.dumps({"type": "result",
                           "modelUsage": {"small": {"contextWindow": 32_000}, "big": {"contextWindow": 200_000}}})
        self.assertEqual(self.parse([_claude_init(), line]).ctx_window, 200_000)

    def test_a_claude_log_without_a_result_has_no_window(self) -> None:
        self.assertIsNone(self.parse([_claude_init(), _tool_use("t1", T0)]).ctx_window)

    def test_a_claude_result_without_model_usage_has_no_window(self) -> None:
        line = json.dumps({"type": "result", "total_cost_usd": 0.1})
        self.assertIsNone(self.parse([_claude_init(), line]).ctx_window)

    def test_a_codex_task_started_gives_the_window(self) -> None:
        state = self.parse([
            _session_meta(),
            _rollout(T0, "event_msg", {"type": "task_started", "model_context_window": 258_400}),
        ])
        self.assertEqual(state.ctx_window, 258_400)

    def test_a_codex_token_count_gives_the_window_under_info(self) -> None:
        state = self.parse([
            _session_meta(),
            _rollout(T0, "event_msg", {"type": "token_count",
                                       "info": {"model_context_window": 128_000}}),
        ])
        self.assertEqual(state.ctx_window, 128_000)

    def test_a_codex_log_without_the_field_has_no_window(self) -> None:
        state = self.parse([_session_meta(),
                            _rollout(T0, "event_msg", {"type": "task_started", "turn_id": "t"})])
        self.assertIsNone(state.ctx_window)

    def test_a_codex_turn_context_gives_the_model_name(self) -> None:
        state = self.parse([_session_meta(),
                            _rollout(T0, "turn_context", {"model": "gpt-6-luna"})])
        self.assertEqual(state.model, "gpt-6-luna")


class StepDuration(LogCase):
    """The acceptance figures: a 65 s step, and a 0 ms step that keeps 0.0."""

    def test_a_sixty_five_second_claude_step_keeps_65(self) -> None:
        state = self.parse([
            _claude_init(),
            _tool_use("t1", "2026-10-24T10:00:00.000Z"),
            _result("t1", "2026-10-24T10:01:05.000Z"),
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.dur, 65.0)
        self.assertEqual(step.outcome, "ok")

    def test_a_sixty_five_second_codex_command_keeps_65(self) -> None:
        state = self.parse([
            "OpenAI Codex v0.161.0", "--------", "model: gpt-6-luna", "--------",
            "codex", "go", "exec", '/usr/bin/bash -lc "sleep 65" in /x', " succeeded in 65s:",
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.dur, 65.0)

    def test_duration_ms_is_used_when_the_event_times_are_missing(self) -> None:
        state = self.parse([
            _claude_init(),
            _tool_use("t1", None),
            _result("t1", None, duration_ms=65_000),
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.dur, 65.0)

    def test_duration_ms_wins_over_the_event_times(self) -> None:
        state = self.parse([
            _claude_init(),
            _tool_use("t1", "2026-10-24T10:00:00.000Z"),
            _result("t1", "2026-10-24T10:00:01.000Z", duration_ms=5_000),
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.dur, 5.0)

    def test_a_zero_millisecond_result_keeps_zero(self) -> None:
        state = self.parse([
            _claude_init(),
            _tool_use("t1", "2026-10-24T10:00:00.000Z"),
            _result("t1", "2026-10-24T10:00:00.000Z", duration_ms=0),
        ])
        step = next(s for s in state.steps if s.kind == "tool")
        self.assertEqual(step.dur, 0.0)
        self.assertIsNotNone(step.dur)


if __name__ == "__main__":
    unittest.main()
