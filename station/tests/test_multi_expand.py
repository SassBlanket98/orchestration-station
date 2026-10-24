from __future__ import annotations

import unittest

import app
from model import (
    DONE,
    FAILED,
    RUNNING,
    GlobalSnapshot,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import render_frame
from widgets import _wd_SGR_RE

NOW = 1_800_000_000.0


def worker(name: str, state: str, summary: list[str] | None = None) -> Worker:
    start = NOW - 900
    end = NOW - 100 if state != RUNNING else None
    return Worker(name=name, state=state, title=f"title {name}", model="sonnet-work", family="claude",
                  start=start, end=end, summary=summary or [])


def station(*workers: Worker) -> Snapshot:
    return Snapshot(title="s", now=NOW, session_start=NOW - 3600, workers=list(workers))


def plain(lines: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line).rstrip() for line in lines]


class Toggling(unittest.TestCase):
    def setUp(self) -> None:
        self.snap = station(worker("a", RUNNING), worker("b", RUNNING), worker("c", DONE, ["sum c"]),
                            worker("d", DONE, ["sum d"]))
        self.ui = UIState()

    def press(self, key: str) -> None:
        app._ap_handle_key(self.ui, key, self.snap, 40)

    def test_any_number_of_workers_stay_open_at_once(self) -> None:
        self.press("enter")                 # a
        self.press("down")
        self.press("enter")                 # b
        self.press("down")
        self.press("enter")                 # c, a finished worker: pinned as your choice
        self.assertEqual(self.ui.expanded, {"a", "b", "c"})
        self.assertEqual(self.ui.pinned, {"c"})

    def test_moving_the_cursor_never_changes_the_set(self) -> None:
        self.press("enter")
        before = set(self.ui.expanded)
        for key in ("down", "down", "down", "up", "end", "home", "pgdn", "pgup"):
            self.press(key)
            self.assertEqual(self.ui.expanded, before, key)

    def test_enter_on_an_open_worker_closes_it_and_unpins_it(self) -> None:
        self.ui.sel = 2
        self.press("enter")                 # c opens
        self.assertEqual((self.ui.expanded, self.ui.pinned), ({"c"}, {"c"}))
        self.press("enter")                 # c closes
        self.assertEqual((self.ui.expanded, self.ui.pinned), (set(), set()))

    def test_a_live_worker_opened_by_hand_is_not_pinned(self) -> None:
        self.press("enter")
        self.assertEqual(self.ui.pinned, set())


class SyncRules(unittest.TestCase):
    def test_the_first_snapshot_opens_every_live_worker(self) -> None:
        ui = UIState()
        app._ap_sync_expanded(ui, None, station(worker("a", RUNNING), worker("b", DONE), worker("c", RUNNING)))
        self.assertEqual(ui.expanded, {"a", "c"})

    def test_a_new_live_worker_opens_and_a_finished_one_is_not_opened(self) -> None:
        ui = UIState()
        before = station(worker("a", RUNNING))
        app._ap_sync_expanded(ui, None, before)
        after = station(worker("a", RUNNING), worker("n", RUNNING), worker("f", DONE))
        app._ap_sync_expanded(ui, before, after)
        self.assertEqual(ui.expanded, {"a", "n"})

    def test_a_worker_that_finishes_closes_unless_pinned(self) -> None:
        ui = UIState(expanded={"a", "b"})
        before = station(worker("a", RUNNING), worker("b", RUNNING))
        after = station(worker("a", DONE), worker("b", RUNNING))
        app._ap_sync_expanded(ui, before, after)
        self.assertEqual(ui.expanded, {"b"})

    def test_a_pinned_worker_stays_open_when_it_finishes(self) -> None:
        ui = UIState(expanded={"a"}, pinned={"a"})
        app._ap_sync_expanded(ui, station(worker("a", RUNNING)), station(worker("a", FAILED)))
        self.assertEqual(ui.expanded, {"a"})

    def test_a_worker_that_starts_again_opens_and_loses_its_pin(self) -> None:
        ui = UIState(expanded={"a"}, pinned={"a"})
        app._ap_sync_expanded(ui, station(worker("a", DONE)), station(worker("a", RUNNING)))
        self.assertEqual((ui.expanded, ui.pinned), ({"a"}, set()))
        app._ap_sync_expanded(ui, station(worker("a", RUNNING)), station(worker("a", DONE)))
        self.assertEqual(ui.expanded, set())   # the new run is not pinned, so it closes when it ends

    def test_workers_that_left_the_snapshot_drop_out_of_both_sets(self) -> None:
        ui = UIState(expanded={"a", "gone"}, pinned={"gone"})
        app._ap_sync_expanded(ui, station(worker("a", RUNNING), worker("gone", DONE)), station(worker("a", RUNNING)))
        self.assertEqual((ui.expanded, ui.pinned), ({"a"}, set()))

    def test_opening_a_station_opens_its_live_workers_and_pins_nothing(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[Stage(path="/r/p/.stage", label="p", last_activity=NOW,
                                                   snap=station(worker("a", RUNNING), worker("b", DONE)))])
        ui = UIState(expanded={"stale"}, pinned={"stale"}, gsel=1)
        app._ap_global_key(ui, "space", gs, 40)
        self.assertEqual((ui.gpage, ui.gstage), ("sess", "/r/p/.stage"))
        self.assertEqual((ui.expanded, ui.pinned), ({"a"}, set()))


class Render(unittest.TestCase):
    def test_two_open_live_cards_and_two_open_summaries_show_together(self) -> None:
        snap = station(worker("live-0", RUNNING), worker("live-1", RUNNING), worker("done-0", DONE, ["sum zero"]),
                       worker("done-1", DONE, ["sum one"]), worker("done-2", DONE, ["sum two"]))
        ui = UIState(sel=0, expanded={"live-0", "live-1", "done-0", "done-1"})
        text = "\n".join(plain(render_frame(snap, ui, 100, 60, NOW)))
        self.assertIn("live-0", text)
        self.assertIn("live-1", text)
        self.assertEqual(text.count("sum zero"), 1)   # open: its detail sits under the row, not on it
        self.assertEqual(text.count("sum one"), 1)
        self.assertEqual(text.count("sum two"), 1)    # closed: the summary is on the row itself

    def test_unopened_rows_are_one_aligned_row_each(self) -> None:
        snap = station(worker("live-0", RUNNING), worker("live-1", RUNNING))
        lines = [ln for ln in plain(render_frame(snap, UIState(sel=0), 100, 40, NOW))
                 if "live-" in ln and "running" in ln]   # the worker rows, not the timeline bars
        self.assertEqual(len(lines), 2)
        self.assertEqual(len({len(ln) for ln in lines}), 1)   # the same grid: both rows end in the same column


if __name__ == "__main__":
    unittest.main()
