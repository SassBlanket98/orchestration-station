"""Station view layout: one expanded worker, grid rows, section order, muted text, selection rules."""
from __future__ import annotations

import re
import unittest

import widgets
from model import (
    DONE,
    FAILED,
    NOREPORT,
    QUIET,
    RUNNING,
    STALLED,
    Finding,
    LogState,
    Milestone,
    Review,
    Snapshot,
    Step,
    UIState,
    Worker,
)
from render import (
    _rd_build,
    _rd_default_sel,
    _rd_sel_on_new_start,
    _rd_tl_items,
    _rd_tl_pack,
    render_frame,
)
from widgets import _wd_fmt_dur, _wd_width

NOW = 1_760_050_000.0
SGR = re.compile(r"\x1b\[[0-9;]*m")
MUTED = "38;2;{};{};{}".format(*widgets._wd_C_MUTED)
DIM = "38;2;{};{};{}".format(*widgets._wd_C_DIM)


def plain(line: str) -> str:
    return SGR.sub("", line)


def _wk(name: str, state: str, start: float, end: float | None = None, summary: list[str] | None = None) -> Worker:
    live = state in (RUNNING, QUIET, STALLED)
    steps = [Step(NOW - 60 * (5 - i), "tool" if i % 2 == 0 else "text", "bash" if i % 2 == 0 else "",
                  f"step {i} pnpm test src/api/routes.ts") for i in range(6)]
    log = LogState(steps=steps, tool_counts={"read": 12, "edit": 5}, tokens_in=84_000, tokens_out=9_000,
                   cost=0.031, ctx_tokens=96_000)
    return Worker(name=name, state=state, title="Implement REST routes", goal="GET/POST /api/accounts",
                  model="sonnet-work medium", family="claude", start=start, end=None if live else end,
                  cpu=42.0, rss=310 << 20, nproc=3, quiet_s=3.0 if state == RUNNING else 0.0, log=log,
                  activity=[0, 1, 3, 5, 8, 2] * 10, summary=summary or [])


def fake(live: int = 2, finished: int = 6) -> Snapshot:
    """`live` running workers, then `finished` done ones, newest first (the Snapshot order)."""
    lv = [_wk(f"live-{i}", RUNNING, NOW - 1500 + i * 300) for i in range(live)]
    fin = [_wk(f"done-{i}", DONE, NOW - 1200 + i * 100, NOW - 1000 + i * 100,
               [f"Summary line {i}: 3 files changed, tests pass"]) for i in range(finished)]
    rev = [Review("review-x", "luna high", "codex", NOW - 900, NOW - 400, False, [Finding(1, "race")])]
    return Snapshot(title="fake", now=NOW, session_start=NOW - 1500, workers=lv + fin[::-1], reviews=rev,
                    milestones=[Milestone(NOW - 300, "lead text")], tokens_total=93_000, cost_total=0.031)


def lines(snap: Snapshot, ui: UIState, cols: int, rows: int) -> list[str]:
    return [plain(x) for x in render_frame(snap, ui, cols, rows, NOW)]


class SelectionRules(unittest.TestCase):
    def test_default_is_first_live_worker(self) -> None:
        snap = fake(2, 6)
        self.assertEqual(_rd_default_sel(snap), 0)

    def test_default_without_live_is_newest_finished(self) -> None:
        snap = fake(0, 3)
        newest = max(range(len(snap.workers)), key=lambda i: snap.workers[i].end or 0)
        self.assertEqual(_rd_default_sel(snap), newest)
        self.assertEqual(snap.workers[newest].name, "done-2")

    def test_default_on_empty_snapshot(self) -> None:
        self.assertEqual(_rd_default_sel(Snapshot()), 0)

    def test_invalid_selection_falls_back_to_default(self) -> None:
        snap = fake(2, 6)
        out = lines(snap, UIState(sel=99, expanded={"live-0"}), 100, 40)
        self.assertTrue(any("live-0" in ln for ln in out))
        self.assertTrue(any("now  " in ln for ln in out))   # live-0 is the expanded card
        self.assertFalse(any("›" in ln for ln in out if "live-1" in ln))

    def test_new_start_keeps_a_live_cursor(self) -> None:
        snap = fake(2, 6)
        self.assertEqual(_rd_sel_on_new_start(snap, 1), 1)

    def test_new_start_moves_a_finished_cursor_to_newest_live(self) -> None:
        snap = fake(2, 6)
        finished = next(i for i, wk in enumerate(snap.workers) if wk.state == DONE)
        self.assertEqual(_rd_sel_on_new_start(snap, finished), 1)   # live-1 started last

    def test_new_start_with_no_cursor_goes_to_newest_live(self) -> None:
        snap = fake(2, 6)
        self.assertEqual(_rd_sel_on_new_start(snap, -1), 1)

    def test_new_start_without_live_keeps_the_cursor(self) -> None:
        snap = fake(0, 3)
        self.assertEqual(_rd_sel_on_new_start(snap, 2), 2)


