"""Ops spark: tools/min bins and sparkline, step rows and why lines."""
from __future__ import annotations

import time
import unittest

import opsspark as osp
from model import LogState, Step, Worker
from widgets import (
    _wd_c,
    _wd_C_AMBER,
    _wd_C_BLUE,
    _wd_C_BORDER,
    _wd_C_CYAN,
    _wd_C_FAINT,
    _wd_C_GREEN,
    _wd_C_MUTED,
    _wd_C_RED,
    _wd_C_SUB,
    _wd_C_TEAL,
    _wd_SGR_RE,
    _wd_spinner,
    _wd_width,
)

NOW = 1_800_000_000.0
START = NOW - osp.OS_SPAN_S


def plain(s: str) -> str:
    return _wd_SGR_RE.sub("", s)


def step(ts: float = NOW, kind: str = "tool", tool: str = "bash", text: str = "ls -la", dur: float | None = None,
         outcome: str = "") -> Step:
    return Step(ts=ts, kind=kind, tool=tool, text=text, dur=dur, outcome=outcome)


def worker(state: str = "RUNNING", failure: str = "", steps: list[Step] | None = None) -> Worker:
    return Worker(name="w", state=state, failure=failure, log=LogState(steps=steps or []))


# Column starts inside a step row: indent 2, time 2..9, tool 12..17, outcome 19..21, duration 22..28, text 31.
TIME_AT, TOOL_AT, OUT_AT, DUR_AT, TEXT_AT = 2, 12, 19, 22, 31


class BinsTests(unittest.TestCase):
    def test_empty_pulses(self) -> None:
        self.assertEqual(osp._os_bins([], NOW), [0] * osp.OS_CELLS)

    def test_all_zero_pulses(self) -> None:
        self.assertEqual(sum(osp._os_bins([(NOW - 10, 0), (NOW - 500, 0)], NOW)), 0)

    def test_pulse_at_window_start_is_excluded(self) -> None:
        self.assertEqual(sum(osp._os_bins([(START, 5)], NOW)), 0)

    def test_pulse_at_now_is_included(self) -> None:
        bins = osp._os_bins([(NOW, 5)], NOW)
        self.assertEqual(sum(bins), 5)
        self.assertEqual(bins[-1], 5)

    def test_pulse_just_inside_window_start_lands_in_first_cell(self) -> None:
        bins = osp._os_bins([(START + 0.5, 3)], NOW)
        self.assertEqual(bins[0], 3)
        self.assertEqual(sum(bins), 3)

    def test_pulses_outside_window_are_ignored(self) -> None:
        self.assertEqual(sum(osp._os_bins([(NOW + 1, 1), (START - 1, 1), (NOW + 3600, 9)], NOW)), 0)

    def test_single_spike_lands_in_one_cell(self) -> None:
        bins = osp._os_bins([(NOW - 600, 4)], NOW)   # 1200 s after the window start: cell 10 of 15
        self.assertEqual(bins[10], 4)
        self.assertEqual(sum(bins), 4)
        self.assertEqual(sum(1 for v in bins if v), 1)

    def test_same_cell_values_add_up(self) -> None:
        bins = osp._os_bins([(NOW - 600, 2), (NOW - 590, 3)], NOW)
        self.assertEqual(bins[10], 5)

    def test_oldest_first(self) -> None:
        bins = osp._os_bins([(START + 1, 1), (NOW, 7)], NOW)
        self.assertEqual(bins[0], 1)
        self.assertEqual(bins[-1], 7)

    def test_length_matches_cells(self) -> None:
        for cells in range(1, 61):
            self.assertEqual(len(osp._os_bins([(NOW - 5, 1)], NOW, cells)), cells)

    def test_zero_cells_is_empty(self) -> None:
        self.assertEqual(osp._os_bins([(NOW, 1)], NOW, 0), [])


