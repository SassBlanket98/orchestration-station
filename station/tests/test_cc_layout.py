from __future__ import annotations

import unittest
from unittest import mock

import cclayout
from model import GlobalSnapshot, Snapshot, Stage, UIState, Worker
from render import render_global
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0


def plain(line: str) -> str:
    return _wd_SGR_RE.sub("", line)


def sample() -> GlobalSnapshot:
    worker = Worker(name="worker-one", state="running", goal="inspect parser")
    stage = Stage(path="/alpha", label="alpha", snap=Snapshot(workers=[worker]))
    return GlobalSnapshot(now=NOW, stages=[stage])


class LayoutTests(unittest.TestCase):
    def make_table(self, width: int, height: int) -> list[str]:
        return [f"T{i}".ljust(width) for i in range(height)]

    def test_supported_sizes_have_exact_frame_dimensions(self) -> None:
        for cols, rows in ((120, 24), (140, 40), (160, 50), (200, 60)):
            ui = UIState(cc_pane_open=True)
            lines = cclayout.cc_frame(sample(), ui, cols, rows, NOW, ("/alpha", "worker", "worker-one"),
                                      self.make_table)
            self.assertEqual(len(lines), rows)
            self.assertTrue(all(_wd_width(line) == cols for line in lines))

    def test_split_widths_and_strip_visibility(self) -> None:
        widths: list[tuple[int, int]] = []
        def table(width: int, height: int) -> list[str]:
            widths.append((width, height))
            return self.make_table(width, height)
        for cols, rows, strip_open, expected_strip in ((120, 24, True, False), (140, 40, True, True),
                                                        (160, 50, False, False)):
            widths.clear()
            ui = UIState(cc_strip_open=strip_open, cc_pane_open=True)
            cclayout.cc_frame(sample(), ui, cols, rows, NOW, None, table)
            left, height = widths[0]
            self.assertGreaterEqual(left, cclayout.TABLE_MIN)
            self.assertGreaterEqual(cols - left - 1, 40)
            self.assertEqual(left + 1 + (cols - left - 1), cols)
            self.assertEqual(height, rows - (8 if rows >= 40 and strip_open and expected_strip else
                                            6 if rows >= 30 and strip_open and expected_strip else 0) - 1)

    def test_tab_hits_match_tab_labels(self) -> None:
        ui = UIState(cc_pane_open=True)
        lines = cclayout.cc_frame(sample(), ui, 140, 40, NOW, None, self.make_table)
        for row0, row1, col0, col1, action, name in ui.hits:
            if action not in ("cctab", "ccstrip"):
                continue
            self.assertEqual(row0, row1)
            label = plain(lines[row0])[col0:col1 + 1].strip()
            self.assertIn(name[:4] if action == "cctab" and len(name) > 4 else name, label)

    def test_scroll_clamps_and_missing_selection(self) -> None:
        ui = UIState(cc_pane_open=True, cc_pane_scroll=10000, cc_strip_scroll=-5)
        lines = cclayout.cc_frame(sample(), ui, 140, 40, NOW, ("/not-found", "stage", ""), self.make_table)
        self.assertTrue(lines)
        self.assertGreaterEqual(ui.cc_strip_scroll, 0)
        self.assertIn("select a session or worker", "\n".join(map(plain, lines)))
        self.assertGreaterEqual(ui.cc_pane_scroll, 0)

    def test_scroll_clamps_to_pane_maximum_for_selected_row(self) -> None:
        gs = sample()
        gs.stages[0].snap.workers[0].prompt = ["long prompt " * 200]
        ui = UIState(cc_pane_open=True, cc_pane_scroll=10000)
        with mock.patch.object(cclayout, "hint_line", lambda _ui, width: " " * width):
            lines = cclayout.cc_frame(gs, ui, 140, 40, NOW,
                                      ("/alpha", "worker", "worker-one"), self.make_table)
        self.assertEqual(len(lines), 40)
        self.assertLess(ui.cc_pane_scroll, 10000)

    def test_render_global_uses_frame_and_falls_back_at_119_columns(self) -> None:
        gs = sample()
        ui = UIState(cc=True)
        with mock.patch("cclayout.hint_line", lambda _ui, width: " " * width):
            ui.cc_pane_open = True
            lines = render_global(gs, ui, 140, 40, NOW)
            self.assertTrue(ui.cc_active)
            text = "\n".join(map(plain, lines))
            self.assertIn("alpha", text)
            self.assertIn("Overview", text)
            fallback = render_global(gs, ui, 119, 40, NOW)
            self.assertFalse(ui.cc_active)
            self.assertTrue(any("alpha" in plain(line) for line in fallback))

    def test_heading_selection_and_table_failure(self) -> None:
        lines = cclayout.cc_frame(sample(), UIState(cc_pane_open=True), 140, 40, NOW, ("/missing", "heading", ""), self.make_table)
        self.assertIn("select a session or worker", "\n".join(map(plain, lines)))
        self.assertEqual(cclayout.cc_frame(sample(), UIState(), 140, 40, NOW, None,
                                           lambda _w, _h: (_ for _ in ()).throw(RuntimeError())), [])

    def test_project_heading_shows_its_session(self) -> None:
        gs = sample()
        gs.stages[0].cwd = "/proj/alpha"
        lines = cclayout.cc_frame(gs, UIState(cc_pane_open=True), 140, 40, NOW, ("", "group", "p:/proj/alpha"), self.make_table)
        self.assertNotIn("select a session or worker", "\n".join(map(plain, lines)))

    def test_falls_back_below_minimum(self) -> None:
        self.assertEqual(cclayout.cc_frame(sample(), UIState(), 119, 40, NOW, None, self.make_table), [])

    def test_closed_pane_uses_full_width_and_button_hit(self) -> None:
        widths = []
        ui = UIState()
        lines = cclayout.cc_frame(sample(), ui, 140, 40, NOW, None,
                                  lambda width, height: (widths.append(width) or self.make_table(width, height)))
        self.assertEqual(widths, [140])
        self.assertIn("details  d", plain(lines[0]))
        self.assertIn((0, 0, 128, 139, "ccpane", ""), ui.hits)

    def test_open_pane_close_label_and_hit(self) -> None:
        ui = UIState(cc_pane_open=True)
        lines = cclayout.cc_frame(sample(), ui, 140, 40, NOW, None, self.make_table)
        self.assertIn("✕  d", plain(lines[0]))
        self.assertIn((0, 0, 136, 139, "ccpane", ""), ui.hits)

    def test_exact_dimensions_with_both_pane_states(self) -> None:
        for cols, rows in ((120, 24), (140, 40), (160, 50), (200, 60)):
            for opened in (False, True):
                lines = cclayout.cc_frame(sample(), UIState(cc_pane_open=opened), cols, rows, NOW, None,
                                          self.make_table)
                self.assertEqual(len(lines), rows)
                self.assertTrue(all(_wd_width(line) == cols for line in lines))

    def test_wide_screen_caps_master_table_when_pane_open(self) -> None:
        widths: list[int] = []
        ui = UIState(cc_pane_open=True)
        cclayout.cc_frame(sample(), ui, 220, 40, NOW, None,
                          lambda width, height: widths.append(width) or self.make_table(width, height))
        self.assertEqual(widths, [cclayout.TABLE_MAX])
        self.assertGreaterEqual(220 - cclayout.TABLE_MAX - 1, 40)

    def test_closed_pane_stays_full_width_at_ultra_wide(self) -> None:
        widths: list[int] = []
        cclayout.cc_frame(sample(), UIState(), 220, 40, NOW, None,
                          lambda width, height: widths.append(width) or self.make_table(width, height))
        self.assertEqual(widths, [220])


if __name__ == "__main__":
    unittest.main()
