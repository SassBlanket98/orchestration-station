from __future__ import annotations

import os
import re
import unittest
from unittest.mock import patch

from model import DONE, RUNNING, GlobalSnapshot, Snapshot, Stage, UIState, Worker
from render import _rd_g_activity_level, _rd_G_ANIM, render_global
from widgets import _wd_SGR_RE


class ActivityAnimationTests(unittest.TestCase):
    def test_worker_and_stage_activity_levels(self) -> None:
        hot = Worker("hot", RUNNING, quiet_s=3, activity=[0, 3])
        quiet = Worker("quiet", RUNNING, quiet_s=90)
        snap = Snapshot(workers=[hot, quiet])
        self.assertEqual(_rd_g_activity_level(hot), 3)
        self.assertEqual(_rd_g_activity_level(quiet), 1)
        self.assertEqual(_rd_g_activity_level(Worker("warm", RUNNING, quiet_s=30)), 2)
        self.assertEqual(_rd_g_activity_level(Worker("done", DONE)), 0)
        self.assertEqual(_rd_g_activity_level(Stage("/s", "s", snap, live=2)), 3)
        self.assertEqual(_rd_g_activity_level(Stage("/s", "s", snap, live=1)), 2)
        self.assertEqual(_rd_g_activity_level(Stage("/s", "s", Snapshot(), live=0)), 0)

    def test_render_motion_is_limited_to_live_rows(self) -> None:
        now = 1760050000.0
        live = Worker("live", RUNNING, start=now - 10, quiet_s=1, activity=[0, 1, 3, 2, 4])
        done = Worker("done", DONE, start=now - 20, end=now - 3)
        gs = GlobalSnapshot(now=now, stages=[
            Stage("/live", "live-station", Snapshot(workers=[live]), live=1, last_activity=now - 1),
            Stage("/done", "done-station", Snapshot(workers=[done]), live=0, last_activity=now - 3)])
        clean = lambda frame: [_wd_SGR_RE.sub("", line) for line in frame]
        raw_first = render_global(gs, UIState(gopen={("", "more", "finished:/done")}), 100, 30, now)
        raw_later = render_global(gs, UIState(gopen={("", "more", "finished:/done")}), 100, 30, now + .3)
        self.assertNotEqual(clean(raw_first), clean(raw_later))
        def row(frame: list[str], label: str) -> str:   # the raw row: motion on a live row is colour (the breathing mark)
            return next(line for line in frame if re.search(rf"[◆◇◐] {label}", _wd_SGR_RE.sub("", line)))
        self.assertNotEqual(row(raw_first, "live-station"), row(raw_later, "live-station"))
        self.assertEqual(row(raw_first, "done-station"), row(raw_later, "done-station"))

    def test_no_color_is_readable_without_terminal_styles(self) -> None:
        now = 1760050000.0
        gs = GlobalSnapshot(now=now, stages=[Stage("/s", "stage", Snapshot(
            workers=[Worker("live", RUNNING, quiet_s=5)]), live=1, last_activity=now - 5)])
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            frame = render_global(gs, UIState(gopen={("", "more", "finished:/done")}), 57, 20, now)
        self.assertTrue(any("1 working" in line for line in frame))
        self.assertTrue(any("1 live" in line or "1▸" in line for line in frame))
        self.assertTrue(any("1 all 1" in line for line in frame))
        self.assertFalse(any("\x1b[" in line for line in frame))

    def test_live_worker_is_recorded_for_the_fade_in(self) -> None:
        now = 1760050000.0
        _rd_G_ANIM.clear()
        worker = Worker("worker", RUNNING, start=now - 2)
        gs = GlobalSnapshot(now=now, stages=[Stage("/flash", "flash", Snapshot(workers=[worker]), live=1,
                                                   last_activity=now)])
        with patch.dict(os.environ, {}, clear=True):
            render_global(gs, UIState(gopen={("", "more", "finished:/done")}), 100, 30, now)
        self.assertEqual(_rd_G_ANIM[("/flash", "worker")][0], RUNNING)

if __name__ == "__main__":
    unittest.main()
