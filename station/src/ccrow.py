"""Control-center worker command and compact activity lines."""
from __future__ import annotations

from model import Worker
from widgets import (
    _wd_c,
    _wd_C_BORDER,
    _wd_C_MUTED,
    _wd_C_SUB,
    _wd_clean,
    _wd_clip,
    _wd_pad,
    _wd_width,
    _wd_wrap,
)


def command_lines(worker: Worker, width: int, expanded: bool, cont: str = "", text_w: int | None = None,
                  max_rows: int = 1) -> list[str]:
    """The worker's current command under its row, at exactly `width` cells per line.

    `cont` is the tree guide of the worker's own row, so the line sits under that worker; the text starts under the
    worker's name and wraps inside `text_w` cells (the name column, which ends before LEVEL) instead of running to
    the screen edge. Collapsed it keeps `max_rows` lines and ends with `…`; expanded it keeps every line."""
    width = max(1, width)
    text_w = width if text_w is None else max(1, min(width, text_w))
    step = worker.log.steps[-1] if worker.log.steps else None
    command = (step.tool + " " + step.text if step and step.tool else step.text if step else "")
    command = _wd_clean(command or worker.goal or worker.title).strip()
    if not command:
        return []
    pad = "  " if cont or width >= 3 else ""
    body_width = max(1, text_w - _wd_width(cont) - _wd_width(pad) - 2)
    wrapped = _wd_wrap(command, body_width)
    if not expanded and len(wrapped) > max_rows:
        wrapped = wrapped[:max_rows]
        wrapped[-1] = _wd_clip(wrapped[-1].rstrip() + " …", body_width)
    color = _wd_C_SUB if expanded else _wd_C_MUTED
    return [_wd_pad(_wd_c(cont, _wd_C_BORDER) + pad + _wd_c(("› " if i == 0 else "  ") + part, color), width)
            for i, part in enumerate(wrapped)]
