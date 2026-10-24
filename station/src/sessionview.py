"""Ops console: the session page. Identity, metrics, TOOLS/MIN, goal, what the lead is doing, and every worker this
session spawned (only this session's: `stage.snap.workers`). Imports model, widgets and the ops* helpers only; render.py
draws the key line under it."""
from __future__ import annotations

from model import (
    DONE,
    FAILED,
    LIVE_STATES,
    Stage,
    UIState,
    Worker,
)
from opsgrid import _og_age, _og_ctx_text, _og_stage_cells, _og_worker_cells
from opsspark import OS_SPAN_S, _os_bins, _os_spark
from widgets import (
    _wd_c,
    _wd_C_AMBER,
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
    _wd_pad,
    _wd_spinner,
    _wd_trunc,
    _wd_width,
    _wd_wrap,
)
from workerview import _WV_STATE_COL, _wv_field, _wv_unknown, _wv_val, _wv_wrap

SV_KEYS = (("↑↓", "scroll"), ("Enter", "worker"), ("Esc", "back"))

_SV_SPARK_MAX = 60
_SV_TEXT_LINES = 4          # most wrapped lines of the goal / brief
_SV_DOING = 6               # lead actions shown


def _sv_head(text: str) -> str:
    return _wd_c(text, _wd_C_DIM, bold=True)


def _sv_counts(workers: list[Worker]) -> tuple[int, int, int, int]:
    """(total, running, done, failed) over this session's workers."""
    running = sum(wk.state in LIVE_STATES for wk in workers)
    done = sum(wk.state == DONE for wk in workers)
    failed = sum(wk.state == FAILED for wk in workers)
    return len(workers), running, done, failed


def _sv_mark(wk: Worker, t: float) -> str:
    if wk.state in LIVE_STATES:
        return _wd_c(_wd_spinner(t), _wd_family_color(wk.family))
    if wk.state == DONE:
        return _wd_c("✔", _wd_C_GREEN)
    if wk.state == FAILED:
        return _wd_c("✖", _wd_C_RED)
    return _wd_c("◌", _wd_C_FAINT)