class SparkTests(unittest.TestCase):
    def test_exact_width_for_all_cell_counts(self) -> None:
        for cells in range(1, 61):
            for values in ([], [1], [0, 3, 2], list(range(90))):
                self.assertEqual(_wd_width(osp._os_spark(values, cells)), cells, (cells, len(values)))

    def test_short_values_pad_left_with_zero_dots(self) -> None:
        out = plain(osp._os_spark([7], 3))
        self.assertEqual(out, "··█")

    def test_long_values_keep_newest(self) -> None:
        self.assertEqual(plain(osp._os_spark([9, 9, 1, 7], 2)), "▂█")

    def test_scaled_to_max(self) -> None:
        self.assertEqual(plain(osp._os_spark([1, 7], 2)), "▂█")

    def test_zero_is_faint_dot(self) -> None:
        out = osp._os_spark([0, 3, 0], 3)
        self.assertEqual(plain(out), "·█·")
        self.assertIn(_wd_c("·", _wd_C_FAINT), out)

    def test_all_zero_is_all_dots(self) -> None:
        self.assertEqual(plain(osp._os_spark([0, 0, 0], 3)), "···")

    def test_colour_ramp_teal_blue_cyan(self) -> None:
        out = osp._os_spark([1, 4, 7], 3)
        self.assertIn(_wd_c("▂", _wd_C_TEAL), out)
        self.assertIn(_wd_c("▅", _wd_C_BLUE), out)
        self.assertIn(_wd_c("█", _wd_C_CYAN), out)

    def test_zero_cells_is_empty(self) -> None:
        self.assertEqual(osp._os_spark([1, 2], 0), "")

    def test_tools_spark_bins_then_spark(self) -> None:
        out = osp._os_tools_spark([(NOW - 600, 4)], NOW)
        self.assertEqual(_wd_width(out), osp.OS_CELLS)
        self.assertEqual(plain(out), "··········█····")

    def test_tools_spark_empty_is_dots(self) -> None:
        out = osp._os_tools_spark([], NOW, 12)
        self.assertEqual(plain(out), "·" * 12)

    def test_tools_spark_width_for_cell_counts(self) -> None:
        for cells in (1, 15, 60):
            self.assertEqual(_wd_width(osp._os_tools_spark([(NOW - 1, 2)], NOW, cells)), cells)


class StepLineTests(unittest.TestCase):
    def test_time_column(self) -> None:
        line = plain(osp._os_step_line(step(ts=NOW), 120, NOW))
        self.assertEqual(line[TIME_AT:TIME_AT + 8], time.strftime("%H:%M:%S", time.localtime(NOW)))

    def test_zero_time_is_dashes(self) -> None:
        line = plain(osp._os_step_line(step(ts=0.0), 120, NOW))
        self.assertEqual(line[TIME_AT:TIME_AT + 8], "--:--:--")

    def test_tool_label(self) -> None:
        line = plain(osp._os_step_line(step(tool="grep"), 120, NOW))
        self.assertEqual(line[TOOL_AT:TOOL_AT + 6], "grep  ")

    def test_non_tool_step_shows_kind(self) -> None:
        line = plain(osp._os_step_line(step(kind="text", tool=""), 120, NOW))
        self.assertEqual(line[TOOL_AT:TOOL_AT + 6], "text  ")
        err = plain(osp._os_step_line(step(kind="error", tool=""), 120, NOW))
        self.assertEqual(err[TOOL_AT:TOOL_AT + 6], "error ")

    def test_tool_colour_is_blue_and_text_sub(self) -> None:
        line = osp._os_step_line(step(tool="read", text="src/app.py"), 120, NOW)
        self.assertIn(_wd_c("read  ", _wd_C_BLUE), line)
        self.assertIn(_wd_c("src/app.py", _wd_C_SUB), line)

    def test_time_is_faint(self) -> None:
        line = osp._os_step_line(step(ts=NOW), 120, NOW)
        self.assertIn(_wd_c(time.strftime("%H:%M:%S", time.localtime(NOW)), _wd_C_FAINT), line)

    def test_ok_is_green(self) -> None:
        line = osp._os_step_line(step(outcome="ok"), 120, NOW)
        self.assertEqual(plain(line)[OUT_AT:OUT_AT + 3], "ok ")
        self.assertIn(_wd_c("ok ", _wd_C_GREEN), line)

    def test_failed_is_red_cross(self) -> None:
        line = osp._os_step_line(step(outcome="failed"), 120, NOW)
        self.assertEqual(plain(line)[OUT_AT:OUT_AT + 3], "✖  ")
        self.assertIn(_wd_c("✖  ", _wd_C_RED), line)

    def test_live_newest_step_shows_spinner(self) -> None:
        line = osp._os_step_line(step(outcome=""), 120, NOW, live=True)
        self.assertEqual(plain(line)[OUT_AT:OUT_AT + 3], _wd_spinner(NOW) + "  ")
        self.assertIn(_wd_c(_wd_spinner(NOW) + "  ", _wd_C_BLUE), line)

    def test_spinner_follows_time(self) -> None:
        a = plain(osp._os_step_line(step(), 120, NOW, live=True))
        b = plain(osp._os_step_line(step(), 120, NOW + 0.1, live=True))
        self.assertEqual(a[OUT_AT], _wd_spinner(NOW))
        self.assertEqual(b[OUT_AT], _wd_spinner(NOW + 0.1))

    def test_not_live_and_no_outcome_is_blank(self) -> None:
        line = plain(osp._os_step_line(step(outcome=""), 120, NOW, live=False))
        self.assertEqual(line[OUT_AT:OUT_AT + 3], "   ")

    def test_outcome_wins_over_live(self) -> None:
        line = plain(osp._os_step_line(step(outcome="ok"), 120, NOW, live=True))
        self.assertEqual(line[OUT_AT:OUT_AT + 3], "ok ")

    def test_duration_formats(self) -> None:
        cases = {None: "       ", 0.0: "  <0.1s", 0.05: "  <0.1s", 0.4: "     0s",
                 42.0: "    42s", 65.0: "01m 05s", 252.0: "04m 12s"}
        for dur, want in cases.items():
            line = plain(osp._os_step_line(step(dur=dur), 120, NOW))
            self.assertEqual(line[DUR_AT:DUR_AT + 7], want, dur)

    def test_duration_amber_from_sixty_seconds(self) -> None:
        self.assertIn(_wd_c("01m 00s", _wd_C_AMBER), osp._os_step_line(step(dur=60.0), 120, NOW))
        self.assertNotIn(_wd_c("    59s", _wd_C_AMBER), osp._os_step_line(step(dur=59.0), 120, NOW))
        self.assertIn(_wd_c("    59s", _wd_C_MUTED), osp._os_step_line(step(dur=59.0), 120, NOW))

    def test_text_starts_at_fixed_column(self) -> None:
        line = plain(osp._os_step_line(step(text="git status"), 120, NOW))
        self.assertEqual(line[TEXT_AT:TEXT_AT + 10], "git status")

    def test_long_text_is_cut_with_ellipsis(self) -> None:
        line = plain(osp._os_step_line(step(text="x" * 300), 60, NOW))
        self.assertEqual(_wd_width(line), 60)
        self.assertTrue(line.endswith("…"))

    def test_width_is_exact(self) -> None:
        steps = [
            step(),
            step(ts=0.0, kind="text", tool="", text="a" * 500, dur=0.02, outcome="ok"),
            step(tool="webfetch_long_name", text="說明 mixed 文字", dur=75.0, outcome="failed"),
            step(kind="error", tool="", text="", dur=None, outcome=""),
        ]
        for w in range(1, 201):
            for st in steps:
                for live in (False, True):
                    line = osp._os_step_line(st, w, NOW, live)
                    self.assertEqual(_wd_width(line), w, (w, st.kind, live))

    def test_width_is_exact_for_the_brief_range(self) -> None:
        for w in range(40, 201):
            self.assertEqual(_wd_width(osp._os_step_line(step(text="ls"), w, NOW)), w)


