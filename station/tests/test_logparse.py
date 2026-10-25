"""Tests for logparse. Synthetic fixtures live in a temp dir; real samples are read-only."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from logparse import activity, parse_log, tool_category
from model import ACT_BUCKETS, MAX_STEPS, LogState

REAL_OPENCODE = [
    "/home/user/Projects/quota_service/.stage/fit-styles.log",
    "/home/user/Projects/worker-dashboard/.stage/st-b1.log",
]
REAL_CODEX = [
    "/home/user/Projects/quota_service/.stage/orch-api-ui.log",
    "/home/user/Projects/worker-dashboard/.stage/st-review.log",
]


FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
CLAUDE_FIXTURE = os.path.join(FIXTURES, "claude-stream.jsonl")
CURSOR_FIXTURE = os.path.join(FIXTURES, "cursor-stream.jsonl")


def _oc(**obj) -> str:
    return json.dumps(obj)


def _claude_init() -> str:
    return json.dumps({
        "type": "system",
        "subtype": "init",
        "model": "claude-x",
        "claude_code_version": "2.1.0",
        "apiKeySource": "none",
        "session_id": "s",
    })


def _usage(out: int) -> dict:
    return {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": out}


def _claude_msg(msg_id: str, content: list, usage: dict | None = None) -> str:
    message: dict = {"id": msg_id, "role": "assistant", "content": content}
    if usage is not None:
        message["usage"] = usage
    return json.dumps({"type": "assistant", "message": message})


def _codex_head() -> list[str]:
    return [
        "OpenAI Codex v0.161.0",
        "--------",
        "model: gpt-6-luna",
        "reasoning effort: medium",
        "--------",
    ]


class LogParseTests(unittest.TestCase):
    def test_tail_parse_detects_harness_from_file_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "large.log")
            with open(CLAUDE_FIXTURE, "rb") as source:
                first = source.read()
            with open(path, "wb") as handle:
                handle.write(first)
                handle.write(b"x" * (300 * 1024))
            state = parse_log(path, prev=LogState(path=path, offset=len(first)))
            self.assertEqual(state.harness, "claude")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, data: str | bytes) -> str:
        path = os.path.join(self.dir, name)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(path, mode) as handle:
            handle.write(data)
        return path

    def _opencode_lines(self) -> list[str]:
        return [
            _oc(type="step_start", timestamp=1000000, part={"modelID": "claude-x"}),
            _oc(
                type="tool_use",
                timestamp=1001000,
                part={"tool": "read", "state": {"input": {"filePath": "/a.txt"}, "title": "read"}},
            ),
            _oc(
                type="tool_use",
                timestamp=1002000,
                part={"tool": "shell", "state": {"input": {"command": "ls -la"}, "title": "shell"}},
            ),
            _oc(
                type="tool_use",
                timestamp=1003000,
                part={"tool": "bash", "state": {"input": {"command": "echo hi"}}},
            ),
            _oc(type="text", timestamp=1004000, part={"text": "hello world"}),
            _oc(
                type="step_finish",
                timestamp=1005000,
                part={
                    "cost": 0.0012,
                    "tokens": {
                        "input": 684,
                        "output": 452,
                        "reasoning": 950,
                        "cache": {"read": 93568, "write": 0},
                    },
                },
            ),
            _oc(type="error", timestamp=1006000, error={"message": "boom"}),
            "not json at all",
        ]

    def test_opencode_fixture(self) -> None:
        path = self._write("oc.log", "\n".join(self._opencode_lines()) + "\n")
        state = parse_log(path, now=1007.0)
        self.assertEqual(state.harness, "opencode")
        self.assertEqual(state.model, "claude-x")
        self.assertEqual(state.errors, 1)
        self.assertEqual(state.tokens_in, 684)
        self.assertEqual(state.tokens_out, 452 + 950)
        self.assertEqual(state.tokens_cache, 93568)
        self.assertAlmostEqual(state.cost, 0.0012)
        self.assertEqual(state.ctx_tokens, 684 + 93568)
        self.assertEqual(state.tool_counts.get("read"), 1)
        self.assertEqual(state.tool_counts.get("shell"), 2)
        self.assertEqual(state.tool_counts.get("edit", 0), 0)
        self.assertEqual([s.kind for s in state.steps], ["tool", "tool", "tool", "text", "error"])
        self.assertEqual(state.steps[0].tool, "read")
        self.assertEqual(state.steps[0].text, "/a.txt")
        self.assertEqual(state.steps[1].text, "ls -la")
        self.assertEqual(state.steps[3].text, "hello world")
        self.assertEqual(state.steps[4].text, "boom")
        self.assertEqual(state.steps[0].ts, 1001.0)
        self.assertEqual(state.first_ts, 1001.0)
        self.assertEqual(state.last_ts, 1006.0)
        buckets = activity(state, 1007.0)
        self.assertEqual(len(buckets), ACT_BUCKETS)
        self.assertEqual(sum(buckets), 5)

    def test_tool_category(self) -> None:
        self.assertEqual(tool_category("Read"), "read")
        self.assertEqual(tool_category("apply_patch"), "edit")
        self.assertEqual(tool_category("Bash"), "shell")
        self.assertEqual(tool_category("websearch"), "search")
        self.assertEqual(tool_category("weird-thing"), "other")
        self.assertEqual(tool_category(""), "other")

    def test_incremental_matches_one_shot(self) -> None:
        lines = self._opencode_lines()
        full = "\n".join(lines) + "\n"
        path = self._write("inc.log", full)
        one_shot = parse_log(path, now=2000.0)

        cut = full.index("hello world") + 3
        path2 = self._write("inc2.log", full[:cut])
        first = parse_log(path2, now=2000.0)
        self.assertTrue(first.carry)
        with open(path2, "a") as handle:
            handle.write(full[cut:])
        second = parse_log(path2, first, now=2000.0)

        def snap(state: LogState) -> tuple:
            steps = tuple((s.ts, s.kind, s.tool, s.text) for s in state.steps)
            return (
                state.harness,
                state.model,
                steps,
                dict(state.tool_counts),
                state.errors,
                state.tokens_in,
                state.tokens_out,
                state.tokens_cache,
                state.cost,
                state.ctx_tokens,
            )

        self.assertEqual(snap(second), snap(one_shot))
        self.assertEqual(second.offset, len(full))
        self.assertEqual(second.carry, "")
        self.assertEqual([s.text for s in second.steps].count("ls -la"), 1)

    def test_truncated_file_resets(self) -> None:
        path = self._write("trunc.log", "\n".join(self._opencode_lines()) + "\n")
        state = parse_log(path, now=10.0)
        self.assertEqual(len(state.steps), 5)
        short = _oc(type="text", timestamp=5000000, part={"text": "only"}) + "\n"
        self._write("trunc.log", short)
        state2 = parse_log(path, state, now=10.0)
        self.assertEqual(state2.offset, len(short))
        self.assertEqual(len(state2.steps), 1)
        self.assertEqual(state2.steps[0].text, "only")
        self.assertEqual(state2.harness, "opencode")

    def test_codex_fixture(self) -> None:
        lines = [
            "Reading additional input from stdin...",
            "OpenAI Codex v0.161.0",
            "--------",
            "workdir: /x",
            "model: gpt-6-luna",
            "provider: openai",
            "reasoning effort: medium",
            "session id: abc",
            "--------",
            "user",
            "# brief",
            "Goal: do the thing",
            "codex",
            "I will do it.",
            "exec",
            '/usr/bin/bash -lc "ls" in /x',
            " exited 2 in 3ms:",
            "file1",
            "file2",
            "Error: could not compile",
            "tokens used",
            "12,345",
        ]
        path = self._write("codex.log", "\n".join(lines) + "\n")
        state = parse_log(path, now=1234.0)
        self.assertEqual(state.harness, "codex")
        self.assertEqual(state.model, "gpt-6-luna (medium)")
        self.assertEqual(state.tokens_in, 12345)
        self.assertIsNone(state.tokens_out)
        self.assertIsNone(state.tokens_cache)
        texts = [s.text for s in state.steps]
        self.assertIn("I will do it.", texts)
        shell_texts = [s.text for s in state.steps if s.kind == "tool"]
        self.assertTrue(any("/usr/bin/bash" in t for t in shell_texts))
        self.assertEqual(state.tool_counts.get("shell"), 1)
        self.assertEqual(state.errors, 1)
        self.assertTrue(any(s.kind == "error" for s in state.steps))
        self.assertFalse(any("do the thing" in t for t in texts))
        self.assertFalse(any(t.startswith("Goal:") for t in texts))
        self.assertNotIn("12,345", texts)

    def test_plain_text_fixture(self) -> None:
        content = "\x1b[31mred\x1b[0m line\nError: bad thing\nerror: lower\nERROR upper\nplain\n"
        path = self._write("plain.txt", content)
        state = parse_log(path, now=55.0)
        self.assertEqual(state.harness, "unknown")
        self.assertEqual(state.errors, 3)
        texts = [s.text for s in state.steps]
        self.assertEqual(texts[0], "red line")
        self.assertNotIn("\x1b", "".join(texts))
        self.assertEqual(state.steps[0].kind, "text")
        self.assertEqual(state.steps[1].kind, "error")

    def test_missing_file(self) -> None:
        path = os.path.join(self.dir, "nope.log")
        state = parse_log(path)
        self.assertEqual(state.path, path)
        self.assertEqual(state.steps, [])
        prev = LogState(path=path, offset=3)
        self.assertIs(parse_log(path, prev), prev)

    def test_empty_file(self) -> None:
        path = self._write("empty.log", "")
        state = parse_log(path, now=1.0)
        self.assertEqual(state.steps, [])
        self.assertEqual(state.offset, 0)
        self.assertEqual(state.harness, "unknown")

    def test_random_bytes_do_not_raise(self) -> None:
        path = self._write("bin.log", os.urandom(500))
        state = parse_log(path, now=1.0)
        self.assertIsInstance(state, LogState)

    def test_activity_buckets(self) -> None:
        state = LogState()
        state.pulses = [(1000.0, 3), (995.0, 2), (994.0, 1), (690.0, 5)]
        buckets = activity(state, 1000.0)
        self.assertEqual(len(buckets), ACT_BUCKETS)
        self.assertEqual(buckets[ACT_BUCKETS - 1], 5)  # now and now-5 land newest
        self.assertEqual(buckets[ACT_BUCKETS - 2], 1)
        self.assertEqual(sum(buckets), 6)  # the 690 pulse is out of the window

    def test_old_pulses_dropped(self) -> None:
        old = _oc(type="text", timestamp=100000, part={"text": "old"})
        new = _oc(type="text", timestamp=999000000, part={"text": "new"})
        path = self._write("pulse.log", old + "\n" + new + "\n")
        state = parse_log(path, now=999100.0)
        self.assertEqual(len(state.pulses), 1)
        self.assertEqual(state.pulses[0][0], 999000.0)

    def test_max_steps_cap(self) -> None:
        lines = [_oc(type="text", timestamp=1000000 + i, part={"text": f"line {i}"}) for i in range(1000)]
        path = self._write("many.log", "\n".join(lines) + "\n")
        state = parse_log(path, now=5000.0)
        self.assertEqual(len(state.steps), MAX_STEPS)
        self.assertEqual(state.steps[0].text, "line 800")
        self.assertEqual(state.steps[-1].text, "line 999")

    def test_codex_exec_marker_held_between_reads(self) -> None:
        part = "\n".join(_codex_head() + ["exec"]) + "\n"
        path = self._write("codex_exec.log", part)
        first = parse_log(path, now=1000.0)
        self.assertIn("exec", first.carry)
        self.assertEqual(first.tool_counts.get("shell", 0), 0)
        self.assertFalse(any(s.kind == "tool" for s in first.steps))
        with open(path, "a") as handle:
            handle.write('/usr/bin/bash -lc "ls" in /x\n')
        second = parse_log(path, first, now=1000.0)
        self.assertEqual(second.carry, "")
        shell_texts = [s.text for s in second.steps if s.kind == "tool"]
        self.assertTrue(any("/usr/bin/bash" in t for t in shell_texts))
        self.assertEqual(second.tool_counts.get("shell"), 1)

    def test_codex_tokens_marker_held_between_reads(self) -> None:
        part = "\n".join(_codex_head() + ["tokens used"]) + "\n"
        path = self._write("codex_tokens.log", part)
        first = parse_log(path, now=1000.0)
        self.assertIn("tokens used", first.carry)
        self.assertIsNone(first.tokens_in)
        with open(path, "a") as handle:
            handle.write("12,345\n")
        second = parse_log(path, first, now=1000.0)
        self.assertEqual(second.tokens_in, 12345)
        self.assertEqual(second.carry, "")

    def test_carry_capped_at_65536(self) -> None:
        big = "a" * 70000
        path = self._write("big.log", big)
        state = parse_log(path, now=10.0)
        self.assertEqual(state.carry, "")
        self.assertTrue(state.steps)
        self.assertEqual(len(state.steps[-1].text), 70000)
        self.assertEqual(state.offset, len(big))
        with open(path, "a") as handle:
            handle.write("\nsecond\n")
        second = parse_log(path, state, now=10.0)
        self.assertEqual(second.carry, "")
        self.assertEqual(second.steps[-1].text, "second")

    def test_absurd_opencode_timestamp_does_not_raise(self) -> None:
        lines = [
            _oc(type="text", timestamp=float("nan"), part={"text": "n"}),
            _oc(type="text", timestamp=float("inf"), part={"text": "i"}),
            _oc(type="text", timestamp=-5, part={"text": "neg"}),
            _oc(type="text", timestamp="nope", part={"text": "str"}),
        ]
        path = self._write("absurd.log", "\n".join(lines) + "\n")
        state = parse_log(path, now=1000.0)
        self.assertEqual(state.harness, "opencode")
        self.assertEqual([s.ts for s in state.steps], [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(state.pulses, [])
        self.assertEqual(sum(activity(state, 1000.0)), 0)

    def test_future_event_clamped_to_now(self) -> None:
        future = int(2000.0 * 1000)
        path = self._write("fut.log", _oc(type="text", timestamp=future, part={"text": "f"}) + "\n")
        state = parse_log(path, now=1000.0)
        self.assertEqual(state.pulses, [(1000.0, 1)])
        self.assertEqual(activity(state, 1000.0)[ACT_BUCKETS - 1], 1)

    def test_pulses_capped(self) -> None:
        lines = [_oc(type="text", timestamp=(i + 1) * 1000, part={"text": f"t{i}"}) for i in range(480)]
        path = self._write("manypulse.log", "\n".join(lines) + "\n")
        state = parse_log(path, now=490.0)
        self.assertEqual(len(state.pulses), ACT_BUCKETS * 4)
        self.assertEqual(state.pulses[0][0], 241.0)
        self.assertEqual(state.pulses[-1][0], 480.0)

    def test_real_samples_fast(self) -> None:
        for path in REAL_OPENCODE + REAL_CODEX:
            if not os.path.exists(path):
                continue
            with self.subTest(path=path):
                start = time.time()
                state = parse_log(path)
                self.assertIsInstance(state, LogState)
                self.assertLess(time.time() - start, 2.0)

    def test_real_opencode_shape(self) -> None:
        path = REAL_OPENCODE[0]
        if not os.path.exists(path):
            self.skipTest("sample missing")
        state = parse_log(path, now=time.time())
        self.assertEqual(state.harness, "opencode")
        self.assertTrue(state.steps)
        self.assertGreater(state.tokens_in or 0, 0)

    def test_real_codex_shape(self) -> None:
        path = REAL_CODEX[0]
        if not os.path.exists(path):
            self.skipTest("sample missing")
        state = parse_log(path, now=time.time())
        self.assertEqual(state.harness, "codex")
        self.assertIn("(", state.model)
        self.assertTrue(state.steps)


class StreamParseTests(unittest.TestCase):
    """claude and cursor-agent ``stream-json`` logs."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, text: str) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def _read_fixture(self, path: str) -> str:
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_detects_claude_from_leading_hook_lines(self) -> None:
        path = self._write("hooks.log", "\n".join([
            json.dumps({"type": "system", "subtype": "hook_started", "session_id": "s"}),
            json.dumps({"type": "system", "subtype": "hook_response", "session_id": "s", "output": ""}),
            _claude_init(),
        ]) + "\n")
        self.assertEqual(parse_log(path, now=1.0).harness, "claude")

    def test_detects_claude_from_init_event(self) -> None:
        path = self._write("init.log", _claude_init() + "\n")
        self.assertEqual(parse_log(path, now=1.0).harness, "claude")

    def test_detects_cursor_from_init_event(self) -> None:
        path = self._write("cursor.log", json.dumps({
            "type": "system",
            "subtype": "init",
            "apiKeySource": "login",
            "model": "Composer 2.5",
            "session_id": "s",
        }) + "\n")
        state = parse_log(path, now=1.0)
        self.assertEqual(state.harness, "cursor")
        self.assertEqual(state.model, "Composer 2.5")

    def test_detects_claude_and_cursor_fixtures(self) -> None:
        self.assertEqual(parse_log(CLAUDE_FIXTURE, now=1.0).harness, "claude")
        self.assertEqual(parse_log(CURSOR_FIXTURE, now=1.0).harness, "cursor")

    def test_non_stream_json_stays_plain(self) -> None:
        path = self._write("plain.log", "Error: nope\n" + json.dumps({"type": "assistant", "message": {"content": []}}) + "\n")
        state = parse_log(path, now=1.0)
        self.assertEqual(state.harness, "unknown")
        self.assertEqual(state.errors, 1)

    def test_codex_and_opencode_markers_win_over_stream_markers(self) -> None:
        hook = json.dumps({"type": "system", "subtype": "hook_started", "session_id": "s"})
        codex = self._write("codex.log", f"{hook}\nOpenAI Codex v0.161.0\n--------\n")
        self.assertEqual(parse_log(codex, now=1.0).harness, "codex")
        oc = self._write("oc.log", "\n".join([hook, _oc(type="step_start", timestamp=1000, part={})]) + "\n")
        self.assertEqual(parse_log(oc, now=1.0).harness, "opencode")

    def test_claude_fixture_steps_tokens_and_cost(self) -> None:
        state = parse_log(CLAUDE_FIXTURE, now=2000.0)
        self.assertEqual(state.harness, "claude")
        self.assertEqual(state.model, "claude-haiku-5-5")
        self.assertEqual(
            [(s.kind, s.tool, s.text) for s in state.steps],
            [("tool", "read", "/work/a.txt"), ("text", "", "hello")],
        )
        self.assertEqual(state.tool_counts, {"read": 1})
        self.assertEqual(state.errors, 0)
        self.assertEqual(state.tokens_in, 4 + 16774)
        self.assertEqual(state.tokens_out, 173)
        self.assertEqual(state.tokens_cache, 39799)
        self.assertAlmostEqual(state.cost, 0.00383969)

    def test_claude_live_run_before_result_sums_messages(self) -> None:
        lines = [line for line in self._read_fixture(CLAUDE_FIXTURE).splitlines() if '"type":"result"' not in line]
        path = self._write("live.log", "\n".join(lines) + "\n")
        state = parse_log(path, now=2000.0)
        self.assertEqual(state.harness, "claude")
        self.assertEqual(len(state.steps), 2)
        self.assertEqual(state.tokens_in, (2 + 13429) + (2 + 3345))
        self.assertEqual(state.tokens_out, 16 + 3)
        self.assertEqual(state.tokens_cache, 13185 + 26614)
        self.assertIsNone(state.cost)

    def test_claude_partial_trailing_line_waits_for_the_rest(self) -> None:
        text = self._read_fixture(CLAUDE_FIXTURE)
        cut = text.index('"text":"hello"') + len('"text"')
        path = self._write("partial.log", text[:cut])
        first = parse_log(path, now=2000.0)
        self.assertTrue(first.carry)
        self.assertEqual([s.kind for s in first.steps], ["tool"])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text[cut:])
        second = parse_log(path, first, now=2000.0)
        self.assertEqual(second.carry, "")
        self.assertEqual([s.text for s in second.steps], ["/work/a.txt", "hello"])
        self.assertEqual(second.tokens_out, 173)

    def test_claude_message_id_counted_once_last_usage_wins(self) -> None:
        path = self._write("ids.log", "\n".join([
            _claude_init(),
            _claude_msg("m1", [{"type": "text", "text": "a"}], _usage(5)),
            _claude_msg("m1", [{"type": "text", "text": "b"}], _usage(9)),
            _claude_msg("m2", [{"type": "text", "text": "c"}], _usage(4)),
        ]) + "\n")
        state = parse_log(path, now=1.0)
        self.assertEqual([s.text for s in state.steps], ["a", "b", "c"])
        self.assertEqual(state.tokens_in, 2)
        self.assertEqual(state.tokens_out, 9 + 4)

    def test_claude_tool_categories_and_error_results(self) -> None:
        path = self._write("tools.log", "\n".join([
            _claude_init(),
            _claude_msg("m3", [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/work/a.py"}},
                {"type": "tool_use", "name": "NotebookEdit", "input": {"notebook_path": "/work/n.ipynb"}},
                {"type": "tool_use", "name": "Grep", "input": {"pattern": "foo"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
                {"type": "tool_use", "name": "Task", "input": {"description": "x"}},
            ], _usage(1)),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "File does not exist."},
                {"type": "tool_result", "tool_use_id": "t2", "content": "fine"},
            ]}}),
        ]) + "\n")
        state = parse_log(path, now=1.0)
        self.assertEqual(
            [(s.kind, s.tool, s.text) for s in state.steps],
            [
                ("tool", "edit", "/work/a.py"),
                ("tool", "notebookedit", "/work/n.ipynb"),
                ("tool", "grep", "foo"),
                ("tool", "bash", "ls -la"),
                ("tool", "task", ""),
                ("error", "", "File does not exist."),
            ],
        )
        self.assertEqual(state.tool_counts, {"edit": 2, "search": 1, "shell": 1, "other": 1})
        self.assertEqual(state.errors, 1)

    def test_cursor_fixture_result_usage_without_cost(self) -> None:
        state = parse_log(CURSOR_FIXTURE, now=2000.0)
        self.assertEqual(state.harness, "cursor")
        self.assertEqual(state.model, "Composer 2.5")
        self.assertEqual([(s.kind, s.text) for s in state.steps], [("text", "ok")])
        self.assertEqual(state.tokens_in, 9744)
        self.assertEqual(state.tokens_out, 109)
        self.assertEqual(state.tokens_cache, 3864)
        self.assertIsNone(state.cost)
        self.assertEqual(state.errors, 0)


if __name__ == "__main__":
    unittest.main()
