"""Control center: the bottom strip (Usage, Events, Logs, Metrics). Pure renderers; imports model, events, widgets and ops* only."""
from __future__ import annotations

from events import fleet_events, log_tail, row_events, row_key  # noqa: F401 - row_events stays on the module for the row-scoped tail
from model import CCEvent, GlobalSnapshot, QuotaAccount, QuotaWindow, Stage, Worker
from opsspark import OS_SPAN_S, _os_bins, _os_clock, _os_spark
from widgets import (
    _wd_bar,
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
    _wd_center,
    _wd_clean,
    _wd_fit,
    _wd_pad,
    _wd_Rgb,
    _wd_trunc,
    _wd_width,
)

STRIP_TABS = ("Usage", "Events", "Logs", "Metrics")

_CX_MIN_BINS = 30        # the chart reads 1-minute bins over the last 30 minutes
_CX_LABEL_W = 14         # event row label column
_CX_KIND_W = 6           # event kind column (tool, msg, state, err)
_CX_EVENT_HEAD_W = 8 + 2 + _CX_LABEL_W + 2 + _CX_KIND_W + 2   # "HH:MM:SS  label  kind  "
_CX_BAR_W = 8            # quota window bar
_CX_QUOTA_NAME_W = 12


def _cst_pct_color(pct: float) -> _wd_Rgb:
    """Quota percentage colour: green, amber from 75 %, red from 90 %."""
    if pct >= 90.0:
        return _wd_C_RED
    if pct >= 75.0:
        return _wd_C_AMBER
    return _wd_C_GREEN


def _cst_stage_labels(gs: GlobalSnapshot) -> dict[str, str]:
    """Stage path -> the label an event with an empty worker part is shown under."""
    return {st.path: st.label or st.path.rsplit("/", 1)[-1] for st in (gs.stages or [])}


def _cst_event_label(key: str, labels: dict[str, str]) -> str:
    """The row label an event key carries: the worker name, else the stage's label."""
    path, _sep, wname = (key or "").rpartition("|")
    if wname:
        return wname
    return labels.get(path) or path.rsplit("/", 1)[-1]


