"""Control center: keys and mouse. Imports model, ccactions, ccspawn, ccpane, ccstrip only; app.py calls it before its own keys."""
from __future__ import annotations

import time

from ccactions import can, confirm_text, run, target_of
from ccpane import PANE_TABS, PANE_TAB_ARROWS, pane_tab_digit
from ccspawn import spawn_cancel, spawn_key, spawn_open, spawn_run
from ccstrip import STRIP_TABS
from model import GlobalSnapshot, Stage, UIState, Worker

_CXK_FOCUS = ("table", "pane", "strip")      # tab cycles these in order
_CXK_PAGE = 10                               # pgup/pgdn step in the pane and the strip
_CXK_ACTIONS = {"s": "stop", "K": "kill", "r": "resume"}
_CXK_ROWS = ("stage", "worker")              # tree kinds an action may stand on; a heading has no stage
_CXK_CLASSIC = "classic view: press v again for the control center"
_CXK_ARM_S = 0.35
_CXK_ARMED: dict[str, object] = {"value": None, "t": 0.0}
_CXK_TARGET: tuple[int | None, int | None, str, str] | None = None
_CXK_PENDING: tuple[int | None, int | None, str, str] | None = None   # identity of the Target the question was built from


def _cxk_stage(gs: GlobalSnapshot, path: str) -> Stage | None:
    """The stage whose row path is `path`, or None."""
    return next((st for st in gs.stages or [] if st.path == path), None)


def _cxk_worker(stage: Stage, name: str) -> Worker | None:
    """The worker called `name` in the stage's snapshot, or None."""
    workers = (stage.snap.workers if stage.snap is not None else []) or []
    return next((wk for wk in workers if wk.name == name), None)


def _cxk_row(gs: GlobalSnapshot, sel: tuple[str, str, str] | None) -> tuple[Stage | None, Worker | None, str]:
    """(stage, worker, why) for the selected tree row; why says what cannot be acted on when stage is None."""
    if not sel or not sel[0] or sel[1] not in _CXK_ROWS:
        return None, None, "no row selected"
    stage = _cxk_stage(gs, sel[0])
    if stage is None:
        return None, None, "row is gone"
    if sel[1] != "worker":
        return stage, None, ""
    worker = _cxk_worker(stage, sel[2])
    if worker is None:
        return None, None, "row is gone"
    return stage, worker, ""


def _cxk_key_target(gs: GlobalSnapshot, row_key: str) -> tuple[Stage | None, Worker | None, str]:
    """(stage, worker, why) for a stored confirm row key ("<stage path>|<worker>")."""
    path, _sep, name = (row_key or "").rpartition("|")
    stage = _cxk_stage(gs, path)
    if stage is None:
        return None, None, "row is gone"
    if not name:
        return stage, None, ""
    worker = _cxk_worker(stage, name)
    if worker is None:
        return None, None, "row is gone"
    return stage, worker, ""


def _cxk_row_key(stage: Stage, worker: Worker | None) -> str:
    """The row key a confirm stores: the stage path, then the worker name ("" for a lead row)."""
    return f"{stage.path}|{worker.name if worker is not None else ''}"


def _cxk_identity(gs: GlobalSnapshot, row_key: str) -> tuple[int | None, int | None, str, str] | None:
    """The current process identity and label for an action confirm row."""
    stage, worker, _why = _cxk_key_target(gs, row_key)
    if stage is None:
        return None
    target = target_of(stage, worker)
    return target.pid, target.start, target.session_id, target.label


def _cxk_sync_confirm(ui: UIState, gs: GlobalSnapshot) -> None:
    """Record a newly armed confirm at the end of the current key handling."""
    global _CXK_TARGET, _CXK_PENDING
    value = ui.cc_confirm
    if value == _CXK_ARMED["value"]:
        if value is None:
            _CXK_TARGET = None
        return
    _CXK_ARMED["value"] = value
    _CXK_ARMED["t"] = time.monotonic()
    _CXK_TARGET = None
    if value is not None and value[0] != "spawn":
        # The identity the question and `can` were built from, not a fresh read: a pid recycled in between must differ.
        _CXK_TARGET = _CXK_PENDING if _CXK_PENDING is not None else _cxk_identity(gs, value[1])
    _CXK_PENDING = None


