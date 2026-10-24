"""C6b: the control center's keys and mouse. State only: every ccactions entry point is faked, so no real process."""
from __future__ import annotations

import contextlib
import types
import unittest
from unittest import mock

import cckeys
from ccactions import Target
from cckeys import cc_key, cc_pointer
from ccpane import PANE_TABS
from ccstrip import STRIP_TABS
from model import RUNNING, GlobalSnapshot, Snapshot, Stage, UIState, Worker

NOW = 1_700_000_000.0
SEL_WORKER = ("/stage/one", "worker", "w1")
SEL_LEAD = ("/stage/one", "stage", "")
SEL_HEAD = ("", "milestone", "")
# far above /proc/sys/kernel/pid_max: target_of's start read finds nothing, so the fixtures stay inert
WORKER_PID = 4_242_424
LEAD_PID = 4_242_425


def _worker() -> Worker:
    return Worker(name="w1", state=RUNNING, pid=WORKER_PID, harness="codex", native_id="n1",
                  resume_cmd="codex resume n1")


def _stage() -> Stage:
    stage = Stage(path="/stage/one", label="proj", lead_pid=LEAD_PID, lead_state="live", cwd="/stage/one",
                  session_id="s1", tmux="t1", resume_cmd="claude --resume s1", tag="CC·W")
    stage.snap = Snapshot(workers=[_worker()])
    return stage


def _gs(with_worker: bool = True) -> GlobalSnapshot:
    stage = _stage()
    if not with_worker:
        stage.snap = Snapshot(workers=[])
    return GlobalSnapshot(now=NOW, stages=[stage])


def _ui(**kw: object) -> UIState:
    """A UIState that the last control center frame drew (cc_active True)."""
    kw.setdefault("cc", True)
    kw.setdefault("cc_pane_open", True)
    kw.setdefault("allow_actions", True)
    return UIState(cc_active=True, **kw)


