"""Project groups and launch chains in the overview."""
from __future__ import annotations

import unittest

from test_session_rows import NOW, keys, plain_lines, session

import app
from model import RUNNING, GlobalSnapshot, LogState, Stage, Step, UIState, Worker
from render import _rd_g_rows, global_rows, render_global
from widgets import _wd_SGR_RE, _wd_width


def stage(sid: str, root: str = "/p/a", tag: str = "CC·W", parent: str = "", state: str = "active") -> Stage:
    st = session(f"{root}#{sid}", root.rsplit("/", 1)[-1], [], tag=tag,
                 name=sid, solo=True, state=state)
    st.dir, st.parent, st.session_id = root, parent, sid
    return st


class ProjectGroups(unittest.TestCase):
    def test_project_groups_mix_harnesses_and_always_have_headings(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage("one"), stage("two", tag="CDX"), stage("three", "/p/b", "")])
        rows = global_rows(gs, UIState())
        self.assertEqual([name for _, kind, name in rows if kind == "group"], ["p:/p/a", "p:/p/b"])
        ui = UIState(gsel=0)
        keys(ui, gs, "enter")
        self.assertEqual([p for p, k, _ in global_rows(gs, ui) if k == "stage"], ["/p/b#three"])
        keys(ui, gs, "right")
        self.assertEqual(len([k for _, k, _ in global_rows(gs, ui) if k == "stage"]), 3)

    def test_tags_app_and_now_are_on_one_session_row(self) -> None:
        st = stage("desk", tag="CDX")
        st.app, st.tmux = True, "secret-tmux-name"
        st.doing = [(NOW, "Edit renderer")]
        gs = GlobalSnapshot(now=NOW, stages=[st])
        lines = plain_lines(gs, UIState())
        row = next(line for line in lines if "desk" in line)
        self.assertIn("CDX", row)
        self.assertNotIn(st.tmux, "\n".join(lines))

    def test_launch_chain_has_multiple_levels_and_filter_removes_parent(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage("grand", parent="child"), stage("child", parent="root"), stage("root")])
        rows = _rd_g_rows(gs, UIState())
        self.assertEqual([(r.stage.session_id, r.depth) for r in rows], [("root", 0), ("child", 1), ("grand", 2)])
        self.assertEqual([(r.stage.session_id, r.depth) for r in _rd_g_rows(gs, UIState(gquery="grand"))], [("grand", 0)])

    def test_cross_project_session_lists_under_its_own_project(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage("launcher"), stage("child", "/p/b", parent="launcher")])
        rows = global_rows(gs, UIState())
        self.assertIn(("/p/b#child", "stage", ""), rows)
        self.assertNotIn("/p/a#child", "\n".join(plain_lines(gs, UIState())))

    def test_cycles_and_missing_parent_remain_roots(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage("a", parent="b"), stage("b", parent="a"), stage("c", parent="missing")])
        rows = _rd_g_rows(gs, UIState())
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r.depth == 0 for r in rows))

    def test_finished_fold_per_project_opens_every_session(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage(str(i), state="closed") for i in range(15)] + [stage("other", "/p/b", state="closed")])
        ui = UIState()
        rows = global_rows(gs, ui)
        folds = [r for r in rows if r[1] == "more"]
        self.assertEqual(len(folds), 2)
        self.assertFalse(any(k == "stage" for _, k, _ in rows))
        self.assertIn("finished 15", "\n".join(plain_lines(gs, ui)))
        ui.gsel = rows.index(folds[0])
        keys(ui, gs, "enter")
        self.assertEqual(sum(k == "stage" for _, k, _ in global_rows(gs, ui)), 15)
        keys(ui, gs, "left")
        self.assertFalse(any(k == "stage" for _, k, _ in global_rows(gs, ui)))

    def test_project_counts_are_local_to_sections_and_no_harness_heading(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage("desk"), stage("old", state="closed")])
        lines = plain_lines(gs, UIState())
        self.assertTrue(any("a" in line and "1 live" in line for line in lines))
        self.assertTrue(any("1 live · 1 done" in line for line in lines))
        self.assertFalse(any("1 live · 1 finished" in line for line in lines))
        self.assertFalse(any("▾ CC·W" in line for line in lines))

    def test_mixed_project_sections_share_one_heading_and_one_finished_fold(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage("one"), stage("two"), stage("rest", state="idle"),
                                            stage("old", state="closed"), stage("older", state="closed")])
        ui = UIState()
        lines = plain_lines(gs, ui)
        headings = [line for line in lines if "2 live · 1 idle · 2 done" in line]
        self.assertEqual(len(headings), 1)
        self.assertNotIn("3 live · 2 finished", "\n".join(lines))
        self.assertEqual(sum(kind == "more" for _, kind, _ in global_rows(gs, ui)), 1)
        ui.gopen.add(("", "more", "finished:/p/a"))
        opened = plain_lines(gs, ui)
        self.assertEqual(sum("finished 2" in line for line in opened), 1)
        self.assertEqual(sum(kind == "stage" for _, kind, _ in global_rows(gs, ui)), 5)

    def test_a_folded_launcher_does_not_hide_the_child_project(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage("launcher"), stage("child", "/p/b", parent="launcher")])
        ui = UIState(gshut_keys={"s:launcher"})
        lines = plain_lines(gs, ui)
        self.assertTrue(any("child" in line for line in lines))

    def test_finished_fold_has_a_mouse_target_and_keeps_page_reachable(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage("old", state="closed")])
        ui = UIState(gsel=0)
        render_global(gs, ui, 120, 40, NOW)
        hit = next(h for h in ui.hits if h[4:] == ("nav", "0"))
        self.assertEqual(app._ap_pointer_keys(ui, f"click:5:{hit[0]}", gs, None, NOW), ["enter"])
        keys(ui, gs, "right")
        ui.gsel = global_rows(gs, ui).index(("/p/a#old", "stage", ""))
        keys(ui, gs, "space")
        self.assertEqual((ui.gpage, ui.gstage), ("sess", "/p/a#old"))

    def test_one_now_per_live_session_and_worker_detail_stays_reachable(self) -> None:
        worker = Worker("builder", RUNNING, model="luna", effort="medium", start=NOW - 60,
                        log=LogState(steps=[Step(NOW - 2, "tool", "edit", "unique action")]))
        st = stage("desk")
        st.solo = False
        st.doing = [(NOW - 2, "session action")]
        st.snap.workers = [worker]
        gs = GlobalSnapshot(now=NOW, stages=[st])
        ui = UIState()
        lines = plain_lines(gs, ui)
        self.assertEqual(sum("session action" in line for line in lines), 1)
        shut = plain_lines(gs, UIState(gshut_keys={"s:desk"}))
        self.assertEqual(sum("session action" in line for line in shut), 1)   # a shut live session keeps its NOW line (the user 2026-10-24)
        ui.gsel = global_rows(gs, ui).index((st.path, "stage", ""))
        keys(ui, gs, "right")
        ui.gsel = global_rows(gs, ui).index((st.path, "worker", "builder"))
        keys(ui, gs, "enter")
        self.assertIn((st.path, "worker", "builder"), ui.gopen)
        for width in (40, 57, 120):
            frame = render_global(gs, ui, width, 40, NOW)
            self.assertTrue(all(_wd_width(_wd_SGR_RE.sub("", line)) == width for line in frame))
        keys(ui, gs, "space")                               # Space (a double click) opens the worker page
        self.assertEqual((ui.gpage, ui.gstage, ui.gworker), ("wk", st.path, "builder"))

    def test_self_cycle_and_three_node_cycle_leave_every_row_visible(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[stage("self", parent="self"), stage("a", parent="c"),
                                            stage("b", parent="a"), stage("c", parent="b")])
        rows = _rd_g_rows(gs, UIState())
        self.assertEqual({r.stage.session_id for r in rows}, {"self", "a", "b", "c"})
        self.assertTrue(all(r.depth == 0 and not r.launcher for r in rows))
