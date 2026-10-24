"""Control center: the detail pane for the selected row. Pure renderers: row data in, fitted lines out. Imports model,
widgets and the ops* helpers only."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from events import row_events
from model import (
    LIVE_STATES,
    TOOL_CATS,
    CCCall,
    Stage,
    Step,
    Worker,
    WorkState,
)
from opsgrid import (
    _og_harn_color,
    _og_pct,
    _og_stage_cells,
    _og_stage_harness,
    _og_tokens,
    _og_worker_cells,
    _og_worker_harness,
)
from opsspark import OS_SPAN_S, _os_bins, _os_clock, _os_dur, _os_spark
from opstree import OT_TAGS, _ot_tag
from widgets import (
    _wd_c,
    _wd_C_AMBER,
    _wd_C_BLUE,
    _wd_C_DIM,
    _wd_C_FAINT,
    _wd_C_GREEN,
    _wd_C_MUTED,
    _wd_C_RED,
    _wd_C_SUB,
    _wd_C_TEXT,
    _wd_clean,
    _wd_fit,
    _wd_fmt_mem,
    _wd_fmt_money,
    _wd_fmt_tok,
    _wd_gauge_bar,
    _wd_pad,
    _wd_pct_color,
    _wd_trunc,
    _wd_width,
    _wd_wrap,
)

PANE_TABS = ("Overview", "Prompt", "Tools", "Files", "Events", "Metrics")
PANE_TAB_ARROWS = ("left", "right")          # cycle tabs when the pane has focus (see cckeys)
_PANE_TAB_SHORT = {"Tools": "Tools", "Files": "Files"}   # [:4] drops the plural on these two only

_CX_LABEL_CAP = 14          # label column cap per tab (same idea as ccstrip._CX_LABEL_W)
_CX_SPARK_MAX = 60          # most sparkline cells
_CX_TOOL_W_MAX = 10         # tool column cap in the Tools table
_CX_TOOL_W_MIN = 6
_CX_EVENT_HEAD = 18         # `HH:MM:SS kind     ` before the event text

# tool name -> category (TOOL_CATS); the log already groups by category, calls need this
_CX_TOOL_CAT = {
    "read": "read", "cat": "read", "view": "read", "open": "read",
    "edit": "edit", "write": "edit", "apply_patch": "edit", "patch": "edit",
    "multiedit": "edit", "notebookedit": "edit",
    "bash": "shell", "shell": "shell", "exec": "shell", "terminal": "shell", "run": "shell",
    "grep": "search", "glob": "search", "search": "search", "find": "search",
}
_CX_FILE_TOOLS = ("edit", "write", "apply_patch", "patch", "read", "notebookedit", "multiedit")
_CX_PATH_RE = re.compile(r"[A-Za-z0-9_.~/\-]+")
_CX_EXT_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$")


def pane_tab_digit(key: str) -> str | None:
    """Map `1`..`len(PANE_TABS)` to a tab name; None when the key is not a pane tab digit."""
    if len(key) == 1 and key.isdigit():
        index = int(key) - 1
        if 0 <= index < len(PANE_TABS):
            return PANE_TABS[index]
    return None


def _cpn_tab_label(name: str, short: bool) -> str:
    if short:
        return _PANE_TAB_SHORT.get(name, name[:4])
    return name


@dataclass
class _cpn_Row:
    """The union of the lead and worker fields the tabs draw, so each tab branches once, in _cpn_row."""
    name: str = ""
    tag: str = ""
    harness: str = ""
    model: str = ""             # display label, e.g. "opus-5.5 · hi"
    state: str = ""
    goal: str = ""
    outcome: str = ""
    lead: bool = False
    brief: list[str] = field(default_factory=list)
    prompt: list[str] = field(default_factory=list)
    system_prompt: list[str] = field(default_factory=list)
    calls: list[CCCall] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    tool_counts: dict[str, int] = field(default_factory=dict)
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cache: int | None = None
    tokens_total: int | None = None
    ctx: int | None = None
    ctx_window: int | None = None
    cost: float | None = None
    elapsed: str = ""
    cpu: float | None = None
    rss: int | None = None
    pid: int | None = None
    errors: int = 0
    result: list[str] = field(default_factory=list)
    failure: str = ""
    flags: frozenset[str] = frozenset()
    pulses: list[tuple[float, int]] = field(default_factory=list)
    tool_pulses: list[tuple[float, int]] = field(default_factory=list)
    doing: list[tuple[float, str]] = field(default_factory=list)
    work: WorkState | None = None


def _cpn_worker_harness(worker: Worker) -> str:
    """The short harness label from the ops grid, falling back to the control center's `harness` field."""
    label = _og_worker_harness(worker)
    if label != "—":
        return label
    mapping = {"claude": "CC", "codex": "Codex", "opencode": "OCode", "cursor": "Cursr", "openclaw": "OClaw"}
    return mapping.get((worker.harness or "").lower(), worker.harness)


