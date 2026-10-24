from __future__ import annotations

import unittest

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
from render import _rd_g_load, _rd_g_stage_progress, render_global
from widgets import _wd_SGR_RE, _wd_width


class GaugeTests(unittest.TestCase):
    def test_load_is_token_burn_rate(self) -> None:
        from model import LogState
        key = ("/t", "burn")
        wk = Worker("w", RUNNING, log=LogState(tokens_in=1000, tokens_out=0))
        self.assertEqual(_rd_g_load(wk, key, 100.0), 0)
        wk.log.tokens_in = 1000 + 40_000          # 40k tokens in one minute: full meter
        self.assertAlmostEqual(_rd_g_load(wk, key, 160.0), 100)
        wk.log.tokens_in += 400                    # 400 tok/min: about 57%, then smoothed
        self.assertTrue(0 < _rd_g_load(wk, key, 220.0) < 100)
        self.assertEqual(_rd_g_load(Worker("w", RUNNING), ("/t", "none"), 1.0), 0)

    def test_stage_counts_exact_done_failed_and_noreport(self) -> None:
        snap = Snapshot(workers=[Worker("a", DONE), Worker("b", FAILED), Worker("c", "noreport"),
                                Worker("d", RUNNING)])
        self.assertEqual(_rd_g_stage_progress(snap), (3, 4, .75))

    def test_narrow_and_wide_frames_fit(self) -> None:
        now = 1000
        stage = Stage("/s", "stage", Snapshot(workers=[
            Worker("live-worker", RUNNING, start=900, cpu=90, activity=[1, 3]),
            Worker("done", DONE, start=800, end=850),
        ]), 1, now)
        gs = GlobalSnapshot(now=now, stages=[stage])
        for cols, rows in ((57, 20), (100, 30)):
            frame = render_global(gs, UIState(), cols, rows, now)
            self.assertEqual(len(frame), rows)
            self.assertTrue(all(_wd_width(_wd_SGR_RE.sub("", line)) <= cols for line in frame))


if __name__ == "__main__":
    unittest.main()
