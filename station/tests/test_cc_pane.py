"""C3: the detail pane draws six tabs as pure lines of exactly the asked width."""
from __future__ import annotations

import unittest

from ccpane import PANE_TABS, pane_lines, pane_max_scroll, pane_tab_bar
from model import (
    DONE,
    FAILED,
    RUNNING,
    CCCall,
    LogState,
    Stage,
    Step,
    Worker,
    WorkState,
)
from widgets import _wd_C_RED, _wd_sgr, _wd_SGR_RE

NOW = 1_700_000_000.0
WIDTHS = (40, 60, 80)


def _plain(line: str) -> str:
    return _wd_SGR_RE.sub("", line)


def _lead() -> Stage:
    return Stage(
        path="/tmp/lead", label="proj", session_name="lead", display_name="sample-project", session_id="sess-1",
        state="active", lead_state="live", lead_since=NOW - 3600, lead_model="opus-5.5", effort="high",
        harness="claude", tag="CC·W", lead_tokens=812000, lead_ctx=82000, lead_ctx_window=200000,
        lead_cost=2.1, lead_tools=41,
        lead_tool_pulses=[(NOW - 120, 2), (NOW - 60, 4)],
        doing=[(NOW - 10, "Edit render.py")],
        calls=[CCCall(NOW - 20, "read", "src/render.py", "ok", 0.3)],
        prompt=["make the pane work"], brief_text=["build the pane"], system_prompt=["you are a lead"],
        goal="build the control center", cwd="/tmp/lead", tmux="lead", resume_cmd="claude --resume sess-1",
        work=WorkState(branch="main", dirty=True, ahead=2, checked=NOW - 30),
    )


def _worker() -> Worker:
    return Worker(
        name="c1-codexread", state=RUNNING, model="luna@medium", effort="high", harness="codex",
        goal="parse rollouts", pid=4321, start=NOW - 900, cpu=12.5, rss=250 * 1024 * 1024,
        level_tag="WORKER",
        log=LogState(
            harness="codex", model="luna", tokens_in=96000, tokens_out=4000, tokens_cache=120000,
            ctx_tokens=60000, ctx_window=272000, cost=0.12, errors=1, last_ts=NOW - 5,
            tool_counts={"read": 20, "edit": 12, "shell": 8, "search": 1},
            steps=[
                Step(NOW - 60, "tool", "read", "src/model.py", 0.4, "ok"),
                Step(NOW - 30, "tool", "apply_patch", "src/codexread.py", 1.2, "failed"),
                Step(NOW - 5, "error", "", "patch did not apply", None, "failed"),
            ],
            pulses=[(NOW - 60, 1), (NOW - 30, 2), (NOW - 5, 1)],
            tool_pulses=[(NOW - 60, 1), (NOW - 30, 2), (NOW - 5, 1)],
        ),
        calls=[
            CCCall(NOW - 60, "read", "/home/d/proj/src/model.py", "ok", 0.4),
            CCCall(NOW - 30, "apply_patch", "src/codexread.py", "failed", 1.2, err="hunk failed"),
            CCCall(NOW - 5, "bash", "python3 -m unittest", "", None),
        ],
        brief_text=["Build the codex reader."],
        prompt=["Read the rollout and parse it."],
        system_prompt=["You are a careful worker."],
        result_text=["Done: reader added."], summary=["reader added"],
        flags=frozenset({"empty log"}),
    )


def _rows() -> list[tuple[Stage, Worker | None]]:
    return [
        (_lead(), None),
        (_lead(), _worker()),
        (Stage(path="/tmp/empty", label="empty"), None),
        (Stage(path="/tmp/empty", label="empty"), Worker(name="empty", state=DONE)),
    ]


class PaneTabBarTests(unittest.TestCase):
    def test_bar_fits_and_spans_stay_inside(self) -> None:
        for width in (20, 30) + WIDTHS:
            line, spans = pane_tab_bar("Tools", width)
            self.assertEqual(len(_plain(line)), width)
            for col0, col1, name in spans:
                self.assertIn(name, PANE_TABS)
                self.assertGreaterEqual(col0, 0)
                self.assertLessEqual(col1, width - 1)
                self.assertGreaterEqual(col1, col0)

    def test_short_names_and_marker(self) -> None:
        wide, wide_spans = pane_tab_bar("Overview", 56)
        self.assertIn("▸Overview", _plain(wide))
        self.assertEqual([name for _, _, name in wide_spans], list(PANE_TABS))
        narrow, narrow_spans = pane_tab_bar("Overview", 40)
        self.assertNotIn("▸", _plain(narrow))
        self.assertIn("Over", _plain(narrow))
        self.assertEqual([name for _, _, name in narrow_spans], list(PANE_TABS))

    def test_unknown_tab_has_no_marker(self) -> None:
        line, spans = pane_tab_bar("Nope", 80)
        self.assertNotIn("▸", _plain(line))
        self.assertEqual(len(spans), len(PANE_TABS))


