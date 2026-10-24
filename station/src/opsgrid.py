"""Ops console: the width plan, metric cells and row/heading lines (R1). Pure text helpers; imports model, widgets,
opsspark and opstree only. The width plan lives here and nowhere else."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from labels import display_model
from model import Stage, Worker, WorkState
from opsspark import _os_bins, _os_spark, _os_tools_spark, _os_why_line
from opstree import _ot_tag
from widgets import (
    _wd_bg_line,
    _wd_c,
    _wd_C_AMBER,
    _wd_C_BLUE,
    _wd_C_BORDER,
    _wd_C_CYAN,
    _wd_C_DIM,
    _wd_C_FAINT,
    _wd_C_GREEN,
    _wd_C_MUTED,
    _wd_C_RED,
    _wd_C_SELBG,
    _wd_C_SUB,
    _wd_C_TEXT,
    _wd_C_VIOLET,
    _wd_clean,
    _wd_clip,
    _wd_ctx_color,
    _wd_family_color,
    _wd_fmt_dur,
    _wd_fmt_money,
    _wd_fmt_tok,
    _wd_pad,
    _wd_spinner,
    _wd_trunc,
    _wd_width,
)

# Metric columns left to right: (key, width). One list drives the plan, the header, every row and the project totals,
# so a header cell and its data can never drift apart. A narrow width drops columns in _OG_DROP order.
_OG_NAME_MIN = 20     # the name column never gets less than this (a session's name is the point of the row)
_OG_TAIL_NAME = 3      # a row keeps at least this much of its name before a tail may take the rest of the name column
_OG_DROP = ("spark", "tools", "tokens", "tag", "harn", "ctx", "cost")   # model and elapsed are the last to go (phone rule)
_OG_PHONE_COLS = 90
_OG_EMPTY_METRICS = ("ctx", "cost", "tokens", "tools")   # may hide on a phone when every drawn row is empty
_OG_WIDE_COLS = 190
_OG_HEAD_NAME = 28
_OG_HEAD_NAME_WIDE = 56   # project titles may use more of the line when the terminal is very wide


@dataclass
class _og_Plan:
    """Which columns a width shows, and their widths (spec section 2)."""
    cols: int
    spark: bool = False
    tools: bool = False
    tokens: bool = False
    short: bool = False      # 4-letter level tags
    tag_w: int = 6
    model_w: int = 16
    fixed: int = 0           # width of everything right of the name column, the 2-cell gutter included
    control_center: bool = False
    hidden: set[str] = field(default_factory=set)   # columns a narrow width dropped

    def columns(self) -> list[tuple[str, int]]:
        """The shown metric columns, left to right."""
        every = [("tag", self.tag_w), ("harn", 5), ("model", self.model_w), ("ctx", 4), ("tokens", 6), ("cost", 6),
                 ("elapsed", 7), ("tools", 5), ("spark", 15)]
        return [(key, width) for key, width in every if key not in self.hidden]

    @property
    def name_w(self) -> int:
        """Cells of the name column (gutter excluded)."""
        return max(0, self.cols - self.fixed)


@dataclass
class _og_Cells:
    """One row's metric values, unformatted. None = unknown (drawn as a FAINT `—`)."""
    tag: str = ""
    harn: str = ""
    model: str = ""
    ctx_pct: int | None = None
    ctx_tokens: int | None = None     # the context size itself; the CTX cell shows it, coloured by ctx_pct
    ctx_has_data: bool = False        # raw ctx exists even when window is missing (cell still draws `—`)
    tokens: int | None = None
    cost: float | None = None
    cost_note: str = ""                 # "local" | "sub" | "" for an unknown cost
    elapsed: str = ""                   # already formatted, e.g. "12m 39s" or "⟳ 30m"
    tools: int | None = None
    pulses: list[tuple[float, int]] = field(default_factory=list)
    now: str = ""
    now_age: float | None = None
    live: bool = False
    sub: bool = True                    # False: this row's NOW sub-line is shown elsewhere (folded or opened rows)


