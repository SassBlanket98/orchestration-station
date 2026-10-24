"""`station --tutorial`: the step card, its gated steps, the demo collector and the demo-mode action guard.
State only, no terminal; no real process is ever signalled (os.kill, pidfd and subprocess are mocked and asserted unused)."""
from __future__ import annotations

import re
import time
import unittest
from unittest import mock

import app
import ccactions
import cckeys
import tutorial
from app import _ap_global_key
from demo import DemoGlobalCollector
from model import UIState
from render import global_rows, render_global

SGR = re.compile(r"\x1b\[[0-9;]*m")
NOW = time.time()   # the demo's workers start relative to the launch time, so a fixed old epoch would show none
TITLES = [s.title for s in tutorial._tu_STEPS]


def _step(title: str) -> int:
    return TITLES.index(title)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        ccactions.cx_set_demo(True)
        ccactions._CX_STOPS.clear()
        self.addCleanup(ccactions.cx_set_demo, False)
        self.addCleanup(ccactions._CX_STOPS.clear)
        p = mock.patch.object(cckeys, "_CXK_ARM_S", 0.0)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(ccactions, "_CX_STOP_GRACE_S", 0.0)
        p.start()
        self.addCleanup(p.stop)
        self.gs = DemoGlobalCollector("/nonexistent/overrides.json").snapshot(now=NOW)
        self.ui = UIState(cc=True, demo=True)
        tutorial.tut_start(self.ui, list)

    def press(self, *keys: str) -> None:
        for k in keys:
            render_global(self.gs, self.ui, 140, 40, NOW)   # sets ui.cc_active, as the app's frame does
            _ap_global_key(self.ui, k, self.gs, 40)

    def goto(self, title: str) -> None:
        """Skip forward to a step with S, which re-runs each step's setup on the way."""
        while TITLES[self.ui.tut] != title:
            _ap_global_key(self.ui, "S", self.gs, 40)

    def card(self, cols: int = 140, rows: int = 46) -> str:
        return "\n".join(SGR.sub("", x) for x in render_global(self.gs, self.ui, cols, rows, NOW))


class DemoCollectorTests(unittest.TestCase):
    def test_rows(self) -> None:
        gs = DemoGlobalCollector("/x").snapshot(now=NOW)
        self.assertEqual([s.state for s in gs.stages], ["active", "active", "idle", "closed"])
        self.assertEqual(len({s.path for s in gs.stages}), 4)
        closed = gs.stages[3]
        self.assertEqual((closed.lead_state, closed.lead_pid), ("gone", None))
        self.assertTrue(closed.resume_cmd.startswith("claude --resume "))

    def test_pin_cycle_stays_in_memory(self) -> None:
        collector = DemoGlobalCollector("/nonexistent/overrides.json")
        path = collector.snapshot().stages[0].path
        self.assertEqual([collector.toggle_idle(path) for _ in range(3)], ["idle", "active", ""])


class GatingTests(Base):
    def test_next_waits_for_the_goal(self) -> None:
        self.goto("Move the cursor")
        n = self.ui.tut
        self.press(".", ".", ">")
        self.assertEqual(self.ui.tut, n, "next does nothing until the step is done")
        self.assertIn("do the step first", self.ui.note)
        self.press("down")
        self.assertTrue(self.ui.tut_ok)
        self.assertEqual(self.ui.tut, n, "doing the step does not advance by itself")
        self.press("down", "up", "j")
        self.assertEqual(self.ui.tut, n, "more keys do not advance it either")
        self.press(".")
        self.assertEqual(self.ui.tut, n + 1)
        self.assertFalse(self.ui.tut_ok, "the next step starts undone")

    def test_reading_steps_advance_with_next(self) -> None:
        self.assertEqual(self.ui.tut, 0)
        self.press("down")
        self.assertEqual(self.ui.tut, 0, "ordinary keys never move a reading step on")
        self.press(".")
        self.assertEqual(self.ui.tut, 1)

    def test_skip_back_restart_quit(self) -> None:
        self.goto("Move the cursor")
        n = self.ui.tut
        self.press("down")
        self.press("R")
        self.assertFalse(self.ui.tut_ok, "restart puts the step back")
        self.press("S")
        self.assertEqual(self.ui.tut, n + 1)
        self.press(",")
        self.assertEqual(self.ui.tut, n)
        self.press("<")
        self.assertEqual(self.ui.tut, n - 1)
        self.press("Q")
        self.assertEqual(self.ui.tut, -1)

    def test_back_stops_at_the_first_step(self) -> None:
        self.press(",", ",")
        self.assertEqual(self.ui.tut, 0)

    def test_last_step_finishes(self) -> None:
        self.goto("You are set")
        self.press(".")
        self.assertEqual(self.ui.tut, -1)

    def test_search_text_is_not_a_tutorial_key(self) -> None:
        self.goto("Search")
        n = self.ui.tut
        self.press("/", ".", ",", "Q", "S")
        self.assertEqual(self.ui.gquery, ".,QS")
        self.assertEqual(self.ui.tut, n)
        self.press("enter")
        self.assertTrue(self.ui.tut_ok)

    def test_task_text_is_not_a_tutorial_key(self) -> None:
        self.goto("Spawn a new worker")
        self.press("n", "down", "enter")
        self.assertEqual(self.ui.cc_spawn_model, "haiku")
        self.press("a", ".", "Q")
        self.assertEqual(self.ui.cc_input, "a.Q")
        self.assertGreaterEqual(self.ui.tut, 0)


