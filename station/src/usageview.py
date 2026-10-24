"""The usage screen (u): one table row per account, the selected account opened, credit and OpenRouter below.

Pure drawing: takes the quota data and a cursor, returns lines. Imports only widgets and model so render can call it.
"""
from __future__ import annotations

import os
import time

from model import OPENROUTER_ACCOUNT, OpenRouterSnapshot, QuotaAccount, QuotaWindow
from widgets import (
    _wd_bar,
    _wd_box,
    _wd_c,
    _wd_levels,
    _wd_SPARK,
    _wd_C_AMBER,
    _wd_C_BLUE,
    _wd_C_DIM,
    _wd_C_GREEN,
    _wd_C_MUTED,
    _wd_C_RED,
    _wd_C_SUB,
    _wd_C_TEXT,
    _wd_clean,
    _wd_SGR_RE,
    _wd_lr,
    _wd_pad,
    _wd_pct_color,
    _wd_spark,
    _wd_thumb,
    _wd_trunc,
    _wd_width,
)

_UV_ON_PACE = 5.0          # points either side of pace that still read as "on pace"
_UV_WIN_SECS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400, "thirty_day": 30 * 86400}


def _uv_plain() -> bool:
    return os.environ.get("NO_COLOR") is not None


def _uv_c(text: str, fg: tuple[int, int, int] | None = None, bold: bool = False) -> str:
    return text if _uv_plain() else _wd_c(text, fg, bold=bold)


def _uv_pct_mark(used: float, stale: bool) -> str:
    """Plain cue for quota bands when colour is off (matches _wd_QUOTA_STOPS thresholds)."""
    if stale:
        return ""
    if used >= 100.0:
        return " cap"
    if used >= 90.0:
        return " high"
    if used >= 70.0:
        return " warn"
    return ""


def _uv_spark_plain(values: list[float], width: int, vmax: float) -> str:
    vals = [0.0] * max(0, width - len(values)) + list(values)[-width:]
    return "".join(_wd_SPARK[min(lv, 7)] if lv > 0 else "_" for lv in _wd_levels(vals, vmax))


