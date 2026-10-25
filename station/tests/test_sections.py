"""The overview's sections and honest labels, from hand-built sessions: activity order, solo, expandable rows, the ↳ line."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from test_session_rows import NOW, keys, line_with, plain_lines, session

from model import DONE, RUNNING, GlobalSnapshot, Stage, UIState, Worker
from render import _rd_g_path_line, global_rows

HOME = os.path.expanduser("~")


def gs_of(*stages: Stage) -> GlobalSnapshot:
    return GlobalSnapshot(now=NOW, stages=list(stages))


class ActivityOrder(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_each_section_is_sorted_by_activity_newest_first_whatever_the_input_order(self) -> None:
        def at(path: str, state: str, activity: float, last: float) -> Stage:
            st = session(path, path[1:], [Worker("w", RUNNING, start=NOW - 10)] if state == "active" else [],
                         last=last, state=state)
            st.activity = activity
            return st
        gs = gs_of(at("/c1", "closed", NOW - 7000, NOW - 10), at("/i1", "idle", NOW - 400, NOW - 9),
                   at("/a1", "active", NOW - 200, NOW - 8), at("/i2", "idle", NOW - 100, NOW - 900),
                   at("/c2", "closed", NOW - 100, NOW - 9000), at("/a2", "active", NOW - 5, NOW - 7))
        opened = UIState(gopen_keys={"p:/i1", "p:/i2"})   # idle projects start shut
        self.assertEqual([r[0] for r in global_rows(gs, opened) if r[1] == "stage"],
                         ["/a2", "/a1", "/i2", "/i1"])

    def test_the_closed_section_uses_the_activity_epoch_not_the_older_last_activity(self) -> None:
        older = session("/c1", "c1", [], last=NOW - 10, state="closed")
        newer = session("/c2", "c2", [], last=NOW - 9000, state="closed")
        older.activity, newer.activity = NOW - 7000, NOW - 100
        ui = UIState(gopen={("", "more", "finished:/c1"), ("", "more", "finished:/c2")})
        self.assertEqual([r[0] for r in global_rows(gs_of(older, newer), ui) if r[1] == "stage"], ["/c2", "/c1"])


class AgeText(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_idle_rows_show_idle_and_their_age_and_closed_rows_show_closed(self) -> None:
        idle = session("/i", "idler", [], last=NOW - 600, state="idle")
        closed = session("/c", "shutter", [], last=NOW - 3600, lead_state="gone", state="closed")
        lines = plain_lines(gs_of(idle, closed), UIState(gopen_keys={"p:/i"}))
        self.assertIn("◐", line_with(lines, "idler"))
        self.assertIn("finished 1", "\n".join(lines))

    def test_active_rows_show_no_age(self) -> None:
        live = session("/a", "runner", [Worker("w", RUNNING, start=NOW - 30)], state="active")
        line = line_with(plain_lines(gs_of(live), UIState()), "runner")
        self.assertNotIn("idle ·", line)
        self.assertNotIn("closed ·", line)


class SoloAndExpandable(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_a_live_session_with_no_workers_says_solo_no_workers(self) -> None:
        lone = session("/s", "lone", [], state="active", solo=True, name="lone-one", tag="CC·W")
        self.assertIn("◇", line_with(plain_lines(gs_of(lone), UIState()), "lone-one"))

    def test_a_closed_session_is_never_solo(self) -> None:
        shut = session("/s", "shut", [], state="closed", lead_state="gone", solo=True, last=NOW - 3600)
        text = "\n".join(plain_lines(gs_of(shut), UIState()))
        self.assertNotIn("solo", text)
        self.assertIn("finished 1", text)

    def test_a_solo_flag_is_ignored_once_the_session_has_workers(self) -> None:
        busy = session("/s", "busy", [Worker("w", RUNNING, start=NOW - 30)], state="active", solo=True)
        text = "\n".join(plain_lines(gs_of(busy), UIState()))
        self.assertNotIn("solo", text)
        self.assertIn("1 live", text)
        self.assertIn("◆", line_with(plain_lines(gs_of(busy), UIState()), "busy"))   # live, open by default

    def test_a_session_whose_workers_have_all_finished_is_still_expandable(self) -> None:
        wrapup = session("/s", "wrapup", [Worker("d", DONE, start=NOW - 90, end=NOW - 80)], state="idle")
        gs = gs_of(wrapup)
        ui = UIState(gopen_keys={"p:/s"})                         # the idle project is open, the session shut
        ui.gsel = global_rows(gs, ui).index(("/s", "stage", ""))
        self.assertNotIn(("/s", "task", "d"), global_rows(gs, ui))
        keys(ui, gs, "enter")
        self.assertIn(("/s", "task", "d"), global_rows(gs, ui))          # its finished task is a line
        self.assertNotIn(("/s", "worker", "d"), global_rows(gs, ui))
        ui.gsel = global_rows(gs, ui).index(("/s", "task", "d"))
        keys(ui, gs, "enter")
        self.assertIn(("/s", "worker", "d"), global_rows(gs, ui))


class PathLine(unittest.TestCase):
    """↳ shows only when the session started elsewhere than its project label, or its workers run elsewhere."""

    def setUp(self) -> None:
        env = patch.dict(os.environ, {"HOME": HOME}, clear=True)  # keep the HOME the expected paths were built from
        env.start()
        self.addCleanup(env.stop)

    def row(self, path: str = "/r/proj/.stage", **fields) -> Stage:
        st = session(path, "proj", [], state="active", lead_state="live")
        st.session_name = "proj-one"
        for key, value in fields.items():
            setattr(st, key, value)
        return st

    def arrow_lines(self, st: Stage) -> list[str]:
        # the ↳ line is no longer drawn in the console tree; the helper keeps its text for the plain view
        from widgets import _wd_SGR_RE
        line = _wd_SGR_RE.sub("", _rd_g_path_line(st, 100))
        return [line] if "↳" in line else []

    def test_no_line_when_the_session_started_and_works_in_its_project(self) -> None:
        st = self.row(cwd="/r/proj", workdirs=["/r/proj", "/r/proj/.stage"])
        self.assertEqual(self.arrow_lines(st), [])

    def test_started_elsewhere_shows_only_the_started_part(self) -> None:
        st = self.row(cwd=os.path.join(HOME, "x"))
        self.assertEqual([ln.strip() for ln in self.arrow_lines(st)], ["↳ started in ~/x"])

    def test_workers_elsewhere_show_only_the_workers_part_with_both_dirs(self) -> None:
        st = self.row(cwd="/r/proj", workdirs=[os.path.join(HOME, "Projects/y"), os.path.join(HOME, "Projects/z")])
        self.assertEqual([ln.strip() for ln in self.arrow_lines(st)], ["↳ workers in ~/Projects/y, ~/Projects/z"])

    def test_both_parts_are_joined_and_more_than_two_dirs_fold_into_a_count(self) -> None:
        dirs = [os.path.join(HOME, d) for d in ("a", "b", "c", "d")]
        st = self.row(cwd=os.path.join(HOME, "start"), workdirs=dirs)
        self.assertEqual([ln.strip() for ln in self.arrow_lines(st)],
                         ["↳ started in ~/start · workers in ~/a, ~/b +2"])

    def test_the_helper_is_not_a_selectable_tree_row(self) -> None:
        st = self.row(cwd=os.path.join(HOME, "x"))
        gs = gs_of(st)
        self.assertIn("↳", _rd_g_path_line(st, 100))
        self.assertNotIn("↳", "\n".join(plain_lines(gs, UIState())))
        self.assertEqual(global_rows(gs, UIState()), [("", "group", "p:/r/proj"), ("/r/proj/.stage", "stage", "")])

    def test_the_project_root_drops_a_hash_suffix_and_the_stage_folder(self) -> None:
        st = self.row(path="/r/proj/.stage#lead-7", cwd="/r/proj")
        self.assertEqual(self.arrow_lines(st), [])


if __name__ == "__main__":
    unittest.main()