def _og_metric_empty(key: str, cells: _og_Cells) -> bool:
    """True when a metric column would draw as blank (`—` or an empty total)."""
    if key == "ctx":
        return cells.ctx_pct is None and not cells.ctx_has_data
    if key == "tokens":
        return cells.tokens is None
    if key == "cost":
        return cells.cost is None
    if key == "tools":
        return cells.tools is None
    return False


def _og_hide_empty_metrics(plan: _og_Plan, rows: list[_og_Cells]) -> None:
    """On a phone, drop metric columns that are empty on every session, worker and project heading row."""
    if plan.cols >= _OG_PHONE_COLS or not rows:
        return
    for key in _OG_EMPTY_METRICS:
        if key in plan.hidden or not all(_og_metric_empty(key, cells) for cells in rows):
            continue
        plan.hidden.add(key)


def _og_plan(cols: int, control_center: bool = False, metric_rows: list[_og_Cells] | None = None) -> _og_Plan:
    """One column plan per width, the same in every mode (v never moves a column). A width that leaves the name column
    under _OG_NAME_MIN drops metric columns in _OG_DROP order until it fits."""
    plan = _og_Plan(cols, control_center=control_center)
    plan.hidden = {key for key, ok in (("spark", cols >= 112), ("tools", cols >= 100), ("tokens", cols >= 90)) if not ok}
    plan.spark = cols >= 112
    plan.tools = cols >= 100
    plan.tokens = cols >= 90
    plan.short = cols < 100
    plan.tag_w = 4 if plan.short else 6
    plan.model_w = 12 if cols < 90 else 16
    phone = cols < _OG_PHONE_COLS and metric_rows
    if phone:
        _og_hide_empty_metrics(plan, metric_rows)
    for dropped in (None, *_OG_DROP):
        if dropped is not None:
            plan.hidden.add(dropped)
            if dropped in ("spark", "tools", "tokens"):
                setattr(plan, dropped, False)
        cells = plan.columns()
        plan.fixed = 2 + sum(width for _, width in cells) + len(cells) - 1
        if cols - plan.fixed >= _OG_NAME_MIN:
            break
    return plan


def _og_header(plan: _og_Plan) -> str:
    """The column header line: every label sits over its data (text left-aligned, numbers right-aligned, as in _og_row)."""
    labels = {"tag": ("LVL" if plan.short else "LEVEL", False), "harn": ("HARN", False), "model": ("MODEL", False),
              "ctx": ("CTX", True), "tokens": ("TOKENS", True), "cost": ("COST", True), "elapsed": ("ELAPSED", True),
              "tools": ("TOOLS", True)}
    cells = []
    for key, width in plan.columns():
        if key == "spark":
            cells.append("TOOLS/MIN" + " " * max(1, width - len("TOOLS/MIN") - len("30m")) + "30m")
        else:
            text, right = labels[key]
            cells.append((_og_lpad if right else _og_rpad)(text, width))
    line = _og_rpad("  SESSION", plan.name_w) + "  " + " ".join(cells)
    return _wd_c(_wd_trunc(line, plan.cols), _wd_C_MUTED)


def _og_worker_cells(wk: Worker, now: float) -> _og_Cells:
    log = wk.log
    live = wk.state in ("running", "quiet", "stalled")
    model = display_model(wk.model or log.model)
    if wk.effort:
        effort = _og_effort(wk.effort)
        model = f"{model} · {effort}" if model else effort
    ctx = _og_pct(log.ctx_tokens, log.ctx_window)
    tokens = _og_tokens(log.tokens_in, log.tokens_out)
    has_log = bool(log.path or log.steps or log.tool_counts or log.tool_pulses or log.last_ts)
    tools = sum(log.tool_counts.values()) if has_log else None
    step = log.steps[-1] if live and log.steps else None
    local = _og_local(wk.family, wk.account, log.harness, wk.model, log.model)
    return _og_Cells(
        tag=wk.level_tag or "WORKER", harn=_og_worker_harness(wk), model=model, ctx_pct=ctx,
        ctx_tokens=log.ctx_tokens, ctx_has_data=log.ctx_tokens is not None or bool(log.ctx_window),
        tokens=tokens, cost=log.cost, cost_note="local" if local else "", elapsed=_og_worker_elapsed(wk, now),
        tools=tools, pulses=list(log.tool_pulses), now=step.text if step else "",
        now_age=max(0.0, now - log.last_ts) if step and log.last_ts else None, live=live,
    )


