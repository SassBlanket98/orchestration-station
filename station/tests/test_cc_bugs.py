"""Two regressions in the global tree: the plain footer, and the half sentence on an opened worker."""
from __future__ import annotations

import unittest

from taskhelp import open_tasks

from model import (
    DONE,
    RUNNING,
    GlobalSnapshot,
    LeadInfo,
    LogState,
    Snapshot,
    Stage,
    Step,
    UIState,
    Worker,
    WorkState,
)
from render import render_global, render_global_plain
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_760_050_000.0
OLD = NOW - 3 * 3600          # older than IDLE_LIST_S: a closed session the count line keeps
GOAL = ("Wrapping the worker goal so that the third visible line never ends mid sentence without a marker and the rest of the "
        "text stays discoverable instead of disappearing quietly off the right edge of the panel where only half of a long "
        "sentence used to show at all")
STEP = ("the long step sentence that runs past the column edge so that only the beginning of it is ever visible "
        "anywhere on the row itself")


def plain(lines: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in lines]


def closed_stage(path: str, label: str, dirty: bool = False) -> Stage:
    stage = Stage(path, label, Snapshot(now=NOW), 0, OLD, LeadInfo("opus"))
    stage.state = "closed"
    stage.work = WorkState(dirty=True) if dirty else WorkState(dirty=False, branch="main", unmerged=False)
    return stage


def live_stage(worker: Worker) -> Stage:
    stage = Stage("/a", "alpha", Snapshot(now=NOW, workers=[worker]), 1, NOW - 30, LeadInfo("luna", "build", "s"))
    stage.state = "active"
    stage.lead_state = "live"
    return stage


def long_worker() -> Worker:
    log = LogState(steps=[Step(NOW - 3, "tool", "bash", STEP, 4.2, "ok")], last_ts=NOW - 3, tokens_in=1200,
                   tokens_out=200, ctx_tokens=50_000, ctx_window=200_000, cost=0.12)
    return Worker("long-worker-name", RUNNING, goal=GOAL, model="luna high", start=NOW - 240, quiet_s=3, log=log)


def opened_lines(cols: int) -> list[str]:
    worker = long_worker()
    gs = GlobalSnapshot(now=NOW, stages=[live_stage(worker)])
    ui = UIState(gopen={("/a", "stage", ""), ("/a", "worker", worker.name)}, gquips=False, glegend=False)
    return plain(render_global(gs, ui, cols, 40, NOW))


class PlainFooterTests(unittest.TestCase):
    def test_plain_footer_is_the_closed_count_line(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[live_stage(long_worker()), closed_stage("/b", "beta", dirty=True),
                                             closed_stage("/c", "gamma")], hidden_idle=5, dismissed=2)
        text = render_global_plain(gs, 140)
        footers = [line for line in text.splitlines() if "need sweep" in line]
        self.assertEqual(len(footers), 1, text)
        footer = footers[0]
        self.assertIn("2 closed · 1 need sweep", footer)
        self.assertIn("a shows closed", footer)
        self.assertIn("session-sweep cleans up", footer)
        self.assertIn("2 dismissed", footer)
        self.assertNotIn("idle sessions hidden", text)

    def test_plain_footer_matches_the_tree_line(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[closed_stage("/b", "beta", dirty=True), closed_stage("/c", "gamma")],
                            dismissed=2)
        plain_footer = next(line for line in render_global_plain(gs, 140).splitlines() if "need sweep" in line)
        tree_footer = next(line for line in plain(render_global(gs, UIState(gquips=False, glegend=False), 140, 40, NOW))
                           if "need sweep" in line)
        for phrase in ("2 closed · 1 need sweep", "a shows closed", "session-sweep cleans up", "2 dismissed"):
            self.assertIn(phrase, plain_footer)
            self.assertIn(phrase, tree_footer)

    def test_plain_footer_is_absent_when_nothing_is_closed(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[live_stage(long_worker())])
        self.assertNotIn("need sweep", render_global_plain(gs, 140))


class OpenedWorkerTests(unittest.TestCase):
    def detail(self, lines: list[str], marker: str = "long-worker-name") -> tuple[int, list[str]]:
        """The opened worker's row index and its detail lines (the box under the row)."""
        row = next(i for i, line in enumerate(lines) if marker in line)
        start = next(i for i in range(row + 1, len(lines)) if lines[i].lstrip().startswith("│"))
        out = []
        for line in lines[start:]:
            if not line.lstrip().startswith("│"):
                break
            out.append(line)
        return row, out

    def goal_lines(self, detail: list[str]) -> list[str]:
        start = next(i for i, line in enumerate(detail) if "goal" in line)
        end = next(i for i in range(start + 1, len(detail)) if "model" in detail[i])
        return detail[start:end]

    def test_the_row_carries_no_half_finished_step(self) -> None:
        for cols in (80, 100, 120, 160):
            with self.subTest(cols=cols):
                lines = opened_lines(cols)
                row = next(i for i, line in enumerate(lines) if "long-worker-name" in line)
                start = next(i for i in range(row + 1, len(lines)) if lines[i].lstrip().startswith("│"))
                for line in lines[row:start]:      # the row and any sub-line, never a truncated copy of the step
                    self.assertNotIn("the long step sentence", line)

    def test_detail_lines_share_one_indent_and_do_not_stop_mid_sentence(self) -> None:
        for cols in (80, 100, 120, 160):
            with self.subTest(cols=cols):
                _row, detail = self.detail(opened_lines(cols))
                indents = {len(line) - len(line.lstrip(" ")) for line in detail}
                self.assertEqual(len(indents), 1, detail)
                goal = self.goal_lines(detail)
                shown = " ".join(line.strip() for line in goal)
                if "used to show at all" in shown:
                    self.assertNotIn("…", goal[-1])      # the whole goal fits
                else:
                    self.assertTrue(goal[-1].rstrip().endswith("…"), goal)   # the rest is marked, not lost
                for line in detail:                        # nothing clipped at the edge without a marker
                    body = line.rstrip()
                    if _wd_width(body) >= cols - 1:
                        self.assertTrue(body.endswith(("…", "→")), body)

    def test_a_long_summary_marks_the_line_it_cuts(self) -> None:
        worker = Worker("done-worker", DONE, start=NOW - 900, end=NOW - 10,
                        summary=[f"summary line {i} that is complete in itself" for i in range(5)])
        stage = Stage("/a", "alpha", Snapshot(now=NOW, workers=[worker]), 0, NOW - 10, LeadInfo("luna"))
        stage.state = "closed"
        gs = GlobalSnapshot(now=NOW, stages=[stage])
        ui = UIState(gopen={("/a", "stage", ""), ("", "more", "finished:/a"), ("/a", "worker", worker.name)},
                     gquips=False, glegend=False)
        open_tasks(gs, ui)
        _row, detail = self.detail(plain(render_global(gs, ui, 120, 40, NOW)), "done-worker")
        summaries = [line for line in detail if "summary line" in line]
        self.assertEqual(len(summaries), 3)                    # three of the five
        self.assertTrue(summaries[-1].rstrip().endswith("…"))  # the cut is marked, not silent


if __name__ == "__main__":
    unittest.main()
