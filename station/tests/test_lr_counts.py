from __future__ import annotations

import re
import unittest

from taskhelp import open_tasks

from model import (
    DONE,
    FAILED,
    NOREPORT,
    RUNNING,
    GlobalSnapshot,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import _rd_g_chips, _rd_g_rowdata, render_global, render_global_plain
from widgets import _wd_SGR_RE

NOW = 1_760_050_000.0


def plain(lines: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in lines]


def chips(gs: GlobalSnapshot, ui: UIState) -> str:
    return _wd_SGR_RE.sub("", _rd_g_chips(gs, ui, 100, 0))


def fixture() -> GlobalSnapshot:
    run = Stage("/run", "run", Snapshot(now=NOW, workers=[Worker("r", RUNNING, start=NOW - 30)]),
                state="active", lead_state="idle")
    lead = Stage("/lead", "lead", Snapshot(now=NOW), state="active", lead_state="live", solo=True)
    idle = Stage("/idle", "idle", Snapshot(now=NOW), state="idle", lead_state="live")
    done = Stage("/done", "done", Snapshot(now=NOW, workers=[Worker("d", DONE, start=NOW - 90, end=NOW - 60),
                                                             Worker("n", NOREPORT, start=NOW - 50, end=NOW - 40)]),
                 state="active", lead_state="idle")
    return GlobalSnapshot(now=NOW, stages=[run, lead, idle, done])


class WorkingCounts(unittest.TestCase):
    def test_masthead_chips_and_plain_agree_on_working_sessions(self) -> None:
        gs = fixture()
        frame = plain(render_global(gs, UIState(), 100, 30, NOW))
        head = next(line for line in frame if "working" in line and "problem" in line)
        masthead = int(re.search(r"(\d+) working", head).group(1))
        chip = int(re.search(r"working (\d+)", chips(gs, UIState())).group(1))
        plain_head = render_global_plain(gs, 100).splitlines()[0]
        plain_working = int(re.search(r"(\d+) working", plain_head).group(1))
        self.assertEqual((masthead, chip, plain_working), (2, 2, 2))   # a running worker, and a live lead in the active section

    def test_no_report_is_neutral_and_failed_is_a_problem(self) -> None:
        nr = Stage("/n", "nr", Snapshot(now=NOW, workers=[Worker("readme", NOREPORT, start=NOW - 9, end=NOW - 1)]),
                   state="active", lead_state="idle")
        self.assertEqual(_rd_g_rowdata(nr).problems, 0)
        ui = UIState(gopen={("/n", "stage", "")}, gquips=False)
        gs_nr = GlobalSnapshot(now=NOW, stages=[nr])
        open_tasks(gs_nr, ui)                                   # a finished task is a line until opened
        text = "\n".join(plain(render_global(gs_nr, ui, 100, 20, NOW)))
        self.assertIn("○ readme", text)
        self.assertNotIn("ended with an error", text)
        self.assertNotRegex(text, r"!\d")

        failed = Stage("/f", "f", Snapshot(now=NOW, workers=[Worker("boom", FAILED, start=NOW - 9, end=NOW - 1)]),
                       state="active", lead_state="idle")
        self.assertEqual(_rd_g_rowdata(failed).problems, 1)

    def test_chips_respect_the_text_query(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[
            Stage("/alpha", "alpha", Snapshot(now=NOW, workers=[Worker("a", RUNNING, start=NOW - 9)]),
                  state="active", lead_state="idle"),
            Stage("/beta", "beta", Snapshot(now=NOW, workers=[Worker("b", RUNNING, start=NOW - 9)]),
                  state="active", lead_state="idle")])
        self.assertIn("working 2", chips(gs, UIState()))
        self.assertIn("working 1", chips(gs, UIState(gquery="alpha")))
