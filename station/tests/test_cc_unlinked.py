"""The session-unknown group at display time: 20 newest workers, one `+N more` fold, exact counters kept."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from taskhelp import open_tasks

import app
from model import DONE, GlobalSnapshot, Group, Snapshot, Stage, UIState, Worker
from render import _rd_g_rowdata, global_rows, render_global

NOW = 1_800_000_000.0
PATH = "/home/u/Projects/proj/.stage"


def unlinked_stage(count: int = 300) -> Stage:
    """An unlinked group whose w000 starts newest and w299 oldest."""
    workers = [Worker(f"w{i:03d}", DONE, start=NOW - 30 - i, end=NOW - 20 - i) for i in range(count)]
    groups = [Group("workers", [wk.name for wk in workers], min(wk.start for wk in workers),
                    done=count, total=count, live=0)]
    stage = Stage(PATH, "proj", Snapshot(now=NOW, workers=workers, groups=groups), 0, NOW - 20)
    stage.state, stage.lead_state, stage.unlinked = "active", "gone", True
    return stage


def frame(gs: GlobalSnapshot, ui: UIState, cols: int = 120, rows: int = 60) -> list[str]:
    """The tree with SGR stripped, whatever the developer's NO_COLOR is set to."""
    with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
        return render_global(gs, ui, cols, rows, NOW)


class UnlinkedRenderCapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stage = unlinked_stage()
        self.gs = GlobalSnapshot(now=NOW, stages=[self.stage])
        self.ui = UIState()

    def worker_names_of(self, gs: GlobalSnapshot, ui: UIState | None = None) -> list[str]:
        return [name for _path, kind, name in global_rows(gs, ui or self.ui) if kind == "worker"]

    def worker_names(self) -> list[str]:
        return self.worker_names_of(self.gs)

    def test_the_group_lists_its_twenty_newest_workers_and_one_more_row(self) -> None:
        lines = frame(self.gs, self.ui)
        self.assertEqual(self.worker_names(), [f"w{i:03d}" for i in range(20)])
        self.assertTrue(any("+280 more (Enter shows all)" in line for line in lines))
        self.assertFalse(any("w020" in line for line in lines))
        self.ui.cc = True   # the app draws this same tree inside the control center's left table
        self.assertTrue(any("+280 more (Enter shows all)" in line for line in frame(self.gs, self.ui)))

    def test_a_small_group_has_no_more_row(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[unlinked_stage(3)])
        ui = open_tasks(gs, UIState())                          # finished work is a task line until opened
        self.assertEqual(len(self.worker_names_of(gs, ui)), 3)
        self.assertFalse(any("more (Enter shows all)" in line for line in frame(gs, ui)))

    def test_enter_on_the_more_row_lists_the_rest(self) -> None:
        rows = global_rows(self.gs, self.ui)
        self.ui.gsel = rows.index((PATH, "more", "unlinked"))
        self.assertEqual(app._ap_global_key(self.ui, "enter", self.gs, 40), "")
        self.assertEqual(len(self.worker_names()), 300)
        self.assertIn("w299", self.worker_names())
        self.assertTrue(any("− fewer" in line for line in frame(self.gs, self.ui)))

    def test_the_stage_counters_stay_exact(self) -> None:
        frame(self.gs, self.ui)
        row = _rd_g_rowdata(self.stage)
        self.assertEqual((row.finished, row.live, row.problems), (300, 0, 0))
        self.assertEqual(len(self.stage.snap.workers), 300)
        self.assertEqual(sum(group.total for group in self.stage.snap.groups), 300)
        self.assertEqual(self.stage.snap.groups[0].done, 300)


if __name__ == "__main__":
    unittest.main()