def _sv_crumb(stage: Stage, t: float, cols: int) -> str:
    sep = _wd_c(" › ", _wd_C_FAINT)
    project = _wd_clean(stage.label)
    name = _wd_clean(stage.display_name or stage.session_name) or stage.session_id[:8]
    left = _wd_c("◆ ", _wd_C_GREEN) + (_wd_c(_wd_trunc(project, cols // 3), _wd_C_MUTED) if project else _wv_unknown())
    left += sep + _wd_c(_wd_trunc(name or "session", cols // 2), _wd_C_TEXT, bold=True)
    state = {"active": "live", "idle": "idle", "closed": "closed"}.get(stage.state, stage.state or "—")
    right = (_wd_c(stage.level_tag or ("LEAD" if not stage.parent else "SPAWN"), _wd_C_DIM) + _wd_c(" · ", _wd_C_FAINT)
             + _wd_c(state, _wd_C_GREEN if state == "live" else _wd_C_MUTED) + _wd_c(" · ", _wd_C_FAINT)
             + _wd_c(_wd_fmt_clock(t, seconds=True), _wd_C_DIM))
    return _wd_lr(left, right, cols)


def _sv_identity(stage: Stage, model: str, cols: int) -> list[str]:
    fields = [
        _wv_field("MODEL", _wv_val(model, _wd_C_TEXT)),
        _wv_field("HARNESS", _wv_val(_wd_clean(stage.lead_kind or stage.tag))),
        _wv_field("ACCOUNT", _wv_val(_wd_clean(stage.account))),
        _wv_field("PID", _wv_val(str(stage.lead_pid) if stage.lead_pid else "")),
        _wv_field("TMUX", _wv_val(_wd_clean(stage.tmux))),
        _wv_field("DIR", _wv_val(_wd_clean(stage.cwd or stage.dir))),
    ]
    return _wv_wrap(fields, cols)


def _sv_metrics(stage: Stage, cells: object, cols: int) -> list[str]:
    cost = getattr(cells, "cost", None)
    cost_txt = (_wd_c(_wd_fmt_money(cost), _wd_C_SUB) if cost is not None
                else _wd_c("— " + ("local" if getattr(cells, "cost_note", "") == "local" else "sub"), _wd_C_FAINT))
    tools = getattr(cells, "tools", None)
    tokens = getattr(cells, "tokens", None)
    ctx = getattr(cells, "ctx_pct", None)
    fields = [
        _wv_field("ELAPSED", _wv_val(cells.elapsed, _wd_C_TEXT)),
        _wv_field("TOOLS", _wv_val("" if tools is None else str(tools), _wd_C_TEXT)),
        _wv_field("TOKENS", _wv_val("" if tokens is None else _wd_fmt_tok(tokens), _wd_C_TEXT)),
        _wv_field("CTX", _wv_val(_og_ctx_text(cells), _wd_ctx_color(ctx))),
        _wv_field("COST", cost_txt),
    ]
    return _wv_wrap(fields, cols)


def _sv_workers_line(stage: Stage, cols: int) -> str:
    total, running, done, failed = _sv_counts(stage.snap.workers)
    parts = [_wd_c("WORKERS ", _wd_C_DIM), _wd_c(f"{total} spawned", _wd_C_TEXT, bold=True)]
    parts.append(_wd_c(f"  {running} running", _wd_C_GREEN if running else _wd_C_FAINT))
    parts.append(_wd_c(f"  {done} done", _wd_C_SUB if done else _wd_C_FAINT))
    parts.append(_wd_c(f"  {failed} failed", _wd_C_RED if failed else _wd_C_FAINT))
    if stage.snap.groups:
        parts.append(_wd_c(f"  {len(stage.snap.groups)} batches", _wd_C_MUTED))
    return _wd_fit("".join(parts), cols)


def _sv_spark(stage: Stage, t: float, cols: int) -> str:
    cells = min(_SV_SPARK_MAX, max(0, cols - 20))
    bins = _os_bins(stage.lead_tool_pulses, t, cells, OS_SPAN_S)
    return _wd_fit(_wd_c("TOOLS/MIN ", _wd_C_DIM) + _os_spark(bins, cells) + _wd_c("  30m", _wd_C_FAINT), cols)


def _sv_text_block(label: str, text: str, cols: int, lines: int = _SV_TEXT_LINES) -> list[str]:
    """A labelled paragraph wrapped under the label, cut to `lines` lines with a `…` when longer."""
    width = max(10, cols - len(label) - 1)
    wrapped = _wd_wrap(text, width) if text.strip() else []
    if not wrapped:
        return [_wd_fit(_wd_c(label + " ", _wd_C_DIM) + _wv_unknown(), cols)]
    out = []
    for i, line in enumerate(wrapped[:lines]):
        last = i == lines - 1 and len(wrapped) > lines
        out.append(_wd_fit((_wd_c(label + " ", _wd_C_DIM) if i == 0 else " " * (len(label) + 1))
                           + _wd_c(line + (" …" if last else ""), _wd_C_TEXT), cols))
    return out


def _sv_doing(stage: Stage, t: float, cols: int) -> list[str]:
    if not stage.doing:
        return []
    out = [_sv_head("LEAD ACTIVITY")]
    for stamp, text in reversed(stage.doing[-_SV_DOING:]):
        age = _og_age(max(0.0, t - stamp)) if stamp else ""
        out.append(_wd_fit("  " + _wd_c(_wd_pad(age, 5), _wd_C_FAINT) + " " + _wd_c(_wd_clean(text), _wd_C_SUB), cols))
    return out


def _sv_changes(stage: Stage, cols: int) -> list[str]:
    work = stage.work
    if work is None or not (work.branch or work.dirty or work.ahead or work.unmerged):
        return []
    badge = work.badge()
    text = _wd_c(work.branch or "—", _wd_C_TEXT) + (_wd_c("  ● " + badge, _wd_C_AMBER) if badge else
                                                    _wd_c("  clean", _wd_C_FAINT))
    return [_wd_fit(_wd_c("GIT ", _wd_C_DIM) + text, cols)]


def _sv_worker_line(wk: Worker, selected: bool, t: float, cols: int) -> str:
    cells = _og_worker_cells(wk, t)
    state = wk.state or "—"
    name_w = max(12, min(30, cols // 4))
    left = (_wd_c("▸ " if selected else "  ", _wd_C_TEXT) + _sv_mark(wk, t) + " "
            + _wd_c(_wd_pad(_wd_trunc(_wd_clean(wk.name), name_w), name_w), _wd_C_TEXT, bold=selected) + " "
            + _wd_c(_wd_pad(_wd_trunc(cells.model or "—", 16), 16), _wd_C_SUB) + " "
            + _wd_c(_wd_pad(state, 9), _WV_STATE_COL.get(wk.state, _wd_C_MUTED)) + " "
            + _wd_c(_wd_pad(cells.elapsed or "—", 8), _wd_C_MUTED))
    tools = "" if cells.tools is None else f"{cells.tools}t"
    left += " " + _wd_c(_wd_pad(tools or "—", 5), _wd_C_MUTED) + " "
    room = max(0, cols - _wd_width(left))
    goal = _wd_clean(wk.goal or wk.title or (wk.summary[0] if wk.summary else ""))
    line = left + _wd_c(_wd_trunc(goal, room), _wd_C_FAINT)
    return _wd_fit(line, cols)


def _sv_worker_rows(stage: Stage, sel: int, t: float, cols: int) -> list[str]:
    head = _wd_fit(_wd_c("  " + "  " + "WORKER".ljust(max(12, min(30, cols // 4))) + " " + "MODEL".ljust(16) + " "
                         + "STATE".ljust(9) + " " + "ELAPSED".ljust(8) + " " + "TOOLS".ljust(5) + " GOAL", _wd_C_DIM), cols)
    workers = stage.snap.workers
    if not workers:
        return [head, _wd_fit(_wd_c("  this session has spawned no workers", _wd_C_FAINT), cols)]
    return [head] + [_sv_worker_line(wk, i == sel, t, cols) for i, wk in enumerate(workers)]


def _sv_sel(ui: UIState, stage: Stage) -> int:
    return min(max(ui.sel, 0), max(0, len(stage.snap.workers) - 1))


def _sv_page(stage: Stage, ui: UIState, cols: int, rows: int, t: float) -> list[str]:
    """Exactly `rows` lines (the key line is drawn by the caller)."""
    if rows <= 0 or cols <= 0:
        return [""] * max(0, rows)
    cells = _og_stage_cells(stage, t)
    rule = _wd_c("─" * cols, _wd_C_BORDER)
    head = [_sv_crumb(stage, t, cols), rule]
    head += _sv_identity(stage, cells.model, cols)
    head += _sv_metrics(stage, cells, cols)
    head.append(_sv_workers_line(stage, cols))
    head.append(_sv_spark(stage, t, cols))
    head += _sv_changes(stage, cols)
    head.append(rule)
    goal = _wd_clean(stage.goal) or _wd_clean(stage.snap.title)
    head += _sv_text_block("GOAL", goal, cols)
    if stage.brief_text and not stage.goal:
        head += _sv_text_block("BRIEF", " ".join(stage.brief_text), cols)
    extra = _sv_doing(stage, t, cols)
    if stage.snap.milestones:
        extra.append(_sv_head("MILESTONES"))
        for ms in reversed(stage.snap.milestones[-3:]):
            stamp = _wd_fmt_clock(ms.ts) if ms.ts is not None else "     "
            extra.append(_wd_fit("  " + _wd_c(stamp + " ", _wd_C_FAINT) + _wd_c(_wd_clean(ms.text), _wd_C_SUB), cols))
    body = extra + ([""] if extra else []) + [_sv_head("WORKERS THIS SESSION")] + _sv_worker_rows(stage, _sv_sel(ui, stage), t, cols)
    room = max(0, rows - len(head) - 1)
    sel_line = len(body) - len(stage.snap.workers) + _sv_sel(ui, stage) if stage.snap.workers else 0
    off = max(0, min(sel_line - room + 1, max(0, len(body) - room))) if sel_line >= room else 0   # keep the cursor in view
    lines = head + [""] + body[off:off + room]
    ui.hits.append((0, 0, 0, max(0, cols // 2), "back", ""))   # the crumb goes back to the tree
    first = len(body) - len(stage.snap.workers)
    for i in range(len(stage.snap.workers)):   # a click picks a worker, a double click opens its page
        if off <= first + i < off + room:
            y = len(head) + 1 + first + i - off
            ui.hits.append((y, y, 0, cols - 1, "svw", str(i)))
    lines = lines[:rows]
    lines += [""] * (rows - len(lines))
    return [_wd_fit(line, cols) for line in lines]


def _sv_key(ui: UIState, stage: Stage, key: str, rows: int) -> str:
    """Keys on the session page. Returns "" (used or ignored) or "worker:<name>" to open that worker's page."""
    count = len(stage.snap.workers)
    ui.sel = _sv_sel(ui, stage)
    if key in ("up", "k"):
        ui.sel = max(0, ui.sel - 1)
    elif key in ("down", "j"):
        ui.sel = min(max(0, count - 1), ui.sel + 1)
    elif key == "pgup":
        ui.sel = max(0, ui.sel - max(rows - 8, 1))
    elif key == "pgdn":
        ui.sel = min(max(0, count - 1), ui.sel + max(rows - 8, 1))
    elif key == "home":
        ui.sel = 0
    elif key == "end":
        ui.sel = max(0, count - 1)
    elif key in ("enter", "space", "o", "right", "l") and count:
        return "worker:" + stage.snap.workers[ui.sel].name
    return ""
