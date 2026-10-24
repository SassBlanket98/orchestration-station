"""The session rows of the overview: name, tag, model and effort; the expanded session's fold; the idle section."""
from __future__ import annotations

import os
import re
import unittest
from unittest.mock import patch

import app
import collect
from model import (
    DONE,
    FAILED,
    RUNNING,
    GlobalSnapshot,
    Group,
    Milestone,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import _rd_g_fit_words, global_rows, render_global
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_760_050_000.0


def code(color: tuple[int, int, int]) -> str:
    return "38;2;{};{};{}".format(*color)


def session(path: str, label: str, workers: list[Worker], *, name: str = "", tag: str = "", model: str = "",
            effort: str = "", last: float = NOW - 30, lead_state: str = "live", solo: bool = False,
            unlinked: bool = False, state: str = "") -> Stage:
    live = sum(wk.state == RUNNING for wk in workers)
    st = Stage(path, label, Snapshot(now=NOW, workers=workers), live, last)
    st.lead_state, st.solo, st.unlinked, st.state = lead_state, solo, unlinked, state
    st.session_name, st.tag, st.lead_model, st.effort = name, tag, model, effort
    return st


def frame_of(gs: GlobalSnapshot, ui: UIState, cols: int = 120, rows: int = 38) -> list[str]:
    return render_global(gs, ui, cols, rows, NOW)


def plain_lines(gs: GlobalSnapshot, ui: UIState, cols: int = 120, rows: int = 38) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in frame_of(gs, ui, cols, rows)]


def line_with(lines: list[str], text: str) -> str:
    return next(line for line in lines if text in line and any(mark in _wd_SGR_RE.sub("", line) for mark in ("◆", "◇", "◐", "○")))


def worker_line(lines: list[str], name: str) -> str:
    """The worker's own row: its branch glyph, not the station row where it may speak."""
    plain = [_wd_SGR_RE.sub("", line) for line in lines]
    return next(line for line in plain if re.search(rf"[├└]─.*\b{name}\b", line))


def colour_before(raw: str, text: str) -> str:
    """The colour code in force just before `text` on a coloured line."""
    codes = re.findall(r"\x1b\[([0-9;]*)m", raw[: raw.index(text)])
    return codes[-1] if codes else ""


def keys(ui: UIState, gs: GlobalSnapshot, *names: str) -> None:
    for name in names:
        app._ap_global_key(ui, name, gs, 40)


def task(workers: list[Worker], label: str, current: bool = False) -> Group:
    """One dispatch wave holding these workers, as collect's _co_groups builds it."""
    live = sum(wk.state == RUNNING for wk in workers)
    ends = [wk.end for wk in workers if wk.end]
    return Group(label, [wk.name for wk in workers], min(wk.start for wk in workers), done=len(workers) - live,
                 total=len(workers), live=live, finished=not live, finished_at=None if live else max(ends, default=None),
                 current=current)


