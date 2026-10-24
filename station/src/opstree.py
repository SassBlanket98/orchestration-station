"""Ops console: box-drawing tree prefixes and level tags (R2). Pure text helpers; imports model and widgets only."""
from __future__ import annotations

from widgets import _wd_c, _wd_C_BLUE, _wd_C_DIM, _wd_C_SUB, _wd_C_TEAL, _wd_C_VIOLET

OT_TAGS = ("LEAD", "SPAWN", "SUB", "WORKER", "CRON")

_OT_TAG_COLOUR = {"LEAD": _wd_C_SUB, "SPAWN": _wd_C_VIOLET, "SUB": _wd_C_TEAL, "WORKER": _wd_C_DIM, "CRON": _wd_C_BLUE}
_OT_TAG_SHORT = {"LEAD": "LEAD", "SPAWN": "SPWN", "SUB": "SUB", "WORKER": "WRKR", "CRON": "CRON"}


def _ot_prefixes(depths: list[int]) -> list[tuple[str, str]]:
    """For nodes listed in tree order with their depth (0 = a child of the project heading), each node's
    (prefix, continuation): the prefix draws `├─ ` / `└─ ` after `│  ` or `   ` for every open ancestor level; the
    continuation is the same width for the node's sub-lines (`│  ` where a later sibling follows, else spaces)."""
    levels: list[int] = []
    prev = -1
    for d in depths:
        prev = max(0, min(d, prev + 1))     # a jump deeper than one level counts as one level
        levels.append(prev)
    n = len(levels)
    last = [True] * n
    for i, d in enumerate(levels):
        for j in range(i + 1, n):
            if levels[j] <= d:
                last[i] = levels[j] < d
                break
    out: list[tuple[str, str]] = []
    open_: list[int] = []                   # open ancestor index at each level
    for i, d in enumerate(levels):
        del open_[d:]
        ancestors = "".join("│  " if not last[a] else "   " for a in open_)
        kids = i + 1 < n and levels[i + 1] > d
        tail = "│  " if (not last[i] or kids) else "   "
        out.append((ancestors + ("└─ " if last[i] else "├─ "), ancestors + tail))
        open_.append(i)
    return out


def _ot_tag(tag: str, short: bool) -> str:
    """The level tag cell, coloured by role, fixed width (6, or 4 with short names when `short`); blank when no tag."""
    width = 4 if short else 6
    if tag not in _OT_TAG_COLOUR:
        return " " * width
    text = _OT_TAG_SHORT[tag] if short else tag
    return _wd_c(text.ljust(width), _OT_TAG_COLOUR[tag])
