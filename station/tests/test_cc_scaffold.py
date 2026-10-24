"""W0 contract: the control center hooks fall back to the classic tree while the stubs return nothing."""
from __future__ import annotations

import unittest

from cckeys import cc_key
from cclayout import cc_applies
from model import GlobalSnapshot, UIState
from render import render_global


class ScaffoldTests(unittest.TestCase):
    def test_applies_only_when_wide_and_tall(self) -> None:
        self.assertTrue(cc_applies(120, 24))
        self.assertFalse(cc_applies(119, 40))
        self.assertFalse(cc_applies(160, 23))

    def test_cc_off_draws_the_classic_tree(self) -> None:
        gs = GlobalSnapshot()
        self.assertEqual(render_global(gs, UIState(cc=False), 140, 40, 0.0), render_global(gs, UIState(), 140, 40, 0.0))

    def test_key_is_not_handled_before_a_cc_frame(self) -> None:
        self.assertIsNone(cc_key(UIState(cc=True), "s", GlobalSnapshot(), 40, None))


if __name__ == "__main__":
    unittest.main()