def _cxk_step(key: str) -> int | None:
    """The scroll delta a key stands for (positive = down/on, negative = up/back); None when it is not a scroll key."""
    if key in ("up", "k"):
        return -1
    if key in ("down", "j"):
        return 1
    if key == "pgup":
        return -_CXK_PAGE
    if key == "pgdn":
        return _CXK_PAGE
    return None


def _cxk_confirm(ui: UIState, key: str, gs: GlobalSnapshot) -> str | None:
    """A key while ui.cc_confirm waits: y acts on a fresh target, anything else cancels; ctrl-c is left to the app."""
    global _CXK_PENDING
    if ui.cc_confirm is None:
        return None
    if key == "ctrl-c":
        return None
    action, row_key, _question = ui.cc_confirm
    if key == "y" and not ui.cc_active:   # the classic view does not show the question: never act on a hidden prompt
        ui.cc_confirm = None
        ui.note = "cancelled (question not visible)"
        if action == "spawn":
            spawn_cancel(ui)
        return ""
    if key == "y" and time.monotonic() - float(_CXK_ARMED["t"]) < _CXK_ARM_S:
        return ""
    if action == "stop" and key == "K":
        if time.monotonic() - float(_CXK_ARMED["t"]) < _CXK_ARM_S:
            return ""
        stage, worker, why = _cxk_key_target(gs, row_key)
        if stage is None:
            ui.cc_confirm = None
            ui.note = why
            return ""
        target = target_of(stage, worker)
        identity = (target.pid, target.start, target.session_id, target.label)
        if identity != _CXK_TARGET:
            ui.cc_confirm = None
            ui.note = "row changed, cancelled"
            return ""
        ok, why = can("kill", target)
        if not ok:
            ui.cc_confirm = None
            ui.note = f"kill: {why}"
            return ""
        _CXK_PENDING = identity
        ui.cc_confirm = ("kill", row_key, confirm_text("kill", target))
        ui.note = ""
        return ""
    ui.cc_confirm = None
    if key != "y":
        ui.note = "cancelled"
        if action == "spawn":
            spawn_cancel(ui)
        return ""
    stage, worker, why = _cxk_key_target(gs, row_key)
    if stage is None:            # the row left the snapshot between the question and the answer
        ui.note = why
        return ""
    if action == "spawn":
        ui.note = spawn_run(ui, gs, row_key)
        return ""
    target = target_of(stage, worker)
    identity = (target.pid, target.start, target.session_id, target.label)
    if identity != _CXK_TARGET:
        ui.note = "row changed, cancelled"
        return ""
    ui.note = run(action, target)
    return ""