def _cst_note(text: str, width: int, count: int) -> list[str]:
    """One dim sentence centred on the middle row of `count` rows; the other rows blank. Exactly `width` cells each."""
    rows = [_wd_fit("", width)] * max(0, count)
    if count > 0:
        rows[count // 2] = _wd_fit(_wd_center(_wd_c(_wd_trunc(text, width), _wd_C_DIM), width), width)
    return rows


def _cst_event_line(ev: CCEvent, sel_key: str, labels: dict[str, str], width: int) -> str:
    """`HH:MM:SS  <label>  <kind>  <text>`: the selected row bright, an err event red, the rest dim; exactly `width` cells."""
    err = ev.kind == "err"
    selected = bool(sel_key) and ev.key == sel_key
    name = _wd_pad(_wd_trunc(_cst_event_label(ev.key, labels), _CX_LABEL_W), _CX_LABEL_W)
    label = (_wd_c(name, _wd_C_TEXT, bold=True) if selected      # the selected row reads brighter
             else _wd_c(name, _wd_C_SUB))
    kind = _wd_c(_wd_pad(_wd_trunc(ev.kind, _CX_KIND_W), _CX_KIND_W), _wd_C_RED if err else _wd_C_DIM)
    tone = _wd_C_RED if err else (_wd_C_TEXT if selected else _wd_C_DIM)
    text = _wd_c(_wd_trunc(_wd_clean(ev.text), max(0, width - _CX_EVENT_HEAD_W)), tone)
    head = _wd_c(_wd_pad(_os_clock(ev.ts), 8), _wd_C_FAINT) + "  " + label + "  " + kind + "  "
    return _wd_fit(head + text, width)


def _cst_events(gs: GlobalSnapshot, stage: Stage | None, worker: Worker | None, width: int, count: int,
               scroll: int) -> list[str]:
    """The fleet tail, oldest at the top and newest at the bottom, `scroll` events back from the newest.
    Exactly `count` rows: blank rows sit above the tail; no events is one centred dim note."""
    events = list(fleet_events(gs) or [])
    if not events:
        return _cst_note("no events yet", width, count)
    labels = _cst_stage_labels(gs)
    sel = row_key(stage, worker) if stage is not None else ""
    end = max(0, len(events) - max(0, int(scroll or 0)))
    chunk = events[max(0, end - count):end] if count > 0 else []
    lines = [_cst_event_line(ev, sel, labels, width) for ev in chunk]
    return [_wd_fit("", width)] * max(0, count - len(lines)) + lines


def _cst_log_path(stage: Stage | None, worker: Worker | None) -> str:
    """The file a row's log tail comes from: the worker's log, else the lead's transcript."""
    if worker is not None:
        return worker.log.path
    if stage is not None:
        return stage.transcript_path
    return ""


def _cst_logs(stage: Stage | None, worker: Worker | None, width: int, count: int, scroll: int) -> list[str]:
    """The row's log tail, newest last at the bottom; `scroll` moves back. No row selected, or no file, is one
    centred dim sentence saying what is missing."""
    if count <= 0:
        return []
    if stage is None and worker is None:
        return _cst_note("select a worker to see its log", width, count)
    path = _cst_log_path(stage, worker)
    tail: list[str] = []
    if path:
        try:
            tail = list(log_tail(path) or [])
        except OSError:      # an unreadable file is data we do not have, not a failure
            tail = []
    if not tail:
        return _cst_note("no log file for this session", width, count)
    start = max(0, len(tail) - count - max(0, int(scroll or 0)))
    return [_wd_fit(_wd_clean(line), width) for line in tail[start:start + count]]


def _cst_fleet_pulses(gs: GlobalSnapshot) -> list[tuple[float, int]]:
    """Every worker's and stage lead's tool pulses, for the fleet chart."""
    pulses: list[tuple[float, int]] = []
    for st in gs.stages or []:
        pulses.extend(st.lead_tool_pulses or [])
        for wk in (st.snap.workers if st.snap is not None else []) or []:
            pulses.extend(wk.log.tool_pulses or [])
    return pulses


def _cst_tools_chart(pulses: list[tuple[float, int]], width: int, now: float) -> str:
    """`N tools/min <spark across the width>`: the fleet tools/min over the last 30 minutes, N the axis max.
    A flat zero series is a dim baseline with `idle` at the right."""
    bins = _os_bins(pulses, now, _CX_MIN_BINS, OS_SPAN_S)
    peak = max(bins, default=0)
    idle = peak <= 0
    label = _wd_c(f"{peak} tools/min ", _wd_C_MUTED)
    tail = _wd_c("idle", _wd_C_DIM) if idle else ""
    tw = _wd_width(tail)
    cells = width - _wd_width(label) - (tw + 2 if tail else 0)
    if cells < 4:            # too narrow for the idle word: spend the width on the chart
        tail, tw = "", 0
        cells = width - _wd_width(label)
    cells = max(0, cells)
    if idle:
        chart = _wd_c("▁" * cells, _wd_C_DIM)
    else:
        chart = _os_spark(_os_bins(pulses, now, cells, OS_SPAN_S), cells) if cells else ""
    out = label + chart
    if tail:
        out += " " * max(0, width - _wd_width(out) - tw) + tail
    return _wd_fit(out, width)


def _cst_window_cell(win: QuotaWindow) -> str:
    """`5h ███▁▁▁▁▁ 42%`: one window's used with a small bar."""
    pct = float(win.used or 0.0)
    bar = _wd_bar(pct / 100.0, _CX_BAR_W, lambda x: _cst_pct_color(x * 100.0))
    return (_wd_c(_wd_trunc(win.name or "", 6), _wd_C_MUTED) + " " + bar + " "
            + _wd_c(f"{min(max(pct, 0.0), 999):.0f}%".rjust(4), _cst_pct_color(pct)))


def _cst_quota_line(acct: QuotaAccount, width: int) -> str:
    """One account: its name, then every window's used with a small bar."""
    name = _wd_c(_wd_pad(_wd_trunc(_wd_clean(acct.name), _CX_QUOTA_NAME_W), _CX_QUOTA_NAME_W), _wd_C_TEXT, bold=True)
    wins = acct.windows or []
    if not wins:
        return _wd_fit("  " + name + _wd_c("  no windows", _wd_C_DIM), width)
    return _wd_fit("  " + "   ".join([name] + [_cst_window_cell(w) for w in wins]), width)


def _cst_usage(gs: GlobalSnapshot, width: int) -> list[str]:
    """One compact quota line per account: name, each window's bar and percent."""
    if not gs.quota_ok:
        return [_wd_fit(_wd_c("quota unavailable", _wd_C_DIM), width)]
    accounts = gs.quota or []
    if not accounts:
        return [_wd_fit(_wd_c("no accounts", _wd_C_DIM), width)]
    return [_cst_quota_line(acct, width) for acct in accounts]


def _cst_metrics(gs: GlobalSnapshot, width: int, now: float) -> list[str]:
    """The fleet tools/min chart over the last 30 minutes."""
    return [_cst_tools_chart(_cst_fleet_pulses(gs), width, now)]


def strip_tab_bar(tab: str, width: int) -> tuple[str, list[tuple[int, int, str]]]:
    """The strip's tab bar line, exactly `width` cells: each tab padded by one space, the active one bold accent with a
    `▸` marker, the rest dim. Returns the line and the click spans [(col0, col1, tab name)], marker included."""
    styled: list[str] = []
    spans: list[tuple[int, int, str]] = []
    col = 0
    for name in STRIP_TABS:
        active = name == tab
        piece = ("▸" if active else "") + name
        pw = _wd_width(piece)
        if col + 1 < width:      # the piece starts after the one-space pad
            spans.append((col + 1, min(col + pw, width - 1), name))
        styled.append(" " + (_wd_c(piece, _wd_C_BLUE, bold=True) if active else _wd_c(piece, _wd_C_DIM)) + " ")
        col += pw + 2       # two pads make the two spaces between the tabs
    return _wd_fit("".join(styled), width), spans


def strip_lines(gs: GlobalSnapshot, tab: str, stage: Stage | None, worker: Worker | None, width: int, height: int,
                scroll: int = 0, now: float = 0.0) -> list[str]:
    """Exactly `height` lines of `width` cells. Usage: quota headroom per account. Events: the fleet tail, newest at the
    bottom, a row tinted when it is the selected row. Logs: the selected row's raw log tail. Metrics: fleet tools/min
    chart. `scroll` moves back in time. An empty tab is one centred dim sentence."""
    if height <= 0:
        return []
    gs = gs if gs is not None else GlobalSnapshot()
    bar = strip_tab_bar(tab, width)[0]
    body_h = height - 1
    if body_h <= 0:
        return [bar]
    t = now if now > 0 else gs.now
    if tab == "Usage":
        body = _cst_usage(gs, width)
    elif tab == "Logs":
        body = _cst_logs(stage, worker, width, body_h, scroll)
    elif tab == "Metrics":
        body = _cst_metrics(gs, width, t)
    elif tab == "Events":
        body = _cst_events(gs, stage, worker, width, body_h, scroll)
    else:
        body = _cst_events(gs, stage, worker, width, body_h, scroll)
    # Every body helper fits its lines itself; the rest of the body is blank.
    return [bar] + body[:body_h] + [_wd_fit("", width)] * max(0, body_h - len(body))
