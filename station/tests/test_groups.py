from __future__ import annotations

import os
import tempfile
import time
import unittest

from taskhelp import open_tasks

import app
import collect
from logparse import LogState
from model import (
    DONE,
    FAILED,
    NOREPORT,
    QUIET,
    RUNNING,
    GlobalSnapshot,
    Group,
    Milestone,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import _rd_cursor_stop, _rd_stops, render_frame, render_global
from widgets import _wd_SGR_RE

NOW = 1_800_000_000.0
COLS, ROWS = 100, 34


def worker(name: str, state: str, start: float, job: str = "", end: float | None = None) -> Worker:
    if end is None and state not in (RUNNING, QUIET):
        end = start + 60
    return Worker(name=name, state=state, model="luna", family="codex", start=start, end=end, job=job,
                  log=LogState(), quiet_s=0.0)


def group_of(*workers: Worker, current: bool = False) -> Group:
    done = sum(wk.state in (DONE, FAILED, NOREPORT) for wk in workers)
    ends = [wk.end for wk in workers if wk.end is not None]
    finished = done == len(workers)
    return Group(label="+".join(sorted({wk.job for wk in workers if wk.job})) or "workers",
                 workers=[wk.name for wk in workers], started=min(wk.start for wk in workers), done=done,
                 total=len(workers), live=sum(wk.state in (RUNNING, QUIET) for wk in workers),
                 finished=finished, finished_at=max(ends) if finished and ends else None, current=current)


def plain(lines: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line).rstrip() for line in lines]


class JobLine(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def brief(self, text: str) -> str:
        path = os.path.join(self._tmp.name, "b.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_first_word_of_the_job_line_lowercased(self) -> None:
        self.assertEqual(collect._co_brief_job(self.brief("# t\nGoal: x\nJob: Build the thing\n")), "build")

    def test_key_is_case_insensitive(self) -> None:
        self.assertEqual(collect._co_brief_job(self.brief("job:   review  \n")), "review")

    def test_absent_or_empty_job_is_blank(self) -> None:
        self.assertEqual(collect._co_brief_job(self.brief("# t\nGoal: x\n")), "")
        self.assertEqual(collect._co_brief_job(self.brief("Job:\n")), "")
        self.assertEqual(collect._co_brief_job(None), "")


class Grouping(unittest.TestCase):
    def test_dispatch_line_makes_a_group_whatever_the_gaps(self) -> None:
        base = NOW
        milestones = [Milestone(base, "dispatch: launched a b c")]
        workers = [worker("a", DONE, base + 1), worker("b", DONE, base + 300), worker("c", RUNNING, base + 900)]
        groups = collect._co_groups(workers, milestones)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].workers, ["a", "b", "c"])
        self.assertEqual((groups[0].done, groups[0].total, groups[0].live), (2, 3, 1))

    def test_same_name_launched_twice_joins_the_nearest_launch(self) -> None:
        milestones = [Milestone(NOW, "dispatch: launched r y"), Milestone(NOW + 600, "dispatch: launched r x")]
        workers = [worker("y", DONE, NOW + 1), worker("r", DONE, NOW + 601), worker("x", DONE, NOW + 602)]
        groups = collect._co_groups(workers, milestones)
        self.assertEqual({tuple(g.workers) for g in groups}, {("y",), ("r", "x")})

    def test_unlaunched_worker_never_joins_a_dispatch_group(self) -> None:
        milestones = [Milestone(NOW, "dispatch: launched a b")]
        workers = [worker("a", DONE, NOW + 1), worker("b", DONE, NOW + 2), worker("loose", DONE, NOW + 3)]
        groups = collect._co_groups(workers, milestones)
        self.assertEqual(sorted(tuple(g.workers) for g in groups), [("a", "b"), ("loose",)])

    def test_unlaunched_workers_group_in_sixty_second_windows(self) -> None:
        starts = [0, 30, 59, 100, 130]
        workers = [worker(f"w{i}", DONE, NOW + s) for i, s in enumerate(starts)]
        groups = collect._co_groups(workers, [])
        self.assertEqual(sorted(tuple(g.workers) for g in groups),
                         [("w0", "w1", "w2"), ("w3", "w4")])

    def test_label_joins_distinct_jobs_in_start_order(self) -> None:
        workers = [worker("a", DONE, NOW, job="build"), worker("b", DONE, NOW + 1, job="read"),
                   worker("c", DONE, NOW + 2, job="build")]
        self.assertEqual(collect._co_groups(workers, [Milestone(NOW, "dispatch: launched a b c")])[0].label,
                         "build+read")

    def test_label_without_jobs_is_the_common_name_prefix(self) -> None:
        workers = [worker("c-coherence", DONE, NOW), worker("c-feasible", DONE, NOW + 1)]
        self.assertEqual(collect._co_groups(workers, [])[0].label, "c")
        workers = [worker("review-plan", DONE, NOW), worker("review-stop-rule", DONE, NOW + 1)]
        self.assertEqual(collect._co_groups(workers, [])[0].label, "review")

    def test_label_falls_back_to_workers_then_to_the_one_name(self) -> None:
        workers = [worker("alpha", DONE, NOW), worker("beta", DONE, NOW + 1)]
        self.assertEqual(collect._co_groups(workers, [])[0].label, "workers")
        self.assertEqual(collect._co_groups([worker("build-config", DONE, NOW)], [])[0].label, "build-config")

    def test_finished_at_is_the_newest_end_once_all_are_done(self) -> None:
        workers = [worker("a", DONE, NOW, end=NOW + 50), worker("b", FAILED, NOW + 1, end=NOW + 90)]
        group = collect._co_groups(workers, [Milestone(NOW, "dispatch: launched a b")])[0]
        self.assertTrue(group.finished)
        self.assertEqual(group.finished_at, NOW + 90)
        self.assertEqual(group.started, NOW)

    def test_current_is_the_newest_group_with_a_live_worker(self) -> None:
        old_live = worker("old", RUNNING, NOW)
        new_done = worker("new", DONE, NOW + 900, end=NOW + 950)
        groups = collect._co_groups([old_live, new_done], [])
        self.assertEqual([g.workers for g in groups], [["new"], ["old"]])   # newest first
        self.assertEqual([g.current for g in groups], [False, True])

    def test_every_group_with_live_workers_is_current(self) -> None:
        groups = collect._co_groups([worker("old", RUNNING, NOW), worker("new", RUNNING, NOW + 900)], [])
        self.assertEqual([g.current for g in groups], [True, True])

    def test_current_falls_back_to_the_newest_group(self) -> None:
        a = worker("a", DONE, NOW, end=NOW + 10)
        b = worker("b", DONE, NOW + 900, end=NOW + 910)
        groups = collect._co_groups([a, b], [])
        self.assertEqual([(g.workers, g.current) for g in groups], [(["b"], True), (["a"], False)])


