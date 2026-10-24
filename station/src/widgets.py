"""Reusable pieces for the dashboard renderer: palette, colours, measuring, boxes, bars, formatting."""
from __future__ import annotations

import math
import os
import re
import time
import unicodedata
from collections.abc import Callable, Sequence
from functools import lru_cache
from itertools import pairwise

_wd_Rgb = tuple[int, int, int]

# --- palette (truecolor foregrounds) -------------------------------------------------------------
# Two themes. "tech" (default) is graphite + teal, after the worker-dashboard web UI; "tokyo" is the original
# Tokyo Night set. Pick with STATUS_THEME=tokyo. Every colour below is a role, not a hue: code says BLUE for "accent".
_wd_THEMES: dict[str, dict[str, _wd_Rgb]] = {
    "tokyo": {
        "TEXT": (192, 202, 245), "SUB": (169, 177, 214), "MUTED": (140, 150, 190), "DIM": (86, 95, 137),
        "FAINT": (59, 66, 97), "BORDER": (65, 72, 104), "BLUE": (122, 162, 247), "VIOLET": (187, 154, 247),
        "CYAN": (125, 207, 255), "TEAL": (42, 195, 222), "GREEN": (158, 206, 106), "AMBER": (224, 175, 104),
        "RED": (247, 118, 142), "ORANGE": (255, 158, 100), "SELBG": (40, 52, 87),
    },
    "tech": {
        "TEXT": (226, 232, 232), "SUB": (190, 200, 202), "MUTED": (164, 176, 180), "DIM": (140, 156, 162),
        "FAINT": (58, 70, 75), "BORDER": (60, 76, 82), "BLUE": (96, 192, 200), "VIOLET": (172, 148, 212),
        "CYAN": (140, 222, 230), "TEAL": (88, 176, 174), "GREEN": (112, 208, 140), "AMBER": (224, 162, 52),
        "RED": (236, 112, 108), "ORANGE": (214, 150, 112), "SELBG": (34, 54, 60),
    },
}
_wd_THEME = os.environ.get("STATUS_THEME", "tech").strip().lower()
_wd_PAL = _wd_THEMES.get(_wd_THEME, _wd_THEMES["tech"])
_wd_C_TEXT: _wd_Rgb = _wd_PAL["TEXT"]
_wd_C_SUB: _wd_Rgb = _wd_PAL["SUB"]
_wd_C_MUTED: _wd_Rgb = _wd_PAL["MUTED"]   # secondary text: labels, summaries, times, key names
_wd_C_DIM: _wd_Rgb = _wd_PAL["DIM"]
_wd_C_FAINT: _wd_Rgb = _wd_PAL["FAINT"]
_wd_C_BORDER: _wd_Rgb = _wd_PAL["BORDER"]
_wd_C_BLUE: _wd_Rgb = _wd_PAL["BLUE"]     # the accent
_wd_C_VIOLET: _wd_Rgb = _wd_PAL["VIOLET"]
_wd_C_CYAN: _wd_Rgb = _wd_PAL["CYAN"]
_wd_C_TEAL: _wd_Rgb = _wd_PAL["TEAL"]
_wd_C_GREEN: _wd_Rgb = _wd_PAL["GREEN"]
_wd_C_AMBER: _wd_Rgb = _wd_PAL["AMBER"]
_wd_C_RED: _wd_Rgb = _wd_PAL["RED"]
_wd_C_ORANGE: _wd_Rgb = _wd_PAL["ORANGE"]
_wd_C_SELBG: _wd_Rgb = _wd_PAL["SELBG"]

_wd_FAMILY_COLORS: dict[str, _wd_Rgb] = {
    "claude": _wd_C_ORANGE,
    "codex": _wd_C_CYAN if _wd_THEME == "tokyo" else (125, 150, 184),
    "grok": _wd_C_VIOLET,
    "deepseek": _wd_C_BLUE,
    "cursor": _wd_C_TEAL,
}

def _wd_lerp_rgb(a: _wd_Rgb, b: _wd_Rgb, x: float) -> _wd_Rgb:
    return (round(a[0] + (b[0] - a[0]) * x), round(a[1] + (b[1] - a[1]) * x), round(a[2] + (b[2] - a[2]) * x))


