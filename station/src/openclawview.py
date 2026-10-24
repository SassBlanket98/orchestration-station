"""Ops console: the openclaw session page (R3): vitals strip, turn table, a turn's tool calls. Imports model, widgets and
the ops* helpers only; render.py draws the key line under it."""
from __future__ import annotations

import time

from collect import _co_agent_label
from model import Stage, Turn, TurnTool, UIState, Vitals
from opsspark import _os_spark
from widgets import (
    _wd_bg_line,
    _wd_c,
    _wd_C_AMBER,
    _wd_C_BLUE,
    _wd_C_BORDER,
    _wd_C_CYAN,
    _wd_C_FAINT,
    _wd_C_GREEN,
    _wd_C_MUTED,
    _wd_C_RED,
    _wd_C_SELBG,
    _wd_C_SUB,
    _wd_C_TEAL,
    _wd_C_TEXT,
    _wd_C_VIOLET,
    _wd_clip,
    _wd_fit,
    _wd_fmt_tok,
    _wd_SGR_RE,
    _wd_trunc,
    _wd_width,
)

OV_KEYS = (("↑↓", "turn"), ("Enter", "tools"), ("Esc", "back"))

# Turn table columns after the arrow: (title, width, leading gap, align); width 0 is the flexible column.
_OV_NARROW = (("", 1, 0, "l"), ("TIME", 5, 1, "l"), ("ASK", 0, 2, "l"), ("TOK", 8, 1, "r"),
              ("COST", 9, 2, "r"), ("LAT", 7, 2, "r"), ("TOOLS", 0, 2, "l"))
_OV_WIDE = (("", 1, 0, "l"), ("TIME", 5, 1, "l"), ("ASK", 0, 2, "l"), ("MODEL", 14, 1, "l"),
            ("IN", 7, 1, "r"), ("OUT", 7, 1, "r"), ("COST", 8, 1, "r"), ("LAT", 7, 1, "r"),
            ("TOOLS", 0, 2, "l"))


def _ov_opens(stage: Stage) -> bool:
    """Whether Enter/o on this session row opens the openclaw page (an OpenClaw session)."""
    return stage.lead_kind == "openclaw" or stage.tag == "OClaw"


def _ov_time(stamp: float) -> str:
    return time.strftime("%H:%M", time.localtime(stamp)) if stamp > 0 else "—"


def _ov_amount(value: int | None, suffix: str = "") -> str:
    return "—" if value is None else f"{_wd_fmt_tok(value)}{suffix}"


def _ov_cost(value: float | None) -> str:
    return "— local" if value is None else f"${value:.2f}"


