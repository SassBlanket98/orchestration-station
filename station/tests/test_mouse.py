"""Mouse: SGR parsing, the hit map each frame publishes, clicks, double-clicks, footer buttons, breadcrumb and wheel."""
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
    Milestone,
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
COLS, ROWS = 100, 34


def plain(line: str) -> str:
    return SGR.sub("", line)


def worker(name: str, state: str, start: float, end: float | None = None, summary: list[str] | None = None) -> Worker:
    log = LogState(steps=[Step(NOW - 5, "tool", "bash", f"step of {name}")])
    return Worker(name=name, state=state, title=f"title {name}", model="sonnet medium", family="claude",
                  start=start, end=end, log=log, activity=[1] * 60, quiet_s=2.0 if state == RUNNING else 0.0,
                  summary=summary or [])


def station(live: int = 2, finished: int = 2, reviews: int = 0, long: bool = False) -> Snapshot:
    """Live workers first (oldest first), then the finished ones newest first, as the collector orders them."""
    names = [f"live-{i}" for i in range(live)]
    lv = [worker(n, RUNNING, NOW - 900 + i * 100) for i, n in enumerate(names)]
    fin = [worker(f"done-{i}", DONE, NOW - 600 + i * 50, NOW - 500 + i * 50, [f"summary {i}"])
           for i in range(finished if not long else 30)]
    revs = [Review(f"review-{i}", "luna", "codex", NOW - 400, NOW - 100,
                   False, [Finding(1 + k % 3, f"finding {k} of review {i}") for k in range(12 if long else 1)])
            for i in range(reviews)]
    return Snapshot(title="demo", now=NOW, session_start=NOW - 1000, workers=lv + fin[::-1], reviews=revs)


def stations(count: int) -> GlobalSnapshot:
    stages = []
    for i in range(count):
        snap = Snapshot(title=f"proj-{i}", now=NOW, session_start=NOW - 1000,
                        workers=[worker(f"w{i}", RUNNING if i % 3 == 0 else DONE, NOW - 300, None if i % 3 == 0 else NOW - 100)])
        stages.append(Stage(path=f"/s/proj-{i}", label=f"proj-{i}", snap=snap, last_activity=NOW - 60 * (i + 1)))
    return GlobalSnapshot(now=NOW, stages=stages)


def sgr(button: int, col: int, row: int, release: bool = False) -> bytes:
    return f"\x1b[<{button};{col};{row}{'m' if release else 'M'}".encode()


class Driver:
    """Runs raw input the way the main loop does: draw the frame, parse the bytes, then translate and run each key."""

    def __init__(self, ui: UIState, snap: Snapshot | None = None, gs: GlobalSnapshot | None = None) -> None:
        self.ui, self.snap, self.gs = ui, snap, gs

    def draw(self, now: float) -> list[str]:
        if self.gs is not None and self.ui.gpage not in ("stage", "worker"):
            return render_global(self.gs, self.ui, COLS, ROWS, now)
        station_snap = self.station_snap()
        return render_frame(station_snap, self.ui, COLS, ROWS, now)

    def station_snap(self) -> Snapshot:
        if self.gs is None:
            assert self.snap is not None
            return self.snap
        stage = next(s for s in self.gs.stages if s.path == self.ui.gstage)
        return stage.snap

    def send(self, raw: bytes, now: float = NOW) -> None:
        """What the main loop does with one read: draw, then parse, then run each key (clicks expand in place)."""
        self.draw(now)
        pending = list(app._ap_parse_keys(raw)[0])
        while pending:
            key = pending.pop(0)
            if key.startswith(("click:", "wheel:")):
                pending[:0] = app._ap_pointer_keys(self.ui, key, self.gs,
                                                   None if self.gs is not None else self.snap, now)
                continue
            if self.gs is not None:
                app._ap_global_key(self.ui, key, self.gs, ROWS)
            else:
                app._ap_handle_key(self.ui, key, self.snap, ROWS)

    def cell(self, action: str, target: str) -> tuple[int, int]:
        """1-based (col, row) of the first region with this action and target in the frame on screen."""
        self.draw(NOW)
        for r0, _, c0, _, act, tgt in self.ui.hits:
            if act == action and tgt == target:
                return c0, r0
        raise AssertionError(f"no {action} region for {target!r}: {self.ui.hits}")

    def click(self, action: str, target: str, now: float = NOW, col_offset: int = 0) -> None:
        col, row = self.cell(action, target)
        self.send(sgr(0, col + col_offset, row), now)

    def press_row(self, action: str, target: str) -> tuple[int, int]:
        """The screen cell of a region as it is now, for a press that is sent later at the same spot."""
        return self.cell(action, target)

    def wheel(self, up: bool, action: str, target: str, now: float = NOW) -> None:
        col, row = self.cell(action, target)
        self.send(sgr(64 if up else 65, col + 2, row + 1), now)