def _og_stage_cells(stage: Stage, now: float) -> _og_Cells:
    live = stage.state == "active" or stage.lead_state in ("live", "background")
    model = display_model(stage.lead_model)
    if stage.effort:
        effort = _og_effort(stage.effort)
        model = f"{model} · {effort}" if model else effort
    pct = _og_pct(stage.lead_ctx, stage.lead_ctx_window)
    name = stage.display_name or stage.session_name or stage.label
    tag = "" if stage.unlinked else stage.level_tag or ("LEAD" if not stage.parent else "SPAWN")
    harn = _og_stage_harness(stage)
    pulse = list(stage.lead_tool_pulses)
    stamp, text = stage.doing[-1] if live and stage.doing else (0.0, "")
    local = _og_local(stage.tag, stage.lead_kind, stage.vitals.provider if stage.vitals else "", model, name)
    return _og_Cells(
        tag=tag, harn=harn, model=model, ctx_pct=pct, ctx_tokens=stage.lead_ctx,
        ctx_has_data=stage.lead_ctx is not None or bool(stage.lead_ctx_window),
        tokens=stage.lead_tokens, cost=stage.lead_cost,
        cost_note="local" if local else "", elapsed=_og_stage_elapsed(stage, now),
        tools=stage.lead_tools, pulses=pulse, now=text, now_age=max(0.0, now - stamp) if stamp else None, live=live,
    )


def _og_cell_values(plan: _og_Plan, cells: _og_Cells, now: float) -> dict[str, str]:
    """Each shown metric column of one row as a styled, padded cell."""
    harn_color = _og_harn_color(cells.harn)
    out = {
        "tag": _ot_tag(cells.tag, plan.short),   # already coloured and padded to plan.tag_w
        "harn": _wd_c(_og_rpad(cells.harn or "—", 5), harn_color if cells.harn else _wd_C_FAINT),
        "model": _wd_c(_og_rpad(_wd_trunc(cells.model or "—", plan.model_w), plan.model_w),
                       _wd_C_MUTED if cells.model else _wd_C_FAINT),
        "ctx": _og_ctx_cell(cells.ctx_tokens, cells.ctx_pct),
    }
    out["tokens"] = _og_money_or_count(_wd_fmt_tok(cells.tokens) if cells.tokens is not None else "—", 6,
                                       cells.tokens is not None, _wd_C_MUTED)
    cost = ("<$0.01" if 0 < cells.cost < 0.01 else _wd_fmt_money(cells.cost)) if cells.cost is not None else "—"
    out["cost"] = _og_metric(cost, 6, _wd_C_TEXT if cells.cost is not None else _wd_C_FAINT, right=True)
    out["elapsed"] = _og_metric(cells.elapsed or "—", 7, _wd_C_MUTED if cells.elapsed else _wd_C_FAINT, right=True)
    out["tools"] = _og_money_or_count(str(cells.tools) if cells.tools is not None else "—", 5,
                                      cells.tools is not None, _wd_C_MUTED)
    out["spark"] = _os_tools_spark(cells.pulses, now, 15)
    return out


def _og_money_or_count(text: str, width: int, known: bool, color: tuple[int, int, int]) -> str:
    return _og_metric(text, width, color if known else _wd_C_FAINT, right=True)


