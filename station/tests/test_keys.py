"""Key map: Enter, Space, Esc, arrows, t/r panel folds, the ? overlay, new-worker selection, focus-aware footer."""
from __future__ import annotations

import re
import unittest

import app
from model import (
    DONE,
    RUNNING,
    Finding,
    GlobalSnapshot,
    LogState,
    Review,
    Snapshot,
    Stage,
    Step,
    UIState,
    Worker,
)
from render import global_rows, render_frame, render_global

NOW = 1_760_050_000.0
SGR = re.compile(r"\x1b\[[0-9;]*m")


def plain(line: str) -> str:
    return SGR.sub("", line)


def worker(name: str, state: str, start: float, end: float | None = None, summary: list[str] | None = None) -> Worker:
    log = LogState(steps=[Step(NOW - 5, "tool", "bash", f"step of {name}")])
    return Worker(name=name, state=state, title=f"title {name}", model="sonnet medium", family="claude",
                  start=start, end=end, log=log, activity=[1] * 60, quiet_s=2.0 if state == RUNNING else 0.0,
                  summary=summary or [])


def station(live: int = 1, finished: int = 2, reviews: int = 0) -> Snapshot:
    """Live workers first (oldest first), then finished ones newest first, as the collector orders them."""
    lv = [worker(f"live-{i}", RUNNING, NOW - 900 + i * 100) for i in range(live)]
    fin = [worker(f"done-{i}", DONE, NOW - 600 + i * 50, NOW - 500 + i * 50, [f"summary {i}"]) for i in range(finished)]
    revs = [Review(f"review-{i}", "luna", "codex", NOW - 400, NOW - 100, False, [Finding(1, "race")])
            for i in range(reviews)]
    return Snapshot(title="demo", now=NOW, session_start=NOW - 1000, workers=lv + fin[::-1], reviews=revs)


def frame(snap: Snapshot, ui: UIState, cols: int = 100, rows: int = 34) -> list[str]:
    return [plain(x) for x in render_frame(snap, ui, cols, rows, NOW)]


def global_of(snap: Snapshot) -> GlobalSnapshot:
    return GlobalSnapshot(now=NOW, stages=[Stage(path="/s/alpha", label="alpha", snap=snap)])


def find(lines: list[str], text: str) -> int:
    return next(i for i, ln in enumerate(lines) if text in ln)


class CursorAndEnter(unittest.TestCase):
    def test_arrows_and_jk_walk_workers_then_the_panel_rows(self) -> None:
        snap = station(1, 2, reviews=1)   # workers 0..2, then timeline, then reviews
        ui = UIState()
        for key in ("down", "j"):
            app._ap_handle_key(ui, key, snap, 30)
        self.assertEqual((ui.sel, ui.panel), (2, ""))
        app._ap_handle_key(ui, "down", snap, 30)
        self.assertEqual(ui.panel, "timeline")
        app._ap_handle_key(ui, "k", snap, 30)
        self.assertEqual((ui.sel, ui.panel), (2, ""))
        app._ap_handle_key(ui, "pgdn", snap, 30)
        self.assertEqual(ui.panel, "reviews")
        app._ap_handle_key(ui, "down", snap, 30)
        self.assertEqual(ui.panel, "reviews")   # clamped at the last row

    def test_finished_row_enter_expands_then_collapses_its_summary(self) -> None:
        snap = station(1, 2)
        ui = UIState(sel=2)   # done-0, the oldest finished row, is selected and closed: its summary sits on the row
        collapsed = frame(snap, ui)
        self.assertIn("summary 0", collapsed[find(collapsed, "done-0")])
        self.assertEqual(app._ap_handle_key(ui, "enter", snap, 30), "")
        expanded = frame(snap, ui)
        self.assertNotIn("summary 0", expanded[find(expanded, "done-0")])
        self.assertIn("summary 0", expanded[find(expanded, "done-0") + 1])   # the detail under the row
        app._ap_handle_key(ui, "enter", snap, 30)
        self.assertIn("summary 0", frame(snap, ui)[find(frame(snap, ui), "done-0")])

    def test_moving_the_cursor_never_changes_the_open_set(self) -> None:
        snap = station(1, 2)
        ui = UIState(sel=2, expanded={"done-0"})            # done-0 open, done-1 closed
        app._ap_handle_key(ui, "up", snap, 30)              # onto done-1
        self.assertEqual(ui.expanded, {"done-0"})
        out = frame(snap, ui)
        self.assertIn("summary 0", out[find(out, "done-0") + 1])   # done-0 stays open under its row
        self.assertIn("summary 1", out[find(out, "done-1")])       # done-1 is one line, summary on the row

    def test_live_row_enter_folds_its_card_into_one_row(self) -> None:
        snap = station(1, 2)
        ui = UIState(sel=0, expanded={"live-0"})
        self.assertIn("step of live-0", "\n".join(frame(snap, ui)))
        app._ap_handle_key(ui, "enter", snap, 30)
        folded = frame(snap, ui)
        self.assertNotIn("step of live-0", "\n".join(folded))
        self.assertIn("live-0", folded[find(folded, "live-0")])
        app._ap_handle_key(ui, "enter", snap, 30)
        self.assertIn("step of live-0", "\n".join(frame(snap, ui)))

    def test_enter_on_a_panel_row_folds_it_and_space_does_nothing_there(self) -> None:
        snap = station(1, 2, reviews=1)
        ui = UIState(panel="timeline")
        self.assertEqual(app._ap_handle_key(ui, "space", snap, 30), "")
        self.assertEqual(ui.view, "overview")
        app._ap_handle_key(ui, "enter", snap, 30)
        self.assertIn("timeline", ui.folded)
        app._ap_handle_key(ui, "enter", snap, 30)
        self.assertNotIn("timeline", ui.folded)