def _cxk_dispatch(ui: UIState, key: str, gs: GlobalSnapshot, rows: int, sel: tuple[str, str, str] | None) -> str | None:
    """A key in the control center. Returns None when the key is not the control center's (app.py then handles it
    as before), else "" (handled, nothing for the main loop to do). Owns, while ui.cc is on and no page is open:
    Tab (focus table/pane/strip), left/right (pane tab, pane focused), [ ] (strip tab), E (strip), v (classic view), s stop, K kill, n spawn,
    r resume, y/n/esc on ui.cc_confirm, the spawn picker's up/down/enter/esc, pgup/pgdn/wheel scroll of the focused part.
    Every action goes through ccactions.can / confirm_text / run and shows its result in ui.note. `sel` is the selected tree row."""
    global _CXK_PENDING
    if not ui.actions_on():
        if ui.cc_spawn:
            spawn_cancel(ui)
            ui.cc_confirm = None
            return ""
        if ui.cc_confirm and ui.cc_confirm[0] in _CXK_ACTIONS.values():
            ui.cc_confirm = None
            return ""
        if ui.cc_active and key in ("n", "s", "K", "r"):
            return ""
    handled = _cxk_confirm(ui, key, gs)
    if handled is not None:
        return handled
    if ui.cc_spawn:              # the picker and its text line take every key while they are open
        return spawn_key(ui, key, gs, sel)
    if not ui.cc_active:         # the last frame was the classic tree: none of these keys are ours
        return None
    if key == "d":
        ui.cc_pane_open = not ui.cc_pane_open
        if not ui.cc_pane_open and ui.cc_focus == "pane":
            ui.cc_focus = "table"
        return ""
    if key == "tab":
        focus = ui.cc_focus if ui.cc_focus in _CXK_FOCUS else "table"
        order = _CXK_FOCUS if ui.cc_pane_open else ("table", "strip")
        if focus == "pane" and not ui.cc_pane_open:
            focus = "table"
        ui.cc_focus = order[(order.index(focus) + 1) % len(order)]
        return ""
    if key == "esc" and ui.cc_pane_open:
        ui.cc_pane_open = False
        if ui.cc_focus in ("pane", "strip"):
            ui.cc_focus = "table"
        return ""
    if key == "esc" and ui.cc_focus == "strip":
        ui.cc_focus = "table"
        return ""
    if ui.cc_focus == "pane" and ui.cc_pane_open:
        if key == "home":
            ui.cc_pane_scroll = 0
            return ""
        if key in PANE_TAB_ARROWS:     # the pane's tabs; in the table ← and → fold and unfold the row
            index = PANE_TABS.index(ui.cc_tab) if ui.cc_tab in PANE_TABS else 0
            ui.cc_tab = PANE_TABS[(index + (1 if key == "right" else -1)) % len(PANE_TABS)]
            ui.cc_pane_scroll = 0
            return ""
        tab = pane_tab_digit(key)
        if tab is not None:
            ui.cc_tab = tab
            ui.cc_pane_scroll = 0
            return ""
        step = _cxk_step(key)
        if step is not None:
            ui.cc_pane_scroll = max(0, ui.cc_pane_scroll + step)   # the layout clamps the maximum
            return ""
    elif ui.cc_focus == "strip":
        if key == "home":
            ui.cc_strip_scroll = 0
            return ""
        step = _cxk_step(key)
        if step is not None:
            ui.cc_strip_scroll = max(0, ui.cc_strip_scroll - step)  # up moves back from the newest
            return ""
    if key in ("[", "]"):
        if not ui.cc_strip_open:
            ui.note = "the strip is hidden: E shows it"
            return ""
        index = STRIP_TABS.index(ui.cc_strip) if ui.cc_strip in STRIP_TABS else 0
        ui.cc_strip = STRIP_TABS[(index + (1 if key == "]" else -1)) % len(STRIP_TABS)]
        ui.cc_strip_scroll = 0
        return ""
    if key == "E":       # `e` is expand-all in the list, so the strip has its own key
        ui.cc_strip_open = not ui.cc_strip_open
        return ""
    if key == "v":
        ui.cc = False
        ui.note = _CXK_CLASSIC
        return ""
    if key == "n":
        spawn_open(ui, gs, sel)
        return ""
    action = _CXK_ACTIONS.get(key)
    if action is not None:
        stage, worker, why = _cxk_row(gs, sel)
        if stage is None:
            ui.note = why
            return ""
        target = target_of(stage, worker)
        ok, why = can(action, target)
        if not ok:
            ui.note = why
            return ""
        ui.cc_confirm = (action, _cxk_row_key(stage, worker), confirm_text(action, target))
        _CXK_PENDING = (target.pid, target.start, target.session_id, target.label)
        return ""
    return None                  # up/down/enter/space in the table stay with the tree


def cc_key(ui: UIState, key: str, gs: GlobalSnapshot, rows: int,
           sel: tuple[str, str, str] | None) -> str | None:
    """Handle one key and update confirm arm state after the key path has run."""
    _cxk_sync_confirm(ui, gs)
    try:
        return _cxk_dispatch(ui, key, gs, rows, sel)
    finally:
        _cxk_sync_confirm(ui, gs)


def cc_pointer(ui: UIState, action: str, target: str) -> list[str]:
    """A click on a control center hit ("cctab", "ccstrip") as the keys it stands for, run through the normal key path."""
    if action == "ccpane":
        ui.cc_pane_open = not ui.cc_pane_open
        if not ui.cc_pane_open and ui.cc_focus == "pane":
            ui.cc_focus = "table"
    elif action == "ccrow":
        if target in ui.expanded:
            ui.expanded.discard(target)
            ui.pinned.discard(target)
        else:
            ui.expanded.add(target)
            ui.pinned.add(target)
    elif action == "cctab" and target in PANE_TABS:
        ui.cc_tab, ui.cc_pane_scroll, ui.cc_focus = target, 0, "pane"
        ui.cc_pane_open = True
    elif action == "ccstrip" and target in STRIP_TABS:
        ui.cc_strip, ui.cc_strip_scroll, ui.cc_focus = target, 0, "strip"
    return []