class StepGoalTests(Base):
    def test_folding(self) -> None:
        self.goto("Fold and unfold")
        self.press("enter")
        self.assertTrue(self.ui.tut_ok)

    def test_session_page_needs_a_visit_and_the_way_back(self) -> None:
        self.goto("The session page")
        self.press("space")
        self.assertEqual(self.ui.gpage, "sess")
        self.assertFalse(self.ui.tut_ok)
        self.press("esc")
        self.assertTrue(self.ui.tut_ok)

    def test_worker_page(self) -> None:
        self.goto("The worker page")
        self.press("space")
        self.assertEqual(self.ui.gpage, "wk")
        self.press("esc")
        self.assertTrue(self.ui.tut_ok)

    def test_all_six_pane_tabs(self) -> None:
        self.goto("The details pane")
        self.press("d")
        self.assertTrue(self.ui.tut_ok)
        self.press(".")
        self.assertEqual(TITLES[self.ui.tut], "The six pane tabs")
        self.assertTrue(self.ui.cc_pane_open, "the step opens the pane itself")
        self.press("tab", "2", "3", "4", "5")
        self.assertFalse(self.ui.tut_ok, "five tabs is not six")
        self.press("6")
        self.assertTrue(self.ui.tut_ok)

    def test_all_four_strip_tabs(self) -> None:
        self.goto("The bottom strip")
        self.press("]", "]")
        self.assertFalse(self.ui.tut_ok)
        self.press("]")
        self.assertTrue(self.ui.tut_ok)

    def test_filter_and_quota(self) -> None:
        self.goto("Filter")
        self.press("2")
        self.assertTrue(self.ui.tut_ok)
        self.press(".")
        self.assertEqual(TITLES[self.ui.tut], "Quota")
        self.assertEqual(self.ui.gfilter, "all", "each step starts from a clean screen")
        self.press("u")
        self.assertFalse(self.ui.tut_ok)
        self.press("u")
        self.assertTrue(self.ui.tut_ok)

    def test_show_more_or_less(self) -> None:
        self.goto("Show more or less")
        self.press("c")
        self.assertFalse(self.ui.tut_ok)
        self.press("e")
        self.assertTrue(self.ui.tut_ok)

    def test_stop_then_kill_show_the_real_prompts_and_send_nothing(self) -> None:
        self.goto("Stop and kill (asks first)")
        with mock.patch("os.kill") as kill, mock.patch.object(ccactions, "_cac_pidfd_signal") as pidfd, \
                mock.patch("subprocess.Popen") as popen:
            self.press("s")
            self.assertIsNotNone(self.ui.cc_confirm)
            self.assertIn("Stop api-routes", self.ui.cc_confirm[2])
            self.press("y")
            self.assertTrue(self.ui.note.startswith("demo: stop"), self.ui.note)
            self.assertFalse(self.ui.tut_ok)
            self.press("K")
            self.assertIn("Kill api-routes", self.ui.cc_confirm[2])
            self.press("y")
            self.assertTrue(self.ui.note.startswith("demo: kill"), self.ui.note)
            self.assertTrue(self.ui.tut_ok)
            kill.assert_not_called()
            pidfd.assert_not_called()
            popen.assert_not_called()

    def test_kill_needs_a_stop_first(self) -> None:
        self.goto("Stop and kill (asks first)")
        self.press("K")
        self.assertIsNone(self.ui.cc_confirm)
        self.assertIn("send stop first", self.ui.note)

    def test_resume_a_closed_session(self) -> None:
        self.goto("Resume a closed session")
        row = global_rows(self.gs, self.ui)[self.ui.gsel]
        self.assertIn("old-spike", row[0])
        with mock.patch("subprocess.Popen") as popen:
            self.press("r")
            self.assertIn("claude --resume demo-old", self.ui.cc_confirm[2])
            self.press("y")
            popen.assert_not_called()
        self.assertTrue(self.ui.tut_ok)

    def test_spawn_walks_the_picker(self) -> None:
        self.goto("Spawn a new worker")
        with mock.patch("subprocess.Popen") as popen:
            self.press("n")
            self.assertTrue(self.ui.cc_spawn)
            self.press("down", "down", "enter")   # sonnet is greyed in the demo list: it says why and stays in the picker
            self.assertEqual(self.ui.cc_spawn_model, "")
            self.assertEqual(self.ui.note, "headroom 3%")
            self.press("up", "enter", "x", "enter")
            self.assertEqual(self.ui.cc_confirm[0], "spawn")
            self.press("y")
            popen.assert_not_called()
        self.assertTrue(self.ui.note.startswith("demo: spawn"))
        self.assertTrue(self.ui.tut_ok)

    def test_end_a_session_signals_nothing(self) -> None:
        self.goto("End a session")
        collector = DemoGlobalCollector("/x")
        with mock.patch("os.kill") as kill:
            self.assertEqual(_ap_global_key(self.ui, "x", self.gs, 40), "close")
            app._ap_close_selected(self.ui, self.gs, collector)   # what the main loop does with "close"
            self.assertIsNotNone(self.ui.close_ask)
            self.assertEqual(_ap_global_key(self.ui, "y", self.gs, 40), "end_lead")
            app._ap_end_lead(self.ui, collector)                   # and with "end_lead"
            kill.assert_not_called()
        self.assertTrue(self.ui.tut_ok)
        self.assertTrue(self.ui.note.startswith("demo:"), self.ui.note)

    def test_classic_view_and_help(self) -> None:
        self.goto("Classic view")
        self.press("v")
        self.assertTrue(self.ui.tut_ok)
        self.press(".")
        self.assertTrue(self.ui.cc, "the next step is back in the control center")
        self.goto("The key list")
        self.press("?")
        self.assertTrue(self.ui.tut_ok)
        self.press("x")   # any key closes the box and is not a tutorial key
        self.assertFalse(self.ui.help)
        self.assertEqual(TITLES[self.ui.tut], "The key list")