def _cpn_row(stage: Stage, worker: Worker | None, now: float) -> _cpn_Row:
    """Flatten the selected row (worker or the lead session) into the fields the tabs use."""
    if worker is None:
        cells = _og_stage_cells(stage, now)
        name = stage.display_name or stage.session_name or stage.label or stage.session_id[:8]
        return _cpn_Row(
            name=name, tag=stage.level_tag or ("LEAD" if not stage.parent else "SPAWN"),
            harness=_og_stage_harness(stage), model=cells.model, state=stage.state or stage.lead_state,
            goal=stage.goal, outcome=stage.outcome, lead=True,
            brief=list(stage.brief_text), prompt=list(stage.prompt), system_prompt=list(stage.system_prompt),
            calls=list(stage.calls), tokens_total=stage.lead_tokens, ctx=stage.lead_ctx,
            ctx_window=stage.lead_ctx_window, cost=stage.lead_cost, elapsed=cells.elapsed,
            pulses=[], tool_pulses=list(stage.lead_tool_pulses), doing=list(stage.doing), work=stage.work,
        )
    log = worker.log
    cells = _og_worker_cells(worker, now)
    tokens_total = _og_tokens(log.tokens_in, log.tokens_out)
    return _cpn_Row(
        name=worker.name, tag=worker.level_tag or "WORKER", harness=_cpn_worker_harness(worker),
        model=cells.model, state=worker.state, goal=worker.goal, lead=False,
        brief=list(worker.brief_text), prompt=list(worker.prompt), system_prompt=list(worker.system_prompt),
        calls=list(worker.calls), steps=list(log.steps), tool_counts=dict(log.tool_counts),
        tokens_in=log.tokens_in, tokens_out=log.tokens_out, tokens_cache=log.tokens_cache,
        tokens_total=tokens_total, ctx=log.ctx_tokens, ctx_window=log.ctx_window, cost=log.cost,
        elapsed=cells.elapsed, cpu=worker.cpu, rss=worker.rss, pid=worker.pid, errors=log.errors,
        result=list(worker.result_text) or list(worker.summary), failure=worker.failure, flags=worker.flags,
        pulses=list(log.pulses), tool_pulses=list(log.tool_pulses), work=stage.work,
    )


# --- small shared pieces -------------------------------------------------------------------------
def _cpn_unknown() -> str:
    return _wd_c("—", _wd_C_FAINT)


def _cpn_head(text: str) -> str:
    return _wd_c(text, _wd_C_DIM, bold=True)


def _cpn_nodata(cols: int, indent: bool = False, text: str = "no data") -> str:
    return _wd_fit(_wd_c(("  " if indent else "") + text, _wd_C_FAINT), cols)