class PaneLinesTests(unittest.TestCase):
    def test_every_tab_is_exactly_sized(self) -> None:
        for width in WIDTHS:
            for tab in PANE_TABS:
                for stage, worker in _rows():
                    lines = pane_lines(stage, worker, tab, width, 12, 0, NOW)
                    self.assertEqual(len(lines), 12)
                    for line in lines:
                        self.assertEqual(len(_plain(line)), width, f"{tab} {width} {worker and worker.name}")

    def test_height_zero(self) -> None:
        self.assertEqual(pane_lines(_lead(), None, "Overview", 80, 0, 0, NOW), [])

    def test_scroll_clamps(self) -> None:
        stage, worker = _lead(), _worker()
        top = pane_lines(stage, worker, "Prompt", 56, 4, 0, NOW)
        maximum = pane_max_scroll(stage, worker, "Prompt", 56, 4)
        self.assertGreater(maximum, 0)
        self.assertNotEqual(top, pane_lines(stage, worker, "Prompt", 56, 4, maximum, NOW))
        self.assertEqual(pane_lines(stage, worker, "Prompt", 56, 4, maximum, NOW),
                         pane_lines(stage, worker, "Prompt", 56, 4, maximum + 50, NOW))
        for tab in PANE_TABS:
            self.assertGreaterEqual(pane_max_scroll(stage, worker, tab, 56, 4), 0)

    def test_prompt_wraps_and_empty_sections_say_so(self) -> None:
        worker = _worker()
        worker.prompt = ["word " * 60]
        stage = Stage(path="/tmp/empty", label="empty")
        lines = [_plain(line) for line in pane_lines(stage, worker, "Prompt", 40, 20, 0, NOW)]
        text = "\n".join(lines)
        self.assertIn("BRIEF", text)
        self.assertIn("FIRST PROMPT", text)
        self.assertIn("word word", text)
        self.assertGreater(sum("word" in line for line in lines), 3)
        self.assertTrue(all(len(line) == 40 for line in lines))
        empty = "\n".join(_plain(line) for line in pane_lines(stage, Worker(name="e", state=DONE),
                                                              "Prompt", 40, 20, 0, NOW))
        self.assertIn("no prompt captured: this harness does not expose it", " ".join(empty.split()))
        self.assertNotIn("FIRST PROMPT", empty)

    def test_tools_failed_row_red_and_newest_last(self) -> None:
        lines = pane_lines(_lead(), _worker(), "Tools", 80, 30, 0, NOW)
        failed = [line for line in lines if "FAIL" in _plain(line)]
        self.assertTrue(failed)
        self.assertIn(_wd_sgr(_wd_C_RED), failed[0])
        tools = "\n".join(_plain(line) for line in lines)
        self.assertLess(tools.index("src/codexread.py"), tools.index("python3 -m unittest"))   # newest last

    def test_events_newest_last(self) -> None:
        lines = [_plain(line) for line in pane_lines(_lead(), _worker(), "Events", 80, 20, 0, NOW)]
        text = "\n".join(lines)
        self.assertLess(text.index("src/model.py"), text.index("patch did not apply"))
        self.assertIn("tool     read src/model.py", text)
        self.assertIn("err      patch did not apply", text)

    def test_files_lists_counts_and_git(self) -> None:
        text = "\n".join(_plain(line) for line in pane_lines(_lead(), _worker(), "Files", 80, 20, 0, NOW))
        self.assertIn("FILES", text)
        self.assertIn("src/codexread.py", text)
        self.assertIn("×1", text)
        self.assertIn("GIT", text)
        self.assertIn("branch", text)
        self.assertIn("main", text)
        self.assertIn("uncommitted", text)
        self.assertIn("2 unpushed", text)

    def test_metrics_sparklines_and_no_data(self) -> None:
        worker = _worker()
        text = "\n".join(_plain(line) for line in pane_lines(_lead(), worker, "Metrics", 56, 10, 0, NOW))
        self.assertIn("TOOLS/MIN", text)
        self.assertIn("EVENTS/MIN", text)
        self.assertNotIn("no data", text)
        self.assertIn("CTX", text)
        empty = "\n".join(_plain(line) for line in pane_lines(
            Stage(path="/tmp/e", label="e"), Worker(name="e", state=DONE), "Metrics", 56, 10, 0, NOW))
        self.assertIn("no metrics yet", empty)
        self.assertNotIn("no data", empty)

    def test_overview_worker_fields(self) -> None:
        text = "\n".join(_plain(line) for line in pane_lines(_lead(), _worker(), "Overview", 80, 20, 0, NOW))
        for part in ("c1-codexread", "WORKER", "Codex", "luna@medium", "parse rollouts",
                     "CTX", "22%", "272k", "$0.12", "CPU", "RSS", "read 20", "FLAGS"):
            self.assertIn(part, text)
        self.assertNotIn("RESULT", text)   # still running

    def test_overview_lead_fields(self) -> None:
        text = "\n".join(_plain(line) for line in pane_lines(_lead(), None, "Overview", 80, 20, 0, NOW))
        for part in ("sample-project", "LEAD", "CC·W", "Opus 5.5@hi", "build the control center",
                     "812k", "41%", "of 200k", "$2.10", "read 1"):
            self.assertIn(part, text)

    def test_finished_worker_shows_result_and_failure(self) -> None:
        worker = _worker()
        worker.state = FAILED
        worker.failure = "patch rejected"
        text = "\n".join(_plain(line) for line in pane_lines(_lead(), worker, "Overview", 80, 24, 0, NOW))
        self.assertIn("FAILED", text)
        self.assertIn("patch rejected", text)
        self.assertIn("RESULT", text)
        self.assertIn("reader added", text)


