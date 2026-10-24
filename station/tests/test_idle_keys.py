"""The pinned marker on a station row (idle/active override), and the p key that sets the pin. The collector side is a stub here."""
from __future__ import annotations

import threading
import unittest

import app
from model import (
    DONE,
    RUNNING,
    GlobalSnapshot,
    LeadInfo,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import _rd_g_keys, _rd_g_rowdata, _rd_HELP, _rd_keyline, render_global
from widgets import _wd_SGR_RE

NOW = 1_760_050_000.0


def _row(pinned: str | None):
    stage = Stage("/s", "proj", Snapshot(now=NOW, workers=[Worker("run", RUNNING, start=NOW - 9)]), 0, NOW - 5, LeadInfo("opus"))
    if pinned is not None:
        stage.pinned = pinned   # the field the other worker adds to Stage; set directly here
    return _rd_g_rowdata(stage)


def _line(row, w: int = 100, selected: bool = False) -> str:
    gs = GlobalSnapshot(now=NOW, stages=[row.stage])
    ui = UIState(gsel=1 if selected else 2)
    frame = render_global(gs, ui, w, 30, NOW)
    return next(_wd_SGR_RE.sub("", ln) for ln in frame if row.stage.label in _wd_SGR_RE.sub("", ln) and "◆" in ln)


def _plain(text: str) -> str:
    return _wd_SGR_RE.sub("", text)


class StubCollector:
    """Stands in for the global collector: only toggle_idle, which returns "idle", "active" or ""."""

    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[str] = []

    def toggle_idle(self, path: str) -> str:
        self.calls.append(path)
        return self.result


class PinKey(unittest.TestCase):
    def setUp(self) -> None:
        self.stage = Stage("/x/.stage", "proj", Snapshot(workers=[Worker("w", DONE)]))
        self.gs = GlobalSnapshot(stages=[self.stage])
        self.ui = UIState()
        self.stage.state = "active"
        self.ui.gsel = 1   # row 0 is the section heading; the station is row 1

    def test_p_on_the_selected_station_asks_for_a_pin(self) -> None:
        self.assertEqual(app._ap_global_key(self.ui, "p", self.gs, 30), "pin")

    def test_p_calls_toggle_idle_for_the_selected_station_and_sets_the_notice(self) -> None:
        cases = {"idle": "proj: moved to idle", "active": "proj: moved to active", "": "proj: automatic again"}
        for result, notice in cases.items():
            with self.subTest(result=result):
                gc = StubCollector(result)
                self.assertTrue(app._ap_pin_selected(self.ui, self.gs, gc))   # type: ignore[arg-type]
                self.assertEqual(gc.calls, ["/x/.stage"])
                self.assertEqual(self.ui.note, notice)

    def test_p_with_no_selection_does_nothing(self) -> None:
        empty = GlobalSnapshot(stages=[])
        self.assertEqual(app._ap_global_key(self.ui, "p", empty, 30), "")
        gc = StubCollector("idle")
        self.assertFalse(app._ap_pin_selected(self.ui, empty, gc))   # type: ignore[arg-type]
        self.assertEqual(gc.calls, [])
        self.assertEqual(self.ui.note, "")

    def test_the_pin_action_asks_the_loop_to_wake_the_collector(self) -> None:
        # the main loop does: if _ap_pin_selected(...): wake.set(); so the helper's return value is the wake decision
        wake = threading.Event()
        gc = StubCollector("active")
        if app._ap_pin_selected(self.ui, self.gs, gc):   # type: ignore[arg-type]
            wake.set()
        self.assertTrue(wake.is_set())

    def test_no_wake_when_there_is_nothing_to_toggle(self) -> None:
        wake = threading.Event()
        empty = GlobalSnapshot(stages=[])
        if app._ap_pin_selected(self.ui, empty, StubCollector("idle")):   # type: ignore[arg-type]
            wake.set()
        self.assertFalse(wake.is_set())

    def test_i_still_toggles_the_legend_and_does_not_pin(self) -> None:
        before = self.ui.glegend
        gc = StubCollector("idle")
        self.assertEqual(app._ap_global_key(self.ui, "i", self.gs, 30), "")
        self.assertNotEqual(self.ui.glegend, before)
        self.assertEqual(gc.calls, [])

    def test_the_keys_help_lists_p(self) -> None:
        self.assertTrue(any("p" in keys.split() for keys, _ in _rd_HELP))


class PinFooter(unittest.TestCase):
    def test_the_footer_leaves_p_to_the_help(self) -> None:
        """the user 2026-10-24: the footer carries the keys that move, open, find and filter; p lives in the ? help."""
        for w in (60, 120, 200):
            self.assertNotIn("[p]", _plain(_rd_keyline(_rd_g_keys(UIState()), w)))


class PinnedMarker(unittest.TestCase):
    def test_a_pinned_station_still_renders_in_the_tree(self) -> None:
        self.assertIn("proj", _line(_row("idle")))
        self.assertIn("proj", _line(_row("active")))

    def test_pin_state_does_not_change_the_station_label(self) -> None:
        self.assertEqual(_line(_row(None)), _line(_row("")))
        self.assertEqual(_line(_row("idle")), _line(_row(None)))


if __name__ == "__main__":
    unittest.main()
