from __future__ import annotations

import argparse
import os
import tempfile
import unittest
import unittest.mock

import app
from model import DONE, GlobalSnapshot, Snapshot, Stage, UIState, Worker
from render import global_rows


class GlobalApp(unittest.TestCase):
    def setUp(self) -> None:
        self.stage = Stage("/x/.stage", "x", Snapshot(workers=[Worker("w", DONE)]))
        self.stage.state = "idle"
        self.gs = GlobalSnapshot(stages=[self.stage])

    def test_mode_and_roots(self) -> None:
        args = argparse.Namespace(demo=False, global_view=False, stage=None)
        with unittest.mock.patch.object(app, "_ap_has_stage_files", return_value=False):
            self.assertTrue(app._ap_global_mode(args, "/tmp"))
        args.global_view = True
        self.assertTrue(app._ap_global_mode(args, "/tmp"))
        args.demo = True
        self.assertFalse(app._ap_global_mode(args, "/tmp"))
        args.demo, args.roots = False, None
        with unittest.mock.patch.dict(app.os.environ, {"STATUS_ROOTS": "/a:/b"}):
            self.assertEqual(app._ap_roots(args), ["/a", "/b"])
        args.roots = ["/c"]
        self.assertEqual(app._ap_roots(args), ["/c"])

    def test_mode_uses_script_directory_without_stage_subdirectory_fallback(self) -> None:
        args = argparse.Namespace(demo=False, global_view=False, stage=None)
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, ".stage"))
            with open(os.path.join(root, ".stage", "worker.pid"), "w") as stream:
                stream.write("1")
            self.assertTrue(app._ap_global_mode(args, root))
            with open(os.path.join(root, "worker.pid"), "w") as stream:
                stream.write("1")
            self.assertFalse(app._ap_global_mode(args, root))
            args.stage = ".stage"
            self.assertFalse(app._ap_global_mode(args, root))
            args.global_view = True
            self.assertTrue(app._ap_global_mode(args, root))
            args.demo = True
            self.assertFalse(app._ap_global_mode(args, root))

    def test_each_stage_marker_selects_local_mode_and_roots_default(self) -> None:
        args = argparse.Namespace(demo=False, global_view=False, stage=None, roots=None)
        for marker in ("pid", "briefs", "PROGRESS.md"):
            with tempfile.TemporaryDirectory() as root:
                if marker == "pid":
                    with open(os.path.join(root, "worker.pid"), "w") as stream:
                        stream.write("1")
                elif marker == "briefs":
                    os.mkdir(os.path.join(root, marker))
                else:
                    with open(os.path.join(root, marker), "w") as stream:
                        stream.write("10:00 milestone")
                self.assertFalse(app._ap_global_mode(args, root), marker)
        with unittest.mock.patch.dict(app.os.environ, {"STATUS_ROOTS": ""}):
            self.assertEqual(app._ap_roots(args), list(app._AP_DEFAULT_ROOTS))
            self.assertEqual(app._AP_DEFAULT_ROOTS, ("~",))

    def test_station_rows_move_and_clamp(self) -> None:
        other = Stage("/y/.stage", "y", Snapshot(workers=[Worker("other", DONE)]), state="idle")
        gs = GlobalSnapshot(stages=[self.stage, other])   # both idle: the idle heading is stop 0, then the open projects
        ui = UIState(gopen_keys={"p:/x", "p:/y"})
        for key in ("up", "k", "home"):
            app._ap_global_key(ui, key, gs, 30)
        self.assertEqual(ui.gsel, 0)
        app._ap_global_key(ui, "down", gs, 30)
        app._ap_global_key(ui, "j", gs, 30)
        self.assertEqual(ui.gsel, 2)
        app._ap_global_key(ui, "pgdn", gs, 30)
        self.assertEqual(ui.gsel, 4)
        app._ap_global_key(ui, "pgup", gs, 30)
        self.assertEqual(ui.gsel, 0)
        app._ap_global_key(ui, "end", gs, 30)
        self.assertEqual(ui.gsel, 4)
        self.assertEqual(app._ap_global_key(ui, "left", gs, 30), "")
        self.assertEqual(ui.gsel, 4)

    def test_enter_and_space_open_the_station_and_esc_returns(self) -> None:
        other = Stage("/y/.stage", "y", Snapshot(workers=[Worker("other", DONE)]), state="idle")
        gs = GlobalSnapshot(stages=[self.stage, other])
        ui = UIState(gsel=4, gopen_keys={"p:/x", "p:/y"}, gfolded={self.stage.path, other.path})   # projects open, both rows shut
        self.assertEqual(app._ap_global_key(ui, "space", gs, 30), "")
        self.assertEqual((ui.gpage, ui.gstage, ui.view), ("sess", other.path, "overview"))
        app._ap_global_key(ui, "esc", gs, 30)
        self.assertEqual((ui.gpage, ui.view), ("", "overview"))
        ui.gsel = 2
        app._ap_global_key(ui, "space", gs, 30)
        self.assertEqual((ui.gpage, ui.gstage), ("sess", self.stage.path))
        app._ap_global_key(ui, "backspace", gs, 30)
        self.assertEqual(ui.gpage, "")

    def test_global_keys_keep_quit_all_mute_and_quota(self) -> None:
        ui = UIState()
        self.assertEqual(app._ap_global_key(ui, "a", self.gs, 30), "toggle_all")
        self.assertEqual(app._ap_global_key(ui, "q", self.gs, 30), "quit")
        self.assertEqual(app._ap_global_key(ui, "ctrl-c", self.gs, 30), "quit")
        app._ap_global_key(ui, "m", self.gs, 30)
        self.assertTrue(ui.muted)
        app._ap_global_key(ui, "u", self.gs, 30)
        self.assertEqual(ui.view, "quota")
        app._ap_global_key(ui, "u", self.gs, 30)
        self.assertEqual(ui.view, "overview")
        app._ap_global_key(ui, "u", self.gs, 30)
        app._ap_global_key(ui, "esc", self.gs, 30)
        self.assertEqual(ui.view, "overview")

    def test_milestone_and_review_pages_still_scroll_and_go_back(self) -> None:
        from model import Milestone
        self.stage.snap.milestones = [Milestone(1, "m")]
        for page in ("milestones", "reviews"):
            ui = UIState(gpage=page, gstage=self.stage.path)
            app._ap_global_key(ui, "down", self.gs, 30)
            app._ap_global_key(ui, "pgdn", self.gs, 30)
            self.assertEqual(ui.scroll, 1 + 22)
            app._ap_global_key(ui, "pgup", self.gs, 30)
            self.assertEqual(ui.scroll, 1)
            app._ap_global_key(ui, "esc", self.gs, 30)
            self.assertEqual(ui.gpage, "")

    def test_global_selection_follow_and_clamp(self) -> None:
        stage2 = Stage("/y/.stage", "y", Snapshot(workers=[Worker("other", DONE)]))
        gs = GlobalSnapshot(stages=[self.stage, stage2])
        ui = UIState()
        rows = global_rows(gs, ui)
        selected = rows[1]
        ui.gsel = 1
        replacement = GlobalSnapshot(stages=[stage2, self.stage])
        app._ap_global_follow_selection(ui, replacement, selected)
        self.assertEqual(global_rows(replacement, ui)[ui.gsel], selected)
        app._ap_global_follow_selection(ui, GlobalSnapshot(), selected)
        self.assertEqual(ui.gsel, 0)

    def test_global_stage_snap_copies_quota(self) -> None:
        self.gs.quota_ok = True
        ui = UIState(gpage="stage", gstage=self.stage.path)
        snap = app._ap_global_stage_snap(self.gs, ui)
        self.assertIs(snap, self.stage.snap)
        self.assertTrue(snap.quota_ok)

    def test_global_animation_uses_activity_level(self) -> None:
        quiet = GlobalSnapshot(stages=[Stage("/quiet", "quiet", Snapshot(
            workers=[Worker("w", "running", quiet_s=90)]), live=1)])
        warm = GlobalSnapshot(stages=[Stage("/warm", "warm", Snapshot(
            workers=[Worker("w", "running", quiet_s=30)]), live=1)])
        self.assertFalse(app._ap_animating(quiet, UIState(), 100))
        self.assertTrue(app._ap_animating(warm, UIState(), 100))

    def test_open_page_returns_to_tree_when_stage_or_worker_disappears(self) -> None:
        ui = UIState(gpage="stage", gstage=self.stage.path, view="detail", scroll=8)
        self.assertFalse(app._ap_global_validate_page(ui, GlobalSnapshot(), ""))
        self.assertEqual((ui.gpage, ui.view, ui.scroll), ("", "overview", 0))
        ui.gpage, ui.gstage, ui.view, ui.scroll = "worker", self.stage.path, "detail", 8
        self.assertFalse(app._ap_global_validate_page(ui, self.gs, "gone"))
        self.assertEqual((ui.gpage, ui.view, ui.scroll), ("", "overview", 0))

    def test_single_stage_follow_preserves_name_and_clamps_when_missing(self) -> None:
        snap = Snapshot(workers=[Worker("a", DONE), Worker("b", DONE)])
        ui = UIState(sel=1)
        self.assertEqual(app._ap_follow_selection(ui, "b", snap), "b")
        self.assertEqual(ui.sel, 1)
        self.assertEqual(app._ap_follow_selection(ui, "gone", snap), "b")
        self.assertEqual(app._ap_follow_selection(ui, "gone", Snapshot()), "")
        self.assertEqual(ui.sel, 0)

    def test_stage_events_have_label_prefix_and_first_sight_is_silent(self) -> None:
        old = GlobalSnapshot(stages=[Stage("/x/.stage", "Project X",
                                           Snapshot(workers=[Worker("w", "running")]))])
        new = GlobalSnapshot(stages=[Stage("/x/.stage", "Project X", Snapshot(
            workers=[Worker("w", DONE)]))])
        self.assertEqual(app._ap_global_events(None, new), [])
        events = app._ap_global_events(old, new)
        self.assertEqual([(event.summary, event.body) for event in events],
                         [("Project X: ✔ w finished", "")])
        old.all_history = True
        self.assertEqual(app._ap_global_events(old, new), [])

    def test_global_detail_load_uses_stage_path_worker_and_tab(self) -> None:
        class DetailCollector:
            def __init__(self) -> None:
                self.call = None

            def detail(self, path: str, name: str, tab: str) -> list[str]:
                self.call = path, name, tab
                return ["brief body"]

        collector = DetailCollector()
        ui = UIState(tab="brief")
        app._ap_load_global_detail(ui, collector, self.stage.path, "w")
        self.assertEqual((collector.call, ui.detail_text),
                         (("/x/.stage", "w", "brief"), ["brief body"]))


if __name__ == "__main__":
    unittest.main()
