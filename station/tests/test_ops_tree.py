"""R2: tree prefixes and continuations, level tags (spec section 1, rows Tree and Level tag)."""
from __future__ import annotations

import unittest

from opstree import OT_TAGS, _ot_prefixes, _ot_tag
from widgets import (
    _wd_c,
    _wd_C_BLUE,
    _wd_C_DIM,
    _wd_C_SUB,
    _wd_C_TEAL,
    _wd_C_VIOLET,
    _wd_SGR_RE,
    _wd_width,
)


def plain(s: str) -> str:
    return _wd_SGR_RE.sub("", s)


class FlatListTests(unittest.TestCase):
    def test_flat_list_uses_branch_then_last(self) -> None:
        self.assertEqual(_ot_prefixes([0, 0, 0]), [("├─ ", "│  "), ("├─ ", "│  "), ("└─ ", "   ")])

    def test_single_node_is_last(self) -> None:
        self.assertEqual(_ot_prefixes([0]), [("└─ ", "   ")])

    def test_empty_list(self) -> None:
        self.assertEqual(_ot_prefixes([]), [])


class NestedTests(unittest.TestCase):
    def test_nested_three_levels(self) -> None:
        got = _ot_prefixes([0, 1, 2, 2, 1, 0])
        self.assertEqual(got, [
            ("├─ ", "│  "),
            ("│  ├─ ", "│  │  "),
            ("│  │  ├─ ", "│  │  │  "),
            ("│  │  └─ ", "│  │     "),
            ("│  └─ ", "│     "),
            ("└─ ", "   "),
        ])

    def test_mockup_status_dot_sh_project(self) -> None:
        # lead, its 3 workers and a launched session with one sub-agent, then a second lead
        prefixes = [p for p, _ in _ot_prefixes([0, 1, 1, 1, 1, 2, 0, 0])]
        self.assertEqual(prefixes[:7], ["├─ ", "│  ├─ ", "│  ├─ ", "│  ├─ ", "│  └─ ", "│     └─ ", "├─ "])

    def test_mockup_openclaw_project(self) -> None:
        prefixes = [p for p, _ in _ot_prefixes([0, 1, 1, 0, 0, 0])]
        self.assertEqual(prefixes, ["├─ ", "│  ├─ ", "│  └─ ", "├─ ", "├─ ", "└─ "])


class LastChildTests(unittest.TestCase):
    def test_last_parent_gives_blank_ancestor_column(self) -> None:
        self.assertEqual(_ot_prefixes([0, 1, 1, 0, 1]), [
            ("├─ ", "│  "),
            ("│  ├─ ", "│  │  "),
            ("│  └─ ", "│     "),
            ("└─ ", "│  "),
            ("   └─ ", "      "),
        ])

    def test_last_parent_with_children(self) -> None:
        # the last top-level node has a child: its continuation still draws the child rail
        self.assertEqual(_ot_prefixes([0, 1]), [("└─ ", "│  "), ("   └─ ", "      ")])

    def test_last_leaf_under_last_parent(self) -> None:
        self.assertEqual(_ot_prefixes([0, 1, 2]), [
            ("└─ ", "│  "),
            ("   └─ ", "   │  "),
            ("      └─ ", "         "),
        ])

    def test_sibling_after_deep_branch_keeps_rail(self) -> None:
        got = _ot_prefixes([0, 1, 2, 1, 0])
        self.assertEqual(got[2][0], "│  │  └─ ")
        self.assertEqual(got[3][0], "│  └─ ")

    def test_lone_top_node_is_last_so_its_column_is_blank(self) -> None:
        got = _ot_prefixes([0, 1, 2, 1])
        self.assertEqual(got[2][0], "   │  └─ ")
        self.assertEqual(got[3][0], "   └─ ")


