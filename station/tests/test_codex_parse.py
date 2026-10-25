"""Codex log parsing: which lines become steps.

Fixtures follow the shapes of real codex logs, trimmed. The two false-step examples
(a JSX line in command output, ``ERROR_CODES,``) are synthetic: no current log holds them.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from logparse import parse_log
from model import LogState

HEAD = [
    "Reading additional input from stdin...",
    "OpenAI Codex v0.161.0",
    "--------",
    "workdir: /home/user/Projects/sample-project/.stage/wt-g-anim",
    "model: gpt-6-luna",
    "reasoning effort: medium",
    "--------",
]


def _texts(state: LogState, kind: str, tool: str | None = None) -> list[str]:
    return [
        s.text
        for s in state.steps
        if s.kind == kind and (tool is None or s.tool == tool)
    ]


class CodexParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, lines: list[str], mode: str = "w") -> str:
        path = os.path.join(self.dir, name)
        with open(path, mode) as handle:
            handle.write("\n".join(lines) + "\n")
        return path

    def _parse(self, lines: list[str]) -> LogState:
        return parse_log(self._write("codex.log", HEAD + lines), now=1000.0)

    def test_command_output_is_never_a_step(self) -> None:
        # Output lines that look like code, markup or errors must stay out of the steps.
        state = self._parse([
            "user",
            "# brief",
            "Goal: run the tests",
            "codex",
            "I will run the tests.",
            "exec",
            '/usr/bin/bash -lc "pytest -q" in /x',
            " exited 1 in 812ms:",
            "<Text style={styles.statusText} numberOfLines={1}>",
            "ERROR_CODES,",
            "ERROR: test_two_bursts (test_collect.TestSession.test_two_bursts)",
            "Error: Test timed out in 5000ms.",
            "# Context pack: station redesign",
            "tokens used",
            "68,375",
        ])
        self.assertEqual(_texts(state, "text"), ["I will run the tests."])
        self.assertEqual(_texts(state, "tool", "shell"), ['/usr/bin/bash -lc "pytest -q"'])
        self.assertEqual(_texts(state, "error"), ["command exited 1"])
        self.assertEqual(state.errors, 1)
        self.assertEqual(state.tokens_in, 68375)
        self.assertEqual(state.model, "gpt-6-luna (medium)")

    def test_output_with_only_success_header_adds_no_steps(self) -> None:
        state = self._parse([
            "codex",
            "Checking.",
            "exec",
            '/usr/bin/bash -lc "cat f" in /x',
            " succeeded in 0ms:",
            "<Text style={styles.statusText} numberOfLines={1}>",
            "ERROR_CODES,",
            "codex",
            "Done.",
        ])
        self.assertEqual(_texts(state, "text"), ["Checking.", "Done."])
        self.assertEqual(_texts(state, "error"), [])
        self.assertEqual(state.errors, 0)
        self.assertEqual(state.tool_counts.get("shell"), 1)

    def test_agent_message_is_one_step_across_lines(self) -> None:
        state = self._parse([
            "codex",
            "First line of the message.",
            "",
            "Second line, with `code`.",
            "exec",
            '/usr/bin/bash -lc "ls" in /x',
            " succeeded in 0ms:",
        ])
        self.assertEqual(
            _texts(state, "text"),
            ["First line of the message. Second line, with `code`."],
        )

    def test_message_line_starting_with_error_is_text(self) -> None:
        state = self._parse(["codex", "Error handling is the next step.", "exec", '/usr/bin/bash -lc "ls" in /x', " succeeded in 0ms:"])
        self.assertEqual(_texts(state, "text"), ["Error handling is the next step."])
        self.assertEqual(state.errors, 0)

    def test_multiline_heredoc_command_is_one_shell_step(self) -> None:
        state = self._parse([
            "exec",
            "/usr/bin/bash -lc \"cat > /tmp/make_quiz_data.py <<'PY'",
            "import json",
            "from pathlib import Path",
            "PY",
            "python /tmp/make_quiz_data.py",
            "python -m json.tool questions.json > /dev/null",
            "\" in /home/user/Projects/micro1/.stage/build-quiz",
            " succeeded in 0ms:",
            "diff --git a/quiz.py b/quiz.py",
        ])
        self.assertEqual(
            _texts(state, "tool", "shell"),
            [
                ("/usr/bin/bash -lc \"cat > /tmp/make_quiz_data.py <<'PY' import json "
                 "from pathlib import Path PY python /tmp/make_quiz_data.py "
                 "python -m json.tool questions.json > /dev/null \""),
            ],
        )
        self.assertEqual(state.tool_counts.get("shell"), 1)
        self.assertEqual(_texts(state, "text"), [])

    def test_command_closed_by_next_marker_loses_cwd(self) -> None:
        state = self._parse([
            "exec",
            '/usr/bin/bash -lc "ls" in /x',
            "exec",
            '/usr/bin/bash -lc "pwd" in /x',
            " succeeded in 0ms:",
            "/x",
        ])
        self.assertEqual(
            _texts(state, "tool", "shell"),
            ['/usr/bin/bash -lc "ls"', '/usr/bin/bash -lc "pwd"'],
        )

    def test_nonzero_exit_is_one_error_step(self) -> None:
        state = self._parse([
            "exec",
            '/usr/bin/bash -lc "make check" in /x',
            " exited 1 in 4ms:",
            "codex",
            "No match.",
        ])
        self.assertEqual(_texts(state, "error"), ["command exited 1"])
        self.assertEqual(state.errors, 1)
        self.assertEqual(_texts(state, "text"), ["No match."])

    def test_search_finding_nothing_is_not_an_error(self) -> None:
        state = self._parse([
            "exec",
            '/usr/bin/bash -lc "rg -n foo src" in /x',
            " exited 1 in 4ms:",
            "exec",
            '/usr/bin/bash -lc "grep -q x y" in /x',
            " exited 2 in 4ms:",
            "exec",
            '/usr/bin/bash -lc "grep -q x y; test -d gone" in /x',
            " exited 1 in 4ms:",
        ])
        self.assertEqual(_texts(state, "error"), ["command exited 2", "command exited 1"])
        self.assertEqual(state.errors, 2)

    def test_apply_patch_paths_are_edit_steps(self) -> None:
        state = self._parse([
            "apply patch",
            "patch: completed",
            "/x/a.py",
            "diff --git a/a.py b/a.py",
            "@@ -1 +1 @@",
            "-Error: old",
            "+Error: new",
            "patch: completed",
            "/x/b.py",
            "/x/c.py",
            "diff --git a/b.py b/b.py",
            "hook: PostToolUse",
            "diff --git a/a.py b/a.py",
            "+ERROR: repeated after the hook",
            "codex",
            "Done.",
        ])
        self.assertEqual(_texts(state, "tool", "edit"), ["/x/a.py", "/x/b.py", "/x/c.py"])
        self.assertEqual(state.tool_counts.get("edit"), 3)
        self.assertEqual(_texts(state, "text"), ["Done."])
        self.assertEqual(state.errors, 0)

    def test_web_search_queries_are_tool_steps(self) -> None:
        state = self._parse([
            "web search: ",
            "web search: site:example.com codex  pricing",
            "codex",
            "Found it.",
        ])
        self.assertEqual(_texts(state, "tool", "websearch"), ["site:example.com codex pricing"])
        self.assertEqual(state.tool_counts.get("search"), 1)
        self.assertEqual(_texts(state, "text"), ["Found it."])

    def test_hook_lines_are_not_steps(self) -> None:
        state = self._parse([
            "hook: SessionStart",
            "hook: SessionStart Completed",
            "    Error: an indented line between blocks is not an error",
            "codex",
            "Hello.",
            "hook: Stop",
            "hook: Stop Completed",
            "tokens used",
            "1,234",
        ])
        self.assertEqual(_texts(state, "text"), ["Hello."])
        self.assertEqual(state.steps[0].kind, "text")
        self.assertEqual(state.errors, 0)
        self.assertEqual(state.tokens_in, 1234)

    def test_column_zero_stream_error_is_an_error_step(self) -> None:
        state = self._parse([
            "stream disconnected before completion: retrying",
            "unexpected status 503 Service Unavailable",
        ])
        self.assertEqual(
            _texts(state, "error"),
            ["stream disconnected before completion: retrying", "unexpected status 503 Service Unavailable"],
        )
        self.assertEqual(state.errors, 2)

    def test_output_stays_output_across_reads(self) -> None:
        part1 = HEAD + [
            "codex",
            "Checking.",
            "exec",
            '/usr/bin/bash -lc "cat f" in /x',
            " succeeded in 0ms:",
            "ERROR_CODES,",
        ]
        path = self._write("split.log", part1)
        first = parse_log(path, now=1000.0)
        self.assertEqual(first.codex_mode, "output")
        self.assertEqual(_texts(first, "text"), ["Checking."])
        with open(path, "a") as handle:
            handle.write("<Text style={styles.statusText} numberOfLines={1}>\ncodex\nDone.\n")
        second = parse_log(path, first, now=1000.0)
        self.assertEqual(_texts(second, "text"), ["Checking.", "Done."])
        self.assertEqual(_texts(second, "error"), [])
        self.assertEqual(second.errors, 0)

    def test_message_split_across_reads_is_one_step(self) -> None:
        part1 = HEAD + ["codex", "The first"]
        path = self._write("msg.log", part1)
        first = parse_log(path, now=1000.0)
        with open(path, "a") as handle:
            handle.write("part of it.\nexec\n/usr/bin/bash -lc \"ls\" in /x\n succeeded in 0ms:\n")
        second = parse_log(path, first, now=1000.0)
        self.assertEqual(_texts(second, "text"), ["The first part of it."])
        self.assertEqual(_texts(second, "tool", "shell"), ['/usr/bin/bash -lc "ls"'])

    def test_command_split_across_reads_is_one_step(self) -> None:
        part1 = HEAD + ["exec", "/usr/bin/bash -lc \"python - <<'PY'", "import json"]
        path = self._write("cmd.log", part1)
        first = parse_log(path, now=1000.0)
        self.assertEqual(first.codex_mode, "command")
        with open(path, "a") as handle:
            handle.write("print(1)\nPY\" in /x\n succeeded in 0ms:\n1\n")
        second = parse_log(path, first, now=1000.0)
        self.assertEqual(
            _texts(second, "tool", "shell"),
            ["/usr/bin/bash -lc \"python - <<'PY' import json print(1) PY\""],
        )
        self.assertEqual(second.tool_counts.get("shell"), 1)

    def test_real_shaped_patch_block(self) -> None:
        # Shape of a real apply_patch block: the diff is printed again after the hook lines.
        state = self._parse([
            "apply patch",
            "patch: completed",
            "/home/user/Projects/sample-project/.stage/wt-g-app/src/app.py",
            "diff --git a/src/app.py b/src/app.py",
            "index 8c80cfc40b8e85eb6480b2f47f3c7a44c64b3c23..7cdc0ecc861f650c5d5a035b21d6dc74371bba96",
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -15,9 +15,10 @@",
            " from dataclasses import dataclass",
            "+from collect import GlobalCollector",
            "hook: PostToolUse",
            "hook: PostToolUse Completed",
            "diff --git a/src/app.py b/src/app.py",
            "+from collect import GlobalCollector",
            "hook: Stop",
        ])
        self.assertEqual(
            _texts(state, "tool", "edit"),
            ["/home/user/Projects/sample-project/.stage/wt-g-app/src/app.py"],
        )
        self.assertEqual(len(state.steps), 1)
        self.assertEqual(state.errors, 0)

    def test_real_shaped_message_and_command(self) -> None:
        state = self._parse([
            "user",
            "# c-taste",
            "Goal: Critique visual taste and animation",
            "codex",
            "I’ll compare each proposal against the supplied context, then report only grounded findings.",
            "exec",
            "/usr/bin/bash -lc \"pwd && sed -n '1,240p' /home/user/Projects/sample-project/.stage/design/context.md\"",
            " succeeded in 0ms:",
            "/home/user/Projects/sample-project/.stage/wt-g-anim",
            "# Context pack: station redesign (read this first)",
            "",
            "Live status for the dashboard.",
            "codex",
            "## Summary",
            "- **ADOPT:** the single expanded worker.",
            "tokens used",
            "68,375",
        ])
        self.assertEqual(
            _texts(state, "text"),
            [
                "I’ll compare each proposal against the supplied context, then report only grounded findings.",
                "## Summary - **ADOPT:** the single expanded worker.",
            ],
        )
        self.assertEqual(len(_texts(state, "tool", "shell")), 1)
        self.assertEqual(state.errors, 0)
        self.assertEqual(state.tokens_in, 68375)

    def test_tokens_last_value_of_a_run_counts(self) -> None:
        # realctx-1 shape: two readings in one run; the later, larger one is the run's total.
        state = self._parse([
            "codex", "Working.", "tokens used", "141,159",
            "codex", "Done.", "tokens used", "151,250",
        ])
        self.assertEqual(state.tokens_in, 151250)
        self.assertIsNone(state.tokens_out)

    def test_tokens_sum_across_banner_runs(self) -> None:
        # s4-freeze shape: three runs, each opened by a banner: 64,361 + 39,821 + 53,824.
        state = self._parse([
            "codex", "First.", "tokens used", "64,361",
            "OpenAI Codex v0.161.0", "--------", "model: gpt-6-luna", "--------",
            "codex", "Second.", "tokens used", "39,821",
            "OpenAI Codex v0.161.0", "--------", "--------",
            "codex", "Third.", "tokens used", "53,824",
        ])
        self.assertEqual(state.tokens_in, 158006)
        self.assertEqual(_texts(state, "text"), ["First.", "Second.", "Third."])

    def test_tokens_falling_value_starts_a_new_run(self) -> None:
        # realctx-2 shape: 154,875 then 116,764 with no banner: two runs.
        state = self._parse([
            "codex", "A.", "tokens used", "154,875",
            "codex", "B.", "tokens used", "116,764",
        ])
        self.assertEqual(state.tokens_in, 271639)

    def test_banner_inside_command_output_is_not_a_run(self) -> None:
        state = self._parse([
            "codex", "A.", "tokens used", "100",
            "exec", '/usr/bin/bash -lc "cat log" in /x', " succeeded in 0ms:",
            "OpenAI Codex v0.161.0",
            "tokens used", "150",
        ])
        self.assertEqual(state.tokens_in, 150)


if __name__ == "__main__":
    unittest.main()
