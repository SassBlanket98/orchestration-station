"""Ops swarm: pulse heat, the hive strip and the swarm band width, parts and static NO_COLOR text."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import widgets
from model import LogState, Worker
from opsgrid import (
    OG_SWARM_MIN,
    _og_hive,
    _og_plan,
    _og_pulse,
    _og_swarm_band,
)
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0
SPIN = widgets._wd_SPINNER
SPARK = "▁▂▃▄▅▆▇█"


def plain(text: str) -> str:
    return _wd_SGR_RE.sub("", text)


def worker(name: str, state: str = "running", family: str = "deepseek", pulses: list[tuple[float, int]] | None = None,
           last_ts: float = 0.0) -> Worker:
    return Worker(name=name, state=state, family=family,
                  log=LogState(tool_pulses=list(pulses or []), last_ts=last_ts))


def band_workers(n: int, state: str = "running", pulse_at: float | None = None) -> list[Worker]:
    out = []
    for i in range(n):
        pulses = [(pulse_at, 1)] if pulse_at is not None else []
        out.append(worker(f"w{i}", state=state, pulses=pulses))
    return out


def spark_count(text: str) -> int:
    return sum(1 for ch in plain(text) if ch in SPARK)


class OpsSwarmTests(unittest.TestCase):
    def test_pulse_fades_linearly_over_0_9s(self):
        wk = worker("w", pulses=[(NOW, 1)])
        self.assertEqual(_og_pulse(wk, NOW), 1.0)
        self.assertAlmostEqual(_og_pulse(wk, NOW + 0.45), 0.5, places=6)
        self.assertEqual(_og_pulse(wk, NOW + 0.9), 0.0)
        self.assertEqual(_og_pulse(wk, NOW + 1.5), 0.0)

    def test_pulse_unknown_finished_and_future(self):
        self.assertEqual(_og_pulse(worker("w", last_ts=NOW), NOW), 0.0)      # no tool call data
        done = worker("w", state="done", pulses=[(NOW, 1)])
        self.assertEqual(_og_pulse(done, NOW), 0.0)
        failed = worker("w", state="failed", pulses=[(NOW, 1)])
        self.assertEqual(_og_pulse(failed, NOW), 0.0)
        future = worker("w", pulses=[(NOW + 5, 1)])
        self.assertEqual(_og_pulse(future, NOW), 1.0)

    def test_hive_one_glyph_per_worker_in_order(self):
        workers = [worker("a", pulses=[(NOW - 10, 1)]), worker("b", state="done"),
                   worker("c", state="failed"), worker("d", state="noreport"), worker("e")]
        hive = _og_hive(workers, 0.0, NOW)
        self.assertEqual(_wd_width(hive), len(workers))
        glyphs = plain(hive)
        self.assertEqual(len(glyphs), len(workers))
        self.assertIn(glyphs[0], SPIN)
        self.assertEqual(glyphs[1], "✔")
        self.assertEqual(glyphs[2], "✖")
        self.assertEqual(glyphs[3], "○")
        self.assertIn(glyphs[4], SPIN)
        # the running pair is out of phase, so the strip ripples
        self.assertNotEqual(glyphs[0], glyphs[4])

    def test_hive_colours_family_and_hot_cyan_bold(self):
        cold = _og_hive([worker("a", family="deepseek")], 0.0, NOW)
        rgb = widgets._wd_family_color("deepseek")
        self.assertIn(f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}", cold)
        hot = _og_hive([worker("a", pulses=[(NOW - 0.2, 1)])], 0.0, NOW)
        cyan = widgets._wd_C_CYAN
        self.assertIn(f"1;38;2;{cyan[0]};{cyan[1]};{cyan[2]}", hot)

    def test_band_exact_width_for_3_8_40_workers(self):
        for n in (3, 8, 40):
            for cols in (60, 61, 72, 80, 89, 90, 99, 100, 111, 112, 119, 120, 121, 140, 160, 200, 220):
                with self.subTest(workers=n, cols=cols):
                    line = _og_swarm_band(_og_plan(cols), "│  │  ", band_workers(n), NOW, 1.0)
                    self.assertEqual(_wd_width(line), cols, plain(line))

    def test_band_parts_in_order(self):
        workers = band_workers(8, pulse_at=NOW - 5)
        workers[0] = worker("a", state="done")
        workers[1] = worker("b", state="failed")
        text = plain(_og_swarm_band(_og_plan(140), "│  │  ", workers, NOW, 1.0))
        self.assertIn("│  │  ╭─ SWARM 8 ", text)
        self.assertIn(" 2/8", text)
        self.assertIn(" tools/min ", text)
        self.assertIn("1 failed", text)
        self.assertLess(text.index("SWARM"), text.index("2/8"))
        self.assertLess(text.index("2/8"), text.index("tools/min"))
        self.assertLess(text.index("tools/min"), text.index("1 failed"))

    def test_band_failed_segment_only_when_positive(self):
        clean = plain(_og_swarm_band(_og_plan(120), "│  ", band_workers(4), NOW, 1.0))
        self.assertNotIn("failed", clean)
        broken = band_workers(4)
        broken[2] = worker("x", state="failed")
        self.assertIn("1 failed", plain(_og_swarm_band(_og_plan(120), "│  ", broken, NOW, 1.0)))

    def test_band_spark_cells_by_width(self):
        # one pulse in every 60 s cell of the last 30 min, so no cell is a zero dot
        pulses = [(NOW - 30 - 60 * i, 1) for i in range(30)]
        workers = [worker("a", pulses=pulses)]
        self.assertEqual(spark_count(_og_swarm_band(_og_plan(120), "│  ", workers, NOW, 1.0)), 30)
        self.assertEqual(spark_count(_og_swarm_band(_og_plan(160), "│  ", workers, NOW, 1.0)), 30)
        self.assertEqual(spark_count(_og_swarm_band(_og_plan(100), "│  ", workers, NOW, 1.0)), 14)
        self.assertEqual(spark_count(_og_swarm_band(_og_plan(89), "│  ", workers, NOW, 1.0)), 0)

    def test_band_rate_from_last_minute_and_unknown_is_faint(self):
        workers = [worker("a", pulses=[(NOW - 10, 4)]), worker("b", pulses=[(NOW - 20, 3)])]
        text = plain(_og_swarm_band(_og_plan(120), "│  ", workers, NOW, 1.0))
        self.assertIn("7 tools/min", text)
        old = [worker("a", pulses=[(NOW - 120, 5)])]
        self.assertIn("0 tools/min", plain(_og_swarm_band(_og_plan(120), "│  ", old, NOW, 1.0)))
        unknown = plain(_og_swarm_band(_og_plan(120), "│  ", band_workers(3), NOW, 1.0))
        self.assertIn("— tools/min", unknown)
        self.assertNotIn("0 tools/min", unknown)

    def test_band_cuts_hive_with_plus_n(self):
        text = plain(_og_swarm_band(_og_plan(60), "│  │  ", band_workers(80), NOW, 1.0))
        self.assertIn("╭─ SWARM 80 ", text)
        self.assertRegex(text, r"\+\d+$")

    def test_band_drops_from_the_right_when_narrow(self):
        workers = band_workers(8, pulse_at=NOW - 5)
        workers[0] = worker("a", state="failed")
        wide = plain(_og_swarm_band(_og_plan(160), "│  ", workers, NOW, 1.0))
        narrow = plain(_og_swarm_band(_og_plan(60), "│  ", workers, NOW, 1.0))
        self.assertIn("failed", wide)
        self.assertNotIn("failed", narrow)
        self.assertIn("1/8", narrow)

    def test_nocolor_band_is_static_across_t(self):
        workers = band_workers(8, pulse_at=NOW - 5)
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            first = _og_swarm_band(_og_plan(120), "│  ", workers, NOW, 0.0)
            second = _og_swarm_band(_og_plan(120), "│  ", workers, NOW, 12.3)
        self.assertEqual(first, second)

    def test_hive_moves_without_nocolor(self):
        workers = band_workers(4)
        self.assertNotEqual(plain(_og_hive(workers, 0.0, NOW)), plain(_og_hive(workers, 0.4, NOW)))

    def test_swarm_minimum(self):
        self.assertEqual(OG_SWARM_MIN, 3)


if __name__ == "__main__":
    unittest.main()