def _ov_duration(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 60:
        return f"{int(value // 60)}m{int(value % 60):02d}s"
    return f"{value:.1f}s"


def _ov_vitals(v: Vitals | None, cols: int) -> list[str]:
    missing = _wd_c("—", _wd_C_FAINT)
    if v is None:
        fields = [f"MODEL {missing}", f"CTX {missing}", f"COMPACT {missing}",
                  f"COST {missing}", f"RUNTIME {missing}", f"STATUS {missing}"]
        return [_wd_fit("   " + "   ".join(fields), cols)]
    model = v.model or "—"
    provider = v.provider or "—"
    fields = [_wd_c("MODEL ", _wd_C_MUTED) + _wd_c(model, _wd_C_CYAN if v.model else _wd_C_FAINT) +
              " " + _wd_c(provider, _wd_C_SUB if v.provider else _wd_C_FAINT)]
    if v.ctx_used is None or v.ctx_budget is None or v.ctx_budget <= 0:
        fields.append(_wd_c("CTX ", _wd_C_MUTED) + missing)
    else:
        pct = min(100, round(v.ctx_used * 100 / v.ctx_budget))
        fill = min(10, max(0, round(pct / 10)))
        bar = "▰" * fill + "▱" * (10 - fill)
        fields.append(_wd_c("CTX ", _wd_C_MUTED) + _wd_c(bar, _wd_C_TEAL) + " " +
                      _wd_c(f"{v.ctx_used / 1000:.1f}k/{v.ctx_budget / 1000:.0f}k {pct}%", _wd_C_SUB))
    fields.append(_wd_c("COMPACT ", _wd_C_MUTED) + _wd_c(str(v.compactions), _wd_C_SUB))
    fields.append(_wd_c("COST ", _wd_C_MUTED) + _wd_c(_ov_cost(v.cost), _wd_C_GREEN if v.cost is not None else _wd_C_FAINT))
    fields.append(_wd_c("RUNTIME ", _wd_C_MUTED) + _wd_c(_ov_duration(v.runtime), _wd_C_SUB))
    status = v.status or "—"
    status_color = (_wd_C_GREEN if status == "running" else _wd_C_RED if status in ("failed", "timeout", "killed")
                    else _wd_C_SUB if v.status else _wd_C_FAINT)
    fields.append(_wd_c("STATUS ", _wd_C_MUTED) + _wd_c(("● " if v.status else "") + status, status_color))
    line = "   "
    lines: list[str] = []
    for item in fields:
        candidate = line + ("   " if line.strip() else "") + item
        if _wd_width(candidate) > cols:
            lines.append(_wd_fit(line, cols))
            line = "   " + item
        else:
            line = candidate
    if line.strip():
        lines.append(_wd_fit(line, cols))
    return lines or [_wd_fit("", cols)]


def _ov_rule(cols: int) -> str:
    return _wd_fit(_wd_c("─" * cols, _wd_C_BORDER), cols)


def _ov_columns(cols: int, wide: bool) -> list[tuple[str, int, int, str]]:
    """Resolved table columns (title, width, leading gap, align), summing to `cols` while there is room."""
    spec = _OV_WIDE if wide else _OV_NARROW
    fixed = sum(lead + width for _, width, lead, _ in spec)
    spare = max(0, cols - fixed)
    if wide:
        ask = min(40, max(16, spare - 15))
    else:
        ask = min(44, max(8, spare - 14))
    ask = min(ask, max(0, spare - 1))
    tools = max(0, spare - ask)
    return [(title, width or (ask if title == "ASK" else tools), lead, align)
            for title, width, lead, align in spec]


def _ov_cell(value: str, width: int, color: tuple[int, int, int] | None = None, right: bool = False) -> str:
    """One cell of exactly `width` cells, right aligned when asked."""
    text = _wd_clip(_wd_c(value, color) if color else value, width)
    pad = " " * max(0, width - _wd_width(text))
    return pad + text if right else text + pad


def _ov_header(cols: int, wide: bool) -> str:
    parts: list[str] = []
    for title, width, lead, align in _ov_columns(cols, wide):
        text = _wd_trunc(title, width)
        parts.append(" " * lead + (text.rjust(width) if align == "r" else text.ljust(width)))
    return _wd_fit(_wd_c("".join(parts), _wd_C_MUTED, bold=True), cols)


def _ov_turn_line(turn: Turn, selected: bool, expanded: bool, cols: int, wide: bool) -> str:
    ask = turn.ask.splitlines()[0] if turn.ask else "—"
    when = _ov_time(turn.start)
    toks = None if turn.tokens_in is None and turn.tokens_out is None else (turn.tokens_in or 0) + (turn.tokens_out or 0)
    cost = "—" if turn.cost is None else f"${turn.cost:.2f}"
    lat = "—" if turn.latency is None else f"{turn.latency:.1f}s"
    tools = " ".join(f"{name}×{count}" for name, count in turn.tools.items()) or "—"
    retry = _wd_c(f" ↻{turn.attempts - 1}", _wd_C_AMBER) if turn.attempts > 1 else ""
    error = _wd_c(" ✖", _wd_C_RED) if turn.outcome in ("error", "aborted") else ""
    markers = retry + error
    mark = _wd_c("▾" if expanded else "▸", _wd_C_CYAN if expanded else _wd_C_BLUE)
    lat_color = _wd_C_AMBER if turn.latency is not None and turn.latency > 30 else _wd_C_SUB if turn.latency is not None else _wd_C_FAINT
    cells: dict[str, tuple[str, tuple[int, int, int] | None]] = {
        "": (mark, None),
        "TIME": (when, _wd_C_MUTED if turn.start else _wd_C_FAINT),
        "ASK": (ask, _wd_C_SUB if turn.ask else _wd_C_FAINT),
        "MODEL": (turn.model or "—", _wd_C_VIOLET if turn.model else _wd_C_FAINT),
        "IN": (_ov_amount(turn.tokens_in), _wd_C_SUB if turn.tokens_in is not None else _wd_C_FAINT),
        "OUT": (_ov_amount(turn.tokens_out), _wd_C_SUB if turn.tokens_out is not None else _wd_C_FAINT),
        "TOK": (_ov_amount(toks), _wd_C_SUB if toks is not None else _wd_C_FAINT),
        "COST": (cost, _wd_C_GREEN if turn.cost is not None else _wd_C_FAINT),
        "LAT": (lat, lat_color),
        "TOOLS": (markers + (" " if markers and turn.tools else "") + tools,
                  _wd_C_SUB if turn.tools else _wd_C_FAINT),
    }
    parts: list[str] = []
    for title, width, lead, align in _ov_columns(cols, wide):
        value, color = cells[title]
        parts.append(" " * lead + _ov_cell(value, width, color, right=align == "r"))
    line = "".join(parts)
    line = _wd_fit(_wd_clip(line, cols), cols)
    return _wd_bg_line(line, _wd_C_SELBG, cols) if selected else line


def _ov_call_line(call: TurnTool, cols: int) -> str:
    mark = "✔" if call.ok is True else "✖" if call.ok is False else "—"
    color = _wd_C_GREEN if call.ok is True else _wd_C_RED if call.ok is False else _wd_C_FAINT
    prefix = (f"         │ {_ov_time(call.ts)}:{time.strftime('%S', time.localtime(call.ts))}  "
              f"{call.name}  ")
    suffix = f"  {_ov_duration(call.dur):>6}  "
    args = call.args or "—"
    args_width = max(0, cols - _wd_width(prefix) - _wd_width(mark) - _wd_width(suffix))
    line = _wd_c(prefix, _wd_C_SUB) + _wd_c(mark, color) + _wd_c(suffix, _wd_C_SUB) + _wd_c(_wd_trunc(args, args_width), _wd_C_SUB)
    return _wd_fit(_wd_clip(line, cols, ell=False), cols)


def _ov_turns(stage: Stage) -> list[Turn]:
    """The stage's turns, newest first, whatever order the collector left them in."""
    return sorted(stage.turns, key=lambda turn: turn.start, reverse=True)


def _ov_turn_content(stage: Stage, ui: UIState, cols: int, now: float) -> list[str]:
    turns = _ov_turns(stage)
    lines: list[str] = []
    if not turns:
        return [_wd_fit("   no turn data", cols)]
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    todays = sum(1 for turn in stage.turns if turn.start > 0 and time.strftime("%Y-%m-%d", time.localtime(turn.start)) == today)
    retries = sum(max(0, turn.attempts - 1) for turn in stage.turns)
    errors = sum(turn.outcome in ("error", "aborted") for turn in stage.turns)
    latency = [round(turn.latency or 0) for turn in turns[:12][::-1]]
    spark = _os_spark(latency, min(12, max(1, cols - 72)))
    summary = f"TURNS {todays} today · {retries} {'retry' if retries == 1 else 'retries'} · {errors} errors"
    lines.append(_wd_fit(_wd_c("   " + summary, _wd_C_TEXT, bold=True) +
                         _wd_c("  latency ", _wd_C_MUTED) + _wd_c(spark, _wd_C_BLUE), cols))
    wide = cols >= 110
    lines.append(_ov_header(cols, wide))
    selected = min(max(ui.sel, 0), len(turns) - 1)
    expanded = next((i for i, turn in enumerate(turns) if turn.run_id == ui.gturn), -1)
    for i, turn in enumerate(turns):
        lines.append(_ov_turn_line(turn, i == selected, i == expanded, cols, wide))
        if i == expanded:
            for call in turn.calls:
                lines.append(_ov_call_line(call, cols))
            if turn.attempts > 1 or turn.outcome in ("error", "aborted"):
                why = f"attempt 1 model error: {turn.error or 'retry'}  →  attempt {turn.attempts} {turn.outcome}"
                lines.append(_wd_fit(_wd_c("         └ " + _wd_trunc(why, max(0, cols - 11)), _wd_C_AMBER), cols))
    return lines


def _ov_page(stage: Stage, ui: UIState, cols: int, rows: int, t: float) -> list[str]:
    """Exactly `rows` lines (the key line is drawn by the caller)."""
    if rows <= 0:
        return []
    cols = max(1, cols)
    name = stage.display_name or stage.session_name or "—"
    clock = time.strftime("%H:%M:%S", time.localtime(t)) if t else "—"
    state = {"active": "live", "idle": "idle", "closed": "closed"}.get(stage.state, stage.state or "—")
    agent = _co_agent_label()
    crumb = _wd_c(f"◆ {agent} › ", _wd_C_CYAN, bold=True) + _wd_c(name, _wd_C_TEXT)
    right = _wd_c(f"{agent} · {state} · {clock}", _wd_C_MUTED)
    gap = max(1, cols - _wd_width(crumb) - _wd_width(right))
    lines = [_wd_fit(crumb + " " * gap + right, cols), _ov_rule(cols)]
    lines.extend(_ov_vitals(stage.vitals, cols))
    lines.append(_ov_rule(cols))
    fixed = lines
    content = _ov_turn_content(stage, ui, cols, t)
    available = max(0, rows - len(fixed))
    # Keep the selected turn and its open calls in view, reserving a marker for omitted older rows.
    if len(content) > available:
        start = 0
        turn_positions = [i for i, line in enumerate(content)
                          if _wd_SGR_RE.sub("", line).startswith(("▸", "▾"))]
        selected = min(max(ui.sel, 0), max(0, len(turn_positions) - 1))
        if turn_positions and turn_positions[selected] >= available:
            start = max(0, turn_positions[selected] - available + 1)
        body = content[start:start + available]
        if start and available:
            earlier = sum(position < start for position in turn_positions)
            body[0] = _wd_fit(_wd_c(f"   … {earlier} older turns", _wd_C_FAINT), cols)
    else:
        body = content
    result = (fixed + body)[:rows]
    result.extend([_wd_fit("", cols)] * (rows - len(result)))
    return result


def _ov_key(ui: UIState, stage: Stage, key: str, rows: int) -> bool:
    """Handle a key on the openclaw page; True when used. Esc/←/Backspace are handled by the caller."""
    turns = _ov_turns(stage)
    last = max(0, len(turns) - 1)
    ui.sel = min(max(ui.sel, 0), last)
    movements = {"up": -1, "k": -1, "down": 1, "j": 1,
                 "pageup": -max(1, rows // 2), "pgup": -max(1, rows // 2),
                 "pagedown": max(1, rows // 2), "pgdn": max(1, rows // 2)}
    normalized = key
    if key in ("↑", "↓"):
        normalized = "up" if key == "↑" else "down"
    if normalized in movements:
        ui.sel = min(last, max(0, ui.sel + movements[normalized]))
        return True
    if normalized in ("home", "g"):
        ui.sel = 0
        return True
    if normalized in ("end", "G"):
        ui.sel = last
        return True
    if normalized in ("enter", "\r", "\n") and turns:
        run_id = turns[ui.sel].run_id
        ui.gturn = "" if ui.gturn == run_id else run_id
        return True
    return False