_wd_SPARK_STOPS: list[tuple[float, _wd_Rgb]] = [(0.0, _wd_lerp_rgb(_wd_C_FAINT, _wd_C_BLUE, 0.55)), (0.5, _wd_C_BLUE), (1.0, _wd_C_CYAN)]
_wd_QUOTA_STOPS: list[tuple[float, _wd_Rgb]] = [(0.0, _wd_C_GREEN), (70.0, _wd_C_AMBER), (95.0, _wd_C_RED)]
_wd_CTX_STOPS: list[tuple[float, _wd_Rgb]] = [(0.0, _wd_C_SUB), (50.0, _wd_C_SUB), (80.0, _wd_C_AMBER), (100.0, _wd_C_RED)]

_wd_RESET = "\x1b[0m"
_wd_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_wd_SPARK = "▁▂▃▄▅▆▇█"
_wd_PARTIALS = " ▏▎▍▌▋▊▉"

_wd_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")
_wd_SPLIT_RE = re.compile(r"(\x1b\[[0-9;]*m)")
_wd_CTRL_RE = re.compile("[\x00-\x1f\x7f-\x9f  ]")


# --- colours -------------------------------------------------------------------------------------
def _wd_lerp(c1: _wd_Rgb, c2: _wd_Rgb, x: float) -> _wd_Rgb:
    x = min(max(x, 0.0), 1.0)
    return (
        round(c1[0] + (c2[0] - c1[0]) * x),
        round(c1[1] + (c2[1] - c1[1]) * x),
        round(c1[2] + (c2[2] - c1[2]) * x),
    )


def _wd_gradient(stops: Sequence[tuple[float, _wd_Rgb]], x: float) -> _wd_Rgb:
    if x <= stops[0][0]:
        return stops[0][1]
    for (x0, c0), (x1, c1) in pairwise(stops):
        if x <= x1:
            return _wd_lerp(c0, c1, (x - x0) / (x1 - x0))
    return stops[-1][1]


def _wd_pct_color(pct: float) -> _wd_Rgb:
    return _wd_gradient(_wd_QUOTA_STOPS, pct)


def _wd_ctx_color(pct: float | None) -> _wd_Rgb:
    """Context fill as a percentage of the danger limit: normal to half of it, amber at 80%, red at the limit."""
    return _wd_C_SUB if pct is None else _wd_gradient(_wd_CTX_STOPS, pct)


def _wd_family_color(family: str) -> _wd_Rgb:
    return _wd_FAMILY_COLORS.get(family, _wd_C_SUB)


def _wd_sgr(fg: _wd_Rgb | None = None, bold: bool = False, bg: _wd_Rgb | None = None,
             ul: bool = False) -> str:
    parts: list[str] = []
    if bold:
        parts.append("1")
    if ul:
        parts.append("4")
    if fg is not None:
        parts.append(f"38;2;{fg[0]};{fg[1]};{fg[2]}")
    if bg is not None:
        parts.append(f"48;2;{bg[0]};{bg[1]};{bg[2]}")
    return "\x1b[" + ";".join(parts) + "m" if parts else ""


def _wd_c(text: str, fg: _wd_Rgb | None = None, bold: bool = False, bg: _wd_Rgb | None = None,
          ul: bool = False) -> str:
    """Wrap `text` in one SGR run; empty text stays empty."""
    if not text:
        return ""
    code = _wd_sgr(fg, bold, bg, ul)
    return code + text + _wd_RESET if code else text


def _wd_bg_line(line: str, bg: _wd_Rgb, w: int) -> str:
    """Put a background behind a whole line (padded to `w` cells first)."""
    code = _wd_sgr(bg=bg)
    line = _wd_pad(line, w)
    return code + line.replace(_wd_RESET, _wd_RESET + code) + _wd_RESET


def _wd_spinner(t: float) -> str:
    return _wd_SPINNER[int(t * 10) % 10]


def _wd_flowbar(level: int, t: float, width: int = 9) -> str:
    """A compact activity bar whose bright marker advances with activity."""
    width = max(3, width)
    if level <= 0:
        return " " * width
    speed = (0.0, .18, .45, .8)[min(max(level, 0), 3)]
    pos = int(t * speed * width) % width
    return "".join("╸" if i == pos else "━" for i in range(width))


# --- text measuring ------------------------------------------------------------------------------
@lru_cache(maxsize=8192)
def _wd_cw(ch: str) -> int:
    if ch < " " or "\x7f" <= ch < "\xa0":
        return 0
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _wd_width(s: str) -> int:
    """Display cells of a (possibly SGR-styled) string."""
    if "\x1b" in s:
        s = _wd_SGR_RE.sub("", s)
    if s.isascii():
        return len(s)
    return sum(map(_wd_cw, s))


