"""The session page: identity, metrics, the lead's activity and only this session's workers."""
from __future__ import annotations

import unittest

import app
from model import (
    DONE,
    FAILED,
    RUNNING,
    GlobalSnapshot,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import render_global
from sessionview import _sv_key, _sv_page
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0


def plain(lines: list[str]) -> str:
    return "\n".join(_wd_SGR_RE.sub("", line) for line in lines)


def make_stage(n: int = 3) -> Stage:
    workers = [Worker(f"w{i}", RUNNING if i == 0 else (FAILED if i == 1 else DONE), goal=f"goal {i}",
                      start=NOW - 100) for i in range(n)]
    st = Stage("/p/.stage", "proj", Snapshot(now=NOW, workers=workers), state="active", lead_state="live")
    st.session_name, st.goal = "my session", "ship the console"
    st.doing = [(NOW - 5, "Edit render.py")]
    return st


class SessionPage(unittest.TestCase):
    def test_exact_size_at_all_widths(self) -> None:
        for cols in (60, 80, 120, 160):
            for rows in (12, 30):
                page = _sv_page(make_stage(), UIState(), cols, rows, NOW)
                self.assertEqual(len(page), rows)
                self.assertTrue(all(_wd_width(line) == cols for line in page), (cols, rows))

    def test_counts_only_this_sessions_workers(self) -> None:
        text = plain(_sv_page(make_stage(5), UIState(), 120, 30, NOW))
        self.assertIn("5 spawned", text)
        self.assertIn("1 running", text)
        self.assertIn("1 failed", text)
        self.assertIn("Edit render.py", text)
        self.assertIn("ship the console", text)

    def test_empty_session_says_so(self) -> None:
        text = plain(_sv_page(make_stage(0), UIState(), 100, 30, NOW))
        self.assertIn("0 spawned", text)
        self.assertIn("no workers", text)

    def test_cursor_stays_in_view_in_a_long_list(self) -> None:
        stage = make_stage(60)
        ui = UIState(sel=55)
        self.assertIn("w55", plain(_sv_page(stage, ui, 100, 20, NOW)))

    def test_keys_move_and_open_a_worker(self) -> None:
        stage, ui = make_stage(), UIState()
        _sv_key(ui, stage, "down", 30)
        self.assertEqual(ui.sel, 1)
        self.assertEqual(_sv_key(ui, stage, "enter", 30), "worker:w1")


class SessionRouting(unittest.TestCase):
    def gs(self) -> GlobalSnapshot:
        return GlobalSnapshot(now=NOW, stages=[make_stage()])

    def test_space_opens_the_session_page_and_a_worker_from_it_returns_here(self) -> None:
        gs, ui = self.gs(), UIState()
        ui.gsel = next(i for i, r in enumerate(app.global_rows(gs, ui)) if r[1] == "stage")
        app._ap_global_key(ui, "space", gs, 30)
        self.assertEqual((ui.gpage, ui.gstage), ("sess", "/p/.stage"))
        self.assertIn("WORKERS THIS SESSION", plain(render_global(gs, ui, 100, 30, NOW)))
        app._ap_global_key(ui, "enter", gs, 30)
        self.assertEqual((ui.gpage, ui.gworker), ("wk", "w0"))
        app._ap_global_key(ui, "esc", gs, 30)
        self.assertEqual(ui.gpage, "sess")
        app._ap_global_key(ui, "esc", gs, 30)
        self.assertEqual(ui.gpage, "")

    def test_enter_on_a_worker_row_expands_and_space_opens_its_page(self) -> None:
        gs, ui = self.gs(), UIState()
        ui.gopen.add(("/p/.stage", "stage", ""))
        rows = app.global_rows(gs, ui)
        ui.gsel = next(i for i, r in enumerate(rows) if r[1] == "worker")
        app._ap_global_key(ui, "enter", gs, 30)
        self.assertEqual(ui.gpage, "")
        app._ap_global_key(ui, "space", gs, 30)
        self.assertEqual(ui.gpage, "wk")
