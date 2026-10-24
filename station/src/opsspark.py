"""Ops console: tools/min sparklines, step lines and failure reasons (R4/R5). Pure text helpers; imports model and
widgets only, never render or app."""
from __future__ import annotations

import math
import time

from model import FAILED, Step, Worker
from widgets import (
    _wd_c,
    _wd_C_AMBER,
    _wd_C_BLUE,
    _wd_C_BORDER,
    _wd_C_CYAN,
    _wd_C_FAINT,
    _wd_C_GREEN,
    _wd_C_MUTED,
    _wd_C_RED,
    _wd_C_SUB,
    _wd_C_TEAL,
    _wd_fit,
    _wd_fmt_dur,
    _wd_pad,
    _wd_spinner,
    _wd_trunc,
    _wd_width,
)

OS_SPAN_S = 1800      # the sparkline covers the last 30 minutes
OS_CELLS = 15         # 2 minutes per cell on a session row

_OS_SPARK_CHARS = "▁▂▃▄▅▆▇█"
_OS_STEP_HEAD_W = 31  # cells before the step text: indent, time, tool, outcome, duration
_OS_WHY_LABEL = "  why "


def _os_bins(pulses: list[tuple[float, int]], now: float, cells: int = OS_CELLS, span: float = OS_SPAN_S) -> list[int]:
    """Tool calls per cell over the last `span` seconds, oldest cell first."""
    if cells <= 0:
        return []
    out = [0] * cells
    start = now - span
    for ts, n in pulses:
        if not start < ts <= now:
            continue
        idx = min(cells - 1, int((ts - start) * cells / span))
        out[idx] += n
    return out


def _os_spark(values: list[int], cells: int) -> str:
    """`▁…█` from the values (TEAL→BLUE→CYAN by height), zero as a FAINT `·`; exactly `cells` wide."""
    if cells <= 0:
        return ""
    vals = list(values)[-cells:]
    vals = [0] * (cells - len(vals)) + vals
    top = max(vals, default=0)
    if top <= 0:
        top = 1
    out: list[str] = []
    for v in vals:
        if v <= 0:
            out.append(_wd_c("·", _wd_C_FAINT))
            continue
        i = min(7, max(0, round(v / top * 7)))
        col = _wd_C_CYAN if i >= 6 else _wd_C_BLUE if i >= 3 else _wd_C_TEAL
        out.append(_wd_c(_OS_SPARK_CHARS[i], col))
    return "".join(out)


def _os_tools_spark(pulses: list[tuple[float, int]], now: float, cells: int = OS_CELLS) -> str:
    """The TOOLS/MIN cell: bins then spark."""
    return _os_spark(_os_bins(pulses, now, cells), cells)


def _os_clock(ts: float) -> str:
    """Local HH:MM:SS, or dashes when the log carried no time."""
    if not ts > 0:
        return "--:--:--"
    try:
        return time.strftime("%H:%M:%S", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return "--:--:--"


def _os_dur(dur: float | None) -> str:
    """`<0.1s` under a tenth of a second, blank when unknown."""
    if dur is None or not math.isfinite(dur):
        return ""
    if dur < 0.1:
        return "<0.1s"
    return _wd_fmt_dur(dur)


def _os_outcome(step: Step, live: bool, now: float) -> str:
    """ok / ✖ / spinner for a running step / blank; always 3 cells."""
    if step.outcome == "ok":
        return _wd_c("ok ", _wd_C_GREEN)
    if step.outcome == "failed":
        return _wd_c("✖  ", _wd_C_RED)
    if live and not step.outcome:
        return _wd_c(_wd_spinner(now) + "  ", _wd_C_BLUE)
    return "   "


def _os_step_line(step: Step, w: int, now: float, live: bool = False) -> str:
    """One step row: `TIME  TOOL ok DUR  STEP` (ok / ✖ / spinner while running); exactly fits `w`."""
    tool = step.tool if step.kind == "tool" and step.tool else step.kind
    dur_col = _wd_C_AMBER if step.dur is not None and step.dur >= 60 else _wd_C_MUTED
    head = ("  " + _wd_c(_os_clock(step.ts), _wd_C_FAINT) + "  "
            + _wd_c(_wd_pad(_wd_trunc(tool, 6), 6), _wd_C_BLUE) + " "
            + _os_outcome(step, live, now)
            + _wd_c(_os_dur(step.dur).rjust(7), dur_col) + "  ")
    text = _wd_c(_wd_trunc(step.text, w - _OS_STEP_HEAD_W), _wd_C_SUB)
    return _wd_fit(head + text, w)


def _os_why(wk: Worker) -> str:
    """The failure reason for a `why` sub-line, "" when there is none."""
    if wk.failure:
        return wk.failure
    if (wk.state or "").lower() != FAILED:
        return ""
    for step in reversed(wk.log.steps):
        if step.kind == "error":
            return step.text
    return ""


def _os_why_line(cont: str, text: str, w: int) -> str:
    """`<cont>  why <reason>`: RED label, SUB reason, cut to `w`."""
    head = _wd_c(cont, _wd_C_BORDER) + _wd_c(_OS_WHY_LABEL, _wd_C_RED)
    reason = _wd_c(_wd_trunc(text, w - _wd_width(cont) - _wd_width(_OS_WHY_LABEL)), _wd_C_SUB)
    return _wd_fit(head + reason, w)
