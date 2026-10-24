from __future__ import annotations

import unittest

from model import GlobalSnapshot, Snapshot, Stage, UIState, Worker
from render import render_global
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0
SIZES = ((120, 24), (140, 40), (160, 50), (200, 60), (119, 40))   # 119 falls back to the classic tree


def _e2e_plain(line: str) -> str:
    return _wd_SGR_RE.sub("", line)


def _e2e_fleet() -> GlobalSnapshot:
    # three stages, 40 workers in total; one 400-character goal and CJK + emoji in a stage label and a worker name
    long_goal = ("refactor the parser so that every nested block keeps its position " * 8)[:400]
    stages: list[Stage] = []
    counts = (20, 13, 7)
    for si, n in enumerate(counts):
        workers: list[Worker] = []
        for wi in range(n):
            goal = long_goal if (si == 0 and wi == 0) else f"task {si}-{wi} inspect module"
            name = "工人🚀" if (si == 2 and wi == 0) else f"worker-{si}-{wi}"
            workers.append(Worker(name=name, state="running" if wi % 3 else "done", goal=goal, start=NOW - 60 * wi))
        label = "数据🚀仪表" if si == 1 else f"project-{si}"
        stages.append(Stage(path=f"/stage{si}", label=label, snap=Snapshot(workers=workers)))
    return GlobalSnapshot(now=NOW, stages=stages)


def _e2e_one() -> GlobalSnapshot:
    worker = Worker(name="worker-one", state="running", goal="inspect parser")
    return GlobalSnapshot(now=NOW, stages=[Stage(path="/alpha", label="alpha", snap=Snapshot(workers=[worker]))])


class ControlCenterFrameTests(unittest.TestCase):
    def test_frame_is_exact_size_at_every_size(self) -> None:
        for pane_open in (False, True):
            for cols, rows in SIZES:
                ui = UIState(cc=True, cc_pane_open=pane_open)
                lines = render_global(_e2e_one(), ui, cols, rows, NOW)
                self.assertEqual(len(lines), rows, (cols, rows, pane_open))
                self.assertTrue(all(_wd_width(line) == cols for line in lines), (cols, rows, pane_open))

    def test_big_fleet_is_exact_size_and_never_tracebacks(self) -> None:
        for pane_open in (False, True):
            for cols, rows in SIZES:
                ui = UIState(cc=True, cc_pane_open=pane_open)
                lines = render_global(_e2e_fleet(), ui, cols, rows, NOW)
                self.assertEqual(len(lines), rows, (cols, rows, pane_open))
                self.assertTrue(all(_wd_width(line) == cols for line in lines), (cols, rows, pane_open))
                self.assertFalse(any("Traceback" in _e2e_plain(line) for line in lines), (cols, rows, pane_open))
                # render_global turns an exception into this line, so a crash would otherwise pass the width check
                self.assertFalse(any("render error" in _e2e_plain(line) for line in lines), (cols, rows, pane_open))

    def test_hits_are_inside_the_frame_with_known_actions(self) -> None:
        for pane_open in (False, True):
            for cols, rows in SIZES:
                ui = UIState(cc=True, cc_pane_open=pane_open)
                render_global(_e2e_fleet(), ui, cols, rows, NOW)
                for row0, row1, col0, col1, action, target in ui.hits:
                    where = (cols, rows, pane_open, (row0, row1, col0, col1))
                    self.assertTrue(1 <= row0 <= row1 <= rows, where)
                    self.assertTrue(1 <= col0 <= col1 <= cols, where)
                    self.assertIsInstance(action, str, where)
                    self.assertIsInstance(target, str, where)

    def test_same_snapshot_renders_the_same_text_twice(self) -> None:
        gs = _e2e_fleet()
        ui = UIState(cc=True)
        first = render_global(gs, ui, 140, 40, NOW)
        second = render_global(gs, ui, 140, 40, NOW)
        self.assertEqual(first, second)

    def test_cc_active_needs_120_columns(self) -> None:
        ui = UIState(cc=True)
        render_global(_e2e_one(), ui, 120, 24, NOW)
        self.assertTrue(ui.cc_active)
        render_global(_e2e_one(), ui, 119, 40, NOW)
        self.assertFalse(ui.cc_active)


if __name__ == "__main__":
    unittest.main()