class SessionRow(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def gs(self) -> GlobalSnapshot:
        worker = Worker("tw-s2", RUNNING, model="gpt-6-luna", effort="med", start=NOW - 247)
        return GlobalSnapshot(now=NOW, stages=[session("/a", "agent-work", [worker], name="m4 handover",
                                                       tag="CC·W", model="sonnet-5.5", effort="med")])

    def test_row_shows_name_tag_model_and_effort_in_order(self) -> None:
        raw = frame_of(self.gs(), UIState())
        line = _wd_SGR_RE.sub("", line_with(raw, "m4 handover"))
        at = [line.index(text) for text in ("m4 handover", "CC·W", "Sonnet 5.5", "med")]
        self.assertEqual(at, sorted(at))
        self.assertNotIn("1 live", line)

    def test_harness_tag_and_model_effort_stay_together(self) -> None:
        raw = line_with(frame_of(self.gs(), UIState()), "m4 handover")
        self.assertIn("CC·W", _wd_SGR_RE.sub("", raw))
        self.assertIn("Sonnet 5.5 · med", _wd_SGR_RE.sub("", raw))

    def test_name_tag_and_model_fit_at_70_columns(self) -> None:
        line = line_with(plain_lines(self.gs(), UIState(), 70), "m4 handover")
        self.assertIn("m4 handover", line)
        self.assertIn("CC·W", line)
        self.assertIn("Sonnet", line)   # the model column is cut with … at 70 columns
        self.assertLessEqual(_wd_width(line), 70)

    def test_model_and_effort_come_back_when_there_is_room(self) -> None:
        line = line_with(plain_lines(self.gs(), UIState(), 100), "m4 handover")
        self.assertIn("Sonnet 5.5", line)
        self.assertIn("med", line)

    def test_row_without_a_session_name_leaves_that_cell_empty(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[session("/a", "plain", [Worker("w", RUNNING, start=NOW)], tag="CDX")])
        line = line_with(plain_lines(gs, UIState()), "plain")
        self.assertIn("CDX", line)
        self.assertNotIn("CC·", line)

    def test_closed_session_shows_only_after_finished_fold_opens(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[session("/b", "beta", [], tag="OCode", model="glm-5.3", last=NOW - 3600, lead_state="gone")])
        self.assertIn("finished 1", "\n".join(plain_lines(gs, UIState())))
        line = line_with(plain_lines(gs, UIState(gopen={("", "more", "finished:/b")})), "beta")
        self.assertIn("◇", line)
        self.assertIn("OCode", line)

    def test_worker_row_shows_effort_after_the_model(self) -> None:
        raw = worker_line(frame_of(self.gs(), UIState(gopen={("/a", "stage", "")})), "tw-s2")
        self.assertLess(raw.index("GPT-6 Luna"), raw.index("med"))
        self.assertLess(raw.index("med"), raw.index("04m 07s"))


class Unlinked(unittest.TestCase):
    def test_unlinked_group_folds_under_its_project(self) -> None:
        group = session("/old", "sample-project", [], name="should-not-show", last=NOW - 3600, lead_state="gone", unlinked=True)
        gs = GlobalSnapshot(now=NOW, stages=[group])
        text = "\n".join(plain_lines(gs, UIState()))
        self.assertIn("finished 1", text)
        self.assertNotIn("should-not-show", text)

    def test_finished_rows_keep_their_own_session_names(self) -> None:
        older = session("/old", "sample-project", [], lead_state="gone", unlinked=True)
        sibling = session("/s", "sample-project", [], name="kept-session", lead_state="gone")
        gs = GlobalSnapshot(now=NOW, stages=[sibling, older])
        ui = UIState(gopen={("", "more", "finished:/old"), ("", "more", "finished:/s")})
        text = "\n".join(plain_lines(gs, ui))
        self.assertIn("kept-session", text)
        self.assertIn("session unknown", text)


class Expanded(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        live1 = Worker("live1", RUNNING, start=NOW - 50)
        new1 = Worker("new1", DONE, start=NOW - 300, end=NOW - 100)
        new2 = Worker("new2", FAILED, start=NOW - 300, end=NOW - 150)
        new3 = Worker("new3", DONE, start=NOW - 300, end=NOW - 120)
        old1 = Worker("old1", DONE, start=NOW - 900, end=NOW - 800)
        old2 = Worker("old2", DONE, start=NOW - 900, end=NOW - 700)
        ancient = Worker("ancient", DONE, start=NOW - 5000, end=NOW - 4000)
        self.a = session("/a", "alpha", [live1, new1, new2, new3, old1, old2, ancient], name="a-session", tag="CC·W")
        self.a.snap.groups = [task([live1], "build", current=True), task([new1, new2, new3], "triage"),
                              task([old1, old2], "plan"), task([ancient], "investigate")]   # newest first
        self.b = session("/b", "beta", [Worker("bl", RUNNING, start=NOW - 40)] + [
            Worker(f"b-{k}", DONE, start=NOW - 300, end=NOW - 100 - k) for k in range(6)], name="b-session", tag="CDX")
        self.gs = GlobalSnapshot(now=NOW, stages=[self.a, self.b])

    def names(self, ui: UIState, path: str) -> list[str]:
        return [r[2] for r in global_rows(self.gs, ui) if r[0] == path and r[1] in ("worker", "more")]

    def kinds(self, ui: UIState, path: str) -> list[tuple[str, str]]:
        return [(r[1], r[2]) for r in global_rows(self.gs, ui) if r[0] == path and r[1] != "idle"]

    def add_older_task(self) -> None:
        """A third finished task, so the two newest finished ones are lines and this one is behind the fold."""
        gone = Worker("gone", DONE, start=NOW - 9000, end=NOW - 8000)
        self.a.snap.workers.append(gone)
        self.a.snap.groups.append(task([gone], "cleanup"))

    def test_a_live_or_failed_task_lists_its_workers_and_a_finished_one_is_a_line(self) -> None:
        self.assertEqual(self.names(UIState(), "/a"), ["live1", "new1", "new3", "new2"])   # triage has a failed worker
        text = "\n".join(plain_lines(self.gs, UIState(), 120, 60))
        self.assertIn("build · 1 live · 0 done", text)     # a label above each open task, since there is more than one
        self.assertIn("triage · 0 live · 3 done", text)
        self.assertIn("▸ plan · 2/2", text)                # finished tasks are one line each (the user 2026-10-24)
        self.assertIn("▸ investigate · 1/1", text)
        self.assertNotIn("old1", text)

    def test_a_task_with_a_live_worker_lists_it_whatever_its_age(self) -> None:
        g = self.a.snap.groups
        self.a.snap.groups = [g[1], g[2], g[3], g[0]]          # the running task is now the oldest of four
        self.assertEqual(self.names(UIState(), "/a"), ["new1", "new3", "new2", "live1"])
        text = "\n".join(plain_lines(self.gs, UIState(), 120, 60))
        self.assertIn("▸ plan · 2/2", text)
        self.assertNotIn("ancient", text)

    def test_a_finished_task_line_opens_to_its_workers(self) -> None:
        ui = UIState(gsel=global_rows(self.gs, UIState()).index(("/a", "task", "old1|old2")))
        self.assertNotIn("old1", self.names(ui, "/a"))     # shut by default
        keys(ui, self.gs, "enter")
        self.assertEqual(self.names(ui, "/a"), ["live1", "new1", "new3", "new2", "old2", "old1"])   # newest finished first
        self.assertIn("▾ plan · 2/2", "\n".join(plain_lines(self.gs, ui, 120, 60)))
        keys(ui, self.gs, "left")                           # ← on an open task closes it again
        self.assertNotIn("old1", self.names(ui, "/a"))

    def test_older_finished_tasks_fold_under_plus_n_done(self) -> None:
        self.add_older_task()
        ui = UIState(gsel=global_rows(self.gs, UIState()).index(("/a", "more", "")))
        self.assertIn("▸ +1 done", "\n".join(plain_lines(self.gs, UIState(), 120, 60)))
        keys(ui, self.gs, "enter")
        self.assertEqual(self.kinds(ui, "/a")[-1], ("task", "gone"))
        text = "\n".join(plain_lines(self.gs, ui, 120, 60))
        self.assertIn("− fewer", text)
        self.assertIn("▸ cleanup · 1/1", text)

    def test_fold_is_a_click_target_that_opens_it(self) -> None:
        self.add_older_task()
        ui = UIState()
        render_global(self.gs, ui, 120, 60, NOW)
        index = global_rows(self.gs, ui).index(("/a", "more", ""))
        hit = next(h for h in ui.hits if h[4] == "nav" and h[5] == str(index))
        ui.gsel = index
        self.assertEqual(app._ap_pointer_keys(ui, f"click:5:{hit[0]}", self.gs, None, 0.0), ["enter"])

    def test_a_task_line_is_a_click_target_too(self) -> None:
        ui = UIState()
        render_global(self.gs, ui, 120, 60, NOW)
        index = global_rows(self.gs, ui).index(("/a", "task", "ancient"))
        hit = next(h for h in ui.hits if h[4] == "nav" and h[5] == str(index))
        ui.gsel = index
        self.assertEqual(app._ap_pointer_keys(ui, f"click:5:{hit[0]}", self.gs, None, 0.0), ["enter"])

    def test_one_task_session_is_listed_plainly(self) -> None:
        only_b = GlobalSnapshot(now=NOW, stages=[self.b])
        self.assertEqual([r[2] for r in global_rows(only_b, UIState()) if r[1] == "worker"],
                         ["bl", "b-0", "b-1", "b-2", "b-3", "b-4", "b-5"])
        text = "\n".join(plain_lines(only_b, UIState(), 120, 60))
        self.assertNotIn("workers · 1 live", text)          # no task label for a single task
        self.assertFalse(any(ln.strip().startswith(("├─ +", "└─ +")) for ln in text.splitlines()))   # and no fold

    def test_task_order_is_the_snapshot_order_not_the_worker_order(self) -> None:
        # a newer task that has finished, under a current one still running: both list their workers, the newer first
        busy, newer = self.a.snap.groups[0], self.a.snap.groups[1]
        self.a.snap.groups = [newer, busy, *self.a.snap.groups[2:]]
        busy.current, newer.current = True, False
        self.assertEqual(self.names(UIState(), "/a"), ["new1", "new3", "new2", "live1"])

    def test_no_milestone_means_no_milestone_line(self) -> None:
        text = "\n".join(plain_lines(self.gs, UIState(), 120, 60))
        self.assertIn("a-session", text)
        self.assertNotIn("no milestone recorded", text)
        self.a.snap.milestones = [Milestone(ts=NOW - 60, text="cut the fold")]
        self.assertIn("cut the fold", "\n".join(plain_lines(self.gs, UIState(), 120, 60)))


class IdleSection(unittest.TestCase):
    """Three sections: active (no heading), idle (open, quiet: heading h hides it), closed (not open: heading H hides it)."""

    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        live = session("/a", "alpha", [Worker("w", RUNNING, start=NOW - 30)], name="live-one")
        idle = session("/b", "beta", [Worker("d", DONE, start=NOW - 90, end=NOW - 80)], name="beta-one",
                       last=NOW - 600, state="idle")
        closed = session("/c", "gamma", [], last=NOW - 3600, lead_state="gone", state="closed")
        self.gs = GlobalSnapshot(now=NOW, stages=[live, idle, closed])
        # idle projects start shut: the heading is there, its sessions are not listed until it opens
        self.all_rows = [("", "group", "p:/a"), ("/a", "stage", ""), ("/a", "worker", "w"),
                         ("", "idle", ""), ("", "group", "p:/b"),
                         ("", "more", "finished:/c")]

    def test_three_sections_in_order_with_the_headings_as_stops(self) -> None:
        self.assertEqual(global_rows(self.gs, UIState()), self.all_rows)
        text = "\n".join(plain_lines(self.gs, UIState(), 140))
        self.assertIn("idle · 1 projects", text)
        self.assertIn("finished 1", text)
        self.assertIn("h hide", text)
        self.assertIn("[H] hide closed", text)

    def test_enter_on_the_idle_heading_folds_only_the_idle_section(self) -> None:
        ui = UIState(gsel=3)
        keys(ui, self.gs, "enter")
        self.assertEqual(ui.gfolded, {"idle"})
        self.assertEqual(global_rows(self.gs, ui), self.all_rows[:4] + self.all_rows[5:])
        self.assertIn("idle · 1 projects", "\n".join(plain_lines(self.gs, ui)))
        self.assertNotIn("beta", "\n".join(plain_lines(self.gs, ui)))
        keys(ui, self.gs, "right")
        self.assertEqual(global_rows(self.gs, ui), self.all_rows)
        keys(ui, self.gs, "left")
        self.assertEqual(ui.gfolded, {"idle"})

    def test_h_hides_the_idle_section_and_leaves_closed_alone(self) -> None:
        ui = UIState(gsel=1)
        keys(ui, self.gs, "h")
        self.assertTrue(ui.ghide_idle)
        self.assertFalse(ui.ghide_closed)
        self.assertEqual(global_rows(self.gs, ui), self.all_rows[:3] + self.all_rows[5:])
        text = "\n".join(plain_lines(self.gs, ui))
        self.assertIn("idle hidden · h shows", text)
        self.assertNotIn("beta", text)
        self.assertIn("gamma", text)
        self.assertIn("[h] show idle", text)
        keys(ui, self.gs, "h")
        self.assertFalse(ui.ghide_idle)
        self.assertIn("beta", "\n".join(plain_lines(self.gs, ui)))

    def test_H_hides_the_closed_section_and_leaves_idle_alone(self) -> None:
        ui = UIState(gsel=1)
        keys(ui, self.gs, "H")
        self.assertTrue(ui.ghide_closed)
        self.assertFalse(ui.ghide_idle)
        self.assertEqual(global_rows(self.gs, ui), self.all_rows[:5])
        text = "\n".join(plain_lines(self.gs, ui, 140))
        self.assertIn("closed hidden · H shows", text)
        self.assertNotIn("gamma", text)
        self.assertIn("beta", text)
        self.assertIn("[H] show closed", text)
        keys(ui, self.gs, "H")
        self.assertFalse(ui.ghide_closed)
        self.assertIn("gamma", "\n".join(plain_lines(self.gs, ui)))

    def test_h_and_H_hide_independently_and_navigation_skips_both(self) -> None:
        ui = UIState(gsel=1)
        keys(ui, self.gs, "h", "H")
        self.assertEqual(global_rows(self.gs, ui), self.all_rows[:3])
        keys(ui, self.gs, "down", "down", "down")
        self.assertEqual(ui.gsel, 2)                     # the cursor stops at the last row that is shown
        keys(ui, self.gs, "H")                           # closed comes back; idle stays hidden
        self.assertEqual(global_rows(self.gs, ui), self.all_rows[:3] + self.all_rows[5:])

    def test_hiding_moves_the_cursor_off_a_row_it_cannot_see(self) -> None:
        ui = UIState(gsel=3)                             # beta
        keys(ui, self.gs, "h")
        rows = global_rows(self.gs, ui)
        self.assertLess(ui.gsel, len(rows))
        self.assertNotEqual(rows[ui.gsel][0], "/b")

    def test_an_open_station_stays_open_when_h_is_pressed(self) -> None:
        ui = UIState(gsel=0)
        keys(ui, self.gs, "h")
        self.assertIn(("/a", "worker", "w"), global_rows(self.gs, ui))

    def test_idle_projects_have_no_cap_and_open_all_their_sessions(self) -> None:
        root = "/p/proj"
        many = [session(f"{root}#i{k}", "proj", [], name=f"idle{k:02d}", last=NOW - 600 - k, state="idle") for k in range(20)]
        gs = GlobalSnapshot(now=NOW, stages=many)
        rows = global_rows(gs, UIState())
        self.assertEqual(sum(r[1] == "stage" for r in rows), 0)      # one shut heading, no cap row
        self.assertEqual(sum(r[1] == "group" for r in rows), 1)
        self.assertNotIn(("", "more", "idle"), rows)
        ui = UIState(gopen_keys={"p:" + root})
        self.assertEqual(sum(r[1] == "stage" for r in global_rows(gs, ui)), 20)

    def test_execution_row_without_session_name_keeps_goal_label(self) -> None:
        goal_a = session("/p/a", "proj", [Worker("w", RUNNING, start=NOW)], tag="CC·W")
        goal_a.session_name = ""
        goal_a.goal = "Close the login bug"
        goal_b = session("/p/b", "proj", [Worker("w2", RUNNING, start=NOW)], tag="CC·W")
        goal_b.session_name = ""
        goal_b.goal = "Write the review report"
        text = "\n".join(plain_lines(GlobalSnapshot(now=NOW, stages=[goal_a, goal_b]), UIState()))
        self.assertIn("Close the login bug", text)
        self.assertIn("Write the review report", text)

    def test_unlinked_bucket_row_says_session_unknown(self) -> None:
        row = session("/old", "sample-project", [], lead_state="gone", unlinked=True)
        text = "\n".join(plain_lines(GlobalSnapshot(now=NOW, stages=[row]), UIState(gopen={("", "more", "finished:/old")})))
        self.assertIn("session unknown", text)

    def test_the_key_line_and_help_name_the_h_and_H_keys(self) -> None:
        text = "\n".join(plain_lines(self.gs, UIState(), 140))
        self.assertIn("[h] hide idle", text)
        self.assertIn("[H] hide closed", text)
        ui = UIState(help=True)
        help_text = "\n".join(plain_lines(self.gs, ui))
        self.assertIn("hide or show the idle section", help_text)
        self.assertIn("the closed section", help_text)

    def test_clicking_the_heading_folds_it(self) -> None:
        ui = UIState(gsel=3)
        render_global(self.gs, ui, 120, 38, NOW)
        hit = next(h for h in ui.hits if h[4] == "nav" and h[5] == "3")
        self.assertEqual(app._ap_pointer_keys(ui, f"click:5:{hit[0]}", self.gs, None, 0.0), ["enter"])

    def test_each_heading_has_its_own_hide_click_target(self) -> None:
        ui = UIState()
        render_global(self.gs, ui, 120, 38, NOW)
        hit = next(h for h in ui.hits if h[4] == "key" and h[5] == "h")
        self.assertEqual(app._ap_pointer_keys(ui, f"click:{hit[2]}:{hit[0]}", self.gs, None, 0.0), ["h"])
        self.assertIn("[H] hide closed", "\n".join(plain_lines(self.gs, ui, 140)))


class IdleStartsFolded(unittest.TestCase):
    """At launch the idle section is open (these are sessions the user has open) and the closed section is folded."""

    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        live = session("/a", "alpha", [Worker("w", RUNNING, start=NOW - 30)], name="live-one")
        idle = session("/b", "beta", [Worker("d", DONE, start=NOW - 90, end=NOW - 80)], name="beta-one",
                       last=NOW - 600, state="idle")
        closed = session("/c", "gamma", [], last=NOW - 3600, lead_state="gone", state="closed")
        self.gs = GlobalSnapshot(now=NOW, stages=[live, idle, closed])

    def test_launch_shows_idle_headings_shut_and_the_finished_fold(self) -> None:
        ui = app._ap_launch_ui("full")
        rows = global_rows(self.gs, ui)
        self.assertIn(("", "group", "p:/b"), rows)
        self.assertNotIn(("/b", "stage", ""), rows)           # idle starts shut
        self.assertNotIn(("/c", "stage", ""), rows)
        self.assertIn(("", "more", "finished:/c"), rows)

    def test_enter_on_the_closed_heading_opens_it_and_h_still_hides_idle(self) -> None:
        ui = app._ap_launch_ui("full")
        ui.gsel = global_rows(self.gs, ui).index(("", "more", "finished:/c"))
        keys(ui, self.gs, "enter")
        self.assertIn(("/c", "stage", ""), global_rows(self.gs, ui))
        keys(ui, self.gs, "h")
        self.assertNotIn(("/b", "stage", ""), global_rows(self.gs, ui))
        self.assertIn(("/c", "stage", ""), global_rows(self.gs, ui))

    def test_e_opens_every_section_and_c_folds_them_all(self) -> None:
        ui = app._ap_launch_ui("full")
        keys(ui, self.gs, "e")
        self.assertIn(("/b", "stage", ""), global_rows(self.gs, ui))   # e opens idle projects
        self.assertIn("beta", "\n".join(plain_lines(self.gs, ui)))
        self.assertNotIn(("/c", "stage", ""), global_rows(self.gs, ui))  # finished fold stays shut
        keys(ui, self.gs, "c")
        self.assertTrue({"idle", "closed"} <= ui.gfolded)   # the sections fold too: c shuts every row
        self.assertNotIn(("/b", "stage", ""), global_rows(self.gs, ui))
        text = "\n".join(plain_lines(self.gs, ui))
        self.assertIn("idle · 1 projects", text)
        self.assertIn("finished 1", text)


class NameTrim(unittest.TestCase):
    def test_the_collector_keeps_at_most_five_whole_words(self) -> None:
        self.assertEqual(collect._co_short_name("monitor redesign from the start now"), "monitor redesign from the start")
        self.assertEqual(collect._co_short_name("one two three four five six"), "one two three four five")
        self.assertEqual(collect._co_short_name("alphabet bravobravo charliechar deltadelta"), "alphabet bravobravo charliechar")   # 36 characters at most
        self.assertEqual(collect._co_short_name("Supercalifragilisticexpialidocious-extra-long"),
                         "Supercalifragilisticexpialidocious-extra-long")

    def test_a_narrow_column_drops_trailing_words_and_never_cuts_inside_one(self) -> None:
        self.assertEqual(_rd_g_fit_words("monitor redesign", 14), "monitor…")
        self.assertEqual(_rd_g_fit_words("monitor redesign", 30), "monitor redesign")
        self.assertEqual(_rd_g_fit_words("monitor redesign", 8), "monitor…")

    def test_the_row_never_shows_a_cut_word(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[session("/a", "alpha", [Worker("w", RUNNING, start=NOW)],
                                                     name="monitor redesign from", tag="CC·W")])
        line = line_with(plain_lines(gs, UIState(), 70), "monitor")
        self.assertNotIn("fro ", line)
        self.assertNotIn("fro…", line)


class TextFilter(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.gs = GlobalSnapshot(now=NOW, stages=[
            session("/a", "alpha", [], name="monitor redesign", tag="CC·W", last=NOW - 60, lead_state="live"),
            session("/b", "beta", [], name="other work", tag="CDX", last=NOW - 60, lead_state="live"),
        ])

    def stages(self, query: str) -> list[str]:
        return [r[0] for r in global_rows(self.gs, UIState(gquery=query)) if r[1] == "stage"]

    def test_the_filter_matches_the_session_name(self) -> None:
        self.assertEqual(self.stages("monitor"), ["/a"])

    def test_the_filter_matches_the_tag(self) -> None:
        self.assertEqual(self.stages("cdx"), ["/b"])


if __name__ == "__main__":
    unittest.main()


class RerunBriefs(unittest.TestCase):
    def test_a_worker_named_twice_in_a_group_is_listed_once(self) -> None:
        workers = [Worker(n, DONE, start=NOW - 900 + i, end=NOW - 800 + i) for i, n in enumerate(("m1", "m2", "m3"))]
        st = session("/r", "research", workers, name="alternatives")
        st.snap.groups = [Group("build", ["m1", "m2", "m3", "m1", "m2"], NOW - 900, done=5, total=5, live=0, finished=True,
                                finished_at=NOW - 700, current=True)]
        ui = UIState(gopen_keys={"s:/r"}, gopen={("/r", "task", "m1|m2|m3|m1|m2")})   # a finished task is a line until opened
        text = "\n".join(plain_lines(GlobalSnapshot(now=NOW, stages=[st]), ui))
        self.assertIn("m1", text)
        for name in ("m1", "m2", "m3"):
            self.assertLessEqual(text.count(name), 1, name)


class SwarmFallback(unittest.TestCase):
    def test_with_no_current_task_the_band_uses_the_newest_not_an_older_failed_one(self) -> None:
        old = [Worker(f"o{i}", FAILED, start=NOW - 900 + i, end=NOW - 800) for i in range(3)]
        new = [Worker(f"n{i}", DONE, start=NOW - 300 + i, end=NOW - 200) for i in range(3)]
        st = session("/s", "proj", old + new, name="sw")
        st.snap.groups = [task(new, "newer"), task(old, "older")]
        for group in st.snap.groups:
            group.current = False
        from render import _rd_g_rowdata, _rd_g_swarm_workers
        self.assertEqual({wk.name for wk in _rd_g_swarm_workers(_rd_g_rowdata(st))}, {"n0", "n1", "n2"})