def _uv_ttr(resets_at: float | None, now: float) -> str:
    """Time to reset, two units at most: 2h14m, 3d4h, 45m."""
    if resets_at is None:
        return ""
    secs = resets_at - now
    if secs <= 0:
        return "now"
    mins = int(secs // 60)
    if mins < 1:
        return "<1m"
    if mins < 60:
        return f"{mins}m"
    hours, rem = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h" + (f"{rem}m" if rem else "")
    days, hrem = divmod(hours, 24)
    return f"{days}d" + (f"{hrem}h" if hrem else "")


def _uv_winner(acct: QuotaAccount) -> QuotaWindow | None:
    """The window that binds the account: a capped one, else the earliest projected run-out, else the fullest."""
    wins = acct.windows
    if not wins:
        return None
    capped = [w for w in wins if w.used >= 100.0]
    if capped:
        return max(capped, key=lambda w: w.used)
    proj = [w for w in wins if w.runs_out_at is not None]
    if proj:
        return min(proj, key=lambda w: w.runs_out_at or 0.0)
    return max(wins, key=lambda w: (w.used, -_UV_WIN_SECS.get(w.window, 7 * 86400)))


def _uv_verdict(win: QuotaWindow, now: float) -> tuple[str, tuple[int, int, int]]:
    """Plain words for how the burn compares with an even spend: (text, colour)."""
    if win.used >= 100.0:
        return "capped", _wd_C_RED
    if win.runs_out_at is not None and win.runs_out_at > now:
        return "out in " + _uv_ttr(win.runs_out_at, now), _wd_C_RED
    if win.pace is None or win.used < 1.0:
        return "", _wd_C_MUTED
    delta = win.used - win.pace
    if delta > _UV_ON_PACE:
        return f"{delta:.0f} over pace", _wd_C_AMBER
    if delta < -_UV_ON_PACE:
        return f"{-delta:.0f} under pace", _wd_C_GREEN
    return "on pace", _wd_C_MUTED


def _uv_pct(used: float, stale: bool) -> str:
    txt = f"{min(max(used, 0), 999):.0f}%".rjust(4)
    mark = _uv_pct_mark(used, stale)
    if _uv_plain():
        return txt + mark
    return _wd_c(txt, _wd_C_DIM if stale else _wd_pct_color(used))


def _uv_bar(win: QuotaWindow, bw: int, stale: bool) -> str:
    if bw <= 0:
        return ""
    if _uv_plain():
        frac = min(max(win.used / 100.0, 0.0), 1.0)
        full = min(bw, int(frac * bw + 0.999))
        pace = None if win.pace is None else win.pace / 100.0
        tick = -1
        if pace is not None:
            tick = min(bw - 1, max(0, int(min(max(pace, 0.0), 1.0) * bw)))
        fill = "#" if stale else "="
        out: list[str] = []
        for i in range(bw):
            if i == tick:
                out.append("|")
            elif i < full:
                out.append(fill)
            else:
                out.append(".")
        return "".join(out)
    pace = None if win.pace is None else win.pace / 100.0
    return _wd_bar(win.used / 100.0, bw, lambda x: _wd_pct_color(x * 100), pace, _wd_C_DIM if stale else None)


def _uv_rank(name: str) -> int:
    """your order: claude, codex, cursor, opencode, then claude-work, then the rest."""
    n = name.lower()
    if n == "claude":
        return 0
    if n == "codex":
        return 1
    if n.startswith("cursor"):
        return 2
    if n.startswith("opencode"):
        return 3
    if n == "claude-work":
        return 4
    return 5


def _uv_groups(accounts: list[QuotaAccount], or_shown: bool) -> tuple[list[QuotaAccount], list[QuotaAccount], list[str]]:
    """(accounts with windows, credit accounts, names with nothing to show). Same-name duplicates collapse."""
    credit_names = {a.name for a in accounts if a.credit is not None}
    live: list[QuotaAccount] = []
    credit: list[QuotaAccount] = []
    quiet: list[str] = []
    seen: set[str] = set()
    for a in accounts:
        if a.credit is not None:
            if a.name == OPENROUTER_ACCOUNT and or_shown:
                continue    # the OpenRouter block below carries it
            credit.append(a)
            continue
        if a.windows and not a.stale:
            live.append(a)
        elif a.name in credit_names or a.name in seen:
            continue        # a windowless twin of a credit account
        else:
            quiet.append(_wd_clean(a.name))
        seen.add(a.name)
    live.sort(key=lambda a: (_uv_rank(a.name), a.name))
    return live, credit, quiet


def _uv_credit_text(a: QuotaAccount) -> tuple[str, bool]:
    c = a.credit
    if c is None or c.balance is None:
        return "no balance", False
    txt = f"${c.balance:.2f} left"
    if c.burn_per_day:
        txt += f" · ${c.burn_per_day:.2f}/day"
    if c.runs_out_at:
        txt += " · out " + time.strftime("%b %d", time.localtime(c.runs_out_at))
    return txt, c.low


def _uv_openrouter(snap: OpenRouterSnapshot, iw: int) -> list[str]:
    head = ""
    if snap.balance is None:
        head += _uv_c("no balance recorded", _wd_C_MUTED)
    else:
        head += _uv_c(f"${snap.balance:.2f}", _wd_C_RED if snap.low else _wd_C_TEXT, bold=True)
        if snap.low and _uv_plain():
            head += " LOW"
        bits = []
        if snap.burn_per_day is not None:
            bits.append(f"${snap.burn_per_day:.2f}/day")
        if snap.runs_out_at:
            bits.append("out " + time.strftime("%b %d", time.localtime(snap.runs_out_at)))
        if snap.floor_usd is not None:
            bits.append(f"floor ${snap.floor_usd:.2f}")
        if bits:
            head += "  " + _uv_c(" · ".join(bits), _wd_C_MUTED)
    if snap.stale:
        head += _uv_c("  stale", _wd_C_DIM)
    out = [head]
    days = sorted(snap.per_day, key=lambda r: r.date)
    if days:
        today = days[-1]
        prev = days[-2] if len(days) > 1 else None
        txt = f"today ${today.usd:.2f} · {today.requests} req"
        if prev is not None:
            txt += f" (yesterday ${prev.usd:.2f})"
        spark_vals = [r.usd for r in days[-14:]]
        spark_w = min(len(days), 14)
        spark = (_uv_spark_plain(spark_vals, spark_w, max(max(spark_vals), 1.0)) if _uv_plain()
                 else _wd_c(_wd_spark(spark_vals, spark_w), _wd_C_SUB))
        out.append("  " + spark + "  " + _uv_c(txt, _wd_C_MUTED))
    total = sum(r.usd for r in snap.per_model)
    top = sorted(snap.per_model, key=lambda r: -r.usd)[:3]
    if top and total > 0:
        out.append("  " + _uv_c(_wd_trunc("top  " + " · ".join(f"{r.model.split('/')[-1]} {100 * r.usd / total:.0f}%" for r in top), iw - 2), _wd_C_DIM))
    return out


def _uv_rule(title: str, note: str, iw: int) -> str:
    """Section header: TITLE ─────── note."""
    head = _uv_c(title, _wd_C_BLUE, bold=True)
    tail = _uv_c(note, _wd_C_DIM) if note else ""
    fill = max(2, iw - _wd_width(title) - _wd_width(note) - (3 if note else 1))
    return head + " " + _uv_c("─" * fill, _wd_C_DIM) + (" " + tail if tail else "")


def _uv_block(a: QuotaAccount, bw: int, iw: int, now: float, hist: list[float] | None, spark: bool = True) -> list[str]:
    """One subscription: its name and verdict, then one bar per window, then a blank line."""
    bind = _uv_winner(a)
    text, col = _uv_verdict(bind, now) if bind is not None else ("", _wd_C_MUTED)
    if bind is not None and not text:
        text = "fine"
    dot = ("* " if a.in_use else "o ") if _uv_plain() else _wd_c("● ", _wd_C_BLUE if a.in_use else _wd_C_DIM)
    left = dot + _uv_c(_wd_clean(a.name), _wd_C_TEXT, bold=True)
    if a.in_use:
        left += _uv_c("  in use", _wd_C_DIM)
    right = _uv_c(text, col)
    if a.extra is not None:
        right += _uv_c("   +$" + (f"{a.extra.usd:.2f}" if a.extra.usd is not None else "?") + " past cap", _wd_C_RED, bold=True)
    out = [_wd_lr(left, right, iw, 2)]
    lw = max([9] + [_wd_width(w.name) + 1 for w in a.windows])
    ordered = sorted(a.windows, key=lambda x: _UV_WIN_SECS.get(x.window, 7 * 86400))
    for w in ordered:
        row = ("    " + _uv_c(_wd_pad(w.name, lw), _wd_C_MUTED) + _uv_bar(w, bw, w.stale) + " " + _uv_pct(w.used, w.stale)
               + "   " + _uv_c(_wd_pad("resets " + _uv_ttr(w.resets_at, now), 15), _wd_C_MUTED))
        if w.projected_pct is not None and w.projected_pct > w.used + 1:
            proj = f"heading to {w.projected_pct:.0f}%"
            if _uv_plain() and w.projected_pct >= 100:
                proj += " !"
            row += _uv_c(proj, _wd_C_RED if w.projected_pct >= 100 else _wd_C_DIM)
        out.append(row.rstrip())
    if spark and hist and len(hist) >= 2 and iw >= 70:
        spark_w = min(len(hist), bw)
        vmax = max(max(hist), 10.0)
        spark = (_uv_spark_plain(hist, spark_w, vmax) if _uv_plain()
                 else _wd_c(_wd_spark(hist, spark_w, vmax), _wd_C_SUB))
        out.append("    " + _uv_c(_wd_pad("last 24h", lw), _wd_C_MUTED) + spark
                   + _uv_c(f"   low {min(hist):.0f}% · high {max(hist):.0f}%", _wd_C_DIM))
    return out


def _uv_two_up(blocks: list[list[str]], cw: int) -> list[str]:
    """Blocks laid out in two columns, left then right, row by row."""
    out: list[str] = []
    for i in range(0, len(blocks), 2):
        left = blocks[i]
        right = blocks[i + 1] if i + 1 < len(blocks) else []
        for k in range(max(len(left), len(right))):
            l_cell = left[k] if k < len(left) else ""
            r_cell = right[k] if k < len(right) else ""
            out.append(_wd_pad(l_cell, cw) + "   " + r_cell)
        out.append("")
    return out


def usage_lines(quota: list[QuotaAccount], quota_ok: bool, openrouter: OpenRouterSnapshot | None,
                openrouter_ok: bool, sel: int, iw: int, now: float,
                history: dict[str, list[float]] | None = None, spark: bool = True, two_up: bool = False,
                airy: bool = True) -> tuple[list[str], int, int]:
    """(body lines, 0, number of subscriptions). Every subscription is a block with all its windows; the
    concerns are separate labelled sections: subscriptions, API credits, OpenRouter, no live data."""
    if not quota_ok:
        return [_uv_c("quota unavailable (quota service not reachable)", _wd_C_DIM)], 0, 0
    history = history or {}
    or_shown = openrouter_ok and openrouter is not None
    live, credit, quiet = _uv_groups(quota, or_shown)
    nw = max((_wd_width(_wd_clean(a.name)) for a in credit), default=6)
    cw = (iw - 3) // 2 if two_up else iw
    bw = min(max(cw - (4 + 12 + 1 + 4 + 3 + 15 + 18), 10), 44)
    gap = [""] if airy else []
    lines: list[str] = [_uv_rule("SUBSCRIPTIONS", f"{len(live)} live", iw)] + gap
    blocks = [_uv_block(a, bw, cw, now, history.get(a.name), spark) for a in live]
    if two_up:
        lines += _uv_two_up(blocks, cw)
    else:
        for blk in blocks:
            lines += blk + [""]
    if not live:
        lines += [_uv_c("  none reporting", _wd_C_DIM), ""]
    if credit:
        lines += [_uv_rule("API CREDITS", "", iw)] + gap
        for a in credit:
            txt, low = _uv_credit_text(a)
            lines.append("  " + _uv_c(_wd_pad(_wd_trunc(_wd_clean(a.name), nw), nw), _wd_C_TEXT, bold=True) + "   "
                         + (_uv_c(txt + " LOW", _wd_C_RED, bold=True) if low else _uv_c(txt, _wd_C_MUTED)))
        lines.append("")
    if or_shown and openrouter is not None:
        lines += [_uv_rule("OPENROUTER", "", iw)] + gap + ["  " + x for x in _uv_openrouter(openrouter, iw - 2)] + [""]
    if quiet:
        lines += [_uv_rule("NO LIVE DATA", "", iw)] + gap + ["  " + _uv_c(_wd_trunc("  ·  ".join(quiet), iw - 2), _wd_C_DIM)]
    while lines and not lines[-1]:
        lines.pop()
    return lines, 0, len(live)


def usage_screen(quota: list[QuotaAccount], quota_ok: bool, openrouter: OpenRouterSnapshot | None,
                 openrouter_ok: bool, scroll: int, cols: int, rows: int, now: float,
                 history: dict[str, list[float]] | None = None) -> list[str]:
    """The boxed screen, `rows` tall; `scroll` is the first line shown when the content is still too tall."""
    iw, ih = cols - 4, rows - 2
    # Step down until it fits: airy, tight, two columns (wide only), no sparklines, no sparklines in two columns.
    steps = [(True, False, True), (True, False, False)]
    if iw >= 120:
        steps.append((True, True, False))
    steps.append((False, False, False))
    if iw >= 120:
        steps.append((False, True, False))
    lines: list[str] = []
    for spark, two_up, airy in steps:
        lines, _at, _n = usage_lines(quota, quota_ok, openrouter, openrouter_ok, 0, iw, now, history, spark, two_up, airy)
        if len(lines) <= ih:
            break
    first = min(max(int(scroll), 0), max(0, len(lines) - ih))
    body = lines[first:first + ih]
    boxed = _wd_box([" " + x for x in body], cols, rows, _uv_c("usage", _wd_C_BLUE, bold=True),
                    thumb=_wd_thumb(len(lines), ih, first))
    if _uv_plain():
        return [_wd_SGR_RE.sub("", line) for line in boxed]
    return boxed