class CollapsePolicy(unittest.TestCase):
    def test_only_the_selected_worker_is_expanded(self) -> None:
        out = lines(fake(2, 6), UIState(sel=1, expanded={"live-1"}), 100, 40)
        steps = [i for i, ln in enumerate(out) if "› " in ln]
        self.assertTrue(steps, "expanded card shows its steps")
        owners = {re.search(r"(live|done)-\d", max((x for x in out[:i] if re.search(r"(live|done)-\d", x)),
                                                     key=out.index)).group(0) for i in steps}
        self.assertEqual(owners, {"live-1"})

    def test_other_live_worker_is_one_row(self) -> None:
        out = lines(fake(2, 6), UIState(sel=0), 100, 40)
        row = next(i for i, ln in enumerate(out) if "live-1" in ln)
        self.assertNotIn("›", out[row + 1])
        self.assertNotIn("Implement", out[row + 1])

    def test_only_the_selected_finished_row_expands(self) -> None:
        snap = fake(2, 6)
        out = lines(snap, UIState(sel=4, expanded={"done-3"}), 100, 40)   # done-3 is open
        self.assertEqual(snap.workers[4].name, "done-3")
        self.assertEqual(sum("Summary line 3" in ln for ln in out), 1)   # its detail, not a second copy
        row = next(ln for ln in out if "done-3" in ln)
        self.assertIn("done", row)
        self.assertNotIn("Summary", row)
        other = next(ln for ln in out if "done-2" in ln)
        self.assertIn("Summary line 2", other)   # an unselected row is one line with its summary


class PanelOrder(unittest.TestCase):
    def test_expanded_live_then_other_live_then_rule_then_finished(self) -> None:
        out = lines(fake(2, 6), UIState(sel=0), 100, 40)
        i_exp = next(i for i, ln in enumerate(out) if "live-0" in ln)
        i_other = next(i for i, ln in enumerate(out) if "live-1" in ln)
        i_rule = next(i for i, ln in enumerate(out) if "─────" in ln and "╭" not in ln and "╰" not in ln)
        i_fin = next(i for i, ln in enumerate(out) if "done-" in ln)
        self.assertLess(i_exp, i_other)
        self.assertLess(i_other, i_rule)
        self.assertLess(i_rule, i_fin)

    def test_finished_rows_newest_first(self) -> None:
        out = lines(fake(2, 6), UIState(sel=0), 160, 45)
        pos = [next(i for i, ln in enumerate(out) if f"done-{k}" in ln) for k in (5, 4, 3, 2, 1, 0)]
        self.assertEqual(pos, sorted(pos))

    def test_sections_in_order(self) -> None:
        out = lines(fake(2, 6), UIState(), 100, 40)
        lead = next(i for i, ln in enumerate(out) if "lead text" in ln)
        workers = next(i for i, ln in enumerate(out) if "╭─ workers" in ln)
        timeline = next(i for i, ln in enumerate(out) if "╭─ timeline" in ln)
        reviews = next(i for i, ln in enumerate(out) if "╭─ reviews" in ln)
        self.assertLess(lead, workers)
        self.assertLess(workers, timeline)
        self.assertLess(timeline, reviews)

    def test_more_finished_when_they_do_not_fit(self) -> None:
        out = lines(fake(2, 12), UIState(sel=0, expanded={"live-0"}), 100, 26)
        self.assertTrue(any(re.search(r"\+\d+ more finished", ln) for ln in out))


class TimelineCap(unittest.TestCase):
    def test_eight_lanes_then_earlier(self) -> None:
        ws = [_wk(f"t-{i}", DONE, NOW - 600 + i, NOW - 100 + i) for i in range(12)]   # all overlap
        snap = Snapshot(title="t", now=NOW, session_start=NOW - 600, workers=ws)
        lanes, dropped = _rd_tl_pack(_rd_tl_items(snap, 98), 8)
        self.assertLessEqual(len(lanes), 8)
        self.assertEqual(dropped, 4)
        out = lines(snap, UIState(), 100, 40)
        self.assertTrue(any("+4 earlier" in ln for ln in out))


class Grid(unittest.TestCase):
    def test_numeric_cluster_ends_every_row_at_the_same_column(self) -> None:
        out = lines(fake(2, 6), UIState(sel=0), 100, 40)
        rows = [ln for ln in out if ("live-" in ln or "done-" in ln) and "━" not in ln]   # not the timeline bars
        self.assertGreaterEqual(len(rows), 6)
        for ln in rows:
            inner = ln.strip("│").rstrip()
            self.assertTrue(inner.endswith("$0.031"), ln)

    def test_at_57_summary_drops_and_elapsed_stays(self) -> None:
        out = lines(fake(0, 6), UIState(sel=0), 57, 30)   # done-5 is the expanded row
        done = [ln for ln in out if "done-0" in ln]
        self.assertEqual(len(done), 1)
        self.assertNotIn("Summary", done[0])
        self.assertIn(_wd_fmt_dur(200.0), done[0])   # done-0 ran 200 s

    def test_model_kept_at_100(self) -> None:
        out = lines(fake(2, 6), UIState(sel=0), 100, 40)
        self.assertTrue(any("done-0" in ln and "sonnet-work" in ln for ln in out))
        self.assertTrue(any("done-0" in ln and "3 files changed" in ln for ln in out))


