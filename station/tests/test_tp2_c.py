"""TP2-C: Usage is the first strip tab and default; quota lives on Usage, not Metrics."""
from __future__ import annotations

import unittest

import ccstrip
from model import GlobalSnapshot, QuotaAccount, QuotaWindow, Snapshot, Stage, UIState
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0


def plain(s: str) -> str:
    return _wd_SGR_RE.sub("", s)


class Tp2CStripDefaults(unittest.TestCase):
    def test_strip_tabs_order(self) -> None:
        self.assertEqual(ccstrip.STRIP_TABS, ("Usage", "Events", "Logs", "Metrics"))

    def test_ui_state_default_strip_is_usage(self) -> None:
        self.assertEqual(UIState().cc_strip, "Usage")

    def test_default_strip_tab_bar_marks_usage(self) -> None:
        bar, _ = ccstrip.strip_tab_bar(UIState().cc_strip, 80)
        self.assertTrue(plain(bar).startswith(" ▸Usage  Events  Logs  Metrics "))


class Tp2CUsageVsMetrics(unittest.TestCase):
    def test_quota_on_usage_not_metrics(self) -> None:
        gs = GlobalSnapshot(
            now=NOW,
            stages=[Stage(path="/p", label="p", snap=Snapshot())],
            quota=[QuotaAccount(name="claude", windows=[QuotaWindow(name="5h", used=50.0, pace=None)])],
            quota_ok=True,
        )
        usage = plain("\n".join(ccstrip.strip_lines(gs, "Usage", None, None, 80, 4, 0, NOW)[1:]))
        metrics = plain("\n".join(ccstrip.strip_lines(gs, "Metrics", None, None, 80, 4, 0, NOW)[1:]))
        self.assertIn("claude", usage)
        self.assertNotIn("claude", metrics)
        self.assertIn("tools/min", metrics)

    def test_strip_at_46_lists_all_four_tabs(self) -> None:
        bar, spans = ccstrip.strip_tab_bar("Usage", 46)
        self.assertEqual(_wd_width(bar), 46)
        self.assertEqual([t for _a, _b, t in spans], list(ccstrip.STRIP_TABS))


if __name__ == "__main__":
    unittest.main()
