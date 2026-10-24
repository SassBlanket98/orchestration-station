"""Finished tasks are one line until opened; tests that look at their workers open them first."""
from __future__ import annotations

from model import GlobalSnapshot, UIState
from render import global_rows


def open_tasks(gs: GlobalSnapshot, ui: UIState) -> UIState:
    """Open every finished-task line the tree shows now (and those its workers' pages reveal), then return `ui`."""
    for path, kind, name in global_rows(gs, ui):
        if kind == "task":
            ui.gopen.add((path, "task", name))
    return ui