class Widths(unittest.TestCase):
    def test_no_raw_line_wider_than_the_frame(self) -> None:
        for cols, rows in ((160, 45), (100, 40), (84, 30), (57, 30), (40, 12)):
            for sel in (0, 2, 5):
                raw = _rd_build(fake(2, 6), UIState(sel=sel), cols, rows, NOW)
                for ln in raw:
                    self.assertLessEqual(_wd_width(ln), cols, (cols, rows, sel, plain(ln)))

    def test_hostile_names_and_models(self) -> None:
        snap = fake(2, 6)
        snap.workers[0].name = "x" * 300
        snap.workers[1].model = "漢字モデル 🚀 " * 10
        snap.workers[2].summary = ["- **bold** " + "漢字 " * 200]
        for cols, rows in ((160, 45), (57, 30), (40, 12)):
            for sel in (0, 2):
                for ln in _rd_build(snap, UIState(sel=sel), cols, rows, NOW):
                    self.assertLessEqual(_wd_width(ln), cols)


class Colour(unittest.TestCase):
    def test_summaries_are_muted_not_dim(self) -> None:
        out = render_frame(fake(2, 6), UIState(sel=4), 100, 40, NOW)
        expanded = next(ln for ln in out if "Summary line" in plain(ln))
        self.assertIn(MUTED, expanded)
        for ln in out:
            if "done-" in plain(ln):
                self.assertNotIn(DIM, ln, plain(ln))

    def test_finished_summary_on_a_row_is_muted(self) -> None:
        out = render_frame(fake(2, 6), UIState(sel=0), 100, 40, NOW)
        row = next(ln for ln in out if "done-0" in plain(ln))
        self.assertIn(MUTED, row)


class FailedAndNoReport(unittest.TestCase):
    def test_failed_and_noreport_rows_keep_their_state(self) -> None:
        snap = fake(0, 0)
        snap.workers = [_wk("bad", FAILED, NOW - 900, NOW - 500), _wk("quiet-end", NOREPORT, NOW - 900, NOW - 600)]
        snap.workers[0].log.steps[-1] = Step(NOW, "error", "", "database lock timeout")
        out = lines(snap, UIState(sel=1, expanded={"quiet-end"}), 100, 30)
        self.assertTrue(any("bad" in ln and "database lock timeout" in ln for ln in out))
        self.assertTrue(any("quiet-end" in ln and "no report" in ln for ln in out))
        self.assertTrue(any("finished · no report" in ln for ln in out))


class ExpandedCard(unittest.TestCase):
    def test_finished_rows_newest_first_even_when_the_snapshot_is_oldest_first(self) -> None:
        snap = fake(2, 6)
        snap.workers = snap.workers[:2] + snap.workers[2:][::-1]   # done-0 first, the old frame's order
        out = lines(snap, UIState(sel=0), 160, 45)
        pos = [next(i for i, ln in enumerate(out) if f"done-{k}" in ln) for k in (5, 4, 3, 2, 1, 0)]
        self.assertEqual(pos, sorted(pos))

    def test_finished_rows_sort_by_end_then_start(self) -> None:
        snap = fake(0, 0)
        snap.workers = [_wk("early", DONE, NOW - 900, NOW - 500), _wk("late-start", DONE, NOW - 300, NOW - 100),
                        _wk("late-end", DONE, NOW - 800, NOW - 50)]
        out = lines(snap, UIState(sel=0), 100, 30)
        pos = [next(i for i, ln in enumerate(out) if name in ln) for name in ("late-end", "late-start", "early")]
        self.assertEqual(pos, sorted(pos))

    def test_token_facts_share_the_output_line(self) -> None:
        out = lines(fake(2, 6), UIState(sel=0, expanded={"live-0"}), 100, 40)
        row = next(ln for ln in out if "last output" in ln and "live-0" not in ln and "live-1" not in ln)
        self.assertIn("in 84k", row)
        self.assertIn("out 9k", row)
        self.assertIn("ctx 96k", row)
        self.assertIn("cpu 42%", row)

    def test_no_line_holds_only_token_facts(self) -> None:
        snap = fake(1, 0)
        snap.workers[0].log.tool_counts = {}
        snap.workers[0].log.errors = 0
        out = lines(snap, UIState(sel=0, expanded={"live-0"}), 100, 30)
        self.assertFalse(any(re.search(r"│.\s+in \d", ln) for ln in out), out)
        self.assertTrue(any("in 84k" in ln and "last output" in ln for ln in out))


if __name__ == "__main__":
    unittest.main()
