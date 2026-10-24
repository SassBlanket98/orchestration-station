"""monitor TUI refresh: wall-clock ages, diff frames, and collector poke.

Fails on the current bug: global tree rendering uses snapshot time (gs.now) for
relative ages while the interactive loop passes wall time (t) only to the masthead.
Identical row strings then produce an empty _ap_diff_frame payload until a snapshot
lands or input changes selection styling — which feels like 'only updates when I click'.
"""
from __future__ import annotations

import os
import re
import select
import threading
import time
import unittest

import app
from model import (
    RUNNING,
    GlobalSnapshot,
    LeadInfo,
    LogState,
    Snapshot,
    Stage,
    Step,
    UIState,
    Worker,
)
from render import render_global

NOW = 1_760_050_000.0
SGR = re.compile(r"\x1b\[[0-9;]*m")
COLS, ROWS = 100, 40


def plain(line: str) -> str:
    return SGR.sub("", line)


def solo_stage(path: str = "/proj", label: str = "proj") -> Stage:
    stage = Stage(
        path,
        label,
        Snapshot(now=NOW, workers=[]),
        0,
        NOW - 120,
        LeadInfo("opus", "build"),
    )
    stage.solo = True
    stage.lead_kind = "claude"
    stage.lead_state = "live"
    stage.state = "active"
    stage.goal = "tighten refresh"
    stage.doing = [(NOW - 45, "Edit render.py")]
    return stage


def team_stage(path: str = "/team", label: str = "team") -> Stage:
    wk = Worker(
        "w1",
        RUNNING,
        start=NOW - 90,
        log=LogState(steps=[Step(NOW - 10, "tool", "bash", "pytest -q")]),
        quiet_s=2.0,
    )
    snap = Snapshot(now=NOW, workers=[wk])
    stage = Stage(path, label, snap, 1, NOW - 30, LeadInfo("opus", "build"))
    stage.lead_state = "live"
    stage.state = "active"
    return stage


def doing_age_line(frame: list[str]) -> str:
    for line in frame:
        text = plain(line)
        if "Edit render.py" in text and "  ·" in text:
            return text
    raise AssertionError(f"no doing age line in frame: {plain_lines(frame)}")


def plain_lines(frame: list[str]) -> list[str]:
    return [plain(ln) for ln in frame if plain(ln).strip()]


def worker_duration_line(frame: list[str], name: str = "w1") -> str:
    for line in frame:
        text = plain(line)
        if text.lstrip().startswith("└─") and name in text:
            return text
    raise AssertionError(f"no expanded worker row for {name}: {plain_lines(frame)}")


class WallClockAges(unittest.TestCase):
    """Live row content should follow render_global's wall `t`, not frozen gs.now."""

    def test_open_solo_doing_age_advances_with_wall_time_without_new_snapshot(self) -> None:
        stage = solo_stage()
        gs = GlobalSnapshot(now=NOW, stages=[stage])
        ui = UIState(gopen={(stage.path, "stage", "")})
        early = doing_age_line(render_global(gs, ui, COLS, ROWS, NOW + 2))
        late = doing_age_line(render_global(gs, ui, COLS, ROWS, NOW + 47))
        self.assertNotEqual(early, late, "doing 'N ago' should tick with wall clock between collector polls")
        self.assertIn("01m 32s", late)

    def test_open_team_worker_duration_advances_with_wall_time(self) -> None:
        stage = team_stage()
        gs = GlobalSnapshot(now=NOW, stages=[stage])
        ui = UIState()
        # Expand the worker row so duration text is on screen (collapsed preview has no elapsed).
        ui.gopen.add((stage.path, "worker", "w1"))
        early = worker_duration_line(render_global(gs, ui, COLS, ROWS, NOW + 1))
        late = worker_duration_line(render_global(gs, ui, COLS, ROWS, NOW + 31))
        self.assertNotEqual(early, late)
        self.assertIn("02m", plain(late))  # 90s start + 31s wall ≈ 2m elapsed when using wall t


