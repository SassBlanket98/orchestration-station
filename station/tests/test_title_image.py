from __future__ import annotations

import os
import re
import unittest
from unittest import mock

import app
from model import GlobalSnapshot, UIState
from render import render_global

NOW = 1_760_050_000.0
ASPECT = 22.0     # about the real title picture on a 1:2 cell


def frame(ui: UIState, cols: int, rows: int) -> list[str]:
    return render_global(GlobalSnapshot(now=NOW), ui, cols, rows, NOW)


class TitleImage(unittest.TestCase):
    def test_no_image_support_keeps_the_text_title(self) -> None:
        ui = UIState()
        text = "\n".join(frame(ui, 120, 50))
        self.assertIn("S T A T I O N", re.sub(r"\x1b\[[0-9;]*m", "", text))
        self.assertEqual(ui.title_place, (0, 0))

    def test_a_tall_terminal_gets_the_picture(self) -> None:
        ui = UIState(title_aspect=ASPECT)
        lines = frame(ui, 120, 45)
        self.assertEqual(ui.title_place, (round(4 * ASPECT), 4))
        self.assertEqual([re.sub(r"\x1b\[[0-9;]*m", "", ln).strip() for ln in lines[:4]], [""] * 4)

    def test_the_picture_shrinks_with_the_window_instead_of_vanishing(self) -> None:
        for cols, rows, height in ((120, 45, 4), (120, 30, 4), (120, 24, 4), (120, 23, 3), (120, 20, 3), (120, 19, 2),
                                   (120, 16, 2), (90, 40, 4), (80, 40, 3), (68, 40, 3), (60, 40, 2), (46, 40, 2)):
            with self.subTest(cols=cols, rows=rows):
                ui = UIState(title_aspect=ASPECT)
                lines = frame(ui, cols, rows)
                self.assertEqual(ui.title_place, (round(height * ASPECT), height))
                self.assertLessEqual(ui.title_place[0] + 2, cols)
                self.assertEqual([re.sub(r"\x1b\[[0-9;]*m", "", ln).strip() for ln in lines[:height]], [""] * height)

    def test_zoomed_in_keeps_the_picture(self) -> None:
        # a zoomed-in Ghostty window: far fewer rows and columns than a zoomed-out one
        for cols, rows in ((100, 28), (90, 26), (70, 22), (60, 20), (46, 17)):
            with self.subTest(cols=cols, rows=rows):
                ui = UIState(title_aspect=ASPECT)
                frame(ui, cols, rows)
                self.assertNotEqual(ui.title_place, (0, 0))

    def test_picture_never_overflows_and_body_survives(self) -> None:
        for cols in range(20, 200, 7):
            for rows in range(6, 70, 3):
                ui = UIState(title_aspect=ASPECT)
                lines = frame(ui, cols, rows)
                pc, ph = ui.title_place
                self.assertLessEqual(pc + 2 if pc else 0, cols)
                self.assertLessEqual(len(lines), rows)
                if ph:
                    self.assertGreaterEqual(rows, ph + 12)

    def test_a_tiny_screen_collapses_the_title_to_one_line(self) -> None:
        ui = UIState(title_aspect=ASPECT)
        frame(ui, 45, 40)   # too narrow for either picture
        self.assertEqual(ui.title_place, (0, 0))
        frame(ui, 120, 15)  # too short
        self.assertEqual(ui.title_place, (0, 0))
        text = "\n".join(re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in frame(ui, 45, 40))
        self.assertIn("◆ Station", text)
        self.assertNotIn("S T A T I O N", text)

    def test_without_image_support_the_text_fallback_is_unchanged(self) -> None:
        ui = UIState()
        text = "\n".join(re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in frame(ui, 120, 40))
        self.assertIn("◆ Station", text)   # short: one line
        self.assertEqual(ui.title_place, (0, 0))


class TitleLeavesWithTheTree(unittest.TestCase):
    def test_other_views_clear_the_placement(self) -> None:
        from model import Snapshot
        from render import render_frame
        ui = UIState(title_aspect=ASPECT)
        frame(ui, 120, 45)
        self.assertNotEqual(ui.title_place, (0, 0))
        render_frame(Snapshot(now=NOW), ui, 120, 45, NOW)
        self.assertEqual(ui.title_place, (0, 0))
        frame(ui, 120, 45)
        ui.view = "quota"
        render_global(GlobalSnapshot(now=NOW), UIState(title_aspect=ASPECT, gpage="milestones"), 120, 40, NOW)


class TitleEnabled(unittest.TestCase):
    def check(self, env: dict[str, str]) -> bool:
        with mock.patch.dict(os.environ, env, clear=True):
            return app._ap_title_enabled()

    def test_detection(self) -> None:
        self.assertTrue(self.check({"TERM": "xterm-ghostty", "TERM_PROGRAM": "ghostty"}))
        self.assertTrue(self.check({"KITTY_WINDOW_ID": "1"}))
        self.assertFalse(self.check({"TERM": "xterm-256color"}))
        self.assertFalse(self.check({"TERM": "xterm-ghostty", "TMUX": "/tmp/x"}))
        self.assertFalse(self.check({"TERM": "xterm-ghostty", "STATUS_TITLE_IMAGE": "0"}))
        self.assertTrue(self.check({"TERM": "xterm-256color", "STATUS_TITLE_IMAGE": "1"}))

    def test_the_picture_is_sent_in_valid_chunks(self) -> None:
        data = app._ap_title_send().decode("ascii")
        parts = re.findall(r"\x1b_G([^;]*);([^\x1b]*)\x1b\\", data)
        self.assertGreater(len(parts), 1)
        self.assertTrue(parts[0][0].startswith("a=t,f=100,t=d,i=51"))
        self.assertIn(b"\x1b[1;2H", app._ap_title_place(60, 3))
        self.assertIn(b"i=51,p=1,c=60,r=3,C=1", app._ap_title_place(60, 3))
        self.assertTrue(all(len(p[1]) <= 4096 for p in parts))
        self.assertTrue(parts[-1][0].endswith("m=0"))
        self.assertTrue(all(p[0].endswith("m=1") for p in parts[:-1]))


if __name__ == "__main__":
    unittest.main()
