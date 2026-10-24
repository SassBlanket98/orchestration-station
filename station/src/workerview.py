"""Ops console: the worker page (R4): identity, metrics, TOOLS/MIN, goal, step table with why lines. Imports model,
widgets and the ops* helpers only; render.py draws the key line under it."""
from __future__ import annotations

from model import (
    DONE,
    FAILED,
    LIVE_STATES,
    QUIET,
    RUNNING,
    STALLED,
    Stage,
    UIState,
    Worker,
)
from opsgrid import _og_ctx_text, _og_worker_cells
from opsspark import (
    _OS_STEP_HEAD_W,
    _OS_WHY_LABEL,
    OS_SPAN_S,
    _os_bins,
    _os_spark,
    _os_step_line,
    _os_why,
    _os_why_line,
)
from widgets import (
    _wd_c,
    _wd_C_AMBER,
    _wd_C_BLUE,
    _wd_C_BORDER,
    _wd_C_DIM,
    _wd_C_FAINT,
    _wd_C_GREEN,
    _wd_C_MUTED,
    _wd_C_RED,
    _wd_C_SUB,
    _wd_C_TEXT,
    _wd_clean,
    _wd_ctx_color,
    _wd_family_color,
    _wd_fit,
    _wd_fmt_clock,
    _wd_fmt_money,
    _wd_fmt_tok,
    _wd_lr,
    _wd_spinner,
    _wd_trunc,
    _wd_width,
    _wd_wrap,
)

WV_KEYS = (("↑↓", "step"), ("b", "brief"), ("r", "report"), ("f", "files"), ("Esc", "back"))

_WV_SPARK_MAX = 60                  # most sparkline cells
_WV_MID = "   "                     # between fields on a line
_WV_STATE_COL = {RUNNING: _wd_C_BLUE, QUIET: _wd_C_AMBER, STALLED: _wd_C_RED, DONE: _wd_C_GREEN, FAILED: _wd_C_RED}
_WV_WHY_CONT = " " * (_OS_STEP_HEAD_W - len(_OS_WHY_LABEL))   # puts `why` under the step's tool column


def _wv_unknown() -> str:
    return _wd_c("—", _wd_C_FAINT)


def _wv_val(text: str, color: tuple[int, int, int] = _wd_C_SUB) -> str:
    """A value, or a FAINT `—` when it is unknown."""
    return _wd_c(text, color) if text not in ("", "—") else _wv_unknown()


def _wv_field(label: str, value: str) -> str:
    return _wd_c(label + " ", _wd_C_DIM) + value


def _wv_wrap(fields: list[str], cols: int) -> list[str]:
    """Fields with gaps between them; a field that does not fit starts the next line, whole."""
    lines: list[str] = []
    line = ""
    for part in fields:
        if line and _wd_width(line) + _wd_width(_WV_MID) + _wd_width(part) > cols:
            lines.append(line)
            line = ""
        line = (line + _wd_c(_WV_MID, _wd_C_FAINT) if line else "") + part
    lines.append(line)
    return [_wd_fit(text, cols) for text in lines]


def _wv_mark(wk: Worker, t: float) -> str:
    if wk.state in LIVE_STATES:
        return _wd_c(_wd_spinner(t), _wd_family_color(wk.family))
    if wk.state == DONE:
        return _wd_c("✔", _wd_C_GREEN)
    if wk.state == FAILED:
        return _wd_c("✖", _wd_C_RED)
    return _wd_c("◌", _wd_C_FAINT)