@contextlib.contextmanager
def _actions(ok: bool = True, why: str = "", result: str = "done"):
    """Fake ccactions.can / confirm_text / run as bound in cckeys; yields the run fake."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(cckeys, "can", return_value=(ok, why)))
        stack.enter_context(mock.patch.object(cckeys, "confirm_text", return_value="Q? (y/n)"))
        run = stack.enter_context(mock.patch.object(cckeys, "run", return_value=result))
        yield run


class GatingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.arm_s = cckeys._CXK_ARM_S
        cckeys._CXK_ARM_S = 0.0
        cckeys._CXK_ARMED.update(value=None, t=0.0)
        cckeys._CXK_TARGET = None

    def tearDown(self) -> None:
        cckeys._CXK_ARM_S = self.arm_s

    def test_classic_frame_handles_nothing(self) -> None:
        keys = ["tab", "esc", "up", "down", "j", "k", "pgup", "pgdn", "home", "1", "2", "3", "4", "5", "6",
                "[", "]", "e", "v", "n", "s", "K", "r", "y", "x", "enter", "space", "?"]
        for key in keys:
            self.assertIsNone(cc_key(UIState(), key, _gs(), 40, SEL_WORKER), key)

    def test_confirm_is_cancelled_on_the_classic_frame(self) -> None:
        # The classic tree never draws the question, so a y there must not act on it.
        ui = UIState(cc_active=False, cc_confirm=("stop", "/stage/one|w1", "Q? (y/n)"))
        with _actions(result="stopped w1") as run:
            self.assertEqual(cc_key(ui, "y", _gs(), 40, None), "")
        self.assertIsNone(ui.cc_confirm)
        self.assertEqual(run.call_count, 0)

    def test_action_keys_are_inert_when_actions_are_disabled(self) -> None:
        gs = _gs()
        for key in ("s", "K", "r", "n", "y"):
            with self.subTest(key=key):
                ui = _ui(allow_actions=False,
                         cc_confirm=("stop", "/stage/one|w1", "Q? (y/n)") if key == "y" else None)
                with mock.patch.object(cckeys, "can") as can, \
                        mock.patch.object(cckeys, "confirm_text") as confirm_text, \
                        mock.patch.object(cckeys, "run") as run, \
                        mock.patch.object(cckeys, "spawn_run") as spawn_run:
                    self.assertEqual(cc_key(ui, key, gs, 40, SEL_WORKER), "")
                can.assert_not_called()
                confirm_text.assert_not_called()
                self.assertEqual(run.call_count, 0)
                spawn_run.assert_not_called()
                self.assertIsNone(ui.cc_confirm)

    def test_confirm_ignores_ctrl_c(self) -> None:
        ui = _ui(cc_confirm=("stop", "/stage/one|w1", "Q? (y/n)"))
        with _actions() as run:
            self.assertIsNone(cc_key(ui, "ctrl-c", _gs(), 40, None))
        self.assertEqual(ui.cc_confirm, ("stop", "/stage/one|w1", "Q? (y/n)"))
        self.assertEqual(run.call_count, 0)

    def test_spawn_flow_is_entered_on_the_classic_frame(self) -> None:
        ui = UIState(cc_active=False, cc_spawn=True, allow_actions=True)
        with mock.patch.object(cckeys, "spawn_key", return_value="") as spawn_key:
            self.assertEqual(cc_key(ui, "down", _gs(), 40, SEL_WORKER), "")
        spawn_key.assert_called_once_with(ui, "down", mock.ANY, SEL_WORKER)

    def test_spawn_key_none_falls_through(self) -> None:
        ui = _ui(cc_spawn=True)
        with mock.patch.object(cckeys, "spawn_key", return_value=None):
            self.assertIsNone(cc_key(ui, "q", _gs(), 40, None))


class FocusTests(unittest.TestCase):
    def test_tab_cycles_table_pane_strip(self) -> None:
        ui = _ui()
        seen = []
        for _ in range(4):
            self.assertEqual(cc_key(ui, "tab", _gs(), 40, SEL_WORKER), "")
            seen.append(ui.cc_focus)
        self.assertEqual(seen, ["pane", "strip", "table", "pane"])

    def test_tab_skips_closed_pane(self) -> None:
        ui = _ui(cc_pane_open=False)
        seen = []
        for _ in range(4):
            cc_key(ui, "tab", _gs(), 40, SEL_WORKER)
            seen.append(ui.cc_focus)
        self.assertEqual(seen, ["strip", "table", "strip", "table"])

    def test_unknown_focus_restarts_at_table(self) -> None:
        ui = _ui(cc_focus="junk")
        cc_key(ui, "tab", _gs(), 40, None)
        self.assertEqual(ui.cc_focus, "pane")

    def test_esc_returns_to_the_table_only_from_pane_or_strip(self) -> None:
        for focus in ("pane", "strip"):
            ui = _ui(cc_focus=focus)
            self.assertEqual(cc_key(ui, "esc", _gs(), 40, None), "")
            self.assertEqual(ui.cc_focus, "table")
        ui = _ui(cc_focus="table")
        self.assertEqual(cc_key(ui, "esc", _gs(), 40, None), "")
        self.assertFalse(ui.cc_pane_open)

    def test_table_still_owns_the_tree_keys(self) -> None:
        for key in ("up", "down", "j", "k", "enter", "space", "left", "right"):
            self.assertIsNone(cc_key(_ui(), key, _gs(), 40, SEL_WORKER), key)


class ScrollTests(unittest.TestCase):
    def test_pane_scroll(self) -> None:
        ui = _ui(cc_focus="pane")
        cc_key(ui, "down", _gs(), 40, None)
        self.assertEqual(ui.cc_pane_scroll, 1)
        cc_key(ui, "j", _gs(), 40, None)
        self.assertEqual(ui.cc_pane_scroll, 2)
        cc_key(ui, "up", _gs(), 40, None)
        self.assertEqual(ui.cc_pane_scroll, 1)
        cc_key(ui, "pgdn", _gs(), 40, None)
        self.assertEqual(ui.cc_pane_scroll, 11)
        cc_key(ui, "pgup", _gs(), 40, None)
        self.assertEqual(ui.cc_pane_scroll, 1)

    def test_closed_pane_ignores_scroll_and_escape_opens_closes(self) -> None:
        ui = _ui(cc_pane_open=False, cc_focus="table", cc_pane_scroll=3)
        for key in ("pgup", "pgdn", "up", "down"):
            cc_key(ui, key, _gs(), 40, SEL_WORKER)
        self.assertEqual(ui.cc_pane_scroll, 3)
        self.assertEqual(cc_key(ui, "esc", _gs(), 40, None), None)
        self.assertEqual(cc_key(ui, "d", _gs(), 40, None), "")
        self.assertTrue(ui.cc_pane_open)
        ui.cc_focus = "pane"
        self.assertEqual(cc_key(ui, "esc", _gs(), 40, None), "")
        self.assertFalse(ui.cc_pane_open)
        self.assertEqual(ui.cc_focus, "table")

    def test_d_toggles_the_pane_and_digits_are_left_to_the_filters(self) -> None:
        ui = _ui(cc_pane_open=False)
        self.assertEqual(cc_key(ui, "d", _gs(), 40, None), "")
        self.assertTrue(ui.cc_pane_open)
        ui.cc_focus = "pane"
        cc_key(ui, "d", _gs(), 40, None)
        self.assertFalse(ui.cc_pane_open)
        self.assertEqual(ui.cc_focus, "table")
        for key in "123456":    # the user 2026-10-24: 1-4 are the filters again; the pane's tabs are left/right
            ui.cc_pane_open = False
            self.assertIsNone(cc_key(ui, key, _gs(), 40, None), key)
            self.assertFalse(ui.cc_pane_open)

    def test_pane_scroll_never_goes_negative(self) -> None:
        ui = _ui(cc_focus="pane")
        for key in ("up", "k", "pgup"):
            self.assertEqual(cc_key(ui, key, _gs(), 40, None), "")
            self.assertEqual(ui.cc_pane_scroll, 0)

    def test_pane_home_returns_to_the_top(self) -> None:
        ui = _ui(cc_focus="pane", cc_pane_scroll=7)
        self.assertEqual(cc_key(ui, "home", _gs(), 40, None), "")
        self.assertEqual(ui.cc_pane_scroll, 0)

    def test_strip_scroll_up_goes_back(self) -> None:
        ui = _ui(cc_focus="strip")
        cc_key(ui, "up", _gs(), 40, None)
        self.assertEqual(ui.cc_strip_scroll, 1)
        cc_key(ui, "k", _gs(), 40, None)
        self.assertEqual(ui.cc_strip_scroll, 2)
        cc_key(ui, "pgup", _gs(), 40, None)
        self.assertEqual(ui.cc_strip_scroll, 12)
        cc_key(ui, "down", _gs(), 40, None)
        self.assertEqual(ui.cc_strip_scroll, 11)
        cc_key(ui, "pgdn", _gs(), 40, None)
        self.assertEqual(ui.cc_strip_scroll, 1)

    def test_strip_scroll_stops_at_the_newest(self) -> None:
        ui = _ui(cc_focus="strip")
        for key in ("down", "j", "pgdn"):
            self.assertEqual(cc_key(ui, key, _gs(), 40, None), "")
            self.assertEqual(ui.cc_strip_scroll, 0)

    def test_strip_home_returns_to_the_newest(self) -> None:
        ui = _ui(cc_focus="strip", cc_strip_scroll=9)
        self.assertEqual(cc_key(ui, "home", _gs(), 40, None), "")
        self.assertEqual(ui.cc_strip_scroll, 0)

    def test_scroll_keys_in_the_table_are_not_ours(self) -> None:
        for key in ("up", "down", "pgup", "pgdn", "home"):
            self.assertIsNone(cc_key(_ui(), key, _gs(), 40, SEL_WORKER), key)


class TabTests(unittest.TestCase):
    def test_left_and_right_step_the_pane_tabs_when_the_pane_has_focus(self) -> None:
        ui = _ui(cc_focus="pane", cc_pane_open=True, cc_tab=PANE_TABS[0], cc_pane_scroll=5)
        for name in PANE_TABS[1:]:
            self.assertEqual(cc_key(ui, "right", _gs(), 40, None), "")
            self.assertEqual((ui.cc_tab, ui.cc_pane_scroll), (name, 0))
            ui.cc_pane_scroll = 5
        cc_key(ui, "right", _gs(), 40, None)
        self.assertEqual(ui.cc_tab, PANE_TABS[0])          # wraps
        cc_key(ui, "left", _gs(), 40, None)
        self.assertEqual(ui.cc_tab, PANE_TABS[-1])

    def test_left_and_right_in_the_table_still_fold_the_row(self) -> None:
        for key in ("left", "right"):
            self.assertIsNone(cc_key(_ui(cc_pane_open=True), key, _gs(), 40, SEL_WORKER), key)

    def test_brackets_cycle_the_strip_tabs_and_wrap(self) -> None:
        ui = _ui(cc_strip="Usage", cc_strip_scroll=4)   # Usage is the first tab
        self.assertEqual(cc_key(ui, "[", _gs(), 40, None), "")
        self.assertEqual(ui.cc_strip, "Metrics")
        self.assertEqual(ui.cc_strip_scroll, 0)
        self.assertEqual(cc_key(ui, "]", _gs(), 40, None), "")
        self.assertEqual(ui.cc_strip, "Usage")

    def test_bracket_from_an_unknown_tab_lands_on_a_real_one(self) -> None:
        ui = _ui(cc_strip="junk")
        cc_key(ui, "]", _gs(), 40, None)
        self.assertIn(ui.cc_strip, STRIP_TABS)

    def test_E_toggles_the_strip_and_e_is_left_to_expand_all(self) -> None:
        ui = _ui()
        self.assertEqual(cc_key(ui, "E", _gs(), 40, None), "")
        self.assertFalse(ui.cc_strip_open)
        cc_key(ui, "E", _gs(), 40, None)
        self.assertTrue(ui.cc_strip_open)
        self.assertIsNone(cc_key(ui, "e", _gs(), 40, None))

    def test_brackets_say_why_when_the_strip_is_hidden(self) -> None:
        ui = _ui(cc_strip_open=False, cc_strip="Events")
        self.assertEqual(cc_key(ui, "]", _gs(), 40, None), "")
        self.assertIn("E shows it", ui.note)
        self.assertEqual(ui.cc_strip, "Events")

    def test_v_returns_to_the_classic_view(self) -> None:
        ui = _ui()
        self.assertEqual(cc_key(ui, "v", _gs(), 40, None), "")
        self.assertFalse(ui.cc)
        self.assertIn("classic view", ui.note)
        self.assertIn("v", ui.note)

    def test_n_opens_the_spawn_picker(self) -> None:
        ui = _ui()
        gs = _gs()
        with mock.patch.object(cckeys, "spawn_open") as spawn_open:
            self.assertEqual(cc_key(ui, "n", gs, 40, SEL_WORKER), "")
        spawn_open.assert_called_once_with(ui, gs, SEL_WORKER)

    def test_pointer_actions_toggle_pane_and_worker_expansion(self) -> None:
        ui = _ui(cc_pane_open=False)
        cc_pointer(ui, "ccpane", "ignored")
        self.assertTrue(ui.cc_pane_open)
        cc_pointer(ui, "ccrow", "w1")
        self.assertIn("w1", ui.expanded)
        self.assertIn("w1", ui.pinned)
        cc_pointer(ui, "ccrow", "w1")
        self.assertNotIn("w1", ui.expanded)
        self.assertNotIn("w1", ui.pinned)


class ActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.arm_s = cckeys._CXK_ARM_S
        cckeys._CXK_ARM_S = 0.0
        cckeys._CXK_ARMED.update(value=None, t=0.0)
        cckeys._CXK_TARGET = None

    def tearDown(self) -> None:
        cckeys._CXK_ARM_S = self.arm_s

    def test_stop_a_worker_asks_first(self) -> None:
        ui = _ui()
        with _actions():
            self.assertEqual(cc_key(ui, "s", _gs(), 40, SEL_WORKER), "")
        self.assertIsNotNone(ui.cc_confirm)
        assert ui.cc_confirm is not None
        self.assertEqual(ui.cc_confirm[0], "stop")
        self.assertEqual(ui.cc_confirm[1], "/stage/one|w1")
        self.assertEqual(ui.cc_confirm[2], "Q? (y/n)")
        self.assertEqual(ui.note, "")

    def test_process_actions_are_disabled_by_default(self) -> None:
        ui = UIState(cc=True, cc_active=True)
        self.assertEqual(cc_key(ui, "s", _gs(), 40, SEL_WORKER), "")
        self.assertEqual(cc_key(ui, "n", _gs(), 40, SEL_WORKER), "")
        self.assertIsNone(ui.cc_confirm)
        self.assertFalse(ui.cc_spawn)

    def test_can_is_asked_about_the_selected_worker(self) -> None:
        with mock.patch.object(cckeys, "can", return_value=(True, "")) as can, \
                mock.patch.object(cckeys, "confirm_text", return_value="Q?"):
            cc_key(_ui(), "s", _gs(), 40, SEL_WORKER)
        action, target = can.call_args.args
        self.assertEqual(action, "stop")
        self.assertIsInstance(target, Target)
        self.assertEqual((target.kind, target.label, target.pid), ("worker", "w1", WORKER_PID))

    def test_kill_and_resume_use_the_same_confirm(self) -> None:
        for key, action in (("K", "kill"), ("r", "resume")):
            ui = _ui()
            with _actions():
                self.assertEqual(cc_key(ui, key, _gs(), 40, SEL_LEAD), "")
            assert ui.cc_confirm is not None
            self.assertEqual(ui.cc_confirm[0], action)
            self.assertEqual(ui.cc_confirm[1], "/stage/one|")

    def test_lead_row_targets_the_stage(self) -> None:
        ui = _ui()
        with _actions():
            cc_key(ui, "s", _gs(), 40, SEL_LEAD)
        with _actions() as run:
            cc_key(ui, "y", _gs(), 40, None)
        _action, target = run.call_args.args
        self.assertEqual((target.kind, target.label, target.pid), ("stage", "proj", LEAD_PID))

    def test_refusal_is_shown_and_no_confirm_opens(self) -> None:
        ui = _ui()
        with _actions(ok=False, why="send stop first"):
            self.assertEqual(cc_key(ui, "K", _gs(), 40, SEL_WORKER), "")
        self.assertEqual(ui.note, "send stop first")
        self.assertIsNone(ui.cc_confirm)

    def test_heading_row_has_nothing_to_act_on(self) -> None:
        ui = _ui()
        with _actions():
            self.assertEqual(cc_key(ui, "s", _gs(), 40, SEL_HEAD), "")
        self.assertEqual(ui.note, "no row selected")
        self.assertIsNone(ui.cc_confirm)

    def test_no_selection_has_nothing_to_act_on(self) -> None:
        ui = _ui()
        with _actions():
            self.assertEqual(cc_key(ui, "s", _gs(), 40, None), "")
        self.assertEqual(ui.note, "no row selected")
        self.assertIsNone(ui.cc_confirm)

    def test_gone_worker_row_is_refused(self) -> None:
        ui = _ui()
        with _actions():
            self.assertEqual(cc_key(ui, "s", _gs(with_worker=False), 40, SEL_WORKER), "")
        self.assertEqual(ui.note, "row is gone")
        self.assertIsNone(ui.cc_confirm)

    def test_y_runs_once_cancels_the_confirm_and_notes(self) -> None:
        ui = _ui()
        with _actions(result="stopped w1 (SIGTERM, pid 4242424)"):
            cc_key(ui, "s", _gs(), 40, SEL_WORKER)
        with _actions(result="stopped w1 (SIGTERM, pid 4242424)") as run:
            self.assertEqual(cc_key(ui, "y", _gs(), 40, None), "")
        self.assertEqual(run.call_count, 1)
        action, target = run.call_args.args
        self.assertEqual((action, target.label, target.pid), ("stop", "w1", WORKER_PID))
        self.assertEqual(ui.note, "stopped w1 (SIGTERM, pid 4242424)")
        self.assertIsNone(ui.cc_confirm)

    def test_any_other_key_cancels(self) -> None:
        for key in ("x", "n", "esc", "tab", "v"):
            ui = _ui()
            with _actions():
                cc_key(ui, "s", _gs(), 40, SEL_WORKER)
            with _actions() as run:
                self.assertEqual(cc_key(ui, key, _gs(), 40, None), "", key)
            self.assertEqual(ui.note, "cancelled", key)
            self.assertIsNone(ui.cc_confirm, key)
            self.assertEqual(run.call_count, 0, key)
            self.assertTrue(ui.cc, key)

    def test_a_row_that_vanishes_before_y_is_refused(self) -> None:
        ui = _ui(cc_confirm=("stop", "/stage/one|w1", "Q? (y/n)"))
        with _actions() as run:
            self.assertEqual(cc_key(ui, "y", _gs(with_worker=False), 40, None), "")
        self.assertEqual(ui.note, "row is gone")
        self.assertIsNone(ui.cc_confirm)
        self.assertEqual(run.call_count, 0)

    def test_a_stage_that_vanishes_before_y_is_refused(self) -> None:
        ui = _ui(cc_confirm=("stop", "/stage/one|w1", "Q? (y/n)"))
        with _actions() as run:
            self.assertEqual(cc_key(ui, "y", GlobalSnapshot(), 40, None), "")
        self.assertEqual(ui.note, "row is gone")
        self.assertEqual(run.call_count, 0)

    def test_changed_target_identity_cancels_action(self) -> None:
        ui = _ui()
        gs = _gs()
        with _actions():
            cc_key(ui, "s", gs, 40, SEL_WORKER)
        gs.stages[0].snap.workers[0].native_id = "restarted-session"
        with _actions() as run:
            self.assertEqual(cc_key(ui, "y", gs, 40, None), "")
        self.assertEqual(ui.note, "row changed, cancelled")
        self.assertIsNone(ui.cc_confirm)
        self.assertEqual(run.call_count, 0)


class ConfirmArmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.arm_s = cckeys._CXK_ARM_S
        cckeys._CXK_ARM_S = 0.35
        cckeys._CXK_ARMED.update(value=None, t=0.0)
        cckeys._CXK_TARGET = None

    def tearDown(self) -> None:
        cckeys._CXK_ARM_S = self.arm_s

    def test_y_immediately_after_arm_is_swallowed_then_accepted_after_delay(self) -> None:
        now = [10.0]
        ui = _ui()
        with mock.patch.object(cckeys.time, "monotonic", side_effect=lambda: now[0]), _actions() as run:
            cc_key(ui, "s", _gs(), 40, SEL_WORKER)
            self.assertEqual(cc_key(ui, "y", _gs(), 40, None), "")
            self.assertIsNotNone(ui.cc_confirm)
            self.assertEqual(run.call_count, 0)
            now[0] += 0.36
            self.assertEqual(cc_key(ui, "y", _gs(), 40, None), "")
        self.assertEqual(run.call_count, 1)

    def test_other_key_cancels_before_arm_delay(self) -> None:
        ui = _ui()
        with _actions():
            cc_key(ui, "s", _gs(), 40, SEL_WORKER)
        self.assertEqual(cc_key(ui, "x", _gs(), 40, None), "")
        self.assertEqual(ui.note, "cancelled")
        self.assertIsNone(ui.cc_confirm)

    def test_y_is_refused_when_the_classic_view_hides_the_question(self) -> None:
        ui = _ui()
        with _actions() as run:
            cc_key(ui, "s", _gs(), 40, SEL_WORKER)
            ui.cc_active = False        # a resize drew the classic tree: no question on screen
            with mock.patch.object(cckeys.time, "monotonic", return_value=cckeys._CXK_ARMED["t"] + 5.0):
                self.assertEqual(cc_key(ui, "y", _gs(), 40, None), "")
        self.assertEqual(run.call_count, 0)
        self.assertIsNone(ui.cc_confirm)
        self.assertIn("not visible", ui.note)

    def test_identity_comes_from_the_target_the_question_was_built_from(self) -> None:
        ui = _ui()
        starts = iter([100, 200, 200, 200])   # the pid is recycled after the question's target was read

        def fake_target(_stage, _worker):
            return types.SimpleNamespace(pid=7, start=next(starts), session_id="s", label="w1")

        with mock.patch.object(cckeys, "target_of", side_effect=fake_target), _actions() as run:
            cc_key(ui, "s", _gs(), 40, SEL_WORKER)
            with mock.patch.object(cckeys.time, "monotonic", return_value=cckeys._CXK_ARMED["t"] + 5.0):
                cc_key(ui, "y", _gs(), 40, None)
        self.assertEqual(run.call_count, 0)
        self.assertEqual(ui.note, "row changed, cancelled")

    def test_spawn_confirm_is_also_armed(self) -> None:
        now = [20.0]
        ui = _ui(cc_spawn=True)
        with mock.patch.object(cckeys.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(cckeys, "spawn_key", side_effect=lambda *_args: setattr(
                    ui, "cc_confirm", ("spawn", "/stage/one|", "Spawn?")) or ""), \
                mock.patch.object(cckeys, "spawn_run", return_value="spawned") as spawn_run:
            self.assertEqual(cc_key(ui, "enter", _gs(), 40, None), "")
            self.assertEqual(cc_key(ui, "y", _gs(), 40, None), "")
            self.assertIsNotNone(ui.cc_confirm)
            self.assertEqual(spawn_run.call_count, 0)
            now[0] += 0.36
            self.assertEqual(cc_key(ui, "y", _gs(), 40, None), "")
        spawn_run.assert_called_once()


class SpawnConfirmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.arm_s = cckeys._CXK_ARM_S
        cckeys._CXK_ARM_S = 0.0

    def tearDown(self) -> None:
        cckeys._CXK_ARM_S = self.arm_s

    def test_y_spawns_once(self) -> None:
        ui = _ui(cc_confirm=("spawn", "/stage/one|", "Spawn deepseek? (y/n)"))
        gs = _gs()
        with mock.patch.object(cckeys, "spawn_run", return_value="spawned deepseek (cc-abc123)") as spawn_run:
            self.assertEqual(cc_key(ui, "y", gs, 40, None), "")
        spawn_run.assert_called_once_with(ui, gs, "/stage/one|")
        self.assertEqual(ui.note, "spawned deepseek (cc-abc123)")
        self.assertIsNone(ui.cc_confirm)

    def test_other_key_cancels_the_pending_spawn(self) -> None:
        ui = _ui(cc_confirm=("spawn", "/stage/one|", "Spawn deepseek? (y/n)"))
        with mock.patch.object(cckeys, "spawn_run") as spawn_run, \
                mock.patch.object(cckeys, "spawn_cancel") as spawn_cancel:
            self.assertEqual(cc_key(ui, "esc", _gs(), 40, None), "")
        self.assertEqual(ui.note, "cancelled")
        self.assertIsNone(ui.cc_confirm)
        spawn_cancel.assert_called_once_with(ui)
        self.assertEqual(spawn_run.call_count, 0)

    def test_plain_confirm_cancel_never_touches_the_spawn_flow(self) -> None:
        ui = _ui(cc_confirm=("stop", "/stage/one|w1", "Q? (y/n)"))
        with mock.patch.object(cckeys, "spawn_cancel") as spawn_cancel:
            cc_key(ui, "x", _gs(), 40, None)
        self.assertEqual(spawn_cancel.call_count, 0)

    def test_spawn_keys_go_to_the_picker_before_anything_else(self) -> None:
        ui = _ui(cc_spawn=True)
        with mock.patch.object(cckeys, "spawn_key", return_value="") as spawn_key, _actions() as run:
            self.assertEqual(cc_key(ui, "s", _gs(), 40, SEL_WORKER), "")
        spawn_key.assert_called_once_with(ui, "s", mock.ANY, SEL_WORKER)
        self.assertIsNone(ui.cc_confirm)
        self.assertEqual(run.call_count, 0)


class PointerTests(unittest.TestCase):
    def test_pane_tab_click(self) -> None:
        ui = _ui(cc_pane_scroll=6, cc_focus="table")
        self.assertEqual(cc_pointer(ui, "cctab", "Tools"), [])
        self.assertEqual((ui.cc_tab, ui.cc_pane_scroll, ui.cc_focus), ("Tools", 0, "pane"))

    def test_strip_tab_click(self) -> None:
        ui = _ui(cc_strip_scroll=6, cc_focus="table")
        self.assertEqual(cc_pointer(ui, "ccstrip", "Logs"), [])
        self.assertEqual((ui.cc_strip, ui.cc_strip_scroll, ui.cc_focus), ("Logs", 0, "strip"))

    def test_unknown_tab_or_action_changes_nothing(self) -> None:
        for action, target in (("cctab", "Nope"), ("ccstrip", "Nope"), ("select", "x")):
            ui = _ui(cc_tab="Overview", cc_strip="Events", cc_pane_scroll=2, cc_strip_scroll=3)
            self.assertEqual(cc_pointer(ui, action, target), [], action)
            self.assertEqual((ui.cc_tab, ui.cc_strip, ui.cc_pane_scroll, ui.cc_strip_scroll, ui.cc_focus),
                             ("Overview", "Events", 2, 3, "table"), action)


if __name__ == "__main__":
    unittest.main()


class ControlCenterKeepsTheFilters(unittest.TestCase):
    """the user 2026-10-24: with the control center on, 1-4 used to open details tabs and the filter chips were dead."""

    def test_digits_filter_the_list_through_the_app_key_path(self) -> None:
        import app
        for key, name in (("2", "working"), ("3", "lagging"), ("4", "problems"), ("1", "all")):
            ui = _ui(cc_pane_open=False)
            self.assertEqual(app._ap_global_key(ui, key, _gs(), 30), "")
            self.assertEqual((ui.gfilter, ui.cc_pane_open), (name, False), key)

    def test_d_with_no_pane_on_screen_says_why(self) -> None:
        import app
        ui = _ui()
        ui.cc_active = False
        app._ap_global_key(ui, "d", _gs(), 30)
        self.assertIn("details", ui.note)