class DepthJumpTests(unittest.TestCase):
    def test_jump_of_two_is_one_level_deeper(self) -> None:
        self.assertEqual(_ot_prefixes([0, 2, 0]), _ot_prefixes([0, 1, 0]))

    def test_first_node_deeper_than_zero_is_top_level(self) -> None:
        self.assertEqual(_ot_prefixes([3]), [("└─ ", "   ")])

    def test_large_jump_mid_list(self) -> None:
        self.assertEqual(_ot_prefixes([0, 1, 5, 1]), _ot_prefixes([0, 1, 2, 1]))

    def test_negative_depth_is_clamped(self) -> None:
        self.assertEqual(_ot_prefixes([-2, 0]), _ot_prefixes([0, 0]))

    def test_never_crashes_on_odd_input(self) -> None:
        for depths in ([9, 9, 9], [0, 7, 0, 3, 1], [-5, 20, 0, 0, 4]):
            with self.subTest(depths=depths):
                self.assertEqual(len(_ot_prefixes(depths)), len(depths))


class WidthInvariantTests(unittest.TestCase):
    PATTERNS = (
        [0], [0, 0, 0], [0, 1, 2, 3, 4], [0, 1, 1, 2, 2, 1, 0, 1, 2, 3, 0], [0, 0, 1, 1, 0],
    )

    def test_prefix_and_continuation_widths_follow_depth(self) -> None:
        for depths in self.PATTERNS:
            for d, (prefix, cont) in zip(depths, _ot_prefixes(depths)):
                with self.subTest(depths=depths, depth=d):
                    self.assertEqual(_wd_width(prefix), 3 * (d + 1))
                    self.assertEqual(_wd_width(cont), 3 * (d + 1))
                    self.assertTrue(prefix.endswith(("├─ ", "└─ ")))
                    self.assertTrue(cont.endswith(("│  ", "   ")))

    def test_siblings_at_one_depth_share_width(self) -> None:
        got = _ot_prefixes([0, 1, 2, 2, 1, 0])
        widths = {_wd_width(p) for p, _ in (got[1], got[4])}
        self.assertEqual(widths, {6})
        self.assertEqual({_wd_width(p) for p, _ in (got[2], got[3])}, {9})


FULL = {"LEAD": "LEAD", "SPAWN": "SPAWN", "SUB": "SUB", "WORKER": "WORKER", "CRON": "CRON"}
SHORT = {"LEAD": "LEAD", "SPAWN": "SPWN", "SUB": "SUB", "WORKER": "WRKR", "CRON": "CRON"}
COLOUR = {"LEAD": _wd_C_SUB, "SPAWN": _wd_C_VIOLET, "SUB": _wd_C_TEAL, "WORKER": _wd_C_DIM, "CRON": _wd_C_BLUE}


class TagTests(unittest.TestCase):
    def test_every_tag_has_a_full_name(self) -> None:
        self.assertEqual(tuple(FULL), OT_TAGS)

    def test_full_width_tags_are_six_wide(self) -> None:
        for tag in OT_TAGS:
            with self.subTest(tag=tag):
                cell = _ot_tag(tag, False)
                self.assertEqual(plain(cell), FULL[tag].ljust(6))
                self.assertEqual(_wd_width(cell), 6)

    def test_short_tags_are_four_wide(self) -> None:
        for tag in OT_TAGS:
            with self.subTest(tag=tag):
                cell = _ot_tag(tag, True)
                self.assertEqual(plain(cell), SHORT[tag].ljust(4))
                self.assertEqual(_wd_width(cell), 4)

    def test_each_tag_uses_its_role_colour(self) -> None:
        for tag in OT_TAGS:
            with self.subTest(tag=tag):
                for short in (False, True):
                    text = plain(_ot_tag(tag, short))
                    self.assertEqual(_ot_tag(tag, short), _wd_c(text, COLOUR[tag]))

    def test_unknown_and_empty_tags_are_blank(self) -> None:
        for tag in ("", "BOGUS", "lead"):
            with self.subTest(tag=tag):
                self.assertEqual(_ot_tag(tag, False), " " * 6)
                self.assertEqual(_ot_tag(tag, True), " " * 4)

    def test_unknown_tag_has_no_colour_codes(self) -> None:
        self.assertNotIn("\x1b", _ot_tag("BOGUS", False))


if __name__ == "__main__":
    unittest.main()