class ParseMouse(unittest.TestCase):
    def test_left_press_is_a_click_and_wheel_has_its_own_key(self) -> None:
        self.assertEqual(app._ap_parse_keys(b"\x1b[<0;12;5M"), (["click:12:5"], b""))
        self.assertEqual(app._ap_parse_keys(b"\x1b[<64;12;5M"), (["wheel:up:12:5"], b""))
        self.assertEqual(app._ap_parse_keys(b"\x1b[<65;12;5M"), (["wheel:down:12:5"], b""))

    def test_releases_motion_and_other_buttons_give_no_key(self) -> None:
        for raw in (b"\x1b[<0;12;5m", b"\x1b[<32;12;5M", b"\x1b[<35;12;5M", b"\x1b[<1;12;5M", b"\x1b[<2;12;5M",
                    b"\x1b[<66;12;5M", b"\x1b[<96;12;5M"):
            self.assertEqual(app._ap_parse_keys(raw), ([], b""), raw)

    def test_modifier_bits_do_not_change_the_button(self) -> None:
        self.assertEqual(app._ap_parse_keys(b"\x1b[<4;3;3M"), (["click:3:3"], b""))   # shift-click
        self.assertEqual(app._ap_parse_keys(b"\x1b[<80;3;3M"), (["wheel:up:3:3"], b""))  # ctrl-wheel

    def test_keys_around_a_mouse_report_still_parse(self) -> None:
        self.assertEqual(app._ap_parse_keys(b"j\x1b[<0;4;9Mq"), (["j", "click:4:9", "q"], b""))