class StationViewKeyTests(Base):
    """w opens the station view (the page the user called a unicorn) from any row, in the tutorial or not."""

    def tearDown(self) -> None:
        pass

    def rows(self) -> list[tuple[str, str, str]]:
        return global_rows(self.gs, self.ui)

    def test_w_from_a_session_row(self) -> None:
        self.ui.tut = -1
        self.ui.gsel = next(i for i, r in enumerate(self.rows()) if r[1] == "stage")
        self.press("w")
        self.assertEqual((self.ui.gpage, self.ui.view, self.ui.tab), ("stage", "overview", "live"))
        self.assertIn("demo-project", self.ui.gstage)
        self.assertTrue(self.ui.expanded, "the live workers' cards start open")
        from render import render_frame
        frame = render_frame(app._ap_global_stage_snap(self.gs, self.ui), self.ui, 140, 46, NOW, app._ap_global_stage(self.gs, self.ui))
        text = "\n".join(SGR.sub("", x) for x in frame)   # the app draws the station page with render_frame
        self.assertIn("workers", text)
        self.assertIn("api-routes", text)
        self.assertIn("reviews", text)
        self.press("esc")
        self.assertEqual(self.ui.gpage, "")

    def test_w_from_a_worker_row_selects_that_worker(self) -> None:
        self.ui.tut = -1
        i = next(i for i, r in enumerate(self.rows()) if r[1] == "worker" and r[2] == "db-schema")
        self.ui.gsel = i
        self.press("w")
        self.assertEqual(self.ui.gpage, "stage")
        stage = next(s for s in self.gs.stages if s.path == self.ui.gstage)
        self.assertEqual(stage.snap.workers[self.ui.sel].name, "db-schema")

    def test_w_on_a_project_heading_opens_its_first_session(self) -> None:
        self.ui.tut = -1
        self.ui.gsel = 0
        self.assertEqual(self.rows()[0][1], "group")
        self.press("w")
        self.assertEqual(self.ui.gpage, "stage")
        self.assertIn("demo-project", self.ui.gstage)

    def test_lead_cost_files_pages(self) -> None:
        self.ui.tut = -1
        self.ui.gsel = 1
        self.press("w", "L")
        self.assertEqual(self.ui.view, "lead")
        self.press("esc", "C")
        self.assertEqual(self.ui.view, "cost")
        self.press("esc", "F")
        self.assertEqual(self.ui.view, "files")

    def test_w_is_advertised(self) -> None:
        from cchint import HINT
        self.assertIn("w station", HINT)
        self.ui.tut = -1
        self.ui.help = True
        self.assertIn("station view", self.card())

    def test_tutorial_steps_for_the_station_view(self) -> None:
        self.goto("The station view")
        self.assertFalse(self.ui.tut_ok)
        self.press("w")
        self.assertFalse(self.ui.tut_ok, "being there is not enough: the way back counts")
        self.press("esc")
        self.assertTrue(self.ui.tut_ok)
        self.goto("Pages inside the station view")
        self.assertEqual(self.ui.gpage, "stage", "the step starts inside the station view")
        self.press("L", "esc", "C", "esc")
        self.assertFalse(self.ui.tut_ok)
        self.press("F")
        self.assertTrue(self.ui.tut_ok)


