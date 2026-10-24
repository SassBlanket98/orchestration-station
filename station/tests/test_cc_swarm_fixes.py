"""Regression tests for the Sol review of the swarm merge: click routing, hit rows and tab/close-button overlap."""
from __future__ import annotations

import unittest

from test_cc_rows_layout import NOW, plain, snapshot

import app
from model import UIState
from render import _rd_g_nodes, render_global


def _click(ui: UIState, col: int, row: int) -> list[str]:
    return app._ap_pointer_keys(ui, f"click:{col}:{row}", snapshot(), None, NOW)


class SwarmReviewFixes(unittest.TestCase):
    def test_pane_button_and_close_label_clicks_reach_the_toggle(self) -> None:
        gs = snapshot()
        ui = UIState(cc=True, gquips=False, glegend=False)
        render_global(gs, ui, 140, 40, NOW)
        hit = next(h for h in ui.hits if h[4] == "ccpane")
        _click(ui, hit[2], hit[0])
        self.assertTrue(ui.cc_pane_open)
        render_global(gs, ui, 140, 40, NOW)
        hit = next(h for h in ui.hits if h[4] == "ccpane")
        _click(ui, hit[2], hit[0])
        self.assertFalse(ui.cc_pane_open)

    def test_command_line_click_toggles_the_worker_card(self) -> None:
        gs = snapshot()
        ui = UIState(cc=True, gquips=False, glegend=False)
        frame = render_global(gs, ui, 140, 40, NOW)
        hit = next(h for h in ui.hits if h[4] == "ccrow")
        self.assertIn("command-0", plain(frame[hit[0] - 1]))      # 1-based hit row is the worker's own command line
        _click(ui, hit[2], hit[0])
        self.assertIn(hit[5], ui.expanded)

    def test_files_link_hit_lands_on_the_visible_label_when_expanded(self) -> None:
        gs = snapshot()
        probe = UIState(cc=True, gquips=False, glegend=False)
        nodes = _rd_g_nodes(gs, probe)
        sel = next(i for i, n in enumerate(nodes) if n.kind == "worker" and n.name == "worker-00")
        ui = UIState(cc=True, gsel=sel, gquips=False, glegend=False, expanded={"worker-00"})
        frame = render_global(gs, ui, 140, 40, NOW)
        files = [h for h in ui.hits if h[4] == "files"]
        self.assertTrue(files)
        self.assertIn("f files", plain(frame[files[0][0] - 1]))

    def test_close_label_never_sits_under_a_tab_span(self) -> None:
        for cols in (120, 130, 140):
            ui = UIState(cc=True, cc_pane_open=True, gquips=False, glegend=False)
            render_global(snapshot(), ui, cols, 40, NOW)
            close = next(h for h in ui.hits if h[4] == "ccpane")
            for h in ui.hits:
                if h[4] == "cctab" and h[0] == close[0]:
                    self.assertLess(h[3], close[2], f"{h[5]} overlaps the close label at {cols} cols")


if __name__ == "__main__":
    unittest.main()
