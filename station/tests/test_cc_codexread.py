"""Control center C1: the codex rollout reader. The fixtures under tests/fixtures/cc_codex/ are synthetic, shaped like
real rollouts (session_meta, turn_context, response_item calls and outputs, token_count) with no real session text."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from codexread import CodexSession, read_codex_session

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cc_codex"
SID = "01a0c0de-0000-7000-8000-00000000c1de"


def epoch(stamp: str) -> float:
    """An ISO-8601 rollout timestamp as epoch seconds."""
    return datetime.fromisoformat(stamp).timestamp()


def stamp(second: int) -> str:
    """A rollout timestamp in the fixture's hour, one per second."""
    minute, sec = divmod(second, 60)
    return f"2026-10-24T10:{minute:02d}:{sec:02d}.000Z"


def meta_line() -> dict:
    return {"timestamp": stamp(0), "type": "session_meta",
            "payload": {"id": SID, "timestamp": stamp(0), "cwd": "/home/tester/projects/demo",
                        "base_instructions": {"text": "You are codex.\nBe brief.\n"}}}


def prompt_line(text: str) -> dict:
    return {"timestamp": stamp(2), "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}


def call_line(seconds: int, arg: str, call_id: str) -> dict:
    return {"timestamp": stamp(seconds), "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": call_id,
                        "input": f'await tools.exec_command({{cmd:"{arg}"}});'}}


class CodexReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def copy(self, name: str) -> str:
        """A writable copy of a fixture, so the incremental tests never touch the tracked files."""
        target = self.dir / name
        shutil.copyfile(FIXTURES / name, target)
        return str(target)

    def write(self, name: str, lines: list[dict | str]) -> str:
        path = self.dir / name
        path.write_text("\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n")
        return str(path)

    def test_first_prompt_and_injected_messages(self) -> None:
        sess = read_codex_session(str(FIXTURES / "basic.jsonl"))
        self.assertIsInstance(sess, CodexSession)
        self.assertEqual(sess.prompt, ["Name: c1-codexread", "Goal: parse a rollout", "", "Job: build"])
        self.assertNotIn("# AGENTS.md instructions", "\n".join(sess.prompt))
        self.assertNotIn("environment_context", "\n".join(sess.prompt))

    def test_basic_fields(self) -> None:
        sess = read_codex_session(str(FIXTURES / "basic.jsonl"))
        self.assertEqual(sess.session_id, SID)
        self.assertEqual(sess.model, "gpt-6-luna")
        self.assertEqual(sess.effort, "high")
        self.assertEqual(sess.cwd, "/home/tester/projects/demo")
        self.assertEqual(sess.started, epoch("2026-10-24T10:00:00.000Z"))
        self.assertEqual(sess.last_ts, epoch("2026-10-24T10:00:16.000Z"))
        self.assertEqual(sess.resume_cmd, f"codex resume {SID}")

    def test_system_prompt(self) -> None:
        sess = read_codex_session(str(FIXTURES / "basic.jsonl"))
        self.assertEqual(sess.system_prompt, ["You are codex.", "Be brief."])

    def test_name_line_wins(self) -> None:
        sess = read_codex_session(str(FIXTURES / "basic.jsonl"))
        self.assertEqual(sess.name, "c1-codexread")
        self.assertNotEqual(sess.name, sess.session_id)

    def test_name_from_heading_first_line(self) -> None:
        sess = read_codex_session(str(FIXTURES / "heading.jsonl"))
        self.assertEqual(sess.name, "Build the widget")
        self.assertEqual(sess.prompt[0], "### Build the widget")

    def test_name_never_the_uuid(self) -> None:
        sess = read_codex_session(str(FIXTURES / "uuid.jsonl"))
        self.assertEqual(sess.prompt[0], SID)
        self.assertEqual(sess.name, f"codex {SID[:8]}")

    def test_name_cut_to_40_chars(self) -> None:
        long_line = "t" * 60
        sess = read_codex_session(self.write("long.jsonl", [meta_line(), prompt_line(long_line)]))
        self.assertEqual(sess.name, long_line[:40])

    def test_no_prompt_yet(self) -> None:
        sess = read_codex_session(str(FIXTURES / "noprompt.jsonl"))
        self.assertEqual(sess.prompt, [])
        self.assertEqual(sess.name, f"codex {SID[:8]}")
        self.assertEqual(sess.resume_cmd, f"codex resume {SID}")

    def test_all_injected_means_no_prompt(self) -> None:
        path = self.write("injected.jsonl", [
            meta_line(),
            prompt_line("<environment_context>\n<cwd>/tmp</cwd>\n</environment_context>"),
            prompt_line("<permissions instructions>\nnone\n</permissions instructions>"),
        ])
        sess = read_codex_session(path)
        self.assertEqual(sess.prompt, [])
        self.assertEqual(sess.name, f"codex {SID[:8]}")

    def test_effort_from_collaboration_mode(self) -> None:
        sess = read_codex_session(str(FIXTURES / "heading.jsonl"))
        self.assertEqual(sess.model, "gpt-6-sol")
        self.assertEqual(sess.effort, "medium")

    def test_paired_call_has_duration(self) -> None:
        sess = read_codex_session(str(FIXTURES / "basic.jsonl"))
        call = sess.calls[0]
        self.assertEqual(call.tool, "send_message")
        self.assertEqual(call.arg, "starting now")
        self.assertEqual(call.outcome, "ok")
        self.assertEqual(call.dur, epoch("2026-10-24T10:00:07.000Z") - epoch("2026-10-24T10:00:05.000Z"))
        self.assertEqual(call.err, "")

    def test_failed_call_reports_exit_code_and_error(self) -> None:
        sess = read_codex_session(str(FIXTURES / "basic.jsonl"))
        call = sess.calls[1]
        self.assertEqual(call.tool, "exec")
        self.assertEqual(call.arg, "python3 -m unittest")
        self.assertEqual(call.outcome, "failed")
        self.assertEqual(call.err, "FAILED test_x")
        self.assertEqual(call.dur, 3.0)

    def test_failed_call_error_skips_the_script_header(self) -> None:
        sess = read_codex_session(str(FIXTURES / "basic.jsonl"))
        call = sess.calls[3]
        self.assertEqual(call.arg, "make test")
        self.assertEqual(call.outcome, "failed")
        self.assertEqual(call.err, "make: *** [test] Error 2")

    def test_unpaired_call(self) -> None:
        sess = read_codex_session(str(FIXTURES / "basic.jsonl"))
        call = sess.calls[2]
        self.assertEqual(call.arg, "ls -la")
        self.assertEqual(call.outcome, "")
        self.assertIsNone(call.dur)
        self.assertEqual(call.err, "")

    def test_call_without_a_timestamp_has_no_duration(self) -> None:
        lines = [
            meta_line(),
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                                                  "input": 'await tools.exec_command({cmd:"pwd"});'}},
            {"timestamp": stamp(5), "type": "response_item",
             "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": "Script completed"}},
        ]
        sess = read_codex_session(self.write("undated.jsonl", lines))
        self.assertEqual(sess.calls[0].outcome, "ok")
        self.assertIsNone(sess.calls[0].dur)

    def test_tokens_and_context_window(self) -> None:
        sess = read_codex_session(str(FIXTURES / "basic.jsonl"))
        self.assertEqual(sess.tokens_in, 1200)
        self.assertEqual(sess.tokens_out, 50)      # output + reasoning
        self.assertEqual(sess.tokens_cache, 900)
        self.assertEqual(sess.ctx_tokens, 700)     # the last request's input
        self.assertEqual(sess.ctx_window, 272000)

    def test_malformed_lines_are_skipped(self) -> None:
        sess = read_codex_session(str(FIXTURES / "malformed.jsonl"))
        self.assertEqual(sess.session_id, SID)
        self.assertEqual(sess.model, "gpt-6-sol")
        self.assertEqual(sess.prompt, ["Fix the thing"])

    def test_malformed_type_values_are_skipped_and_later_records_parse(self) -> None:
        lines: list[dict | str] = [meta_line(), prompt_line("Fix the thing")]
        for index, kind in enumerate(([], {}, 7, None)):
            lines.append({"timestamp": stamp(3 + index), "type": "response_item",
                          "payload": {"type": kind, "role": "user", "content": "ignored"}})
        lines.append({"timestamp": stamp(8), "type": [], "payload": {"type": "message"}})
        lines.append({"timestamp": stamp(9), "type": "response_item",
                      "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "call_bad",
                                  "input": 'await tools.exec_command({cmd:"echo ok"});'}})
        sess = read_codex_session(self.write("badtypes.jsonl", lines))
        self.assertEqual(sess.session_id, SID)
        self.assertEqual(sess.prompt, ["Fix the thing"])
        self.assertEqual([call.arg for call in sess.calls], ["echo ok"])

    def test_a_message_part_with_an_unhashable_type_is_skipped(self) -> None:
        lines = [meta_line(), {"timestamp": stamp(2), "type": "response_item",
                               "payload": {"type": "message", "role": "user",
                                           "content": [{"type": [], "text": "bad"}, {"type": "input_text", "text": "good"}]}}]
        sess = read_codex_session(self.write("badcontent.jsonl", lines))
        self.assertEqual(sess.prompt, ["good"])

    def test_incremental_append_reads_only_the_new_bytes(self) -> None:
        path = self.copy("basic.jsonl")
        first = read_codex_session(path)
        self.assertEqual(len(first.calls), 4)
        with open(path, "r+b") as handle:      # bytes already parsed: a re-read would lose session_meta
            handle.write(b"X" * 200)
        with open(path, "ab") as handle:
            handle.write((json.dumps(call_line(30, "second pass", "call_z")) + "\n").encode())
        second = read_codex_session(path)
        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(second.prompt, first.prompt)
        self.assertEqual(len(second.calls), 5)
        self.assertEqual(second.calls[-1].arg, "second pass")
        self.assertEqual(second.last_ts, epoch(stamp(30)))

    def test_truncated_file_is_parsed_again(self) -> None:
        path = self.copy("basic.jsonl")
        first = read_codex_session(path)
        self.assertEqual(len(first.calls), 4)
        lines = Path(path).read_bytes().splitlines(keepends=True)
        Path(path).write_bytes(b"".join(lines[:5]))   # session_meta, turn_context, two injected, the prompt
        second = read_codex_session(path)
        self.assertEqual(second.calls, [])
        self.assertEqual(second.prompt, first.prompt)
        self.assertEqual(second.model, "gpt-6-luna")

    def test_same_size_rewrite_is_parsed_again(self) -> None:
        path = self.copy("basic.jsonl")
        first = read_codex_session(path)
        self.assertEqual(first.model, "gpt-6-luna")
        raw = Path(path).read_bytes()
        before = os.stat(path).st_mtime_ns
        Path(path).write_bytes(raw.replace(b'"gpt-6-luna"', b'"gpt-6-solx"'))
        os.utime(path, ns=(before, before + 1_000_000))
        second = read_codex_session(path)
        self.assertEqual(second.model, "gpt-6-solx")

    def test_newest_200_calls_are_kept(self) -> None:
        lines = [meta_line(), prompt_line("do the work")]
        lines += [call_line(index, f"step {index}", f"call_{index}") for index in range(210)]
        sess = read_codex_session(self.write("many.jsonl", lines))
        self.assertEqual(len(sess.calls), 200)
        self.assertEqual(sess.calls[0].arg, "step 10")
        self.assertEqual(sess.calls[-1].arg, "step 209")

    def test_prompt_lines_are_capped(self) -> None:
        text = "\n".join(f"line {index}" for index in range(450))
        sess = read_codex_session(self.write("long.jsonl", [meta_line(), prompt_line(text)]))
        self.assertEqual(len(sess.prompt), 400)
        self.assertEqual(sess.prompt[-1], "line 399")

    def test_returned_session_is_a_copy(self) -> None:
        path = self.copy("basic.jsonl")
        first = read_codex_session(path)
        first.calls.clear()
        first.prompt.append("junk")
        second = read_codex_session(path)
        self.assertEqual(len(second.calls), 4)
        self.assertNotIn("junk", second.prompt)

    def test_now_does_not_change_the_parse(self) -> None:
        path = self.copy("basic.jsonl")
        self.assertEqual(read_codex_session(path, now=0.0), read_codex_session(path, now=4e9))

    def test_missing_file_is_none(self) -> None:
        self.assertIsNone(read_codex_session(str(self.dir / "nope.jsonl")))

    def test_empty_file_is_none(self) -> None:
        path = self.dir / "empty.jsonl"
        path.write_text("")
        self.assertIsNone(read_codex_session(str(path)))

    def test_file_that_is_not_a_rollout_is_none(self) -> None:
        self.assertIsNone(read_codex_session(str(FIXTURES / "not-rollout.jsonl")))

    def test_only_malformed_lines_is_none(self) -> None:
        self.assertIsNone(read_codex_session(self.write("garbage.jsonl", ["nope", "[1, 2, 3]"])))


if __name__ == "__main__":
    unittest.main()
