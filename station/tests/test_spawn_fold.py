"""the user 2026-10-24: folding a lead folds the sessions it spawned into it; the fold counts them and never hides a failure."""
from __future__ import annotations

import re
import unittest

import app
from model import FAILED, RUNNING, GlobalSnapshot, Snapshot, Stage, UIState, Worker
from render import global_rows, render_global
from widgets import _wd_SGR_RE

NOW = 1_760_050_000.0


def stage(path: str, name: str, sid: str, parent: str = "", workers: list[Worker] | None = None) -> Stage:
    workers = workers or []
    st = Stage(path, name, Snapshot(now=NOW, workers=workers), sum(w.state == RUNNING for w in workers), NOW - 20)
    st.lead_state, st.solo, st.session_name, st.session_id, st.parent = "live", not workers, name, sid, parent
    st.state, st.dir = "active", "/proj"      # one project, so the spawns nest under their launcher
    return st


def world(failed: bool = False) -> GlobalSnapshot:
    spawn_workers = [Worker("p1", FAILED if failed else RUNNING, start=NOW - 30)]
    return GlobalSnapshot(now=NOW, stages=[
        stage("/proj/lead", "the lead", "L"),
        stage("/proj/a", "spawn a", "A", "L", spawn_workers),
        stage("/proj/b", "spawn b", "B", "L", [Worker("p2", RUNNING, start=NOW - 30)]),
    ])


def text(gs: GlobalSnapshot, ui: UIState) -> str:
    return "\n".join(_wd_SGR_RE.sub("", line) for line in render_global(gs, ui, 140, 30, NOW))


def paths(gs: GlobalSnapshot, ui: UIState) -> list[str]:
    return [p for p, kind, _ in global_rows(gs, ui) if kind == "stage"]


class SpawnFold(unittest.TestCase):
    def test_open_lead_lists_its_spawns_and_enter_folds_them_in(self) -> None:
        gs, ui = world(), UIState()
        self.assertEqual(paths(gs, ui), ["/proj/lead", "/proj/a", "/proj/b"])
        lead = next(i for i, r in enumerate(global_rows(gs, ui)) if r[0] == "/proj/lead" and r[1] == "stage")
        ui.gsel = lead
        app._ap_global_key(ui, "enter", gs, 30)
        self.assertEqual(paths(gs, ui), ["/proj/lead"])
        self.assertIn("▸ 2 spawns", text(gs, ui))
        self.assertNotIn("spawn a", text(gs, ui))
        app._ap_global_key(ui, "enter", gs, 30)
        self.assertEqual(paths(gs, ui), ["/proj/lead", "/proj/a", "/proj/b"])
        self.assertNotIn("▸ 2 spawns", text(gs, ui))

    def test_a_folded_lead_still_shows_a_failure_inside(self) -> None:
        gs, ui = world(failed=True), UIState()
        ui.gshut_keys.add("s:L")
        self.assertIn("▸ 2 spawns · ▲ 1", text(gs, ui))

    def test_the_tail_shrinks_with_the_name_column_and_keeps_the_failure(self) -> None:
        """Sol delta review 2026-10-24: at 40 columns a folded row two levels down lost its `▲1` to a clip."""
        failing = [Worker("p", FAILED, start=NOW - 30)]
        gs = GlobalSnapshot(now=NOW, stages=[
            stage("/proj/r", "root", "R"),
            stage("/proj/m", "middle", "M", "R"),
            stage("/proj/g", "grand", "G", "M"),
            stage("/proj/x", "leaf", "X", "G", failing),
        ])
        ui = UIState(gshut_keys={"s:G"})
        for cols in (40, 46, 60, 80, 140):
            with self.subTest(cols=cols):
                lines = [_wd_SGR_RE.sub("", x) for x in render_global(gs, ui, cols, 30, NOW)]
                self.assertTrue(all(len(x) == cols for x in lines), cols)
                rows = [x for x in lines if re.search(r"▲ ?1", x)]
                self.assertTrue(rows, (cols, "\n".join(lines)))
                header = next(x for x in lines if "SESSION" in x)
                # the metric block starts where the header's does: the tail did not shift it
                for key in ("LEAD", "WORKER"):
                    if key in header:
                        self.assertTrue(all(x.find(key) in (-1, header.find(key)) for x in rows), (cols, rows))

    def test_a_row_with_no_spawns_shows_no_fold_tail(self) -> None:
        gs, ui = world(), UIState()
        ui.gshut_keys.add("s:A")      # a spawn folded over nothing
        self.assertNotIn("spawn a", text(gs, UIState(gshut_keys={"s:L"})))
        self.assertNotIn("▸ 0", text(gs, ui))


if __name__ == "__main__":
    unittest.main()
