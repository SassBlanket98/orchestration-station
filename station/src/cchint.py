"""Control center: the one-line hint row at the bottom of the frame. Pure; imports model and widgets only."""
from __future__ import annotations

from ccpane import PANE_TABS, PANE_TAB_ARROWS
from model import UIState
from widgets import (
    _wd_c,
    _wd_C_AMBER,
    _wd_C_DIM,
    _wd_C_TEXT,
    _wd_fit,
    _wd_trunc,
    _wd_width,
)

HINT = (
    "w station · d details · Tab focus · ←→ pane tabs · 1-6 pane tab · "
    "[ ] strip tab · E strip · v classic · ? keys"
)
_CX_ACTION_HINT = "s stop · n spawn · r resume · K kill"

_CX_SEP = " · "
_CX_SPAWN = "spawn: pick a model"
_CXH_CONFIRM_MAX = 99         # matches ccactions._CX_CONFIRM_MAX
_CX_STOP_KILL = " · K kill"   # appended only on the real stop question line, when it was not clipped


def pane_tab_cycle_keys() -> tuple[str, ...]:
    """Pane tab cycle keys; must match cckeys when the pane has focus."""
    return PANE_TAB_ARROWS


def _cx_hint(width: int, allow_actions: bool = False) -> str:
    """HINT, shortened from the right on whole ` · ` item boundaries; the first item alone is truncated with `…`."""
    hint = HINT.replace(" · [ ]", f" · {_CX_ACTION_HINT} · [ ]") if allow_actions else HINT
    if _wd_width(hint) <= width:
        return hint
    items = hint.split(_CX_SEP)
    for n in range(len(items) - 1, 0, -1):
        candidate = _CX_SEP.join(items[:n])
        if _wd_width(candidate) <= width:
            return candidate
    return _wd_trunc(items[0], width)


def _cx_line(text: str, width: int, fg: tuple[int, int, int], bold: bool = False) -> str:
    """One bright line: control characters become spaces, then the text is cut to `width` with `…` and padded."""
    return _wd_fit(_wd_c(_wd_trunc(text, width), fg, bold=bold), width)


def hint_line(ui: UIState, width: int) -> str:
    """The bottom row, exactly `width` visible cells: the confirm question `ui.cc_confirm[2]` (bright amber) when a
    confirm is open, else `ui.note` (bright) when set, else HINT in dim, shortened from the right (with a `…`) to fit."""
    if width <= 0:
        return ""
    if ui.cc_confirm is not None:
        action, _row_key, question = ui.cc_confirm
        text = question
        if (action == "stop" and "any other key cancels" in question
                and len(question) + len(_CX_STOP_KILL) <= _CXH_CONFIRM_MAX
                and _wd_width(question + _CX_STOP_KILL) <= width):
            text = question + _CX_STOP_KILL
        return _cx_line(text, width, _wd_C_AMBER, bold=True)
    if ui.note:
        return _cx_line(ui.note, width, _wd_C_TEXT)
    if ui.cc_spawn:
        return _cx_line(_CX_SPAWN, width, _wd_C_TEXT)
    return _wd_fit(_wd_c(_cx_hint(width, ui.actions_on()), _wd_C_DIM), width)
