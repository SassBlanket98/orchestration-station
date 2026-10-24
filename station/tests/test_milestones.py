from __future__ import annotations

import unittest

from test_overview_nav import NOW, gs_of, keys, solo_stage, text

from model import Milestone, UIState


def with_milestones() -> object:
    stg = solo_stage("/s", "lone")
    stg.snap.milestones = [Milestone(NOW - 3000, "first thing"), Milestone(NOW - 2000, "second thing"), Milestone(NOW - 1000, "third thing")]
    return gs_of(stg)


class Milestones(unittest.TestCase):
    def test_milestones_are_detail_under_an_open_session(self) -> None:
        gs = with_milestones()
        shown = text(gs, UIState(gsel=1, gshut_keys={"s:/s"}))   # a live solo starts open
        self.assertNotIn("third thing", shown)
        self.assertNotIn("first thing", shown)
        shown = text(gs, UIState(gsel=1, gopen={("/s", "stage", "")}))
        self.assertIn("third thing", shown)
        self.assertIn("M all 3", shown)

    def test_m_expands_the_list_newest_first_and_again_collapses_it(self) -> None:
        gs, ui = with_milestones(), UIState(gsel=1, gopen={("/s", "stage", "")})
        keys(ui, gs, "M")
        shown = text(gs, ui)
        self.assertTrue(shown.index("third thing") < shown.index("second thing") < shown.index("first thing"))
        keys(ui, gs, "M")
        self.assertNotIn("first thing", text(gs, ui))

    def test_no_milestone_no_line(self) -> None:
        self.assertNotIn("⚑", text(gs_of(solo_stage("/s", "lone")), UIState(gsel=1, gopen={("/s", "stage", "")})))


if __name__ == "__main__":
    unittest.main()