class StationClicks(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = UIState()
        self.snap = station(live=2, finished=2)
        self.d = Driver(self.ui, self.snap)

    def test_click_selects_a_worker_row_and_any_column_of_it_counts(self) -> None:
        self.assertEqual(self.ui.sel, 0)
        self.d.click("select", "done-1", now=NOW)
        self.assertEqual(self.snap.workers[self.ui.sel].name, "done-1")
        self.d.click("select", "live-1", now=NOW + 5, col_offset=30)
        self.assertEqual(self.ui.sel, 1)

    def test_click_on_the_selected_row_is_enter(self) -> None:
        self.d.click("select", "live-0", now=NOW)           # already selected: Enter opens its card
        self.assertEqual(self.ui.expanded, {"live-0"})
        self.d.click("select", "live-0", now=NOW + 5)
        self.assertEqual(self.ui.expanded, set())

    def test_two_clicks_on_one_row_within_0_4_s_open_the_detail(self) -> None:
        col, row = self.d.press_row("select", "done-0")     # the same spot, twice
        self.d.send(sgr(0, col, row), NOW)                   # selects it
        self.d.send(sgr(0, col, row), NOW + 0.3)            # double-click: Space
        self.assertEqual((self.ui.view, self.ui.tab), ("detail", "live"))
        self.assertEqual(self.snap.workers[self.ui.sel].name, "done-0")

    def test_double_click_opens_the_worker_under_the_first_press_after_the_rows_moved(self) -> None:
        col, row = self.d.press_row("select", "done-0")
        self.d.send(sgr(0, col, row), NOW)                   # selecting done-0 folds the live card: rows move up
        self.d.send(sgr(0, col, row), NOW + 0.2)             # this spot now holds another row; the double still opens done-0
        self.assertEqual(self.ui.view, "detail")
        self.assertEqual(self.snap.workers[self.ui.sel].name, "done-0")

    def test_slower_second_click_is_enter_not_a_double_click(self) -> None:
        self.d.click("select", "done-0", now=NOW)           # selects it; the rows move
        self.d.click("select", "done-0", now=NOW + 0.5)     # 0.5 s apart: Enter, not Space
        self.assertEqual(self.ui.view, "overview")
        self.assertEqual(self.ui.expanded, {"done-0"})

    def test_double_click_needs_the_same_row(self) -> None:
        self.d.click("select", "done-0", now=NOW)
        self.d.click("select", "done-1", now=NOW + 0.1)     # another row, 0.1 s later: a plain select
        self.assertEqual(self.ui.view, "overview")
        self.assertEqual(self.snap.workers[self.ui.sel].name, "done-1")

    def test_a_triple_click_does_not_open_twice(self) -> None:
        col, row = self.d.press_row("select", "done-0")
        self.d.send(sgr(0, col, row), NOW)
        self.d.send(sgr(0, col, row), NOW + 0.1)            # opens
        self.ui.view = "overview"                           # back, as Esc would
        self.d.send(sgr(0, col, row), NOW + 0.2)            # no longer a double: Enter
        self.assertEqual(self.ui.view, "overview")

    def test_clicking_a_panel_title_folds_and_unfolds_it(self) -> None:
        self.snap.reviews = [Review("review-0", "luna", "codex", NOW - 400, NOW - 100, False,
                                    [Finding(1, "race")])]
        self.d.click("toggle_panel", "reviews", now=NOW)
        self.assertIn("reviews", self.ui.folded)
        self.d.click("toggle_panel", "reviews", now=NOW + 5)
        self.assertNotIn("reviews", self.ui.folded)
        self.d.click("toggle_panel", "timeline", now=NOW + 10)
        self.assertIn("timeline", self.ui.folded)
        self.d.click("toggle_panel", "timeline", now=NOW + 15)
        self.assertNotIn("timeline", self.ui.folded)

    def test_footer_buttons_run_their_key(self) -> None:
        self.d.click("key", "Enter", now=NOW)               # the cursor starts on a live row: [Enter] opens its card
        self.assertEqual(self.ui.expanded, {"live-0"})
        self.d.click("key", "?", now=NOW + 5)
        self.assertTrue(self.ui.help)

    def test_help_overlay_is_closed_by_any_click_and_the_click_does_nothing_else(self) -> None:
        self.ui.help = True
        self.d.send(sgr(0, 5, 20))
        self.assertFalse(self.ui.help)
        self.assertEqual(self.ui.sel, 0)

    def test_clicks_outside_any_region_do_nothing(self) -> None:
        before = (self.ui.sel, set(self.ui.expanded), self.ui.panel, set(self.ui.folded), self.ui.view)
        self.d.draw(NOW)
        self.d.send(sgr(0, 99, 1), NOW)            # the header row, far right: no region there
        self.assertEqual(before, (self.ui.sel, set(self.ui.expanded), self.ui.panel, set(self.ui.folded), self.ui.view))

    def test_wheel_over_the_worker_list_scrolls_it_three_rows(self) -> None:
        self.snap = station(live=2, finished=30)
        self.d.snap = self.snap
        self.d.wheel(False, "scroll", "workers")
        self.assertEqual(self.ui.pscroll["workers"], 3)
        self.d.draw(NOW)
        self.assertEqual(self.ui.ptop["workers"], 3)
        self.d.wheel(True, "scroll", "workers")
        self.assertEqual(self.ui.pscroll["workers"], 0)

    def test_wheel_over_reviews_scrolls_their_lines(self) -> None:
        self.snap = station(live=1, finished=1, reviews=2, long=True)
        self.d.snap = self.snap
        self.d.wheel(False, "scroll", "reviews")
        self.d.draw(NOW)
        self.assertEqual(self.ui.ptop["reviews"], 3)

    def test_wheel_stops_at_the_top(self) -> None:
        self.d.wheel(True, "scroll", "workers")
        self.assertEqual(self.ui.pscroll.get("workers", 0), 0)

    def test_wheel_on_the_detail_log_moves_it_and_pauses_follow(self) -> None:
        self.ui.view, self.ui.tab, self.ui.sel = "detail", "live", 0
        self.d.draw(NOW)
        self.d.wheel(True, "scroll", "detail")
        self.assertEqual((self.ui.scroll, self.ui.follow), (3, False))
        self.d.wheel(False, "scroll", "detail")
        self.assertEqual((self.ui.scroll, self.ui.follow), (0, True))

    def test_a_cursor_key_hands_the_list_back_to_the_cursor(self) -> None:
        self.snap = station(live=2, finished=30)
        self.d.snap = self.snap
        self.d.wheel(False, "scroll", "workers")
        self.d.send(b"j")
        self.assertNotIn("workers", self.ui.pscroll)

    def test_breadcrumb_is_only_in_the_global_station_view(self) -> None:
        self.assertNotIn("all orchestrators", "\n".join(plain(x) for x in self.d.draw(NOW)))
        self.assertFalse([h for h in self.ui.hits if h[4] == "back"])


class OverviewClicks(unittest.TestCase):
    def setUp(self) -> None:
        self.gs = stations(12)
        for stage in self.gs.stages:
            stage.state = "idle"
        roots = {f"p:{stage.path}" for stage in self.gs.stages}
        self.ui = UIState(gsel=2, gopen_keys=roots)      # idle projects start shut: open them so their rows show
        self.d = Driver(self.ui, gs=self.gs)
        self.first = self.gs.stages[0].path

    def idx(self, path: str, kind: str = "stage", name: str = "") -> str:
        return str(global_rows(self.gs, self.ui).index((path, kind, name)))

    def open_page(self) -> None:
        self.d.send(b" ", NOW)                              # Space on the cursor row (proj-0) opens its page

    def test_click_moves_the_cursor_and_toggles_the_row(self) -> None:
        was_open = self.ui.gsel == 0 and ("s:/s/proj-4" in self.ui.gopen_keys)
        self.assertFalse(was_open)
        self.d.click("nav", self.idx("/s/proj-4"), now=NOW)
        self.assertEqual(global_rows(self.gs, self.ui)[self.ui.gsel], ("/s/proj-4", "stage", ""))
        self.assertIn("s:/s/proj-4", self.ui.gopen_keys)              # a finished station opens its workers
        self.assertEqual(self.ui.gpage, "")
        self.d.click("nav", self.idx("/s/proj-4"), now=NOW + 5)
        self.assertNotIn("s:/s/proj-4", self.ui.gopen_keys)           # the next click shuts it again
        self.assertEqual(self.ui.gpage, "")

    def test_any_line_of_a_station_block_hits_it(self) -> None:
        col, row = self.d.cell("nav", self.idx("/s/proj-0"))
        self.d.send(sgr(0, 9, row + 1), NOW)              # the group-progress line under the station row
        self.assertEqual(global_rows(self.gs, self.ui)[self.ui.gsel][0], "/s/proj-0")

    def test_double_click_opens_the_page_and_leaves_the_row_as_it_was(self) -> None:
        before = "s:/s/proj-1" in self.ui.gopen_keys
        self.d.click("nav", self.idx("/s/proj-1"), now=NOW)
        self.d.click("nav", self.idx("/s/proj-1"), now=NOW + 0.2)
        self.assertEqual((self.ui.gpage, self.ui.gstage), ("sess", "/s/proj-1"))
        self.assertEqual("s:/s/proj-1" in self.ui.gopen_keys, before)

    def test_wheel_scrolls_the_list_three_rows(self) -> None:
        self.gs = stations(40)                              # more rows than the screen holds
        self.d = Driver(self.ui, gs=self.gs)
        self.d.wheel(False, "scroll", "cards")
        self.assertEqual(self.ui.pscroll["cards"], 3)
        self.d.draw(NOW)
        self.assertEqual(self.ui.ptop["cards"], 3)
        self.d.wheel(True, "scroll", "cards")
        self.assertEqual(self.ui.pscroll["cards"], 0)

    def test_filter_chips_are_click_targets(self) -> None:
        self.d.click("key", "2", now=NOW)
        self.assertEqual(self.ui.gfilter, "working")
        self.d.click("key", "1", now=NOW + 5)
        self.assertEqual(self.ui.gfilter, "all")

    def test_station_page_has_a_breadcrumb_back_to_the_list(self) -> None:
        self.open_page()
        self.assertIn("◆ proj-0 ›", plain(self.d.draw(NOW)[0]))
        self.d.click("back", "", now=NOW + 10)
        self.assertEqual((self.ui.gpage, self.ui.view), ("", "overview"))

    def test_quota_view_from_a_station_has_no_breadcrumb(self) -> None:
        self.open_page()
        self.ui.gpage = "stage"                             # the station page (a worker page's b/r reach it)
        self.ui.view = "quota"                              # u leaves the station page open underneath
        self.assertNotIn("Station ›", plain(self.d.draw(NOW)[0]))
        self.assertFalse([h for h in self.ui.hits if h[4] == "back"])

    def test_breadcrumb_goes_back_from_a_worker_page_too(self) -> None:
        self.ui.gpage, self.ui.gstage, self.ui.view = "worker", "/s/proj-0", "detail"
        self.ui.tab = "live"
        self.d.click("back", "", now=NOW)
        self.assertEqual((self.ui.gpage, self.ui.view), ("", "overview"))

    def test_footer_esc_button_goes_back_one_level(self) -> None:
        self.open_page()
        self.assertEqual(self.ui.gpage, "sess")
        self.d.click("key", "Esc", now=NOW + 10)
        self.assertEqual(self.ui.gpage, "")

    def test_help_closes_on_click_in_the_global_list(self) -> None:
        self.ui.help = True
        self.d.send(sgr(0, 3, 3), NOW)
        self.assertFalse(self.ui.help)
        self.assertEqual(self.ui.gpage, "")

    def test_hit_map_rows_are_one_based_and_inside_the_frame(self) -> None:
        self.d.draw(NOW)
        self.assertTrue(self.ui.hits)
        for r0, r1, c0, c1, _, _ in self.ui.hits:
            self.assertTrue(1 <= r0 <= r1 <= ROWS, (r0, r1))
            self.assertTrue(1 <= c0 <= c1 <= COLS, (c0, c1))


class LeadStrip(unittest.TestCase):
    def test_clicking_the_lead_strip_opens_and_closes_the_milestone_list(self) -> None:
        snap = station(live=1, finished=1)
        snap.milestones = [Milestone(NOW - 60, "first"), Milestone(NOW - 30, "second")]
        ui = UIState()
        d = Driver(ui, snap)
        d.click("lead", "", now=NOW)
        self.assertTrue(ui.lead_open)
        d.click("lead", "", now=NOW + 5)
        self.assertFalse(ui.lead_open)


class FooterAndHitMapFrames(unittest.TestCase):
    def test_footer_buttons_sit_on_the_last_row(self) -> None:
        ui = UIState()
        render_frame(station(), ui, COLS, ROWS, NOW)
        footer = [h for h in ui.hits if h[4] == "key"]
        self.assertTrue(footer)
        self.assertTrue(all(h[0] == h[1] == ROWS for h in footer))

    def test_no_hits_under_the_help_overlay(self) -> None:
        ui = UIState(help=True)
        render_frame(station(), ui, COLS, ROWS, NOW)
        self.assertEqual(ui.hits, [])

    def test_render_error_leaves_no_stale_hits(self) -> None:
        ui = UIState()
        render_frame(station(), ui, COLS, ROWS, NOW)
        self.assertTrue(ui.hits)
        render_frame(station(), ui, 0, 0, NOW)     # clamps to 1x1: nothing to click
        self.assertEqual(ui.hits, [])


class TabClicks(unittest.TestCase):
    def test_clicking_a_tab_label_opens_that_tab(self) -> None:
        ui = UIState()
        ui.view, ui.tab, ui.sel = "detail", "live", 0
        d = Driver(ui, station())
        d.click("tab", "report")
        self.assertEqual(ui.tab, "report")
        d.click("tab", "brief")
        self.assertEqual(ui.tab, "brief")
        d.click("tab", "live")
        self.assertEqual(ui.tab, "live")

    def test_tabs_are_clickable_on_a_worker_page_in_the_global_view(self) -> None:
        ui = UIState()
        d = Driver(ui, gs=stations(2))
        ui.gpage, ui.gstage, ui.view, ui.tab = "worker", "/s/proj-0", "detail", "live"
        d.click("tab", "brief")
        self.assertEqual(ui.tab, "brief")


if __name__ == "__main__":
    unittest.main()
