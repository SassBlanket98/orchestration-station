"""TP2-A slice C: quota NO_COLOR, wide layout, touch bar only on phones."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from test_overview_nav import fixture
from test_usageview import accounts, openrouter

from model import Snapshot, UIState
from render import render_frame, render_global
from widgets import _wd_SGR_RE

NOW = 1_760_050_000.0


def plain(lines: list[str]) -> str:
    return _wd_SGR_RE.sub("", "\n".join(lines))


class TouchBarDesktop(unittest.TestCase):
    def test_no_touch_bar_from_90_columns_up(self) -> None:
        gs = fixture()
        for cols in (90, 119, 120, 150, 190):
            ui = UIState(cc=True)
            render_global(gs, ui, cols, 30, NOW)
            touch = [h for h in ui.hits if h[4] == "key" and h[1] - h[0] == 2]
            self.assertEqual(touch, [], cols)


class QuotaNoColorIntegration(unittest.TestCase):
    def test_global_quota_via_render_frame_is_plain(self) -> None:
        snap = Snapshot(quota=accounts(), quota_ok=True, openrouter=openrouter(), openrouter_ok=True)
        ui = UIState(view="quota")
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            lines = render_frame(snap, ui, 120, 40, NOW)
        self.assertFalse(any("\x1b[" in line for line in lines))
        self.assertIn("capped", plain(lines))


if __name__ == "__main__":
    unittest.main()
