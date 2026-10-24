"""Live indicator: life-spine speed and motion, breathing quiet mark, frozen stalled mark."""
from __future__ import annotations

import unittest

from model import DONE, QUIET, RUNNING, STALLED, LogState, Step, Worker
from render import (
    _rd_card,
    _rd_card_height,
    _rd_LEVELS,
    _rd_mark,
    _rd_spine,
    _rd_spine_period,
)
from widgets import _wd_C_RED, _wd_SGR_RE, _wd_width

NOW = 1_760_050_000.0
T0 = 1200.05          # 12000 frames at 10 fps: the start of a hot cycle
SPINE = {"▏", "▍", "▊"}


def glyphs(cells: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", c) for c in cells]


def live(name: str = "w", quiet_s: float = 2.0, state: str = RUNNING, empty: bool = False) -> Worker:
    log = LogState() if empty else LogState(offset=10, steps=[Step(NOW - 3, "tool", "bash", "pytest -q")])
    return Worker(name=name, state=state, start=NOW - 120, quiet_s=quiet_s, family="codex", log=log, cpu=20.0)


def head_rows(cells: list[str]) -> list[int]:
    return [i for i, c in enumerate(glyphs(cells)) if c == "▊"]


class SpineSpeed(unittest.TestCase):
    def test_bands_by_quiet_time(self) -> None:
        self.assertEqual(_rd_spine_period(live(quiet_s=3.0)), 12)
        self.assertEqual(_rd_spine_period(live(quiet_s=10.0)), 12)
        self.assertEqual(_rd_spine_period(live(quiet_s=30.0)), 24)
        self.assertEqual(_rd_spine_period(live(quiet_s=89.0)), 24)
        self.assertEqual(_rd_spine_period(live(quiet_s=120.0, state=QUIET)), 36)

    def test_empty_log_worker_is_calm_whatever_its_quiet_time(self) -> None:
        self.assertEqual(_rd_spine_period(live(quiet_s=2.0, empty=True)), 24)
        self.assertEqual(_rd_spine_period(live(quiet_s=200.0, empty=True)), 24)

    def test_head_travels_down_and_back_in_twelve_frames(self) -> None:
        wk = live(quiet_s=2.0)
        heads = [head_rows(_rd_spine(wk, T0 + k / 10, 7))[0] for k in range(12)]
        self.assertEqual(heads, [0, 1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1])

    def test_spine_frames_move_between_t_and_t_plus_point_one(self) -> None:
        wk = live(quiet_s=2.0)
        self.assertNotEqual(glyphs(_rd_spine(wk, T0, 7)), glyphs(_rd_spine(wk, T0 + 0.1, 7)))

    def test_trail_and_body_around_the_head(self) -> None:
        cells = glyphs(_rd_spine(live(quiet_s=2.0), T0 + 0.6, 7))   # head at row 6 (bottom)
        self.assertEqual(cells, ["▏", "▏", "▏", "▏", "▏", "▍", "▊"])

    def test_one_glyph_per_row_at_any_height(self) -> None:
        for n in (1, 2, 3, 5, 7):
            self.assertEqual(len(_rd_spine(live(), T0, n)), n)
        self.assertEqual(_rd_spine(live(), T0, 0), [])

    def test_calm_worker_moves_slower_than_hot(self) -> None:
        hot = [glyphs(_rd_spine(live(quiet_s=2.0), T0 + k / 10, 7)) for k in range(12)]
        calm = [glyphs(_rd_spine(live(quiet_s=30.0), T0 + k / 10, 7)) for k in range(12)]
        self.assertNotEqual(hot, calm)
        steps = [head_rows(_rd_spine(live(quiet_s=30.0), T0 + k / 10, 7))[0] for k in range(12)]
        self.assertEqual(steps, [0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6])   # 24-frame cycle: each row held two frames

    def test_empty_log_worker_matches_the_calm_pattern(self) -> None:
        empty = live(quiet_s=2.0, empty=True)
        calm = live(quiet_s=30.0)
        for k in range(24):
            t = T0 + k / 10
            self.assertEqual(glyphs(_rd_spine(empty, t, 7)), glyphs(_rd_spine(calm, t, 7)), k)


class SpineStates(unittest.TestCase):
    def test_stalled_is_frozen_red_dashes(self) -> None:
        wk = live(quiet_s=400.0, state=STALLED)
        frames = [_rd_spine(wk, T0 + d, 7) for d in (0.0, 0.1, 0.5)]
        for cells in frames:
            self.assertEqual(glyphs(cells), ["┆"] * 7)
            self.assertTrue(all(f"38;2;{_wd_C_RED[0]};{_wd_C_RED[1]};{_wd_C_RED[2]}" in c for c in cells))
        self.assertEqual(frames[0], frames[1])
        self.assertEqual(frames[0], frames[2])

    def test_finished_is_solid_and_still(self) -> None:
        done = Worker(name="d", state=DONE, start=NOW - 90, end=NOW - 10, log=LogState(offset=4))
        for d in (0.0, 0.3, 1.7):
            self.assertEqual(glyphs(_rd_spine(done, T0 + d, 5)), ["│"] * 5)
        self.assertEqual(_rd_spine(done, T0, 5), _rd_spine(done, T0 + 0.3, 5))


class QuietAndStalledMarks(unittest.TestCase):
    def test_quiet_mark_breathes_between_two_glyphs(self) -> None:
        wk = live(quiet_s=120.0, state=QUIET)
        seen = {glyphs([_rd_mark(wk, T0 + k * 0.5)])[0] for k in range(20)}
        self.assertEqual(seen, {"◌", "◍"})

    def test_quiet_mark_breath_is_slow(self) -> None:
        wk = live(quiet_s=120.0, state=QUIET)
        marks = [glyphs([_rd_mark(wk, T0 + k / 10)])[0] for k in range(24)]
        self.assertEqual(marks[0] * 12 + marks[12] * 12, "".join(marks))   # two halves, each 1.2 s

    def test_stalled_mark_is_a_static_red_triangle(self) -> None:
        wk = live(quiet_s=400.0, state=STALLED)
        marks = {glyphs([_rd_mark(wk, T0 + k * 0.3)])[0] for k in range(10)}
        self.assertEqual(marks, {"▲"})


class CardSpine(unittest.TestCase):
    def test_selected_card_has_spine_in_column_zero_and_right_height(self) -> None:
        wk = live(quiet_s=2.0)
        wk.log.steps = [Step(NOW - 3 * k, "tool", "bash", f"step {k}") for k in range(6)]
        for lvl in range(len(_rd_LEVELS)):
            card = _rd_card(wk, lvl, 60, NOW, T0, True, None)
            self.assertEqual(len(card), _rd_card_height(wk, lvl), lvl)
            first = [c[0] for c in glyphs(card)]
            self.assertTrue(set(first) <= SPINE, (lvl, first))
            self.assertTrue(all(_wd_width(c) <= 60 for c in card), lvl)

    def test_unselected_card_keeps_the_accent_bar(self) -> None:
        card = glyphs(_rd_card(live(quiet_s=2.0), 0, 60, NOW, T0, False, None))
        self.assertEqual({c[0] for c in card}, {"▎"})


if __name__ == "__main__":
    unittest.main()