class PanelFolds(unittest.TestCase):
    def test_t_and_r_fold_and_unfold_from_any_row(self) -> None:
        snap = station(1, 2, reviews=1)
        ui = UIState(sel=1)
        app._ap_handle_key(ui, "t", snap, 30)
        app._ap_handle_key(ui, "r", snap, 30)
        self.assertEqual(ui.folded, {"timeline", "reviews"})
        app._ap_handle_key(ui, "t", snap, 30)
        self.assertEqual(ui.folded, {"reviews"})
        self.assertEqual(ui.sel, 1)   # folding does not move the cursor

    def test_folded_panel_draws_one_title_line_with_a_count(self) -> None:
        snap = station(1, 2, reviews=2)
        ui = UIState(folded={"timeline", "reviews"})
        out = frame(snap, ui)
        self.assertRegex("\n".join(out), r"▸ timeline ── \d+")
        self.assertRegex("\n".join(out), r"▸ reviews ── 2")
        self.assertEqual(len(out), 34)
        self.assertNotIn("review-0", "\n".join(out))   # the reviews body is gone
        self.assertNotIn("▸ timeline", "\n".join(frame(snap, UIState())))

    def test_folded_timeline_gives_its_rows_back_to_the_workers(self) -> None:
        snap = station(1, 4)
        out = "\n".join(frame(snap, UIState(folded={"timeline"})))
        for i in range(4):
            self.assertIn(f"done-{i}", out)   # all finished rows fit once the timeline is one line


class SpaceAndEsc(unittest.TestCase):
    def test_space_opens_the_worker_page_and_esc_goes_back_one_level(self) -> None:
        snap = station(1, 2)
        ui = UIState(sel=1)
        self.assertEqual(app._ap_handle_key(ui, "space", snap, 30), "load_detail")
        self.assertEqual(ui.view, "detail")
        self.assertEqual(app._ap_handle_key(ui, "esc", snap, 30), "")
        self.assertEqual(ui.view, "overview")
        self.assertEqual(app._ap_handle_key(ui, "esc", snap, 30), "")   # a lone station: nothing to go back to
        self.assertEqual(ui.view, "overview")

    def test_global_esc_goes_worker_page_then_station_then_stations_list(self) -> None:
        gs = global_of(station(1, 2))
        ui = UIState(gsel=1)
        self.assertEqual(app._ap_global_key(ui, "space", gs, 30), "")   # open the station page
        self.assertEqual(ui.gpage, "sess")
        ui.gpage = "stage"                                              # the station page (reached from a worker page's b/r)
        ui.sel = 1
        self.assertEqual(app._ap_global_key(ui, "space", gs, 30), "load_detail")
        self.assertEqual(ui.view, "detail")
        app._ap_global_key(ui, "esc", gs, 30)
        self.assertEqual((ui.gpage, ui.view), ("stage", "overview"))
        app._ap_global_key(ui, "esc", gs, 30)
        self.assertEqual(ui.gpage, "")
        self.assertEqual(app._ap_global_key(ui, "esc", gs, 30), "")   # the top: Esc does nothing, never quits

    def test_esc_never_quits_at_any_level(self) -> None:
        snap = station(1, 2)
        for view in ("overview", "detail", "quota"):
            self.assertNotEqual(app._ap_handle_key(UIState(view=view), "esc", snap, 30), "quit")
        gs = global_of(snap)
        self.assertNotEqual(app._ap_global_key(UIState(), "esc", gs, 30), "quit")