def _bare_worker() -> Worker:
    return Worker(name="bare", state=DONE)


def _bare_stage() -> Stage:
    return Stage(path="/tmp/bare", label="bare")


class PaneFillTests(unittest.TestCase):
    def test_overview_recent_activity_only_when_room(self) -> None:
        stage, worker = _lead(), _worker()
        roomy = "\n".join(_plain(line) for line in pane_lines(stage, worker, "Overview", 80, 30, 0, NOW))
        self.assertIn("Recent activity", roomy)
        self.assertLess(roomy.index("Recent activity"), roomy.index("patch did not apply"))
        tight = "\n".join(_plain(line) for line in pane_lines(stage, worker, "Overview", 80, 12, 0, NOW))
        self.assertNotIn("Recent activity", tight)

    def test_recent_activity_has_last_six_events_only(self) -> None:
        worker = _worker()
        worker.log.steps = [Step(NOW - 100 + i, "tool", "read", f"f{i}.py", 0.1, "ok") for i in range(9)]
        text = "\n".join(_plain(line) for line in pane_lines(_lead(), worker, "Overview", 80, 60, 0, NOW))
        self.assertNotIn("f5.py", text)   # the 6 newest of the merged steps and calls: f6..f8 plus the 3 calls
        self.assertIn("f6.py", text)
        self.assertIn("f8.py", text)

    def test_empty_tabs_show_one_centred_sentence(self) -> None:
        cases = {
            "Tools": "no tool calls recorded yet",
            "Events": "no events logged yet for this row",
            "Files": "no file reads or edits recorded yet",
            "Metrics": "no metrics yet",
            "Prompt": "no prompt captured: this harness does not expose it",
        }
        for tab, sentence in cases.items():
            lines = [_plain(line) for line in pane_lines(_bare_stage(), _bare_worker(), tab, 80, 12, 0, NOW)]
            filled = [line for line in lines if line.strip()]
            self.assertEqual(len(filled), 1, tab)
            self.assertIn(sentence, filled[0])
            lead = len(filled[0]) - len(filled[0].lstrip())
            tail = len(filled[0]) - len(filled[0].rstrip())
            self.assertLessEqual(abs(lead - tail), 1, tab)   # centred

    def test_narrow_empty_sentence_is_cut_to_width(self) -> None:
        for width in WIDTHS:
            for tab in PANE_TABS:
                lines = pane_lines(_bare_stage(), _bare_worker(), tab, width, 6, 0, NOW)
                self.assertTrue(all(len(_plain(line)) == width for line in lines), f"{tab} {width}")

    def test_all_optional_fields_empty_is_safe(self) -> None:
        for width in WIDTHS:
            for tab in PANE_TABS:
                for height in (0, 1, 5, 30):
                    lines = pane_lines(_bare_stage(), _bare_worker(), tab, width, height, 0, NOW)
                    self.assertEqual(len(lines), height)
                    self.assertTrue(all(len(_plain(line)) == width for line in lines))
                    pane_max_scroll(_bare_stage(), _bare_worker(), tab, width, height)
                lead = pane_lines(_bare_stage(), None, tab, width, 8, 0, NOW)
                self.assertEqual(len(lead), 8)


if __name__ == "__main__":
    unittest.main()