def _og_now_text(cells: _og_Cells, now: float) -> tuple[str, float | None]:
    """The sub-line's words and age. A live row always has one: the last action, else the last tool call, else an honest
    'nothing recorded'."""
    if cells.now:
        return cells.now, cells.now_age
    newest = max((ts for ts, n in cells.pulses if n > 0 and ts > 0), default=0.0)
    if newest:
        return "last tool call", max(0.0, now - newest)
    return ("no activity recorded", None) if cells.tag in ("LEAD", "SPAWN", "CRON", "") else ("", None)


def _og_row(plan: _og_Plan, prefix: str, cont: str, mark: str, name: str, cells: _og_Cells, now: float,
            why: str = "", selected: bool = False, tail: str = "") -> list[str]:
    """A session or worker row, then its `› now · 4s` sub-line (live rows always) and its `why` line."""
    if tail:   # a tail (the swarm hive, `▸ N spawns`) closes the name column; it never pushes a metric out of its cell
        avail = plan.name_w - _wd_width(prefix) - 2
        # the tail may take what the name does not need, but never fewer than 2 cells (`▲1`, a hidden failure) while a name fits
        tail = _wd_clip(tail, max(avail - 1 - _OG_TAIL_NAME, 2)) if avail >= 2 + 1 + 1 else ""
    room = plan.name_w - (_wd_width(tail) + 1 if tail else 0)
    name_w = max(0, room - _wd_width(prefix) - 2)
    left = _wd_c(prefix, _wd_C_BORDER) + mark + " " + _wd_c(
        _wd_trunc(name, name_w), _wd_C_TEXT if cells.live else _wd_C_SUB, bold=cells.live)
    if tail:
        left = _wd_pad(left, room) + " " + tail
    values = _og_cell_values(plan, cells, now)
    parts = [values[key] for key, _ in plan.columns()]
    line = _wd_pad(left, plan.name_w) + "  " + " ".join(parts)
    out = [_og_fit_line(line, plan.cols)]
    if cells.live and cells.sub:
        text, age_s = _og_now_text(cells, now)
        if text:
            age = _wd_fmt_dur(age_s) if age_s is not None else ""
            suffix = _wd_c("  · " + age, _wd_C_DIM) if age else ""
            lead = _wd_c(cont, _wd_C_BORDER) + "  "
            body_w = max(0, plan.cols - _wd_width(lead) - _wd_width(suffix) - 1)   # the cells under it are empty: use the line
            words = _wd_c(_wd_trunc("› " + text, body_w), _wd_C_SUB if cells.now else _wd_C_DIM)
            out.append(_og_fit_line(lead + words + suffix, plan.cols))
    if why:
        out.append(_og_fit_line(_os_why_line(cont, why, plan.cols), plan.cols))
    if selected:
        out = [_wd_bg_line(row, _wd_C_SELBG, plan.cols) for row in out]
    return out


def _og_badge(work: WorkState | None) -> str:
    """The coloured work-state badge (`● uncommitted`, `● 2 unpushed`, `● unmerged`, `✔ merged`), "" when clean/unknown."""
    if work is None:
        return ""
    if work.dirty:
        return _wd_c("● uncommitted", _wd_C_AMBER)
    if work.ahead:
        return _wd_c(f"● {work.ahead} unpushed", _wd_C_AMBER)
    if work.unmerged:
        return _wd_c("● unmerged", _wd_C_BLUE)
    if work.unmerged is False and work.branch and work.dirty is False:
        return _wd_c("✔ merged", _wd_C_GREEN)
    return ""