class WhyTests(unittest.TestCase):
    def test_failure_has_precedence(self) -> None:
        wk = worker("FAILED", failure="exit 1 · boom", steps=[step(kind="error", text="other")])
        self.assertEqual(osp._os_why(wk), "exit 1 · boom")

    def test_last_error_step_when_failed(self) -> None:
        wk = worker("FAILED", steps=[step(kind="error", text="first"), step(text="ok step"),
                                     step(kind="error", text="last err")])
        self.assertEqual(osp._os_why(wk), "last err")

    def test_no_error_step_when_not_failed(self) -> None:
        wk = worker("DONE", steps=[step(kind="error", text="warned")])
        self.assertEqual(osp._os_why(wk), "")

    def test_failed_without_reason_is_empty(self) -> None:
        self.assertEqual(osp._os_why(worker("FAILED", steps=[step(text="fine")])), "")
        self.assertEqual(osp._os_why(worker("FAILED")), "")

    def test_running_worker_is_empty(self) -> None:
        self.assertEqual(osp._os_why(worker("RUNNING")), "")

    def test_why_line_structure(self) -> None:
        line = osp._os_why_line("     ", "missing module", 60)
        self.assertEqual(plain(line), "     " + "  why " + "missing module" + " " * (60 - 5 - 6 - 14))
        self.assertIn(_wd_c("     ", _wd_C_BORDER), line)
        self.assertIn(_wd_c("  why ", _wd_C_RED), line)
        self.assertIn(_wd_c("missing module", _wd_C_SUB), line)

    def test_why_line_width_is_exact(self) -> None:
        for w in range(1, 201):
            for cont in ("", "  ", "│ ", " " * 27):
                line = osp._os_why_line(cont, "exit 1 · ModuleNotFoundError: No module named 'collect'", w)
                self.assertEqual(_wd_width(line), w, (w, cont))

    def test_why_line_cut_keeps_label(self) -> None:
        line = plain(osp._os_why_line("", "y" * 200, 40))
        self.assertEqual(_wd_width(line), 40)
        self.assertTrue(line.startswith("  why "))
        self.assertTrue(line.endswith("…"))


if __name__ == "__main__":
    unittest.main()
