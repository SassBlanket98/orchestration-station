from __future__ import annotations

import unittest

import app
from model import (
    GlobalSnapshot,
    Group,
    LeadInfo,
    LogState,
    Snapshot,
    Stage,
    Step,
    UIState,
    Worker,
)
from render import _rd_g_nodes, _rd_g_rowdata, _rd_g_tasks, render_global
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0


def plain(line: str) -> str:
    return _wd_SGR_RE.sub("", line)


def snapshot() -> GlobalSnapshot:
    workers = [Worker(f"worker-{i:02d}", "running", goal=f"goal for worker {i}", start=NOW - 20,
                      log=LogState(steps=[Step(NOW - 1, "tool", "bash", f"command-{i} --do work")]))
               for i in range(12)]
    stage = Stage("/alpha", "alpha", Snapshot(now=NOW, workers=workers), 1, NOW - 60, LeadInfo("luna", "build", "s"))
    stage.state = "active"
    stage.lead_state = "live"
    return GlobalSnapshot(now=NOW, stages=[stage])


class ControlCenterWorkerRows(unittest.TestCase):
    def test_worker_command_lines_and_hit_at_supported_widths(self) -> None:
        for cols, rows in ((120, 30), (160, 40)):
            with self.subTest(cols=cols):
                gs = snapshot()
                ui = UIState(cc=True, gquips=False, glegend=False)
                frame = render_global(gs, ui, cols, rows, NOW)
                text = "\n".join(map(plain, frame))
                header = next(line for line in text.splitlines() if "SESSION" in line)
                self.assertNotIn("NOW", header)
                self.assertIn("command-0", text)
                self.assertTrue(all(_wd_width(line) == cols for line in frame))
                hits = [hit for hit in ui.hits if hit[4] == "ccrow"]
                self.assertTrue(hits)
                self.assertEqual(hits[0][5], "worker-00")
                self.assertEqual(hits[0][0], hits[0][1])

    def test_selected_worker_and_subline_stay_visible_when_scrolling(self) -> None:
        gs = snapshot()
        probe = UIState(cc=True, gquips=False, glegend=False)
        nodes = _rd_g_nodes(gs, probe)
        selected = next(i for i, node in enumerate(nodes) if node.kind == "worker" and node.name == "worker-11")
        ui = UIState(cc=True, gsel=selected, gquips=False, glegend=False)
        frame = list(map(plain, render_global(gs, ui, 120, 30, NOW)))
        name_row = next(i for i, line in enumerate(frame) if "worker-11" in line)
        self.assertTrue(any("command-11" in line for line in frame[name_row + 1:name_row + 3]))

    def test_a_finished_worker_is_one_line_and_a_failed_one_keeps_its_command(self) -> None:
        """the user 2026-10-24: done workers took two lines each; collapsed they are one, live and failed keep the command."""
        def worker(name: str, state: str) -> Worker:
            return Worker(name, state, goal=f"goal of {name}", start=NOW - 90,
                          summary=["## Summary", "all done"] if state == "done" else [],   # a report replaces the step list
                          log=LogState(steps=[Step(NOW - 5, "tool", "bash", f"command-{name} --run")]))
        workers = [worker("live-one", "running"), worker("done-one", "done"), worker("failed-one", "failed")]
        stage = Stage("/alpha", "alpha", Snapshot(now=NOW, workers=workers), 1, NOW - 60, LeadInfo("luna", "build", "s"))
        stage.state, stage.lead_state = "active", "live"
        gs = GlobalSnapshot(now=NOW, stages=[stage])
        def rows(ui: UIState) -> str:    # the session rows only: the Events strip below repeats every command
            return "\n".join(map(plain, render_global(gs, ui, 120, 30, NOW))).split("▸Events")[0]
        text = rows(UIState(cc=True, gquips=False, glegend=False))
        self.assertIn("command-live-one", text)
        self.assertIn("command-failed-one", text)
        self.assertIn("done-one", text)
        self.assertNotIn("command-done-one", text)
        self.assertNotIn("goal of done-one", text)
        probe = UIState(cc=True, gquips=False, glegend=False)
        done = next(i for i, node in enumerate(_rd_g_nodes(gs, probe)) if node.kind == "worker" and node.name == "done-one")
        clicked = UIState(cc=True, gsel=done, expanded={"done-one"}, gquips=False, glegend=False)
        self.assertIn("command-done-one", rows(clicked))
        pressed = UIState(cc=True, gsel=done, gquips=False, glegend=False)   # Enter, as the app routes it (Sol review)
        rows(pressed)                                                         # the frame sets `cc_active`
        app._ap_global_key(pressed, "enter", gs, 30)
        self.assertIn("command-done-one", rows(pressed))

    def test_expanded_worker_says_each_thing_once(self) -> None:
        """your recording 2026-10-24: a flush-left `model ... elapsed` line, and the newest step printed twice."""
        step = Step(NOW - 1, "text", "", "Implementing the changes.")
        worker = Worker("tp2-a", "running", model="composer", goal="finish slice C", start=NOW - 300, pid=7,
                        log=LogState(steps=[Step(NOW - 9, "text", "", "Reading the stream doc"), step]))
        stage = Stage("/alpha", "alpha", Snapshot(now=NOW, workers=[worker]), 1, NOW - 60, LeadInfo("luna", "build", "s"))
        stage.state, stage.lead_state = "active", "live"
        gs = GlobalSnapshot(now=NOW, stages=[stage])
        ui = UIState(cc=True, expanded={"tp2-a"}, cc_strip_open=False, gquips=False, glegend=False)
        lines = [plain(line).rstrip() for line in render_global(gs, ui, 150, 40, NOW)]
        self.assertEqual(sum("Implementing the changes." in line for line in lines), 1)
        self.assertEqual(sum("Reading the stream doc" in line for line in lines), 1)
        self.assertFalse([line for line in lines if line.startswith("model ")])
        self.assertEqual(sum("elapsed " in line for line in lines), 0)   # the ELAPSED column already says it
        card = [line for line in lines if "composer" in line and "pid 7" in line]
        self.assertEqual(len(card), 1)
        self.assertGreater(len(card[0]) - len(card[0].lstrip()), 8)      # the model stays, inside the card

    def test_task_counts_follow_the_workers_listed(self) -> None:
        """Phone screenshot 2026-10-24: `build · 2 live · 2 done` over one live and two done workers (a re-run brief)."""
        workers = [Worker("A2", "running", start=NOW - 60), Worker("A5", "done", start=NOW - 90, end=NOW - 40),
                   Worker("A4", "done", start=NOW - 95, end=NOW - 50)]
        group = Group("build", ["A2", "A2", "A5", "A4"], NOW - 95, done=2, total=4, live=2)
        stage = Stage("/alpha", "alpha", Snapshot(now=NOW, workers=workers, groups=[group]), 1, NOW - 60,
                      LeadInfo("luna", "build", "s"))
        stage.state, stage.lead_state = "active", "live"
        tasks, _ = _rd_g_tasks(_rd_g_rowdata(stage))
        shown = tasks[0][0]
        self.assertEqual((shown.live, shown.done, shown.total), (1, 2, 3))

    def test_classic_80_column_render_is_unchanged_by_cc_flag(self) -> None:
        gs = snapshot()
        normal = render_global(gs, UIState(gquips=False, glegend=False), 80, 30, NOW)
        fallback = render_global(gs, UIState(cc=True, gquips=False, glegend=False), 80, 30, NOW)
        self.assertEqual(normal, fallback)


if __name__ == "__main__":
    unittest.main()