def _wv_crumb(stage: Stage, wk: Worker, t: float, cols: int) -> str:
    """project › session › mark name (left); level tag · state · clock (right)."""
    sep = _wd_c(" › ", _wd_C_FAINT)
    project = _wd_clean(stage.label)
    session = _wd_clean(stage.session_name)
    left = (_wd_c(_wd_trunc(project, cols // 3), _wd_C_MUTED) if project else _wv_unknown()) + sep
    left += (_wd_c(_wd_trunc(session, cols // 3), _wd_C_MUTED) if session else _wv_unknown()) + sep
    left += _wv_mark(wk, t) + " " + _wd_c(_wd_clean(wk.name), _wd_C_TEXT, bold=True)
    state = wk.state or "—"
    right = (_wd_c(wk.level_tag or "WORKER", _wd_C_DIM) + _wd_c(" · ", _wd_C_FAINT)
             + _wd_c(state, _WV_STATE_COL.get(wk.state, _wd_C_MUTED)) + _wd_c(" · ", _wd_C_FAINT)
             + _wd_c(_wd_fmt_clock(t, seconds=True), _wd_C_DIM))
    return _wd_lr(left, right, cols)


def _wv_identity(wk: Worker, model: str, cols: int) -> list[str]:
    fields = [
        _wv_field("MODEL", _wv_val(model, _wd_C_TEXT)),
        _wv_field("ROUTE", _wv_val(_wd_clean(wk.account))),
        _wv_field("MODE", _wv_unknown()),           # the worker record has no mode
        _wv_field("PID", _wv_val(str(wk.pid) if wk.pid else "")),
        _wv_field("JOB", _wv_val(_wd_clean(wk.job))),
    ]
    return _wv_wrap(fields, cols)


def _wv_metrics(wk: Worker, cells: object, cols: int) -> list[str]:
    cost_note = getattr(cells, "cost_note", "")
    if getattr(cells, "cost", None) is not None:
        cost = _wd_c(_wd_fmt_money(cells.cost), _wd_C_SUB)
    else:
        cost = _wd_c("— " + ("local" if cost_note == "local" else "sub"), _wd_C_FAINT)
    tools = getattr(cells, "tools", None)
    tokens = getattr(cells, "tokens", None)
    ctx = getattr(cells, "ctx_pct", None)
    flags = ", ".join(sorted(_wd_clean(flag) for flag in wk.flags))
    fields = [
        _wv_field("ELAPSED", _wv_val(cells.elapsed, _wd_C_TEXT)),
        _wv_field("TOOLS", _wv_val("" if tools is None else str(tools), _wd_C_TEXT)),
        _wv_field("TOKENS", _wv_val("" if tokens is None else _wd_fmt_tok(tokens), _wd_C_TEXT)),
        _wv_field("CTX", _wv_val(_og_ctx_text(cells), _wd_ctx_color(ctx))),
        _wv_field("COST", cost),
        _wv_field("FLAGS", _wd_c(flags, _wd_C_AMBER) if flags else _wd_c("none", _wd_C_FAINT)),
    ]
    return _wv_wrap(fields, cols)


def _wv_spark(wk: Worker, t: float, cols: int) -> str:
    cells = min(_WV_SPARK_MAX, max(0, cols - 20))
    bins = _os_bins(wk.log.tool_pulses, t, cells, OS_SPAN_S)
    return _wd_fit(_wd_c("TOOLS/MIN ", _wd_C_DIM) + _os_spark(bins, cells) + _wd_c("  30m", _wd_C_FAINT), cols)


def _wv_why(reason: str, cols: int) -> str:
    if reason:
        return _os_why_line(_WV_WHY_CONT, reason, cols)
    return _wd_fit(_wd_c(_WV_WHY_CONT, _wd_C_BORDER) + _wd_c(_OS_WHY_LABEL, _wd_C_RED) + _wv_unknown(), cols)


def _wv_text(label: str, text: str, cols: int, lines: int) -> list[str]:
    """A labelled paragraph (the brief or the result), wrapped under the label and cut to `lines` lines."""
    text = _wd_clean(text)
    lead = len(label) + 1
    wrapped = _wd_wrap(text, max(10, cols - lead)) if text else []
    if not wrapped:
        return [_wd_fit(_wd_c(label + " ", _wd_C_DIM) + _wv_unknown(), cols)]
    out = []
    for i, line in enumerate(wrapped[:lines]):
        more = i == lines - 1 and len(wrapped) > lines
        out.append(_wd_fit((_wd_c(label + " ", _wd_C_DIM) if i == 0 else " " * lead)
                           + _wd_c(line + (" …" if more else ""), _wd_C_SUB), cols))
    return out


def _wv_entries(wk: Worker, t: float, cols: int) -> list[str]:
    """Every step line in order (newest last), each failed step followed by its why line."""
    steps = wk.log.steps
    if not steps:
        return [_wd_fit(_wd_c("  no steps recorded", _wd_C_FAINT), cols)]
    live = wk.state in LIVE_STATES
    reason = _os_why(wk)
    out: list[str] = []
    for i, step in enumerate(steps):
        out.append(_os_step_line(step, cols, t, live and i == len(steps) - 1))
        if step.outcome == "failed":
            out.append(_wv_why(reason, cols))
    return out


def _wv_entry_count(wk: Worker) -> int:
    return max(1, len(wk.log.steps) + sum(step.outcome == "failed" for step in wk.log.steps))


def _wv_page(stage: Stage, wk: Worker, ui: UIState, cols: int, rows: int, t: float) -> list[str]:
    """Exactly `rows` lines (the key line is drawn by the caller)."""
    if rows <= 0 or cols <= 0:
        return [""] * max(0, rows)
    cells = _og_worker_cells(wk, t)
    rule = _wd_c("─" * cols, _wd_C_BORDER)
    goal = _wd_clean(wk.goal)
    head = [_wv_crumb(stage, wk, t, cols), rule]
    head += _wv_identity(wk, cells.model, cols)
    head += _wv_metrics(wk, cells, cols)
    head.append(_wv_spark(wk, t, cols))
    head.append(rule)
    head.append(_wd_fit(_wd_c("GOAL ", _wd_C_DIM) + (_wd_c(_wd_trunc(goal, cols - 5), _wd_C_TEXT) if goal
                                                     else _wv_unknown()), cols))
    each = 4 if rows >= 28 else (2 if rows >= 20 else 0)   # how many lines the brief and the result get
    if each:
        head += _wv_text("BRIEF", " ".join(wk.brief_text), cols, each)
        result = " ".join(wk.result_text or wk.summary)
        head += _wv_text("RESULT", result, cols, each) if result or wk.state not in LIVE_STATES else []
    head.append("")
    head.append(_wd_fit(_wd_c("  " + "TIME".ljust(8) + "  " + "TOOL".ljust(6) + " " + "   "
                              + "DUR".rjust(7) + "  STEP", _wd_C_DIM), cols))
    entries = _wv_entries(wk, t, cols)
    room = max(0, rows - len(head))
    off = min(max(ui.scroll, 0), max(0, len(entries) - room))   # 0 = newest visible
    ui.scroll = off
    end = len(entries) - off
    window = entries[max(0, end - room):end] if room else []
    lines = (head + window)[:rows]
    lines += [""] * (rows - len(lines))
    return [_wd_fit(line, cols) for line in lines]


def _wv_key(ui: UIState, stage: Stage, wk: Worker, key: str, rows: int) -> str:
    """Handle a key on the worker page. Returns "" (used or ignored), or "brief" / "report" / "files" for the app to
    open that page with this worker selected. Esc/←/Backspace are handled by the caller."""
    if key == "b":
        return "brief"
    if key == "r":
        return "report"
    if key == "f":
        return "files"
    top = _wv_entry_count(wk)   # the page clamps the offset again when it draws
    page = max(rows - 8, 1)
    if key in ("up", "k"):
        ui.scroll = min(ui.scroll + 1, top)
    elif key in ("down", "j"):
        ui.scroll = max(ui.scroll - 1, 0)
    elif key == "pgup":
        ui.scroll = min(ui.scroll + page, top)
    elif key == "pgdn":
        ui.scroll = max(ui.scroll - page, 0)
    elif key == "home":
        ui.scroll = top
    elif key == "end":
        ui.scroll = 0
    return ""