class HelpOverlay(unittest.TestCase):
    def test_question_mark_opens_and_any_key_closes(self) -> None:
        snap = station(1, 2)
        ui = UIState()
        app._ap_handle_key(ui, "?", snap, 30)
        self.assertTrue(ui.help)
        self.assertEqual(app._ap_handle_key(ui, "x", snap, 30), "")
        self.assertFalse(ui.help)

    def test_any_key_closes_it_without_acting_on_that_key(self) -> None:
        snap = station(1, 2)
        ui = UIState(help=True, sel=0)
        app._ap_handle_key(ui, "down", snap, 30)
        self.assertFalse(ui.help)
        self.assertEqual(ui.sel, 0)   # the key only closed the box

    def test_q_closes_help_first_and_quits_next_ctrl_c_always_quits(self) -> None:
        snap = station(1, 2)
        ui = UIState(help=True)
        self.assertEqual(app._ap_handle_key(ui, "q", snap, 30), "")
        self.assertEqual(app._ap_handle_key(ui, "q", snap, 30), "quit")
        self.assertEqual(app._ap_handle_key(UIState(help=True), "ctrl-c", snap, 30), "quit")

    def test_global_help_toggles_too(self) -> None:
        gs = global_of(station(1, 2))
        ui = UIState()
        app._ap_global_key(ui, "?", gs, 30)
        self.assertTrue(ui.help)
        self.assertEqual(app._ap_global_key(ui, "a", gs, 30), "")
        self.assertFalse(ui.help)

    def test_overlay_lists_the_keys_over_the_station(self) -> None:
        out = frame(station(1, 2), UIState(help=True))
        text = "\n".join(out)
        self.assertIn("keys", text)
        for needle in ("Enter", "Space", "Esc", "t  i  m", "any key closes it", "quit"):
            self.assertIn(needle, text)
        self.assertEqual(len(out), 34)

    def test_overlay_marks_process_actions_as_unavailable_without_opt_in(self) -> None:
        out = [plain(x) for x in render_global(global_of(station(1, 2)), UIState(help=True), 100, 34, NOW)]
        text = "\n".join(out)
        self.assertIn("needs --allow-actions", text)
        for key in ("s  K", "r  n", '"x"'):
            self.assertNotIn(key, text)

    def test_overlay_advertises_actions_when_opted_in(self) -> None:
        out = [plain(x) for x in render_global(global_of(station(1, 2)),
                                                UIState(help=True, allow_actions=True), 100, 34, NOW)]
        text = "\n".join(out)
        self.assertIn("s  K", text)
        self.assertIn("r  n", text)

    def test_overlay_on_the_global_list(self) -> None:
        out = [plain(x) for x in render_global(global_of(station(1, 2)), UIState(help=True), 100, 34, NOW)]
        self.assertIn("any key closes it", "\n".join(out))
        self.assertEqual(len(out), 34)


