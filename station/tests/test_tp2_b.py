"""TP2-B: detail pane label column and pane tab key agreement."""
from __future__ import annotations

import unittest

from test_cc_pane import NOW, _lead, _plain, _worker

import cchint
from ccpane import PANE_TAB_ARROWS, PANE_TABS, pane_lines, pane_tab_bar, pane_tab_digit
from widgets import _wd_C_MUTED, _wd_C_TEXT, _wd_sgr


class PaneTabAgreementTests(unittest.TestCase):
    def test_digit_keys_match_tabs_in_order(self) -> None:
        for index, name in enumerate(PANE_TABS, start=1):
            self.assertEqual(pane_tab_digit(str(index)), name)
        self.assertIsNone(pane_tab_digit("0"))
        self.assertIsNone(pane_tab_digit("7"))

    def test_cckeys_and_cchint_use_the_same_cycle_keys(self) -> None:
        self.assertEqual(cchint.pane_tab_cycle_keys(), PANE_TAB_ARROWS)
        self.assertEqual(PANE_TAB_ARROWS, ("left", "right"))

    def test_tools_and_files_are_tabs_three_and_four(self) -> None:
        self.assertEqual(PANE_TABS[2:4], ("Tools", "Files"))
        self.assertEqual(pane_tab_digit("3"), "Tools")
        self.assertEqual(pane_tab_digit("4"), "Files")

    def test_short_tab_bar_keeps_tools_and_files_plural(self) -> None:
        line, _spans = pane_tab_bar("Tools", 40)
        plain = _plain(line)
        self.assertIn("Tools", plain)
        self.assertIn("Files", plain)
        self.assertNotIn("Tool  ", plain)


class OverviewLabelColumnTests(unittest.TestCase):
    _LABELS = ("GOAL", "NOW", "TOKENS", "CTX", "COST", "ELAPSED", "TOOLS")

    def _value_starts(self, lines: list[str]) -> list[int]:
        starts: list[int] = []
        for line in lines:
            text = _plain(line)
            for label in self._LABELS:
                if text.startswith(label):
                    rest = text[len(label):]
                    starts.append(len(label) + len(rest) - len(rest.lstrip()))
                    break
        return starts

    def test_overview_values_share_one_column(self) -> None:
        for width in (30, 46, 60, 80):
            lines = pane_lines(_lead(), _worker(), "Overview", width, 24, 0, NOW)
            starts = self._value_starts(lines)
            self.assertGreaterEqual(len(starts), 5, width)
            self.assertEqual(len(set(starts)), 1, (width, starts))

    def test_overview_labels_use_muted_and_values_text(self) -> None:
        lines = pane_lines(_lead(), _worker(), "Overview", 60, 24, 0, NOW)
        goal = next(line for line in lines if _plain(line).startswith("GOAL"))
        self.assertIn(_wd_sgr(_wd_C_MUTED), goal)
        self.assertIn(_wd_sgr(_wd_C_TEXT), goal)


class PaneDigitKeyTests(unittest.TestCase):
    def test_digit_selects_tab_with_pane_focus(self) -> None:
        from test_cc_keys import _gs, _ui, cc_key

        ui = _ui(cc_focus="pane", cc_pane_open=True, cc_tab="Overview")
        self.assertEqual(cc_key(ui, "4", _gs(), 40, None), "")
        self.assertEqual(ui.cc_tab, "Files")


if __name__ == "__main__":
    unittest.main()