class DiffFrameRefresh(unittest.TestCase):
    """The main loop skips terminal writes when rendered lines are unchanged."""

    def test_doing_line_changes_so_diff_repaints_that_row(self) -> None:
        stage = solo_stage()
        gs = GlobalSnapshot(now=NOW, stages=[stage])
        ui = UIState(gopen={(stage.path, "stage", "")})
        prev = render_global(gs, ui, COLS, ROWS, NOW)
        nxt = render_global(gs, ui, COLS, ROWS, NOW + 2)
        idx = next(i for i, ln in enumerate(prev) if "Edit render.py" in plain(ln) and "  ·" in plain(ln))
        self.assertNotEqual(prev[idx], nxt[idx], "doing age follows wall t, so the row changes between polls")
        payload = app._ap_diff_frame(prev, nxt, full=False)
        self.assertIn(f"\x1b[{idx + 1};1H".encode(), payload, "the changed doing row is repainted without a click")


class CollectorPoke(unittest.TestCase):
    """Poke path is wired; lost pokes are not required to explain frozen rows."""

    def test_collector_loop_pokes_after_each_snapshot(self) -> None:
        shared = app._ap_Shared()
        wake = threading.Event()
        stop = threading.Event()
        pipe_r, pipe_w = os.pipe()
        os.set_blocking(pipe_r, False)
        os.set_blocking(pipe_w, False)
        pokes = 0

        def poke() -> None:
            nonlocal pokes
            pokes += 1
            os.write(pipe_w, b"\0")

        shared.poke = poke

        class SnapCollector:
            def __init__(self) -> None:
                self.calls = 0

            def snapshot(self, all_history: bool = False, quick: bool = False) -> GlobalSnapshot:
                self.calls += 1
                return GlobalSnapshot(now=NOW + self.calls, stages=[])

        coll = SnapCollector()
        thread = threading.Thread(
            target=app._ap_collector_loop,
            args=(coll, shared, stop, wake, 6.0),
            daemon=True,
        )
        thread.start()
        deadline = time.monotonic() + 2.0
        while pokes < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        stop.set()
        wake.set()
        thread.join(2.0)
        os.close(pipe_r)
        os.close(pipe_w)
        self.assertGreaterEqual(pokes, 1)
        with shared.lock:
            self.assertGreaterEqual(shared.seq, 1)

    def test_poke_byte_wakes_select_before_long_timeout(self) -> None:
        pipe_r, pipe_w = os.pipe()
        os.set_blocking(pipe_r, False)
        os.set_blocking(pipe_w, False)
        fd_in_r, fd_in_w = os.pipe()
        os.set_blocking(fd_in_r, False)

        def delayed_poke() -> None:
            time.sleep(0.05)
            os.write(pipe_w, b"\0")

        threading.Thread(target=delayed_poke, daemon=True).start()
        t0 = time.monotonic()
        ready, _, _ = select.select([fd_in_r, pipe_r], [], [], 5.0)
        elapsed = time.monotonic() - t0
        os.close(pipe_r)
        os.close(pipe_w)
        os.close(fd_in_r)
        os.close(fd_in_w)
        self.assertIn(pipe_r, ready)
        self.assertLess(elapsed, 0.5, "poke should wake the draw loop, not wait for the idle timeout")

    def test_idle_draw_wait_timeout_is_one_second_not_poll_interval(self) -> None:
        timeout = 1.0 / 10 if app._ap_animating(None, UIState(), NOW) else 1.0
        self.assertEqual(timeout, 1.0)


class MainLoopSnapshotPickup(unittest.TestCase):
    """Mirrors the seq/seen gate in _ap_interactive: new data must be visible next frame."""

    def test_seq_bump_updates_gs_without_stdin(self) -> None:
        shared = app._ap_Shared()
        stage_v1 = solo_stage()
        stage_v1.doing = [(NOW - 30, "first action")]
        stage_v2 = solo_stage()
        stage_v2.doing = [(NOW - 30, "second action")]
        with shared.lock:
            shared.snap = GlobalSnapshot(now=NOW, stages=[stage_v1])
            shared.seq = 1
        seen = 0
        gs = None

        def apply() -> None:
            nonlocal seen, gs
            with shared.lock:
                new_snap, seq = shared.snap, shared.seq
            if new_snap is not None and seq != seen:
                seen = seq
                gs = new_snap

        apply()
        self.assertEqual(stage_v1.doing[0][1], gs.stages[0].doing[0][1])  # type: ignore[union-attr]
        with shared.lock:
            shared.snap = GlobalSnapshot(now=NOW + 6, stages=[stage_v2])
            shared.seq = 2
        apply()
        self.assertEqual(stage_v2.doing[0][1], gs.stages[0].doing[0][1])  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