class DemoModeTests(unittest.TestCase):
    def tearDown(self) -> None:
        ccactions.cx_set_demo(False)

    def test_off_by_default_so_real_checks_still_apply(self) -> None:
        self.assertFalse(ccactions.cx_is_demo())
        target = ccactions.Target(kind="worker", label="w", pid=48211, live=True)
        ok, _why = ccactions.can("stop", target)
        self.assertFalse(ok, "a made-up pid must fail the /proc checks outside the tutorial")

    def test_run_and_spawn_do_nothing_in_demo(self) -> None:
        ccactions.cx_set_demo(True)
        target = ccactions.Target(kind="worker", label="w", pid=48211, live=True)
        with mock.patch("os.kill") as kill, mock.patch("subprocess.Popen") as popen, \
                mock.patch.object(ccactions, "_cac_log") as log:
            self.assertTrue(ccactions.run("stop", target).startswith("demo: stop"))
            self.assertTrue(ccactions.spawn("luna", "/", "x").startswith("demo: spawn"))
            kill.assert_not_called()
            popen.assert_not_called()
            log.assert_not_called()
        ccactions._CX_STOPS.clear()


class CardTests(Base):
    def test_card_fits_and_names_the_step(self) -> None:
        for cols, rows in ((80, 24), (120, 40), (200, 50)):
            lines = [SGR.sub("", x) for x in render_global(self.gs, self.ui, cols, rows, NOW)]
            self.assertEqual(len(lines), rows)
            self.assertTrue(all(len(x) <= cols for x in lines), (cols, rows))
            text = "\n".join(lines)
            self.assertIn(f"tutorial 1/{len(TITLES)}", text)
            self.assertIn("Welcome", text)
            self.assertIn(". next", text)

    def test_every_step_draws_at_every_size(self) -> None:
        for i in range(len(TITLES)):
            for cols, rows in ((80, 24), (140, 46)):
                self.ui.tut = i
                tutorial._tu_enter(self.ui, lambda: global_rows(self.gs, self.ui))
                text = self.card(cols, rows)
                self.assertIn(f"tutorial {i + 1}/{len(TITLES)}", text)
                self.assertNotIn("render error", text)

    def test_card_shows_the_ask_then_the_tick(self) -> None:
        self.goto("Move the cursor")
        self.assertIn("▶ Press ↑ or ↓", self.card())
        self.assertIn("S skip", self.card())
        self.press("down")
        self.assertIn("✔ Done. Press . for the next step", self.card())
        self.assertNotIn("S skip", self.card())

    def test_checklist_progress(self) -> None:
        self.goto("The six pane tabs")
        self.assertIn("✔ Overview", self.card())
        self.assertIn("· Prompt", self.card())
        self.press("tab", "2")
        self.assertIn("✔ Prompt", self.card())

    def test_no_card_when_off(self) -> None:
        self.ui.tut = -1
        self.assertNotIn("tutorial ", self.card())

    def test_tiny_terminal_has_no_card(self) -> None:
        lines = ["x" * 20] * 6
        self.assertEqual(tutorial.tut_overlay(lines, self.ui, 20, 6), lines)

    def test_every_step_is_reachable_by_its_ask(self) -> None:
        """Each asking step has a goal, so next can never be stuck without a way to reach it."""
        for step in tutorial._tu_STEPS:
            if step.ask:
                self.assertIsNotNone(step.done, step.title)


class EntryTests(unittest.TestCase):
    def test_needs_a_terminal(self) -> None:
        args = app._ap_parser().parse_args(["--tutorial"])
        with mock.patch("sys.stdout.isatty", return_value=False):
            self.assertEqual(app._ap_tutorial(args), 2)
        self.assertFalse(ccactions.cx_is_demo())

    def test_flag_parses(self) -> None:
        self.assertTrue(app._ap_parser().parse_args(["--tutorial"]).tutorial)


if __name__ == "__main__":
    unittest.main()