def _og_project_head(plan: _og_Plan, name: str, opened: bool, live: bool, counts: str, badge: str = "",
                     right: str = "", selected: bool = False, totals: _og_Cells | None = None) -> str:
    """`▾ name ───── 2 live · 1 idle  ● 2 unpushed ──   1.2M   $0.82   61`. The counts end before the metric columns; the
    project's totals sit inside TOKENS, COST and TOOLS so they line up under their headers."""
    arrow = _wd_c("▾" if opened else "▸", _wd_C_BLUE if live else _wd_C_DIM)
    counts_part = "  " + _wd_c(_wd_clean(counts), _wd_C_MUTED)
    badge_part = "  " + badge if badge else ""
    age_part = "  " + _wd_c(_wd_clean(right), _wd_C_DIM) if right else ""
    name_cap = _OG_HEAD_NAME_WIDE if plan.cols >= _OG_WIDE_COLS else _OG_HEAD_NAME
    want = min(_wd_width(name), name_cap)          # the name keeps this much before the tail sheds anything
    tails = (counts_part + badge_part + age_part, counts_part + badge_part, counts_part)   # age goes first
    attempts = [(zone, tail) for zone in (plan.name_w + 2, plan.cols) for tail in tails] + [(plan.cols, "")]
    for zone, tail in attempts:                    # a phone borrows the metric zone before it drops the counts
        tail += " "
        name_w = max(1, zone - 4 - _wd_width(tail) - 2)      # `▾ ` and 2 spaces round the title, 2 cells of rule
        if name_w >= want:
            break
    title = _wd_c(_wd_trunc(name, name_w), _wd_C_TEXT if live else _wd_C_SUB, bold=live)
    head = arrow + " " + title + "  "
    rule_w = max(0, zone - _wd_width(head) - _wd_width(tail))
    left = head + _wd_c("─" * rule_w, _wd_C_BORDER) + tail
    if zone >= plan.cols:                          # the whole line went to the name: no room for totals
        return _og_selected(_og_fit_line(left, plan.cols), plan.cols, selected)
    cells = []
    for key, width in plan.columns():
        blank = " " * width
        if totals is not None and key == "tokens" and totals.tokens is not None:
            blank = _og_metric(_wd_fmt_tok(totals.tokens), width, _wd_C_MUTED, right=True)
        elif totals is not None and key == "cost" and totals.cost is not None:
            blank = _og_metric(_wd_fmt_money(totals.cost), width, _wd_C_TEXT if live else _wd_C_MUTED, right=True)
        elif totals is not None and key == "tools" and totals.tools is not None:
            blank = _og_metric(str(totals.tools), width, _wd_C_MUTED, right=True)
        cells.append(blank)
    line = _wd_pad(_wd_clip(left, zone), zone) + " ".join(cells)
    return _og_selected(_og_fit_line(line, plan.cols), plan.cols, selected)


def _og_idle_divider(plan: _og_Plan, projects: int, selected: bool = False) -> str:
    """`╌╌ idle · N projects · quiet under 2h ╌╌╌ h hide`."""
    head = _wd_c(f"╌╌ idle · {projects} projects · quiet under 2h ", _wd_C_DIM)
    hint = "h hide"
    tail = " " + _wd_c(hint, _wd_C_FAINT)
    middle = _wd_c("╌" * max(2, plan.cols - _wd_width(head) - _wd_width(tail)), _wd_C_FAINT)
    return _og_selected(_og_fit_line(head + middle + tail, plan.cols), plan.cols, selected)


def _og_closed_line(plan: _og_Plan, closed: int, need_sweep: int, selected: bool = False) -> str:
    """`31 closed · 4 need sweep · a shows closed · session-sweep cleans up`."""
    text = f"{closed} closed · {need_sweep} need sweep  ·  a shows closed  ·  session-sweep cleans up"
    return _og_selected(_og_fit_line(_wd_c(text, _wd_C_MUTED), plan.cols), plan.cols, selected)


def _og_finished_line(plan: _og_Plan, prefix: str, n: int, last_age: float | None, opened: bool,
                      selected: bool = False) -> str:
    """`└─ ▸ finished 6   last 25m ago` under its project."""
    age = _og_age(last_age) + " ago" if last_age is not None else "— ago"
    text = _wd_c(prefix, _wd_C_BORDER) + _wd_c("▾" if opened else "▸", _wd_C_MUTED) + _wd_c(
        f" finished {n}   last {age}", _wd_C_MUTED)
    return _og_selected(_og_fit_line(text, plan.cols), plan.cols, selected)


