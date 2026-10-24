"""Control center: the spawn flow (model picker, then one line of task text, then the confirm). Imports model and
ccactions only. cckeys calls it; it never touches processes itself (ccactions.spawn does, behind the confirm)."""
from __future__ import annotations

import os
import time

from ccactions import Choice, cx_is_demo, spawn, spawn_choices
from model import GlobalSnapshot, Stage, UIState

try:
    import ccactions
except ModuleNotFoundError:
    class _CXActions:
        spawn = staticmethod(spawn)
        spawn_choices = staticmethod(spawn_choices)

    ccactions = _CXActions()

_now = time.time
_CX_CHOICES: list[Choice] = []
_CX_CHOICES_AT: float | None = None
_CX_CHOICES_DEMO = False


def choices() -> list[Choice]:
    """The picker list from `ccactions.spawn_choices()`, cached for 30 s (loaded by spawn_open)."""
    global _CX_CHOICES, _CX_CHOICES_AT, _CX_CHOICES_DEMO
    now = _now()
    if _CX_CHOICES_AT is None or now - _CX_CHOICES_AT > 30 or _CX_CHOICES_DEMO != cx_is_demo():
        _CX_CHOICES = ccactions.spawn_choices()
        _CX_CHOICES_AT = now
        _CX_CHOICES_DEMO = cx_is_demo()
    return _CX_CHOICES


def project_dir_of(gs: GlobalSnapshot, row_key: str) -> str:
    """The directory a spawn from the row `row_key` ("<stage path>|<worker>") starts in: stage.cwd, else stage.dir,
    else stage.path, the first that is an existing directory; "" when none or the stage is gone."""
    stage_path = row_key.rsplit("|", 1)[0]
    stage = next((item for item in gs.stages if item.path == stage_path), None)
    if stage is None:
        return ""
    for path in (stage.cwd, stage.dir, stage.path):
        if path and (cx_is_demo() or os.path.isdir(path)):
            return path
    return ""


def _cx_stage(gs: GlobalSnapshot, sel: tuple[str, str, str] | None) -> Stage | None:
    if sel is None:
        return None
    return next((stage for stage in gs.stages if stage.path == sel[0]), None)


def _cx_stage_key(gs: GlobalSnapshot, row_key: str) -> Stage | None:
    """The stage named by a stored spawn row key, or None when it has gone."""
    stage_path = (row_key or "").rsplit("|", 1)[0]
    return next((stage for stage in gs.stages if stage.path == stage_path), None)


def _prompt(ui: UIState) -> None:
    picks = choices()
    if not picks:
        ui.note = "spawn: picker unavailable"
        return
    index = min(max(ui.cc_spawn_i, 0), len(picks) - 1)
    ui.cc_spawn_i = index
    choice = picks[index]
    if not choice.id:
        ui.note = f"spawn: {choice.label}"
        return
    reason = f" ({choice.reason})" if not choice.ok else ""
    ui.note = f"spawn: ↑↓ pick a model, Enter select, Esc cancel · {choice.label}{reason}"


def spawn_open(ui: UIState, gs: GlobalSnapshot, sel: tuple[str, str, str] | None) -> None:
    """`n`: open the picker (ui.cc_spawn True, cc_spawn_i 0, cc_spawn_model "", cc_input "") and write its prompt to ui.note.
    Refuses with a ui.note and stays closed when nothing is selected or the row has no project directory."""
    if sel is None:
        ui.cc_spawn = False
        ui.cc_spawn_row = ""
        ui.note = "spawn: select a session first"
        return
    stage = _cx_stage(gs, sel)
    if stage is None or not project_dir_of(gs, f"{stage.path}|"):
        ui.cc_spawn = False
        ui.cc_spawn_row = ""
        ui.note = "spawn: no project directory for this row"
        return
    ui.cc_spawn, ui.cc_spawn_i = True, 0
    ui.cc_spawn_row = f"{stage.path}|"
    ui.cc_spawn_model, ui.cc_input = "", ""
    picks = choices()
    if not picks:
        ui.note = "spawn: picker unavailable"
        ui.cc_spawn = False
        ui.cc_spawn_row = ""
        return
    if not picks[0].id:
        ui.note = f"spawn: {picks[0].label}"
        ui.cc_spawn = False
        ui.cc_spawn_row = ""
        return
    _prompt(ui)


def spawn_key(ui: UIState, key: str, gs: GlobalSnapshot, sel: tuple[str, str, str] | None) -> str | None:
    """A key while ui.cc_spawn is open (picker or text input); None when ui.cc_spawn is False. Returns "" when handled.
    Enter on the typed text sets ui.cc_confirm = ("spawn", "<stage path>|", question) and closes the picker."""
    if not ui.cc_spawn:
        return None
    normalized = key.lower()
    if normalized in ("esc", "escape"):
        spawn_cancel(ui)
        ui.note = "spawn cancelled"
        return ""
    if not ui.cc_spawn_model:
        picks = choices()
        if not picks:
            ui.note = "spawn: picker unavailable"
            return ""
        if normalized in ("up", "k"):
            ui.cc_spawn_i = max(0, ui.cc_spawn_i - 1)
            _prompt(ui)
        elif normalized in ("down", "j"):
            ui.cc_spawn_i = min(len(picks) - 1, ui.cc_spawn_i + 1)
            _prompt(ui)
        elif normalized in ("enter", "\n", "\r"):
            choice = picks[min(max(ui.cc_spawn_i, 0), len(picks) - 1)]
            if not choice.ok:
                ui.note = choice.reason
            elif choice.id:
                ui.cc_spawn_model, ui.cc_input = choice.id, ""
                ui.note = f"task for {choice.id}: {ui.cc_input}▌"
        return ""

    if normalized in ("enter", "\n", "\r"):
        if ui.cc_input:
            row_key = ui.cc_spawn_row
            stage = _cx_stage_key(gs, row_key)
            project_dir = project_dir_of(gs, row_key)
            if stage is None or not project_dir:
                ui.note = "spawn: no project directory for this row"
                return ""
            ui.cc_confirm = (
                "spawn", row_key,
                f"Spawn {ui.cc_spawn_model} in {project_dir}: {ui.cc_input[:40]}?  (y/n)",
            )
            ui.cc_spawn = False
            ui.note = ""
            return ""
    elif normalized == "space":
        if len(ui.cc_input) < 300:
            ui.cc_input += " "
    elif normalized in ("backspace", "\b", "\x7f"):
        ui.cc_input = ui.cc_input[:-1]
    elif len(key) == 1 and key.isprintable() and len(ui.cc_input) < 300:
        ui.cc_input += key
    ui.note = f"task for {ui.cc_spawn_model}: {ui.cc_input}▌"
    return ""


def spawn_run(ui: UIState, gs: GlobalSnapshot, row_key: str) -> str:
    """The confirmed `y`: call ccactions.spawn(ui.cc_spawn_model, project_dir_of(gs, row_key), ui.cc_input), clear
    cc_spawn_model and cc_input, return the one-line result."""
    model, text = ui.cc_spawn_model, ui.cc_input
    ui.cc_spawn_model, ui.cc_input = "", ""
    ui.cc_spawn_row = ""
    project_dir = project_dir_of(gs, row_key)
    if not project_dir:
        return "spawn: row is gone"
    return ccactions.spawn(model, project_dir, text)


def spawn_cancel(ui: UIState) -> None:
    """Close the picker/input and clear cc_spawn, cc_spawn_model, cc_input."""
    ui.cc_spawn, ui.cc_spawn_model, ui.cc_input = False, "", ""
    ui.cc_spawn_row = ""