def _wd_clean(text: object) -> str:
    """Replace control characters (tabs, ESC, NUL, newlines) with spaces."""
    return _wd_CTRL_RE.sub(" ", str(text))


def _wd_trunc(text: str, w: int) -> str:
    """Truncate plain text to `w` cells with a trailing `…`."""
    if w <= 0:
        return ""
    text = _wd_clean(text)
    if _wd_width(text) <= w:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        cw = _wd_cw(ch)
        if used + cw > w - 1:
            break
        out.append(ch)
        used += cw
    return "".join(out) + "…"


def _wd_clip(s: str, w: int, ell: bool = True) -> str:
    """Cut a styled string to `w` cells, keeping its SGR codes."""
    if w <= 0:
        return ""
    if _wd_width(s) <= w:
        return s
    limit = w - 1 if ell else w
    out: list[str] = []
    used = 0
    for part in _wd_SPLIT_RE.split(s):
        if not part:
            continue
        if part[0] == "\x1b":
            out.append(part)
            continue
        keep: list[str] = []
        stop = False
        for ch in part:
            cw = _wd_cw(ch)
            if used + cw > limit:
                stop = True
                break
            keep.append(ch)
            used += cw
        out.append("".join(keep))
        if stop:
            break
    if ell:
        out.append("…")
    out.append(_wd_RESET)
    return "".join(out)


def _wd_pad(s: str, w: int) -> str:
    n = w - _wd_width(s)
    return s + " " * n if n > 0 else s


def _wd_fit(s: str, w: int) -> str:
    """Exactly `w` cells, ending in a reset."""
    s = _wd_clip(s, w, ell=False)
    return _wd_pad(s, w) + _wd_RESET


def _wd_center(s: str, w: int) -> str:
    n = w - _wd_width(s)
    if n <= 0:
        return s
    left = n // 2
    return " " * left + s + " " * (n - left)


def _wd_lr(left: str, right: str, w: int, gap: int = 1) -> str:
    """Left text at the left edge, right text flush right; the right text is dropped when cramped."""
    if not right:
        return _wd_pad(_wd_clip(left, w), w)
    lw, rw = _wd_width(left), _wd_width(right)
    if lw + gap + rw <= w:
        return left + " " * (w - lw - rw) + right
    avail = w - rw - gap
    if avail < 6:
        return _wd_pad(_wd_clip(left, w), w)
    return _wd_pad(_wd_clip(left, avail), avail) + " " * gap + right


def _wd_split_cells(word: str, w: int) -> tuple[str, str]:
    used = 0
    for i, ch in enumerate(word):
        cw = _wd_cw(ch)
        if used + cw > w and i > 0:
            return word[:i], word[i:]
        used += cw
    return word, ""