class CollectorGroups(unittest.TestCase):
    """The real collector on a stage directory: job lines, dispatch lines and start times from files."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.stage = self._tmp.name
        self.base = time.mktime((2026, 10, 9, 14, 0, 0, 0, 0, -1))

    def write(self, rel: str, text: str, mtime: float) -> None:
        path = os.path.join(self.stage, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.utime(path, (mtime, mtime))

    def worker(self, name: str, start: float, job: str) -> None:
        self.write(f"{name}.pid", "4194304", start)          # beyond pid_max: never live
        self.write(f"{name}.log", "line\n", start + 5)
        self.write(f"briefs/{name}.md", f"# {name}\nJob: {job}\n", start)

    def snapshot(self) -> Snapshot:
        collector = collect.Collector(self.stage, quota=False)
        self.addCleanup(collector.close)
        return collector.snapshot(now=self.base + 3600, all_history=True, procs={})

    def test_groups_and_jobs_from_a_stage(self) -> None:
        self.write("PROGRESS.md", "14:00 dispatch: launched build-a build-b\n14:02 dispatch: launched review-x\n",
                   self.base + 130)
        self.worker("build-a", self.base + 1, "build")
        self.worker("build-b", self.base + 2, "build")
        self.worker("review-x", self.base + 120, "review")
        self.worker("loose-c", self.base + 400, "read")
        snap = self.snapshot()
        self.assertEqual({wk.name: wk.job for wk in snap.workers},
                         {"build-a": "build", "build-b": "build", "review-x": "review", "loose-c": "read"})
        self.assertEqual([(g.label, g.workers) for g in snap.groups],
                         [("read", ["loose-c"]), ("review", ["review-x"]), ("build", ["build-a", "build-b"])])
        self.assertEqual([g.current for g in snap.groups], [True, False, False])   # nothing live: newest


class Rendering(unittest.TestCase):
    def setUp(self) -> None:
        self.env = {"NO_COLOR": "1"}
        # build: one running, one done (current); review: finished, earlier
        self.b1 = worker("build-a", RUNNING, NOW - 300, job="build")
        self.b2 = worker("build-b", DONE, NOW - 280, job="build", end=NOW - 100)
        self.r1 = worker("review-x", DONE, NOW - 900, job="review", end=NOW - 700)
        self.snap = Snapshot(title="s", now=NOW, session_start=NOW - 3600, workers=[self.b1, self.b2, self.r1],
                             groups=[group_of(self.b1, self.b2, current=True), group_of(self.r1)])

    def frame(self, ui: UIState, snap: Snapshot | None = None) -> list[str]:
        return plain(render_frame(snap or self.snap, ui, COLS, ROWS, NOW))

    def test_station_header_and_fold_line(self) -> None:
        text = "\n".join(self.frame(UIState(group_open={"*"})))
        self.assertIn("build", text)
        self.assertIn("1/2 workers · started", text)
        self.assertIn("▸ review · 1 worker ✔", text)   # the fold line, one row
        self.assertNotIn("▍review-x", text)            # folded: no worker row for it in the panel

    def test_all_live_groups_are_above_earlier(self) -> None:
        old = worker("old-live", RUNNING, NOW - 900, job="old")
        new = worker("new-live", RUNNING, NOW, job="new")
        done = worker("done", DONE, NOW - 1200, job="done")
        snap = Snapshot(title="s", now=NOW, session_start=NOW - 3600, workers=[new, old, done],
                        groups=[group_of(new, current=True), group_of(old, current=True), group_of(done)])
        text = "\n".join(self.frame(UIState(group_open={"*"}), snap))
        self.assertLess(text.index("new"), text.index("earlier"))
        self.assertLess(text.index("old"), text.index("earlier"))

    def test_fold_line_enter_unfolds_and_enter_again_folds(self) -> None:
        ui = UIState(group_open={"*"})
        self.frame(ui)
        self.assertEqual(_rd_stops(self.snap, ui)[:4], ["w0", "w1", "group:*", "group:review-x"])
        for _ in range(3):   # past build-a, build-b and the "earlier" line
            app._ap_handle_key(ui, "down", self.snap, ROWS)
        self.assertEqual(_rd_cursor_stop(self.snap, ui), "group:review-x")
        app._ap_handle_key(ui, "enter", self.snap, ROWS)
        self.assertEqual(ui.group_open, {"*", "review-x"})
        self.assertIn("review-x", "\n".join(self.frame(ui)))
        self.assertIn("w2", _rd_stops(self.snap, ui))    # its worker row is now a stop
        app._ap_handle_key(ui, "enter", self.snap, ROWS)
        self.assertEqual(ui.group_open, {"*"})

    def test_click_on_fold_line_moves_there_and_toggles(self) -> None:
        ui = UIState(group_open={"*"})
        self.frame(ui)
        col, row = next((c0, r0) for r0, _, c0, _, act, tgt in ui.hits if act == "group" and tgt == "review-x")
        keys = app._ap_pointer_keys(ui, f"click:{col}:{row}", None, self.snap, NOW)
        self.assertEqual(keys, ["down", "down", "down", "enter"])
        for key in keys:
            app._ap_handle_key(ui, key, self.snap, ROWS)
        self.assertEqual(ui.group_open, {"*", "review-x"})

    def test_second_press_of_a_double_click_on_the_fold_line_does_not_toggle_back(self) -> None:
        ui = UIState(group_open={"*"})
        self.frame(ui)
        col, row = next((c0, r0) for r0, _, c0, _, act, tgt in ui.hits if act == "group" and tgt == "review-x")
        for key in app._ap_pointer_keys(ui, f"click:{col}:{row}", None, self.snap, NOW):
            app._ap_handle_key(ui, key, self.snap, ROWS)
        self.assertEqual(ui.group_open, {"*", "review-x"})
        for key in app._ap_pointer_keys(ui, f"click:{col}:{row}", None, self.snap, NOW + 0.1):   # the double's second press
            app._ap_handle_key(ui, key, self.snap, ROWS)
        self.assertEqual(ui.group_open, {"*", "review-x"})

    def test_cursor_on_a_folded_worker_sits_on_its_group_line(self) -> None:
        ui = UIState(sel=2, group_open={"*"})   # review-x, folded away
        self.assertEqual(_rd_cursor_stop(self.snap, ui), "group:review-x")

    def test_station_row_shows_the_lead_phase_and_the_counts(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[Stage("/a", "alpha", self.snap, 1, NOW)])
        ui = open_tasks(gs, UIState())                          # the finished review task is a line until opened
        text = "\n".join(plain(render_global(gs, ui, 100, 24, NOW)))
        self.assertIn("build", text)
        self.assertIn("1 live", text)
        self.assertIn("build-b", text)
        self.assertIn("review-x", text)


class EarlierFold(unittest.TestCase):
    def test_earlier_groups_hide_behind_one_line_until_opened(self) -> None:
        r = Rendering()
        r.setUp()
        closed = "\n".join(r.frame(UIState()))
        self.assertIn("▸ earlier", closed)
        opened = "\n".join(r.frame(UIState(group_open={"*"})))
        self.assertIn("▾ earlier", opened)
        self.assertNotIn("▸ review", closed)
        self.assertIn("▸ review", opened)


if __name__ == "__main__":
    unittest.main()