class NewWorkerSelection(unittest.TestCase):
    def test_a_new_live_worker_moves_a_finished_cursor_to_the_newest_live(self) -> None:
        before = station(1, 2)
        after = station(2, 2)   # live-1 starts last
        ui = UIState(sel=before.workers.index(next(w for w in before.workers if w.name == "done-1")))
        name = app._ap_follow_snapshot(ui, before, "done-1", False)
        self.assertEqual(name, "done-1")
        started = app._ap_started(before, after)
        self.assertTrue(started)
        name = app._ap_follow_snapshot(ui, after, name, started)
        self.assertEqual(name, "live-1")
        self.assertEqual(after.workers[ui.sel].name, "live-1")

    def test_a_live_cursor_stays_when_a_worker_starts(self) -> None:
        before = station(1, 2)
        after = station(2, 2)
        ui = UIState(sel=0)
        name = app._ap_follow_snapshot(ui, after, "live-0", app._ap_started(before, after))
        self.assertEqual(name, "live-0")

    def test_no_start_means_the_finished_cursor_is_not_pulled_back(self) -> None:
        snap = station(1, 2)
        ui = UIState(sel=2)
        for _ in range(3):   # snapshots keep arriving with the same workers
            name = app._ap_follow_snapshot(ui, snap, "done-0", app._ap_started(snap, snap))
        self.assertEqual((name, ui.sel), ("done-0", 2))

    def test_panel_cursor_moves_to_the_newest_live_worker_on_a_start(self) -> None:
        before = station(1, 1)
        after = station(2, 1)
        ui = UIState(panel="timeline")
        name = app._ap_follow_snapshot(ui, after, "", app._ap_started(before, after))
        self.assertEqual((name, ui.panel), ("live-1", ""))

    def test_selection_follows_the_worker_by_name_when_the_list_shifts(self) -> None:
        before = station(1, 2)
        early = worker("live-early", RUNNING, NOW - 1200)   # sorts ahead of live-0 (oldest live first)
        after = Snapshot(title="demo", now=NOW, session_start=NOW - 1000, workers=[early] + before.workers)
        ui = UIState(sel=0)   # live-0 was index 0
        name = app._ap_follow_snapshot(ui, after, "live-0", app._ap_started(before, after))
        self.assertEqual((name, ui.sel), ("live-0", 1))

    def test_finished_only_arrivals_are_not_starts(self) -> None:
        before = station(0, 2)
        after = station(0, 3)
        self.assertFalse(app._ap_started(before, after))
        self.assertFalse(app._ap_started(None, after))


class FocusFooter(unittest.TestCase):
    def last(self, snap: Snapshot, ui: UIState, cols: int = 100) -> str:
        return frame(snap, ui, cols)[-1].rstrip()

    def test_live_worker_row_shows_the_keys_that_act_on_it(self) -> None:
        text = self.last(station(1, 2, reviews=1), UIState(sel=0, expanded={"live-0"}))
        for needle in ("[Enter] fold", "[Space] open", "[t] timeline", "[r] reviews", "[?] keys", "[q] quit"):
            self.assertIn(needle, text)
        self.assertNotIn("[Esc]", text)   # no station above this one in the single-stage view

    def test_finished_and_folded_rows_read_their_own_enter_label(self) -> None:
        self.assertIn("[Enter] collapse", self.last(station(1, 2), UIState(sel=2, expanded={"done-0"})))
        self.assertIn("[Enter] expand", self.last(station(1, 2), UIState(sel=2)))
        self.assertIn("[Enter] fold", self.last(station(1, 2), UIState(sel=0, expanded={"live-0"})))
        self.assertIn("[Enter] unfold", self.last(station(1, 2), UIState(sel=0)))

    def test_panel_row_has_no_open_key(self) -> None:
        text = self.last(station(1, 2, reviews=1), UIState(panel="reviews"))
        self.assertIn("[Enter] fold", text)
        self.assertNotIn("[Space]", text)

    def test_global_station_shows_esc_back(self) -> None:
        ui = UIState(gpage="stage", gstage="/s/alpha", sel=0)
        text = plain(render_frame(station(1, 2), ui, 100, 34, NOW)[-1]).rstrip()
        self.assertIn("[Esc] back", text)

    def test_detail_footer(self) -> None:
        text = self.last(station(1, 2), UIState(view="detail", sel=0, tab="live"))
        self.assertIn("[Esc] back", text)
        self.assertNotIn("[Enter]", text)

    def test_narrow_terminal_drops_from_the_right_and_keeps_help_and_quit(self) -> None:
        for cols in (40, 57):
            self.assertTrue(self.last(station(1, 2), UIState(sel=0), cols).endswith("[?] keys  [q] quit"), cols)

    def test_global_tree_footer_is_bracketed(self) -> None:
        text = plain(render_global(global_of(station(1, 2)), UIState(), 100, 20, NOW)[-1]).rstrip()
        self.assertIn("[Enter] fold", text)
        self.assertIn("[Space] open", text)
        self.assertTrue(text.endswith("[?] keys  [q] quit"))


class GlobalTreeKeys(unittest.TestCase):
    def test_enter_and_space_open_the_station_and_help_works_on_the_tree(self) -> None:
        gs = global_of(station(1, 2))
        self.assertEqual(global_rows(gs, UIState())[1][0], "/s/alpha")
        ui = UIState(gsel=1)
        app._ap_global_key(ui, "space", gs, 30)
        self.assertEqual(ui.gpage, "sess")
        ui2 = UIState()
        app._ap_global_key(ui2, "?", gs, 30)
        self.assertTrue(ui2.help)


if __name__ == "__main__":
    unittest.main()