def _wd_wrap(text: str, w: int) -> list[str]:
    """Word-wrap plain text to `w` cells; very long words are broken. Always returns >= 1 line."""
    text = _wd_clean(text)
    w = max(1, w)
    if not text.strip():
        return [""]
    indent = min(len(text) - len(text.lstrip(" ")), w // 2)
    lines: list[str] = []
    cur = " " * indent
    curw = indent
    for word in text.split():
        ww = _wd_width(word)
        while ww > w:
            if curw > indent:
                lines.append(cur)
                cur, curw = " " * indent, indent
            room = max(1, w - curw)
            head, word = _wd_split_cells(word, room)
            lines.append(cur + head)
            cur, curw = " " * indent, indent
            ww = _wd_width(word)
        if not word:
            continue
        if curw > indent and curw + 1 + ww > w:
            lines.append(cur)
            cur, curw = " " * indent, indent
        if curw > indent:
            cur += " "
            curw += 1
        cur += word
        curw += ww
    if curw > indent or not lines:
        lines.append(cur)
    return lines


def _wd_markdown(line: str, plain: bool = False) -> list[tuple[str, _wd_Rgb | None, bool]] | str:
    """Clean supported Markdown into styled spans, or a plain string."""
    line = _wd_clean(line).strip()
    line = re.sub(r"^(?:[-*]\s+|\d{1,2}\.\s+)", "", line)
    pattern = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|`([^`]+)`|\[([^]]+)\]\(([^)]*)\)|https?://\S+")
    spans: list[tuple[str, _wd_Rgb | None, bool]] = []
    pos = 0
    for match in pattern.finditer(line):
        if match.start() > pos:
            spans.append((line[pos:match.start()], None, False))
        token = match.group(0)
        if match.group(1) or match.group(2):
            value, color, bold = match.group(1) or match.group(2), _wd_C_TEXT, True
        elif match.group(3):
            value, color, bold = match.group(3), _wd_C_CYAN, False
        elif match.group(4):
            value, color, bold = match.group(4), None, False
        else:
            value = token
            if len(token) > 30:
                host = token.split("/", 3)[2]
                value = host + "/…" if "/" in token[len("https://"):] else host
            color, bold = None, False
        spans.append((value, None if plain else color, False if plain else bold))
        pos = match.end()
    if pos < len(line):
        spans.append((line[pos:], None, False))
    spans = spans or [("", None, False)]
    return "".join(span[0] for span in spans) if plain else spans


# --- boxes ---------------------------------------------------------------------------------------
def _wd_box(lines: Sequence[str], w: int, h: int, title: str = "", color: _wd_Rgb = _wd_C_BORDER,
            note: str = "", thumb: tuple[int, int] | None = None, spine: Sequence[str] = ()) -> list[str]:
    """Rounded panel of exactly `w` x `h` cells. `title`/`note` are pre-styled strings.
    `spine` replaces the left border row by row; each entry is one pre-styled cell."""
    if w < 2 or h < 2:
        return [" " * max(w, 0)] * max(h, 0)
    iw, ih = w - 2, h - 2
    bc = _wd_sgr(color)
    rs = _wd_RESET

    def edge(left: str, right: str, label: str) -> str:
        tmax = iw - 4
        if label and tmax >= 1:
            label = _wd_clip(label, tmax)
            fill = iw - 3 - _wd_width(label)
            return (f"{bc}{left}─ {rs}{label}{rs}{bc} {'─' * fill}{right}{rs}")
        return f"{bc}{left}{'─' * iw}{right}{rs}"

    out = [edge("╭", "╮", title)]
    right_plain = f"{bc}│{rs}"
    right_track = f"{_wd_sgr(_wd_C_FAINT)}│{rs}"
    right_thumb = f"{bc}┃{rs}"
    left_edge = f"{bc}│{rs}"
    for i in range(ih):
        line = _wd_fit(lines[i], iw) if i < len(lines) else " " * iw
        if thumb is None:
            r = right_plain
        else:
            r = right_thumb if thumb[0] <= i < thumb[0] + thumb[1] else right_track
        out.append((spine[i] if i < len(spine) else left_edge) + line + r)
    out.append(edge("╰", "╯", note))
    return out


def _wd_thumb(total: int, visible: int, first: int) -> tuple[int, int] | None:
    """Scrollbar thumb (start row, length) for `total` lines with `visible` rows showing from `first`."""
    if total <= visible or visible <= 0:
        return None
    size = max(1, visible * visible // total)
    span = total - visible
    pos = round(min(max(first, 0), span) / span * (visible - size))
    return pos, size


# --- bars and sparklines ---------------------------------------------------------------------------
def _wd_bar(frac: float, width: int, color_at: Callable[[float], _wd_Rgb], pace: float | None = None,
            fill_color: _wd_Rgb | None = None) -> str:
    """Bar of `width` cells with eighth-block partials on a faint `·` track.

    `color_at` maps a cell's position (0..1) to its colour; `fill_color` overrides it (stale bars)."""
    if width <= 0:
        return ""
    frac = 0.0 if math.isnan(frac) else min(max(frac, 0.0), 1.0)
    eighths = round(frac * width * 8)
    full, part = divmod(eighths, 8)
    tick = -1
    if pace is not None and not math.isnan(pace):
        tick = min(width - 1, max(0, int(min(max(pace, 0.0), 1.0) * width)))
    track = _wd_sgr(_wd_C_FAINT)
    out: list[str] = []
    for i in range(width):
        if i < full:
            ch = "█"
        elif i == full and part:
            ch = _wd_PARTIALS[part]
        else:
            ch = None
        if i == tick:
            # The pace tick is drawn *over* the cell rather than replacing it, so the
            # filled length still matches the value it is labelled with.
            col = _wd_C_SUB if ch is None else (fill_color if fill_color is not None
                                                else color_at((i + 0.5) / width))
            out.append(f"{_wd_sgr(col)}│")
            continue
        if ch is None:
            out.append(f"{track}·")
            continue
        col = fill_color if fill_color is not None else color_at((i + 0.5) / width)
        out.append(f"{_wd_sgr(col)}{ch}")
    return "".join(out) + _wd_RESET


def _wd_gauge_bar(frac: float, width: int, color: _wd_Rgb, *, shimmer: int = -1) -> str:
    """Compact eighth-block progress gauge; shimmer marks one live cell."""
    width = max(0, width)
    if not width:
        return ""
    frac = min(max(float(frac), 0.0), 1.0)
    units = round(frac * width * 8)
    full, part = divmod(units, 8)
    out: list[str] = []
    for i in range(width):
        if i < full:
            ch = "█"
        elif i == full and part:
            ch = _wd_PARTIALS[part]
        else:
            ch = "░"
        col = _wd_C_CYAN if i == shimmer else color if (i < full or (i == full and part)) else _wd_C_FAINT
        out.append(_wd_sgr(col) + ch)
    return "".join(out) + _wd_RESET


def _wd_load_meter(value: float, width: int = 5) -> str:
    """Render a compact load level as block heights, preserving empty cells."""
    chars = "▁▂▃▄▅▆▇█"
    count = max(0, width)
    level = min(8, max(0, round(min(max(value, 0.0), 100.0) / 100 * 8)))
    col = _wd_C_DIM if value < 35 else _wd_C_AMBER if value > 75 else _wd_C_BLUE
    return "".join(_wd_sgr(col if i >= count - 1 else _wd_C_DIM) + chars[level - 1 if level else 0]
                   for i in range(count)) + _wd_RESET


def _wd_levels(values: Sequence[float], vmax: float | None = None) -> list[int]:
    """Bar heights 0..7 for values (0 stays 0, anything above gets at least 1)."""
    clean: list[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = 0.0
        clean.append(f if f > 0 and not math.isnan(f) else 0.0)
    top = vmax if vmax is not None else max(clean, default=0.0)
    if top <= 0:
        top = 1.0
    return [0 if v <= 0 else 1 + min(6, int(v / top * 6.999)) for v in clean]


def _wd_spark_levels(levels: Sequence[int]) -> str:
    out: list[str] = []
    faint = _wd_sgr(_wd_C_FAINT)
    for lv in levels:
        if lv <= 0:
            out.append(f"{faint}▁")
        else:
            col = _wd_gradient(_wd_SPARK_STOPS, lv / 7)
            out.append(f"{_wd_sgr(col)}{_wd_SPARK[min(lv, 7)]}")
    return "".join(out) + _wd_RESET if out else ""


def _wd_spark(values: Sequence[float], width: int, vmax: float | None = None) -> str:
    """Newest `width` values as a sparkline; shorter data is padded with empty cells on the left."""
    if width <= 0:
        return ""
    vals = list(values)[-width:]
    vals = [0.0] * (width - len(vals)) + vals
    return _wd_spark_levels(_wd_levels(vals, vmax))


# --- formatting ----------------------------------------------------------------------------------
def _wd_fmt_dur(sec: float) -> str:
    """42s, 04m 12s, 1h 03m."""
    s = int(max(sec, 0))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60:02d}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


def _wd_fmt_mins(sec: float) -> str:
    """Coarse duration: 45s, 6m, 1h 03m."""
    s = int(max(sec, 0))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


def _wd_fmt_tok(n: float) -> str:
    n = int(max(n, 0))
    if n < 1000:
        return str(n)
    if n < 100_000:
        v = n / 1000
        return (f"{v:.1f}".rstrip("0").rstrip(".") if v < 10 else f"{v:.0f}") + "k"
    if n < 1_000_000:
        return f"{n / 1000:.0f}k"
    v = n / 1_000_000
    return (f"{v:.1f}".rstrip("0").rstrip(".") if v < 10 else f"{v:.0f}") + "M"


def _wd_fmt_money(x: float) -> str:
    x = max(x, 0.0)
    return f"${x:.3f}" if x < 0.1 else f"${x:.2f}"


def _wd_fmt_mem(b: float) -> str:
    b = max(b, 0)
    if b < 1024 * 1024:
        return f"{b / 1024:.0f}K"
    if b < 1024**3:
        return f"{b / 1024 ** 2:.0f}M"
    return f"{b / 1024 ** 3:.1f}G"


def _wd_fmt_clock(ts: float, seconds: bool = False) -> str:
    try:
        return time.strftime("%H:%M:%S" if seconds else "%H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return "--:--:--" if seconds else "--:--"