def _cpn_empty(text: str, cols: int) -> list[str]:
    """One dim sentence centred in the pane, wrapped onto more centred lines only when the pane is narrower than it."""
    out: list[str] = []
    for line in _wd_wrap(text, max(1, cols)):
        pad = max(0, (cols - _wd_width(line)) // 2)
        out.append(_wd_fit(" " * pad + _wd_c(line, _wd_C_FAINT), cols))
    return out


def _cpn_label_width(labels: list[str], cols: int) -> int:
    """Shared label column width for one tab's label/value rows."""
    if cols < 4:
        return max(1, cols - 2)
    longest = max((len(label) for label in labels), default=0)
    return max(1, min(min(longest, _CX_LABEL_CAP), cols - 2))


def _cpn_labeled_plain(label: str, text: str, label_w: int, cols: int,
                       color: tuple[int, int, int] = _wd_C_TEXT, max_lines: int = 0) -> list[str]:
    """Plain-text value lines: MUTED label column, wrapped values start under the value column."""
    value_col = label_w + 1
    room = max(1, cols - value_col)
    plain = _wd_clean(text).strip()
    if not plain:
        return [_wd_fit(_wd_c(label.ljust(label_w), _wd_C_MUTED) + " " + _cpn_unknown(), cols)]
    wrapped = _wd_wrap(plain, room)
    cap = max_lines if max_lines > 0 else len(wrapped)
    out: list[str] = []
    for i, line in enumerate(wrapped[:cap]):
        more = i == cap - 1 and len(wrapped) > cap
        prefix = _wd_c(label.ljust(label_w), _wd_C_MUTED) + " " if i == 0 else " " * value_col
        out.append(_wd_fit(prefix + _wd_c(line + (" …" if more else ""), color), cols))
    return out


def _cpn_labeled_styled(label: str, body: str, label_w: int, cols: int) -> str:
    """One line whose value is already coloured (gauges, token breakdowns, tool counts)."""
    return _wd_fit(_wd_c(label.ljust(label_w), _wd_C_MUTED) + " " + body, cols)


def _cpn_labeled_block(cols: int, rows: list[tuple[str, str, tuple[int, int, int], int]],
                       styled: list[tuple[str, str]] | None = None) -> list[str]:
    """Several label/value rows sharing one label width; `rows` are plain (label, text, colour, max_lines)."""
    labels = [label for label, _text, _color, _cap in rows] + [label for label, _body in (styled or [])]
    label_w = _cpn_label_width(labels, cols)
    out: list[str] = []
    for label, text, color, cap in rows:
        out += _cpn_labeled_plain(label, text, label_w, cols, color, cap)
    for label, body in styled or []:
        out.append(_cpn_labeled_styled(label, body, label_w, cols))
    return out


def _cpn_model_text(model: str) -> str:
    """`model@effort`: the shared cells join the two with ` · `; a label that already carries `@` keeps its own."""
    head, _, tail = model.partition(" · ")
    if not tail:
        return head
    return head if "@" in head else f"{head}@{tail}"


def _cpn_identity(row: _cpn_Row, cols: int) -> str:
    tag = row.tag if row.tag in OT_TAGS else ("LEAD" if row.lead else "WORKER")
    name = _wd_trunc(_wd_clean(row.name) or "—", max(6, cols // 3))
    harn = (_wd_c(row.harness, _og_harn_color(row.harness)) if row.harness else _cpn_unknown())
    model = _wd_c(_cpn_model_text(row.model), _wd_C_MUTED) if row.model else _cpn_unknown()
    return _wd_fit(_ot_tag(tag, cols < 80) + " " + _wd_c(name, _wd_C_TEXT, bold=True)
                   + "  " + harn + "  " + model, cols)


def _cpn_now(row: _cpn_Row) -> str:
    """The newest step or call as one line, "" when the row has neither."""
    cands: list[tuple[float, str]] = []
    if row.calls:
        call = row.calls[-1]
        cands.append((call.ts, f"{call.tool} {call.arg}".strip()))
    if row.steps:
        step = row.steps[-1]
        cands.append((step.ts, f"{step.tool or step.kind} {step.text}".strip()))
    if row.doing:
        stamp, text = row.doing[-1]
        cands.append((stamp, _wd_clean(text)))
    return max(cands, key=lambda item: item[0])[1] if cands else ""


def _cpn_tokens_text(row: _cpn_Row) -> str:
    if row.tokens_in is not None or row.tokens_out is not None or row.tokens_cache is not None:
        parts = []
        for label, value in (("in", row.tokens_in), ("out", row.tokens_out), ("cache", row.tokens_cache)):
            parts.append(f"{label} " + (_wd_fmt_tok(value) if value is not None else "—"))
        return " · ".join(parts)
    if row.tokens_total is not None:
        return _wd_fmt_tok(row.tokens_total)
    return ""


def _cpn_tokens_body(row: _cpn_Row) -> str:
    text = _cpn_tokens_text(row)
    return _wd_c(text, _wd_C_TEXT) if text else _cpn_unknown()


def _cpn_ctx_body(row: _cpn_Row, cols: int) -> str:
    pct = _og_pct(row.ctx, row.ctx_window)
    if row.ctx is None and row.ctx_window is None:
        return _cpn_unknown()
    if row.ctx_window:
        used = f"{pct}%" if pct is not None else "—"
        body = _wd_c(f"{used} of {_wd_fmt_tok(row.ctx_window)}", _wd_C_TEXT)
        if pct is not None:
            width = min(14, max(4, cols - 34))
            body += " " + _wd_gauge_bar(pct / 100, width, _wd_pct_color(pct))
        return body
    return _wd_c(f"{_wd_fmt_tok(row.ctx)} (window unknown)", _wd_C_TEXT)


def _cpn_cats(row: _cpn_Row) -> dict[str, int]:
    counts = {cat: 0 for cat in TOOL_CATS}
    if row.tool_counts:
        for name, value in row.tool_counts.items():
            counts[name if name in counts else "other"] += int(value or 0)
    elif row.calls:
        for call in row.calls:
            counts[_cpn_cat(call.tool)] += 1
    return counts


def _cpn_cat(tool: str) -> str:
    name = (tool or "").lower()
    if name in _CX_TOOL_CAT:
        return _CX_TOOL_CAT[name]
    for key, cat in _CX_TOOL_CAT.items():
        if key in name:
            return cat
    return "other"


def _cpn_tool_body(row: _cpn_Row) -> str:
    counts = _cpn_cats(row)
    total = sum(counts.values())
    if not total:
        return _cpn_unknown()
    parts = [_wd_c(f"{total} calls", _wd_C_TEXT)]
    for cat in TOOL_CATS:
        if counts.get(cat):
            parts.append(_wd_c(f"{cat} {counts[cat]}", _wd_C_SUB))
    if row.errors:
        parts.append(_wd_c(f"{row.errors} failed", _wd_C_RED))
    return _wd_c("  ·  ", _wd_C_FAINT).join(parts)


def _cpn_result_rows(row: _cpn_Row) -> list[tuple[str, str, tuple[int, int, int], int]]:
    rows: list[tuple[str, str, tuple[int, int, int], int]] = []
    if row.failure:
        rows.append(("FAILED", row.failure, _wd_C_RED, 4))
    if row.result and row.state not in LIVE_STATES:
        rows.append(("RESULT", " ".join(row.result), _wd_C_TEXT, 6))
    if row.lead and row.outcome:
        rows.append(("OUTCOME", _wd_clean(row.outcome), _wd_C_TEXT, 0))
    if row.flags:
        rows.append(("FLAGS", ", ".join(sorted(_wd_clean(flag) for flag in row.flags)), _wd_C_AMBER, 0))
    return rows


def _cpn_overview(row: _cpn_Row, cols: int) -> list[str]:
    out = [_cpn_identity(row, cols)]
    plain: list[tuple[str, str, tuple[int, int, int], int]] = [
        ("GOAL", row.goal, _wd_C_TEXT, 3),
        ("NOW", _cpn_now(row), _wd_C_TEXT, 2),
    ]
    if row.cpu is not None:
        plain.append(("CPU", f"{row.cpu:.0f}%", _wd_C_TEXT, 0))
    if row.rss is not None:
        plain.append(("RSS", _wd_fmt_mem(row.rss), _wd_C_TEXT, 0))
    if row.pid:
        plain.append(("PID", str(row.pid), _wd_C_TEXT, 0))
    plain.append(("COST", _wd_fmt_money(row.cost) if row.cost is not None else "", _wd_C_TEXT, 0))
    elapsed = row.elapsed if row.elapsed and row.elapsed != "—" else ""
    plain.append(("ELAPSED", elapsed, _wd_C_TEXT, 0))
    plain += _cpn_result_rows(row)
    styled = [("TOKENS", _cpn_tokens_body(row)), ("CTX", _cpn_ctx_body(row, cols)),
              ("TOOLS", _cpn_tool_body(row))]
    out += _cpn_labeled_block(cols, plain, styled)
    return out


def _cpn_prompt(row: _cpn_Row, cols: int) -> list[str]:
    if not any(_wd_clean(line).strip() for lines in (row.brief, row.prompt, row.system_prompt) for line in lines):
        return _cpn_empty("no prompt captured: this harness does not expose it", cols)
    out: list[str] = []
    for label, lines in (("BRIEF", row.brief), ("FIRST PROMPT", row.prompt), ("SYSTEM PROMPT", row.system_prompt)):
        out.append(_cpn_head(label))
        text = " ".join(_wd_clean(line) for line in lines).strip()
        if text:
            out += [_wd_fit(_wd_c(line, _wd_C_SUB), cols) for line in _wd_wrap(text, cols)]
        else:
            out.append(_cpn_nodata(cols, indent=True, text="none recorded"))
    return out


def _cpn_tool_calls(row: _cpn_Row) -> list[CCCall]:
    """The row's calls (newest last); a log with only tool steps is adapted to the same shape."""
    if row.calls:
        return list(row.calls)
    return [CCCall(ts=step.ts, tool=step.tool or step.kind, arg=step.text, outcome=step.outcome, dur=step.dur)
            for step in row.steps if step.kind == "tool"]


def _cpn_tool_widths(cols: int) -> tuple[int, int]:
    tool_w = min(_CX_TOOL_W_MAX, max(_CX_TOOL_W_MIN, cols // 5))
    return tool_w, max(4, cols - (22 + tool_w))   # time, spaces, tool, result, duration


def _cpn_tool_head(cols: int) -> str:
    tool_w, arg_w = _cpn_tool_widths(cols)
    return _wd_fit(_wd_c(f"{'TIME':<8} {'TOOL':<{tool_w}} {'ARG':<{arg_w}} {'RES':<4} {'DUR':>6}", _wd_C_DIM), cols)


def _cpn_tool_row(call: CCCall, cols: int) -> str:
    tool_w, arg_w = _cpn_tool_widths(cols)
    clock = _os_clock(call.ts)
    tool = _wd_trunc(_wd_clean(call.tool) or "—", tool_w)
    arg = _wd_trunc(_wd_clean(call.arg), arg_w)
    failed = call.outcome == "failed"
    result = "FAIL" if failed else "ok" if call.outcome == "ok" else "…"
    dur = _os_dur(call.dur)
    if failed:   # the whole failed row is red, so the FAIL columns read at a glance
        plain = f"{clock} {tool:<{tool_w}} {arg:<{arg_w}} {result:<4} {dur:>6}"
        return _wd_fit(_wd_c(plain, _wd_C_RED), cols)
    res_color = _wd_C_GREEN if call.outcome == "ok" else _wd_C_FAINT
    line = (_wd_c(clock, _wd_C_FAINT) + " " + _wd_c(_wd_pad(tool, tool_w), _wd_C_BLUE) + " "
            + _wd_c(_wd_pad(arg, arg_w), _wd_C_SUB) + " " + _wd_c(_wd_pad(result, 4), res_color) + " "
            + _wd_c(dur.rjust(6), _wd_C_MUTED))
    return _wd_fit(line, cols)


def _cpn_tools(row: _cpn_Row, cols: int) -> list[str]:
    calls = _cpn_tool_calls(row)
    if not calls:
        return _cpn_empty("no tool calls recorded yet", cols)
    out = [_cpn_tool_head(cols)]
    for call in calls:
        out.append(_cpn_tool_row(call, cols))
        if call.err:
            out.append(_wd_fit(_wd_c("  ↳ " + _wd_trunc(_wd_clean(call.err), max(1, cols - 4)), _wd_C_RED), cols))
    return out


def _cpn_path(text: str) -> str:
    """The first path-looking token in a call's argument (a token with `/`, else one with a file extension)."""
    fallback = ""
    for token in _CX_PATH_RE.findall(text or ""):
        if len(token) < 3 or token.startswith(("//", "www.")) or token in ("...",):
            continue
        if "/" in token:
            return token
        if not fallback and _CX_EXT_RE.search(token):
            fallback = token
    return fallback


def _cpn_files(row: _cpn_Row, cols: int) -> list[str]:
    counts: dict[str, list] = {}
    for call in _cpn_tool_calls(row):
        if not any(key in (call.tool or "").lower() for key in _CX_FILE_TOOLS):
            continue
        path = _cpn_path(call.arg)
        if not path:
            continue
        rec = counts.setdefault(path, [0, 0.0])
        rec[0] += 1
        rec[1] = max(rec[1], call.ts)
    ordered = sorted(counts.items(), key=lambda item: (-item[1][1], item[0]))
    if not ordered and row.work is None:
        return _cpn_empty("no file reads or edits recorded yet", cols)
    out = [_cpn_head(f"FILES {len(ordered)}" if ordered else "FILES")]
    if not ordered:
        out.append(_cpn_nodata(cols, indent=True, text="no file reads or edits yet"))
    for path, (hits, stamp) in ordered:
        tail = f"×{hits}  {_os_clock(stamp)}"
        room = max(6, cols - _wd_width(tail) - 2)
        out.append(_wd_fit(_wd_c(_wd_trunc(path, room), _wd_C_TEXT) + "  " + _wd_c(tail, _wd_C_FAINT), cols))
    out += _cpn_git(row.work, cols)
    return out


def _cpn_git(work: WorkState | None, cols: int) -> list[str]:
    out = [_cpn_head("GIT")]
    if work is None:
        out.append(_cpn_nodata(cols, indent=True))
        return out
    bits: list[str] = []
    if work.dirty:
        bits.append(_wd_c("uncommitted", _wd_C_AMBER))
    elif work.dirty is False:
        bits.append(_wd_c("clean", _wd_C_GREEN))
    if work.ahead:
        bits.append(_wd_c(f"{work.ahead} unpushed", _wd_C_AMBER))
    if work.unmerged:
        bits.append(_wd_c("unmerged", _wd_C_BLUE))
    elif work.unmerged is False and work.branch and work.dirty is False:
        bits.append(_wd_c("merged", _wd_C_GREEN))
    state = _wd_c("  ·  ", _wd_C_FAINT).join(bits) if bits else _cpn_unknown()
    plain: list[tuple[str, str, tuple[int, int, int], int]] = [
        ("branch", _wd_clean(work.branch) or "—", _wd_C_TEXT, 0),
    ]
    styled = [("state", state)]
    if work.checked:
        plain.append(("checked", _os_clock(work.checked), _wd_C_TEXT, 0))
    out += _cpn_labeled_block(cols, plain, styled)
    return out


def _cpn_event_line(ts: float, kind: str, text: str, cols: int, failed: bool = False) -> str:
    clock = _os_clock(ts)
    kind_text = _wd_trunc(_wd_clean(kind) or "—", 8)
    body = _wd_trunc(_wd_clean(text), max(1, cols - _CX_EVENT_HEAD - 1))
    if failed:
        return _wd_fit(_wd_c(f"{clock} {kind_text:<8} {body}", _wd_C_RED), cols)
    kind_color = _wd_C_RED if kind_text in ("err", "error") else _wd_C_MUTED if kind_text in ("msg", "text") else _wd_C_BLUE
    return _wd_fit(_wd_c(clock, _wd_C_FAINT) + " " + _wd_c(_wd_pad(kind_text, 8), kind_color) + " "
                   + _wd_c(body, _wd_C_SUB), cols)


def _cpn_events(row: _cpn_Row, cols: int) -> list[str]:
    if row.steps:   # steps carry the text/error events the raw calls do not
        out: list[str] = []
        for step in row.steps:
            kind = "err" if step.kind == "error" else "tool" if step.kind == "tool" else "msg"
            text = f"{step.tool} {step.text}".strip() if step.kind == "tool" else step.text
            out.append(_cpn_event_line(step.ts, kind, text, cols, step.outcome == "failed"))
        return out
    calls = _cpn_tool_calls(row)
    if calls:
        return [_cpn_event_line(call.ts, "tool", f"{call.tool} {call.arg}".strip(), cols,
                               call.outcome == "failed") for call in calls]
    if row.doing:
        return [_cpn_event_line(stamp, "msg", text, cols) for stamp, text in row.doing]
    return _cpn_empty("no events logged yet for this row", cols)


def _cpn_meter_body(label: str, pulses: list[tuple[float, int]], now: float, cols: int) -> str:
    cells = min(_CX_SPARK_MAX, max(0, cols - 20))
    if not pulses or cells <= 0:
        return _wd_c("no data", _wd_C_FAINT)
    bins = _os_bins(pulses, now, cells, OS_SPAN_S)
    return _os_spark(bins, cells) + _wd_c("  30m", _wd_C_FAINT)


def _cpn_metrics(row: _cpn_Row, cols: int, now: float) -> list[str]:
    if not (row.pulses or row.tool_pulses or _cpn_tokens_text(row) or row.ctx is not None
            or row.ctx_window is not None):
        return _cpn_empty("no metrics yet: no token, context or event counts recorded", cols)
    labels = ["TOOLS/MIN", "EVENTS/MIN", "CTX", "TOKENS"]
    label_w = _cpn_label_width(labels, cols)
    return [
        _cpn_labeled_styled("TOOLS/MIN", _cpn_meter_body("TOOLS/MIN", row.tool_pulses, now, cols), label_w, cols),
        _cpn_labeled_styled("EVENTS/MIN", _cpn_meter_body("EVENTS/MIN", row.pulses, now, cols), label_w, cols),
        _cpn_labeled_styled("CTX", _cpn_ctx_body(row, cols), label_w, cols),
        _cpn_labeled_styled("TOKENS", _cpn_tokens_body(row), label_w, cols),
    ]


def _cpn_fill(stage: Stage, worker: Worker | None, cols: int, room: int) -> list[str]:
    """Overview's dim "Recent activity" block: a blank line, a head and the row's last 6 events, newest last.
    Empty unless the whole block fits in `room` lines."""
    events = row_events(stage, worker)[-6:]
    if not events or len(events) + 2 > room:
        return []
    out = [_wd_fit("", cols), _cpn_head("Recent activity")]
    for event in events:
        out.append(_cpn_event_line(event.ts, event.kind, event.text, cols, event.kind == "err"))
    return out


def _cpn_content(stage: Stage, worker: Worker | None, tab: str, cols: int, now: float,
                 height: int = 0) -> list[str]:
    row = _cpn_row(stage, worker, now)
    tab = tab if tab in PANE_TABS else PANE_TABS[0]
    if tab == "Prompt":
        return _cpn_prompt(row, cols)
    if tab == "Tools":
        return _cpn_tools(row, cols)
    if tab == "Files":
        return _cpn_files(row, cols)
    if tab == "Events":
        return _cpn_events(row, cols)
    if tab == "Metrics":
        return _cpn_metrics(row, cols, now)
    out = _cpn_overview(row, cols)
    return out + _cpn_fill(stage, worker, cols, height - len(out))


def pane_tab_bar(tab: str, width: int) -> tuple[str, list[tuple[int, int, str]]]:
    """The tab bar line, exactly `width` cells, and the click spans [(col0, col1, tab name)] (0-based, inclusive)
    relative to the line's start."""
    width = max(0, width)
    active = tab if tab in PANE_TABS else ""
    best_line = ""
    best_spans: list[tuple[int, int, str]] = []
    for short, marker in ((False, True), (False, False), (True, False)):
        parts: list[str] = []
        spans: list[tuple[int, int, str]] = []
        col = 0
        for name in PANE_TABS:
            label = _cpn_tab_label(name, short)
            text = ("▸" if marker and name == active else "") + label
            if parts:
                col += 2
            spans.append((col, col + len(label) - 1, name))
            parts.append(_wd_c(text, _wd_C_TEXT, bold=True) if name == active else _wd_c(text, _wd_C_DIM))
            col += len(text)
        best_line, best_spans = "  ".join(parts), spans
        if col <= width:
            break
    spans = [span for span in best_spans if span[1] < width]
    return _wd_fit(best_line, width), spans


def pane_lines(stage: Stage, worker: Worker | None, tab: str, width: int, height: int, scroll: int = 0,
               now: float = 0.0) -> list[str]:
    """Exactly `height` lines of `width` cells for `tab` of the selected row (worker None = the lead/session row).
    Scrolls down by `scroll` lines, clamped. A row with no data for a tab shows one dim "no data" line, never an error."""
    if height <= 0:
        return []
    if width <= 0:
        return [""] * height
    content = _cpn_content(stage, worker, tab, width, now, height)
    top = min(max(scroll, 0), max(0, len(content) - height))
    window = content[top:top + height]
    window += [""] * (height - len(window))
    return [_wd_fit(line, width) for line in window]


def pane_max_scroll(stage: Stage, worker: Worker | None, tab: str, width: int, height: int) -> int:
    """The largest useful `scroll` for pane_lines with the same arguments."""
    if height <= 0:
        return 0
    if width <= 0:
        return 0
    return max(0, len(_cpn_content(stage, worker, tab, width, 0.0, height)) - height)