def _og_lpad(text: str, width: int) -> str:
    text = _wd_trunc(text, width)
    return " " * max(0, width - _wd_width(text)) + text


def _og_rpad(text: str, width: int) -> str:
    return _wd_pad(_wd_trunc(text, width), width)


def _og_metric(text: str, width: int, color: tuple[int, int, int], right: bool = False) -> str:
    text = _wd_trunc(text, width)
    return _wd_c((_og_lpad if right else _og_rpad)(text, width), color)


_OG_EFFORT = {"low": "lo", "medium": "med", "high": "hi", "xhigh": "xhi", "minimal": "min"}


def _og_effort(effort: str) -> str:
    return _OG_EFFORT.get(effort.lower(), effort)


def _og_age(sec: float) -> str:
    """A coarse age: `40s`, `25m`, `1h 05m`, `3d`."""
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h {sec % 3600 // 60:02d}m"
    return f"{sec // 86400}d"


def _og_pct(used: int | None, window: int | None) -> int | None:
    return round(used * 100 / window) if used is not None and window else None


def _og_tokens(tokens_in: int | None, tokens_out: int | None) -> int | None:
    return None if tokens_in is None and tokens_out is None else (tokens_in or 0) + (tokens_out or 0)


def _og_local(*values: str) -> bool:
    text = " ".join(str(value or "").lower() for value in values)
    return any(hint in text for hint in ("local", "llamacpp", "ollama", "ornith"))


def _og_worker_harness(wk: Worker) -> str:
    mapping = {"claude": "CC", "codex": "Codex", "opencode": "OCode", "cursor": "Cursr", "openclaw": "OClaw"}
    return mapping.get((wk.log.harness or "").lower(), mapping.get(wk.family.lower(), "—"))


def _og_stage_harness(stage: Stage) -> str:
    if stage.tag:
        return stage.tag
    kind = (stage.lead_kind or "").lower()
    for key, label in (("claude", "CC"), ("codex", "Codex"), ("opencode", "OCode"), ("cursor", "Cursr"), ("openclaw", "OClaw")):
        if key in kind:
            return label
    return "OClaw" if stage.vitals else "—"


def _og_harn_color(harn: str) -> tuple[int, int, int]:
    if harn.startswith("CC"):
        return _wd_family_color("claude")
    if harn == "Codex":
        return _wd_family_color("codex")
    if harn == "OCode":
        return _wd_C_BLUE
    if harn == "Cursr":
        return _wd_family_color("cursor")
    if harn == "OClaw":
        return _wd_C_VIOLET
    return _wd_C_MUTED


def _og_ctx_cell(tokens: int | None, pct: int | None) -> str:
    """The context size (`84k`), shaded by how full the window is; plain when the window is unknown."""
    if tokens is None:
        return _og_metric("—", 4, _wd_C_FAINT, right=True)
    return _og_metric(_wd_fmt_tok(tokens), 4, _wd_ctx_color(pct), right=True)


def _og_ctx_text(cells: object) -> str:
    """`82k · 41%` for the detail views; `82k` without a window, empty when unknown."""
    tokens, pct = getattr(cells, "ctx_tokens", None), getattr(cells, "ctx_pct", None)
    if tokens is None:
        return "" if pct is None else f"{pct}%"
    return _wd_fmt_tok(tokens) + ("" if pct is None else f" · {pct}%")


def _og_worker_elapsed(wk: Worker, now: float) -> str:
    if not wk.start:
        return "—"
    end = now if wk.state in ("running", "quiet", "stalled") else (wk.end or now)
    return _wd_fmt_dur(max(0, end - wk.start))


def _og_stage_elapsed(stage: Stage, now: float) -> str:
    if not stage.lead_since:
        return "—"
    end = now if stage.state == "active" or stage.lead_state in ("live", "background") else (stage.activity or now)
    return _wd_fmt_dur(max(0, end - stage.lead_since))


def _og_fit_line(line: str, cols: int) -> str:
    return _wd_pad(_wd_clip(line, cols), cols)


def _og_selected(line: str, cols: int, selected: bool) -> str:
    return _wd_bg_line(line, _wd_C_SELBG, cols) if selected else line


OG_SWARM_MIN = 3   # workers in a session's current task that make it a swarm (the user 2026-10-24)

_OG_SW_LIVE = ("running", "quiet", "stalled")
_OG_SW_PULSE_S = 0.9      # a tool call's heat fades over this long
_OG_SW_HOT_S = 0.6        # hive glyph turns CYAN bold this long after a tool call
_OG_SW_RATE_S = 60.0      # tools/min window
_OG_SW_BAR_CELLS = 8
_OG_SW_SPARK_MIN = 90     # no combined sparkline below this width
_OG_SW_SPARK_WIDE = 120   # 30 cells from here, else 14
_OG_SW_SPARK_30 = 30
_OG_SW_SPARK_14 = 14


def _og_sw_static() -> bool:
    """NO_COLOR pins the spinner to one frame so --plain and the tests see the same text."""
    return os.environ.get("NO_COLOR") is not None


def _og_sw_state(wk: Worker) -> str:
    return (wk.state or "").lower()


def _og_sw_live(wk: Worker) -> bool:
    return _og_sw_state(wk) in _OG_SW_LIVE


def _og_sw_newest(wk: Worker) -> float:
    """Epoch of the newest tool call the log recorded, 0.0 when it has none."""
    return max((ts for ts, n in wk.log.tool_pulses if n > 0 and ts > 0), default=0.0)


def _og_sw_age(wk: Worker, now: float) -> float | None:
    """Seconds since that worker's newest tool call, clamped at 0 when the clock runs ahead."""
    newest = _og_sw_newest(wk)
    return max(0.0, now - newest) if newest else None


def _og_pulse(wk: Worker, now: float) -> float:
    """0..1 heat of a worker's newest tool call: 1 right after it, fading to 0 over 0.9 s (the name brightens to CYAN)."""
    if not _og_sw_live(wk):
        return 0.0
    age = _og_sw_age(wk, now)
    if age is None:
        return 0.0
    return max(0.0, 1.0 - age / _OG_SW_PULSE_S)


def _og_sw_glyph(wk: Worker, index: int, frame_t: float, now: float) -> str:
    """One hive cell: spinner, ✔, ✖ or ○."""
    state = _og_sw_state(wk)
    if state not in _OG_SW_LIVE:
        if state == "done":
            return _wd_c("✔", _wd_C_GREEN)
        if state == "failed":
            return _wd_c("✖", _wd_C_RED)
        return _wd_c("○", _wd_C_DIM)
    age = _og_sw_age(wk, now)
    hot = age is not None and age < _OG_SW_HOT_S
    glyph = _wd_spinner(frame_t + index * 0.1)   # one frame apart, so the strip ripples
    return _wd_c(glyph, _wd_C_CYAN, bold=True) if hot else _wd_c(glyph, _wd_family_color(wk.family))


def _og_sw_glyphs(workers: list[Worker], t: float, now: float) -> list[str]:
    frame_t = 0.0 if _og_sw_static() else t
    return [_og_sw_glyph(wk, i, frame_t, now) for i, wk in enumerate(workers)]


def _og_hive(workers: list[Worker], t: float, now: float) -> str:
    """One glyph per worker in launch order: spinner in family colour, out of phase so the strip ripples; CYAN bold while
    that worker made a tool call in the last 0.6 s; ✔ GREEN / ✖ RED when finished."""
    return "".join(_og_sw_glyphs(workers, t, now))


def _og_sw_hive_fit(glyphs: list[str], avail: int) -> str:
    """The whole hive when it fits, else the first k glyphs and a FAINT `+n`; never cut mid-glyph."""
    total = len(glyphs)
    if total == 0 or sum(_wd_width(g) for g in glyphs) <= avail:
        return "".join(glyphs)
    for k in range(total - 1, 0, -1):
        suffix = f"+{total - k}"
        if sum(_wd_width(g) for g in glyphs[:k]) + _wd_width(suffix) <= avail:
            return "".join(glyphs[:k]) + _wd_c(suffix, _wd_C_FAINT)
    suffix = f"+{total}"
    return _wd_c(suffix, _wd_C_FAINT) if _wd_width(suffix) <= avail else ""


def _og_sw_bar(done: int, total: int) -> str:
    """`▰▰▱▱ 3/8`: any finished counts as done."""
    full = min(_OG_SW_BAR_CELLS, max(0, round(done * _OG_SW_BAR_CELLS / total))) if total else 0
    return ("  " + _wd_c("▰" * full, _wd_C_GREEN) + _wd_c("▱" * (_OG_SW_BAR_CELLS - full), _wd_C_FAINT)
            + _wd_c(f" {done}/{total}", _wd_C_SUB))


def _og_sw_rate(workers: list[Worker], now: float) -> tuple[int, bool]:
    """Combined tool calls in the last minute, and whether any worker carries tool data at all."""
    pulses = [pulse for wk in workers for pulse in wk.log.tool_pulses]
    if not pulses:
        return 0, False
    return sum(n for ts, n in pulses if now - _OG_SW_RATE_S < ts <= now), True


def _og_sw_chunks(workers: list[Worker], now: float, cols: int) -> list[str]:
    """The parts after the hive, left to right; the caller drops them from the right when the band is narrow."""
    chunks: list[str] = []
    total = len(workers)
    if total:
        done = sum(1 for wk in workers if not _og_sw_live(wk))
        chunks.append(_og_sw_bar(done, total))
        rate, has_data = _og_sw_rate(workers, now)
        chunks.append(_wd_c("  ·  ", _wd_C_FAINT)
                      + (_wd_c(str(rate), _wd_C_CYAN, bold=True) if has_data else _wd_c("—", _wd_C_FAINT))
                      + _wd_c(" tools/min", _wd_C_MUTED))
        if cols >= _OG_SW_SPARK_MIN:
            cells = _OG_SW_SPARK_30 if cols >= _OG_SW_SPARK_WIDE else _OG_SW_SPARK_14
            pulses = [pulse for wk in workers for pulse in wk.log.tool_pulses]
            chunks.append(" " + _os_spark(_os_bins(pulses, now, cells), cells))
    failed = sum(1 for wk in workers if _og_sw_state(wk) == "failed")
    if failed:
        chunks.append(_wd_c("  ·  ", _wd_C_FAINT) + _wd_c(f"{failed} failed", _wd_C_RED))
    return chunks


def _og_swarm_band(plan: _og_Plan, cont: str, workers: list[Worker], now: float, t: float) -> str:
    """`<cont>╭─ SWARM 8 <hive>  ▰▰▱▱ 3/8  ·  67 tools/min <combined spark>  ·  1 failed`, exactly plan.cols wide."""
    total = len(workers)
    head = (_wd_c(cont, _wd_C_BORDER) + _wd_c("╭─ ", _wd_C_VIOLET)
            + _wd_c("SWARM", _wd_C_VIOLET, bold=True) + _wd_c(f" {total} ", _wd_C_TEXT))
    hive = _og_sw_hive_fit(_og_sw_glyphs(workers, t, now), max(0, plan.cols - _wd_width(head)))
    chunks = _og_sw_chunks(workers, now, plan.cols)
    while chunks and _wd_width(head) + _wd_width(hive) + sum(_wd_width(c) for c in chunks) > plan.cols:
        chunks.pop()      # drop parts right to left: failed, spark, rate, bar
    return _og_fit_line(head + hive + "".join(chunks), plan.cols)

