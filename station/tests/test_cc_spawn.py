"""C6d: model picker and one-line task hand-off."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ccspawn
from ccactions import Choice
from model import GlobalSnapshot, Stage, UIState


class SpawnFlow(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.stage = Stage(path=str(self.root / "stage"), label="proj", dir=str(self.project))
        self.gs = GlobalSnapshot(stages=[self.stage])
        self.sel = (self.stage.path, "", "")
        self.ok = Choice("luna", "luna  go 5h 10%", True)
        self.bad = Choice("tight", "tight  go 5h 99%", False, "headroom 1%")
        ccspawn._CX_CHOICES = []
        ccspawn._CX_CHOICES_AT = None

    def choices(self, picks: list[Choice] | None = None) -> mock._patch:
        return mock.patch.object(ccspawn.ccactions, "spawn_choices",
                                 return_value=picks if picks is not None else [self.ok, self.bad])

    def test_open_refusals(self) -> None:
        ui = UIState()
        ccspawn.spawn_open(ui, self.gs, None)
        self.assertEqual(ui.note, "spawn: select a session first")
        ccspawn.spawn_open(ui, self.gs, ("/missing", "", ""))
        self.assertEqual(ui.note, "spawn: no project directory for this row")
        self.stage.dir = str(self.root / "missing")
        ccspawn.spawn_open(ui, self.gs, self.sel)
        self.assertEqual(ui.note, "spawn: no project directory for this row")
        self.assertFalse(ui.cc_spawn)

    def test_picker_navigation_clamps_and_grey_choice_explains(self) -> None:
        ui = UIState()
        with self.choices():
            ccspawn.spawn_open(ui, self.gs, self.sel)
            ccspawn.spawn_key(ui, "up", self.gs, self.sel)
            self.assertEqual(ui.cc_spawn_i, 0)
            ccspawn.spawn_key(ui, "down", self.gs, self.sel)
            ccspawn.spawn_key(ui, "j", self.gs, self.sel)
            self.assertEqual(ui.cc_spawn_i, 1)
            ccspawn.spawn_key(ui, "enter", self.gs, self.sel)
            self.assertEqual(ui.note, "headroom 1%")
            self.assertEqual(ui.cc_spawn_model, "")

    def test_text_input_space_backspace_and_empty_enter(self) -> None:
        ui = UIState()
        with self.choices():
            ccspawn.spawn_open(ui, self.gs, self.sel)
            ccspawn.spawn_key(ui, "enter", self.gs, self.sel)
            ccspawn.spawn_key(ui, "enter", self.gs, self.sel)
            self.assertIsNone(ui.cc_confirm)
            self.assertTrue(ui.cc_spawn)
            ccspawn.spawn_key(ui, "a", self.gs, self.sel)
            ccspawn.spawn_key(ui, "space", self.gs, self.sel)
            ccspawn.spawn_key(ui, "b", self.gs, self.sel)
            ccspawn.spawn_key(ui, "backspace", self.gs, self.sel)
            self.assertEqual(ui.cc_input, "a ")
            self.assertTrue(ui.note.startswith("task for luna: a ▌"))

    def test_task_text_is_capped_at_three_hundred_characters(self) -> None:
        ui = UIState()
        with self.choices():
            ccspawn.spawn_open(ui, self.gs, self.sel)
            ccspawn.spawn_key(ui, "enter", self.gs, self.sel)
            for _ in range(301):
                ccspawn.spawn_key(ui, "x", self.gs, self.sel)
        self.assertEqual(len(ui.cc_input), 300)

    def test_confirm_text_reresolves_project_and_closes_picker(self) -> None:
        ui = UIState()
        with self.choices():
            ccspawn.spawn_open(ui, self.gs, self.sel)
            ccspawn.spawn_key(ui, "enter", self.gs, self.sel)
            for char in "build it":
                ccspawn.spawn_key(ui, char, self.gs, self.sel)
            ccspawn.spawn_key(ui, "enter", self.gs, self.sel)
        self.assertEqual(ui.cc_confirm, (
            "spawn", f"{self.stage.path}|", f"Spawn luna in {self.project}: build it?  (y/n)"))
        self.assertFalse(ui.cc_spawn)
        self.assertEqual(ui.note, "")
        self.assertEqual(ui.cc_spawn_model, "luna")
        self.assertEqual(ui.cc_input, "build it")

    def test_confirm_uses_the_row_that_opened_the_picker(self) -> None:
        project_b = self.root / "project-b"
        project_b.mkdir()
        stage_b = Stage(path=str(self.root / "stage-b"), label="proj-b", dir=str(project_b))
        self.gs.stages.append(stage_b)
        ui = UIState()
        sel_b = (stage_b.path, "", "")
        with self.choices():
            ccspawn.spawn_open(ui, self.gs, self.sel)
            ccspawn.spawn_key(ui, "enter", self.gs, self.sel)
            ccspawn.spawn_key(ui, "x", self.gs, sel_b)
            ccspawn.spawn_key(ui, "enter", self.gs, sel_b)
        self.assertEqual(ui.cc_confirm[1], f"{self.stage.path}|")
        with mock.patch.object(ccspawn.ccactions, "spawn", return_value="spawned") as spawn:
            ccspawn.spawn_run(ui, self.gs, ui.cc_confirm[1])
        spawn.assert_called_once_with("luna", str(self.project), "x")
        self.assertEqual(ui.cc_spawn_row, "")

    def test_enter_refuses_when_the_opening_stage_is_gone(self) -> None:
        ui = UIState(cc_spawn=True, cc_spawn_model="luna", cc_input="task", cc_spawn_row="/gone|")
        ccspawn.spawn_key(ui, "enter", GlobalSnapshot(), self.sel)
        self.assertIsNone(ui.cc_confirm)
        self.assertIn("row", ui.note)

    def test_spawn_run_calls_spawn_and_clears_input(self) -> None:
        ui = UIState(cc_spawn_model="luna", cc_input="do task")
        with mock.patch.object(ccspawn.ccactions, "spawn", return_value="spawned") as spawn:
            result = ccspawn.spawn_run(ui, self.gs, f"{self.stage.path}|worker")
        self.assertEqual(result, "spawned")
        spawn.assert_called_once_with("luna", str(self.project), "do task")
        self.assertEqual((ui.cc_spawn_model, ui.cc_input), ("", ""))

    def test_spawn_run_missing_row_clears_input(self) -> None:
        ui = UIState(cc_spawn_model="luna", cc_input="do task")
        with mock.patch.object(ccspawn.ccactions, "spawn") as spawn:
            self.assertEqual(ccspawn.spawn_run(ui, self.gs, "/missing|"), "spawn: row is gone")
        spawn.assert_not_called()
        self.assertEqual((ui.cc_spawn_model, ui.cc_input), ("", ""))

    def test_escape_cancels_in_picker_and_input(self) -> None:
        for input_mode in (False, True):
            ui = UIState()
            with self.choices():
                ccspawn.spawn_open(ui, self.gs, self.sel)
                if input_mode:
                    ccspawn.spawn_key(ui, "enter", self.gs, self.sel)
                self.assertEqual(ccspawn.spawn_key(ui, "esc", self.gs, self.sel), "")
            self.assertFalse(ui.cc_spawn)
            self.assertEqual((ui.cc_spawn_model, ui.cc_input), ("", ""))
            self.assertEqual(ui.note, "spawn cancelled")

    def test_choices_cache_expires_after_thirty_seconds(self) -> None:
        now = [10.0]
        picks = [self.ok]
        with mock.patch.object(ccspawn, "_now", side_effect=lambda: now[0]), \
             mock.patch.object(ccspawn.ccactions, "spawn_choices", return_value=picks) as load:
            self.assertEqual(ccspawn.choices(), picks)
            self.assertEqual(ccspawn.choices(), picks)
            self.assertEqual(load.call_count, 1)
            now[0] = 40.1
            self.assertEqual(ccspawn.choices(), picks)
            self.assertEqual(load.call_count, 2)

    def test_empty_choices_keep_picker_closed(self) -> None:
        ui = UIState()
        with self.choices([]):
            ccspawn.spawn_open(ui, self.gs, self.sel)
        self.assertFalse(ui.cc_spawn)
        self.assertEqual(ui.note, "spawn: picker unavailable")

    def test_unavailable_choice_uses_label_as_reason(self) -> None:
        ui = UIState()
        unavailable = [Choice("", "quota unavailable", False)]
        with self.choices(unavailable):
            ccspawn.spawn_open(ui, self.gs, self.sel)
        self.assertFalse(ui.cc_spawn)
        self.assertEqual(ui.note, "spawn: quota unavailable")


if __name__ == "__main__":
    unittest.main()
