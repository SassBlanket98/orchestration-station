"""Layout and panels: turns a Snapshot into a finished terminal frame."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from cclayout import cc_applies, cc_frame
from ccrow import command_lines
from collect import WorkerChanges, worker_changes
from labels import display_model
from logparse import tool_category
from model import (
    DONE,
    DONE_SHOWN,
    FAILED,
    IDLE_LIST_S,
    LIVE_STATES,
    NOREPORT,
    OPENROUTER_ACCOUNT,
    QUIET,
    QUIET_S,
    RUNNING,
    SPEND_PROVIDER_ORDER,
    STALL_S,
    STALLED,
    TOOL_CATS,
    Finding,
    GlobalSnapshot,
    Group,
    LogState,
    Milestone,
    QuotaAccount,
    QuotaCredit,
    QuotaExtra,
    QuotaWindow,
    Review,
    Snapshot,
    SpendSnapshot,
    Stage,
    Step,
    UIState,
    Worker,
)
from openclawview import OV_KEYS, _ov_page
from opsgrid import (
    OG_SWARM_MIN,
    _og_age,
    _og_badge,
    _og_Cells,
    _og_finished_line,
    _og_fit_line,
    _og_header,
    _og_hive,
    _og_idle_divider,
    _og_Plan,
    _og_plan,
    _og_project_head,
    _og_pulse,
    _og_row,
    _og_selected,
    _og_stage_cells,
    _og_swarm_band,
    _og_worker_cells,
)
from opsspark import _os_step_line, _os_why
from opstree import _ot_prefixes
from sessionview import SV_KEYS, _sv_page
from tutorial import tut_overlay
from usageview import usage_screen
from widgets import (
    _wd_bar,
    _wd_bg_line,
    _wd_box,
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
    _wd_C_TEAL,
    _wd_C_TEXT,
    _wd_C_VIOLET,
    _wd_center,
    _wd_clean,
    _wd_clip,
    _wd_family_color,
    _wd_fit,
    _wd_fmt_clock,
    _wd_fmt_dur,
    _wd_fmt_mem,
    _wd_fmt_mins,
    _wd_fmt_money,
    _wd_fmt_tok,
    _wd_gauge_bar,
    _wd_lerp,
    _wd_levels,
    _wd_lr,
    _wd_markdown,
    _wd_pad,
    _wd_pct_color,
    _wd_Rgb,
    _wd_SGR_RE,
    _wd_spark,
    _wd_spark_levels,
    _wd_spinner,
    _wd_thumb,
    _wd_trunc,
    _wd_width,
    _wd_wrap,
)
from workerview import WV_KEYS, _wv_page

_rd_LEVELS = ((5, 1, 1, 1), (3, 1, 1, 1), (2, 1, 1, 1), (1, 1, 1, 1),
              (1, 0, 1, 1), (1, 0, 0, 1), (1, 0, 0, 0))
_rd_DETAIL_KEYS = (("Esc", "back"), ("Tab", "switch"), ("↑↓", "scroll"), ("G", "follow"), ("u", "quota"))
_rd_QUOTA_KEYS = (("Esc", "back"), ("↑↓", "scroll"))
_rd_OPENROUTER_KEYS = (("Esc", "back"), ("↑↓", "scroll"), ("5", "openrouter"))
_rd_KEY_TAIL = (("?", "keys"), ("q", "quit"))   # always shown, last; the keys before them drop from the right
_rd_HELP = (
    ("", "LIST"),
    ("↑↓  j k", "move; PgUp PgDn five rows; g G top and bottom"),
    ("Enter", "fold or unfold the row; ← folds it, → unfolds it"),
    ("Space", "open the session or worker page (double-click does the same); Esc goes back"),
    ("e  c", "unfold everything, idle and closed too; fold everything"),
    ("", "FIND"),
    ("/", "search by text (Enter keeps it, Esc clears)"),
    ("1 2 3 4", "filter: all, working, lagging, problems; f cycles them"),
    ("", "STATION VIEW"),
    ("w", "open the station view of the row's session: live worker cards, timeline, reviews"),
    ("L C F", "inside it: lead page, cost page, files page; t timeline, r reviews; Esc goes back"),
    ("", "DETAILS  (control center)"),
    ("d", "show or hide the details pane; Tab moves focus: list, pane, strip"),
    ("← →", "with the pane focused: its tabs. ↑↓ scroll it"),
    ("[ ]  E", "the strip's tabs: usage, events, logs, metrics; E shows or hides it"),
    ("v", "control center or classic view"),
    ("", "ACT  (asks before it does anything)"),
    ("s  K", "stop the session or worker; kill it (K also works inside the stop question)"),
    ("r  n", "resume it; spawn a new one"),
    ("x", "end the lead session of the row under the cursor"),
    ("", "SHOW MORE OR LESS"),
    ("h  H", "hide or show the idle section; the closed section"),
    ("a  A", "all history; the unattached runs"),
    ("p  M", "pin the session active or idle; milestones"),
    ("t  i  m", "quip lines; legend; mute notifications"),
    ("u  5", "quota"),
    ("?  q", "this help (any key closes it); quit"),
)
_rd_TABS = (("live", "Live"), ("brief", "Brief"), ("report", "Report"))
_rd_SPINE_FRAMES = (12, 24, 36)   # life-spine cycle in frames at 10 fps: hot, calm, quiet (d-live 3.1)
_rd_HOT_S = 10                    # output this recent: hot spine
_RD_TALL = 100_000                # a panel body rendered this tall holds every row (wheel scrolling windows it)
_RD_HEADING = "Station"         # the app heading, on the terminal
_RD_CRUMB = "◆ " + _RD_HEADING + " ›"  # station view, global mode: the top-left back button


# --- small helpers -------------------------------------------------------------------------------
def _rd_sub(t: str) -> str:
    return _wd_c(t, _wd_C_SUB)


def _rd_dim(t: str) -> str:
    return _wd_c(t, _wd_C_DIM)


def _rd_muted(t: str) -> str:
    """Secondary text in the station view (labels, summaries, times). Dim is for rules and borders only."""
    return _wd_c(t, _wd_C_MUTED)


def _rd_join(parts: list[str], sep: str) -> str:
    return sep.join(p for p in parts if p)


def _rd_kv(label: str, value: str, vcolor: _wd_Rgb = _wd_C_SUB) -> str:
    return _wd_c(label + " ", _wd_C_MUTED) + _wd_c(value, vcolor)


def _rd_is_live(wk: Worker) -> bool:
    return wk.state in LIVE_STATES


def _rd_dot() -> str:
    return _wd_c(" · ", _wd_C_DIM)


def _rd_elapsed(wk: Worker, now: float) -> float:
    if wk.start <= 0:
        return 0.0
    end = wk.end if wk.end is not None else now
    return max(0.0, end - wk.start)


def _rd_eta(snap: Snapshot, wk: Worker) -> float | None:
    """Expected run time: median of finished workers on the same model (3+), else of all finished (3+).
    The same rule as app._ap_eta, which the notifier uses; tests/test_insights.py holds the two equal."""
    done = [w for w in snap.workers if w.state == DONE and w.end is not None and w.start > 0]
    same = [(w.end or w.start) - w.start for w in done if w.model and w.model == wk.model]
    pool = same if len(same) >= 3 else [(w.end or w.start) - w.start for w in done]
    return statistics.median(pool) if len(pool) >= 3 else None


def _rd_eta_span(seconds: float) -> str:
    """A countdown, rounded: 45s under a minute, then whole minutes."""
    if seconds < 59.5:
        return f"{int(seconds + 0.5)}s"
    return f"{int(seconds / 60 + 0.5)}m"


def _rd_eta_label(snap: Snapshot, wk: Worker, now: float) -> str:
    """"eta 3m" while a live worker is inside its expected run time, "over by 1m" in amber past it, "" with no estimate."""
    if not _rd_is_live(wk) or wk.start <= 0:
        return ""
    eta = _rd_eta(snap, wk)
    if eta is None:
        return ""
    left = eta - _rd_elapsed(wk, now)
    if left >= 0:
        return _rd_sub(f"eta {_rd_eta_span(left)}")
    return _wd_c(f"over by {_rd_eta_span(-left)}", _wd_C_AMBER)


def _rd_mark(wk: Worker, t: float, dim: bool = False) -> str:
    st = wk.state
    if st == RUNNING:
        return _wd_c(_wd_spinner(t), _wd_family_color(wk.family))
    if st == QUIET:
        return _wd_c("◍" if int(t * 10) // 12 % 2 else "◌", _wd_C_AMBER)   # slow breath, 2.4 s cycle
    if st == STALLED:
        return _wd_c("▲", _wd_C_RED)
    if st == DONE:
        return _wd_c("✔", _wd_C_GREEN)
    if st == FAILED:
        return _wd_c("✖", _wd_C_RED)
    return _wd_c("○", _wd_C_AMBER)   # distinct shape from FAILED: no-report is not a hard error


def _rd_mark_color(wk: Worker) -> _wd_Rgb:
    return {DONE: _wd_C_GREEN, FAILED: _wd_C_RED, QUIET: _wd_C_AMBER, STALLED: _wd_C_RED}.get(
        wk.state, _wd_C_SUB if wk.state == NOREPORT else _wd_family_color(wk.family))


def _rd_accent_color(wk: Worker, t: float) -> _wd_Rgb:
    if wk.state == RUNNING:
        k = (1.0 + math.sin(2.0 * math.pi * t / 2.4)) / 2.0
        return _wd_lerp(_wd_C_BORDER, _wd_family_color(wk.family), k)
    if wk.state == QUIET:
        return _wd_C_AMBER
    if wk.state == STALLED:
        return _wd_C_RED     # frozen, not blinking: the stall is a state, not an alarm
    return _wd_C_BORDER


def _rd_flash(ui: UIState, wk: Worker, t: float) -> tuple[_wd_Rgb, float] | None:
    """(target colour, fraction faded) while a worker's flash is running, else None."""
    ts = ui.flashes.get(wk.name)
    if ts is None:
        return None
    age = t - ts
    if age < 0 or age >= 3:
        return None
    if wk.state == DONE:
        return _wd_C_GREEN, age / 3
    if wk.state in (FAILED, STALLED):
        return _wd_C_RED, age / 3
    if wk.state == NOREPORT:
        return _wd_C_MUTED, age / 3
    return _wd_C_BLUE, age / 3


def _rd_tint(fl: tuple[_wd_Rgb, float] | None, base: _wd_Rgb) -> _wd_Rgb:
    return base if fl is None else _wd_lerp(fl[0], base, fl[1])


def _rd_empty_log(wk: Worker) -> bool:
    """claude -p and cursor-agent print nothing until they finish: no output yet is not silence."""
    return wk.log.offset == 0 and not wk.log.steps


def _rd_spine_period(wk: Worker) -> int:
    """Frames per spine cycle: hot within 10 s of output, calm within QUIET_S, quiet after. No output yet is calm."""
    if _rd_empty_log(wk):
        return _rd_SPINE_FRAMES[1]
    if wk.quiet_s <= _rd_HOT_S:
        return _rd_SPINE_FRAMES[0]
    return _rd_SPINE_FRAMES[1] if wk.quiet_s < QUIET_S else _rd_SPINE_FRAMES[2]


def _rd_spine(wk: Worker, t: float, n: int, fl: tuple[_wd_Rgb, float] | None = None) -> list[str]:
    """Life-spine for n card rows (d-live 3.1). Live: a bright head travels down and back with a trail;
    stalled: frozen red dashes; finished: a solid rule with no motion."""
    if n <= 0:
        return []
    if wk.state == STALLED:
        return [_wd_c("┆", _wd_C_RED)] * n
    if wk.state not in LIVE_STATES:
        return [_wd_c("│", _rd_tint(fl, _rd_accent_color(wk, t)))] * n
    period = _rd_spine_period(wk)
    phase = (int(t * 10) % period) / period
    head_row = int((1.0 - abs(2.0 * phase - 1.0)) * (n - 1) + 0.5)   # 0 -> bottom -> 0 over one cycle
    head = _rd_tint(fl, _wd_family_color(wk.family))
    body = _rd_tint(fl, _wd_C_BORDER)
    out: list[str] = []
    for r in range(n):
        d = abs(r - head_row)
        out.append(_wd_c("▊", head) if d == 0 else _wd_c("▍", head) if d == 1 else _wd_c("▏", body))
    return out


def _rd_edge(wk: Worker, t: float, fl: tuple[_wd_Rgb, float] | None, n: int, spine: bool) -> list[str]:
    """Column-0 glyph for each of a card's n rows: the life-spine on the expanded card, the breathing accent elsewhere."""
    if spine:
        return _rd_spine(wk, t, n, fl)
    return [_wd_c("▎", _rd_tint(fl, _rd_accent_color(wk, t)))] * n


def _rd_gutter(edge: list[str], lines: list[str]) -> list[str]:
    """Spine glyph in column 0. The name row sits against it; the rows under it indent two cells."""
    return [e + ln if k == 0 else e + "  " + ln for k, (e, ln) in enumerate(zip(edge, lines, strict=True))]


def _rd_hms(ts: float) -> str:
    return _wd_fmt_clock(ts, True)


def _rd_title_main(title: str, extra: str = "") -> str:
    out = _wd_c(title, _wd_C_BLUE, bold=True)
    return out + (_wd_c(" ── " + extra, _wd_C_MUTED) if extra else "")


# --- selection -----------------------------------------------------------------------------------
def _rd_live_idx(snap: Snapshot) -> list[int]:
    return [i for i, wk in enumerate(snap.workers) if _rd_is_live(wk)]


def _rd_default_sel(snap: Snapshot) -> int:
    """Where the cursor starts: the first live worker, else the newest finished one."""
    live = _rd_live_idx(snap)
    if live:
        return live[0]
    return max(range(len(snap.workers)), key=lambda i: (snap.workers[i].end or snap.workers[i].start, i), default=0)


def _rd_sel_on_new_start(snap: Snapshot, sel: int) -> int:
    """Cursor after a worker starts. A live cursor stays put; a finished or missing one moves to the newest live worker."""
    valid = 0 <= sel < len(snap.workers)
    live = _rd_live_idx(snap)
    if live and not (valid and _rd_is_live(snap.workers[sel])):
        return max(live, key=lambda i: (snap.workers[i].start, i))
    return sel if valid else _rd_default_sel(snap)


def _rd_clamp_sel(snap: Snapshot, ui: UIState) -> int:
    if 0 <= int(ui.sel) < len(snap.workers):
        return int(ui.sel)
    return _rd_default_sel(snap)


def _rd_panel_keys(snap: Snapshot) -> list[str]:
    """Panel title rows the cursor can reach, after the workers: timeline, then reviews. Same rule as the overview draws."""
    keys: list[str] = []
    if snap.workers or snap.reviews:
        keys.append("timeline")
    if snap.reviews:
        keys.append("reviews")
    return keys


_RD_EARLIER = "*"        # Group key of the one line that folds every earlier group
_RD_GROUP = "group:"   # cursor stop of an earlier group's line: "group:<Group.key>"


def _rd_wstop(i: int) -> str:
    """Cursor stop of the worker at index i in Snapshot.workers."""
    return f"w{i}"


def _rd_split(snap: Snapshot, idxs: list[int]) -> tuple[list[tuple[int, Worker]], list[tuple[int, Worker]]]:
    """Workers at idxs as (live, in snapshot order; finished, newest first)."""
    picked = [(i, snap.workers[i]) for i in idxs]
    return ([(i, wk) for i, wk in picked if _rd_is_live(wk)],
            _rd_newest_first([(i, wk) for i, wk in picked if not _rd_is_live(wk)]))


def _rd_plan(snap: Snapshot, ui: UIState) -> tuple[list[Group], list[tuple[int, Worker]], list[tuple[int, Worker]],
                                                   list[tuple[Group, list[tuple[int, Worker]], list[tuple[int, Worker]]]]]:
    """The workers panel's rows in display order: the current group (or None) with its live and finished workers,
    then the earlier groups, newest first, each with its live and finished workers. Groups apply in the full density
    only; without a flagged current group, every worker is in the first two lists."""
    currents = [group for group in snap.groups if group.current]
    if ui.density != "full" or not currents:
        live, fin = _rd_split(snap, list(range(len(snap.workers))))
        return [], live, fin, []
    index = {wk.name: i for i, wk in enumerate(snap.workers)}

    def members(group: Group) -> list[int]:
        return [index[name] for name in group.workers if name in index]

    live: list[tuple[int, Worker]] = []
    fin: list[tuple[int, Worker]] = []
    for group in currents:
        group_live, group_fin = _rd_split(snap, members(group))
        live.extend(group_live)
        fin.extend(group_fin)
    earlier = [(group, *_rd_split(snap, members(group))) for group in snap.groups if not group.current]
    return currents, live, fin, earlier


def _rd_stops(snap: Snapshot, ui: UIState) -> list[str]:
    """Every row the cursor can reach, top to bottom: "w<index>" for a worker, "group:<key>" for an earlier group's
    line (its workers follow it while it is open), then the panel titles."""
    _, live, fin, earlier = _rd_plan(snap, ui)
    stops = [_rd_wstop(i) for i, _ in live + fin]
    if earlier:
        stops.append(_RD_GROUP + _RD_EARLIER)
    for group, g_live, g_fin in earlier if _RD_EARLIER in ui.group_open else []:
        stops.append(_RD_GROUP + group.key)
        if group.key in ui.group_open:
            stops += [_rd_wstop(i) for i, _ in g_live + g_fin]
    return stops + _rd_panel_keys(snap)


def _rd_cursor_stop(snap: Snapshot, ui: UIState) -> str:
    """The row under the cursor, a stop from _rd_stops. A cursor on a worker that is folded away sits on its group's line."""
    stops = _rd_stops(snap, ui)
    if ui.panel in stops:
        return ui.panel
    if _rd_wstop(ui.sel) in stops:
        return _rd_wstop(ui.sel)
    if 0 <= ui.sel < len(snap.workers):
        name = snap.workers[ui.sel].name
        line = next((_RD_GROUP + group.key for group in snap.groups if name in group.workers), "")
        if line in stops:
            return line
        if line and _RD_GROUP + _RD_EARLIER in stops:   # its group is inside the folded "earlier" line
            return _RD_GROUP + _RD_EARLIER
    return stops[0] if stops else ""


def _rd_panel_sel(snap: Snapshot, ui: UIState) -> str:
    """The panel or group line under the cursor, or "" for a worker row. With no workers the cursor starts on the first panel."""
    stop = _rd_cursor_stop(snap, ui)
    return "" if stop.startswith("w") else stop


# --- hit map -------------------------------------------------------------------------------------
# Renderers record clickable and wheelable regions as (row0, row1, col0, col1, action, target), 0-based and
# inclusive, in their own coordinates. _rd_at moves what a block recorded to where the block is drawn.
def _rd_hit(ui: UIState, r0: int, r1: int, c0: int, c1: int, action: str, target: str) -> None:
    ui.hits.append((r0, r1, c0, c1, action, target))


@contextmanager
def _rd_at(ui: UIState, dy: int, dx: int) -> Iterator[None]:
    start = len(ui.hits)
    yield
    ui.hits[start:] = [(r0 + dy, r1 + dy, c0 + dx, c1 + dx, act, tgt) for r0, r1, c0, c1, act, tgt in ui.hits[start:]]


def _rd_scroll_rows(ui: UIState, name: str, render: Callable[[int], tuple[list[str], list[int | str | None]]],
                    ih: int) -> tuple[list[str], list[int | str | None]]:
    """A list panel: drawn as the cursor needs it, or, once the wheel has moved it (pscroll), windowed from that row."""
    off = ui.pscroll.get(name)
    if off is None:
        ui.ptop[name] = 0
        return render(ih)
    lines, owners = render(_RD_TALL)
    off = min(max(off, 0), max(0, len(lines) - ih))
    ui.pscroll[name] = ui.ptop[name] = off
    return lines[off:off + ih], owners[off:off + ih]


# --- header and footer ---------------------------------------------------------------------------
def _rd_counts(snap: Snapshot) -> dict[str, int]:
    n: dict[str, int] = {}
    for wk in snap.workers:
        n[wk.state] = n.get(wk.state, 0) + 1
    return n


_rd_ATTN_WORKERS = (FAILED, STALLED)


def _rd_attention_workers(snap: Snapshot) -> list[int]:
    """Indexes of the workers that need a look: failed, stalled. A finished worker with no report is neutral. Snapshot order."""
    return [i for i, wk in enumerate(snap.workers) if wk.state in _rd_ATTN_WORKERS]


def _rd_attention_reviews(snap: Snapshot) -> list[Review]:
    return [r for r in snap.reviews if any(f.prio == 1 for f in r.findings)]


def _rd_attention_count(snap: Snapshot) -> int:
    """The header's ⚠ count: attention workers plus reviews with a P1 finding."""
    return len(_rd_attention_workers(snap)) + len(_rd_attention_reviews(snap))


def _rd_attention_first(snap: Snapshot) -> tuple[str, int]:
    """Where the ! key goes: ("worker", index) for the first attention worker, else ("reviews", -1) for a P1 review,
    else ("", -1)."""
    workers = _rd_attention_workers(snap)
    if workers:
        return "worker", workers[0]
    if _rd_attention_reviews(snap):
        return "reviews", -1
    return "", -1


def _rd_attention_hit(ui: UIState, header: str, snap: Snapshot) -> None:
    """The ⚠ count in the header is a button: a click presses !, which selects the first attention item."""
    label = f"⚠ {_rd_attention_count(snap)}"
    plain = _wd_SGR_RE.sub("", header)
    at = plain.find(label)
    if _rd_attention_count(snap) and at >= 0:
        c0 = _wd_width(plain[:at])
        _rd_hit(ui, 0, 0, c0, c0 + _wd_width(label) - 1, "key", "!")


def _rd_header(snap: Snapshot, ui: UIState, cols: int, t: float, crumb: bool = False, name: str = "") -> str:
    n = _rd_counts(snap)
    title = _wd_clean(name or snap.title).strip()
    attn = _rd_attention_count(snap)
    states = ((RUNNING, "running", "▸", _wd_C_GREEN), (QUIET, "quiet", "◌", _wd_C_AMBER),
              (STALLED, "stalled", "▲", _wd_C_RED), (DONE, "done", "✔", _wd_C_GREEN),
              (FAILED, "failed", "✖", _wd_C_RED), (NOREPORT, "no report", "○", _wd_C_SUB))

    def counts_text(short: bool, older: bool) -> str:
        if short:
            out = " ".join(_wd_c(f"{n[k]}{mk}", col) for k, _, mk, col in states if n.get(k))
        else:
            out = _rd_join([_wd_c(f"{n[k]} {lb}", col) for k, lb, _, col in states if n.get(k)], _rd_muted(" · "))
            if n.get(RUNNING):
                out = _wd_c(_wd_spinner(t), _wd_C_BLUE) + " " + out
        if attn:
            out += "  " + _wd_c(f"⚠ {attn}", _wd_C_RED, bold=True)
        if older and snap.hidden_older > 0 and not snap.all_history:
            out += "  " + _rd_muted(f"+{snap.hidden_older} older")
        return out

    # the station view in global mode opens with the back button, "◆ Station ›"
    crumb_txt = _wd_c(_RD_CRUMB, _wd_C_BLUE, bold=True)
    glyph = crumb_txt + " " if crumb else _wd_c("◆ ", _wd_C_BLUE)
    bare = crumb_txt if crumb else _wd_c("◆", _wd_C_BLUE)
    head_name = _wd_c(_RD_HEADING, _wd_C_TEXT, bold=True)

    def left_of(ttl: str, counts: str, suffix: bool = True) -> str:
        if crumb and ttl:   # "◆ Station › openclaw": the back button, then which station this is
            return glyph + _wd_c(ttl, _wd_C_TEXT, bold=True) + ("   " + counts if counts else "")
        head = glyph + head_name + (_rd_muted(" · " + ttl) if ttl and suffix else "")
        return head + ("   " + counts if counts else "")

    tot: list[str] = []
    if snap.cost_total is not None:   # no token sum: it adds every worker's window together
        tot.append(_rd_sub(_wd_fmt_money(snap.cost_total)))
    clock = _rd_sub(_rd_hms(t))
    totals = _wd_c("Σ ", _wd_C_MUTED) + _rd_join(tot, _rd_dot()) if tot else ""
    muted = _rd_muted("muted ") + clock if ui.muted else clock
    # shed in order: lead text, Σ totals, "+N older", long counts -> marks, then the title
    for use_tot, use_older, short, lead in ((True, True, False, True), (True, True, False, False),
                                            (False, True, False, False), (False, False, False, False),
                                            (False, False, True, False)):
        left = left_of(title, counts_text(short, use_older))
        for right in ((_rd_join([totals, muted], "   ") if use_tot and totals else muted), clock):
            lw, rw = _wd_width(left), _wd_width(right)
            if lw + 3 + rw > cols:
                continue
            if lead and (ui.density == "compact" or ui.view != "overview") and snap.milestones:
                avail = cols - lw - rw - 6
                if avail >= 12:
                    left += "   " + _rd_muted("lead: ") + _rd_sub(_wd_trunc(snap.milestones[-1].text, avail - 6))
            return _wd_fit(_wd_lr(left, right, cols, 2), cols)
    # still too tight: marks, and the title takes the cut
    counts = counts_text(True, False)
    room = cols - _wd_width(clock) - 3 - _wd_width(left_of("", counts)) - 2
    left = left_of(_wd_trunc(title, room) if title and room > 0 else "", counts, False)
    if _wd_width(left) + 2 + _wd_width(clock) > cols:
        left = bare
    return _wd_fit(_wd_lr(left, clock, cols, 2), cols)


def _rd_keyline(main: tuple[tuple[str, str], ...] | list[tuple[str, str]], cols: int,
                ui: UIState | None = None, y: int = 0) -> str:
    """Footer: bracketed buttons for the keys valid here. [?] and [q] always stay; `main` drops from its right end.
    With ui, each button is recorded in the hit map on row y, as a key the click presses."""
    def text(items: list[tuple[str, str]]) -> str:
        return _rd_join([_rd_sub("[" + k + "]") + " " + _rd_muted(v) for k, v in items], "  ")
    items = list(main)
    while items and _wd_width(text(items + list(_rd_KEY_TAIL))) > cols:
        items.pop()
    items += list(_rd_KEY_TAIL)
    if ui is not None:
        x = 0
        for k, v in items:
            w = _wd_width(f"[{k}] {v}")
            _rd_hit(ui, y, y, x, x + w - 1, "key", k)
            x += w + 2
    return _wd_fit(_wd_clip(text(items), cols), cols)


def _rd_station_keys(snap: Snapshot, ui: UIState, sel: int, stage: Stage | None) -> list[tuple[str, str]]:
    """Keys that act on the cursor row here. Esc only means something once a station is open (global view).
    The station pages are offered from the station itself; K only when its lead is live or in background."""
    keys: list[tuple[str, str]] = []
    panel = _rd_panel_sel(snap, ui)
    if panel:
        keys.append(("Enter", "unfold" if panel in ui.folded else "fold"))
    elif 0 <= sel < len(snap.workers):
        wk = snap.workers[sel]
        if _rd_is_live(wk):
            keys.append(("Enter", "fold" if wk.name in ui.expanded else "unfold"))
        else:
            keys.append(("Enter", "collapse" if wk.name in ui.expanded else "expand"))
        keys.append(("Space", "open"))
    if ui.gpage in ("stage", "worker"):
        keys.append(("Esc", "back"))
    panels = _rd_panel_keys(snap)
    if panels or snap.workers:
        keys.append(("↑↓", "move"))
    if "timeline" in panels:
        keys.append(("t", "timeline"))
    if "reviews" in panels:
        keys.append(("r", "reviews"))
    if ui.gpage in ("", "stage"):
        keys += [("L", "lead"), ("C", "cost"), ("F", "files")]
    keys.append(("u", "quota"))
    return keys


def _rd_fold_line(title: str, count: int, cols: int, selected: bool) -> str:
    """A folded panel: its title on one line, with ▸ and a count."""
    mark = _wd_c("▸ ", _wd_C_BLUE if selected else _wd_C_MUTED)
    return _wd_fit(_wd_clip(mark + _rd_title_main(title, str(count)), cols), cols)


def _rd_help_overlay(lines: list[str], cols: int, rows: int, ui: UIState) -> list[str]:
    """The ? box, centred over the frame. Its rows replace the frame rows they cover."""
    help_rows = _rd_HELP
    if not ui.actions_on():
        help_rows = tuple((k, v) for k, v in _rd_HELP if k not in ("s  K", "r  n", "x"))
        help_rows = tuple((k, "ACT  (needs --allow-actions)" if v.startswith("ACT  ") else v)
                          for k, v in help_rows)
    body = [_wd_c(v, _wd_C_BLUE, bold=True) if not k else _rd_sub(_wd_pad(k, 10)) + _rd_muted(v)
            for k, v in help_rows]
    w = min(cols, max(_wd_width(x) for x in body) + 4)
    h = min(rows, len(body) + 2)
    box = _wd_box(body[:max(h - 2, 0)], w, h, _rd_title_main("keys"))
    top, left = max(0, (rows - h) // 2), max(0, (cols - w) // 2)
    out = list(lines)
    for k, row in enumerate(box):
        if top + k < len(out):
            out[top + k] = " " * left + row + " " * max(0, cols - left - w)
    return out


_rd_G_FOOTER_FULL = (("↑↓", "select"), ("→", "unfold"), ("←", "fold"),
                     ("Enter", "toggle"), ("Space", "open page"), ("e", "all finished"), ("a", "all"),
                     ("u", "quota"), ("m", "mute"), ("q", "quit"))


# --- card pieces ---------------------------------------------------------------------------------
def _rd_name_line(wk: Worker, now: float, w: int, t: float, sel: bool,
                  fl: tuple[_wd_Rgb, float] | None, eta: str = "") -> str:
    """Mark, name, model, ETA, elapsed. `w` excludes the spine column, which the caller adds."""
    name_c = _rd_tint(fl, _wd_C_BLUE if sel else _wd_C_TEXT)
    left = _rd_mark(wk, t) + " " + _wd_c(_wd_clean(wk.name), name_c, bold=True)
    right = _rd_join([_wd_c(_wd_clean(display_model(wk.model)), _wd_family_color(wk.family)), eta,
                      _rd_sub(_wd_fmt_dur(_rd_elapsed(wk, now)) if wk.start > 0 else "")], "  ")
    return _wd_lr(left, right, w, 2)


def _rd_title_row(wk: Worker, cw: int) -> str:
    title = _wd_clean(wk.title).strip()
    goal = _wd_clean(wk.goal).strip()
    if title:
        out = _rd_sub(_wd_trunc(title, cw))
        left = cw - _wd_width(title)
        if goal and left >= 18:
            out = _rd_sub(title) + _rd_muted("  ·  goal: " + _wd_trunc(goal, left - 11))
        return out
    return _rd_muted(_wd_trunc("goal: " + goal, cw))


def _rd_stats_row(wk: Worker, cw: int, cap: int) -> str:
    """Output age on the left; token and context facts, then cpu, mem and procs, on the right.
    Narrow cards drop the last fact first."""
    q = max(0.0, wk.quiet_s)
    if wk.state == STALLED:
        last = _wd_c(f"STALLED · no output for {_wd_fmt_mins(q)}", _wd_C_RED, bold=True)
    else:
        col = _wd_C_RED if q >= STALL_S else _wd_C_AMBER if q >= QUIET_S else _wd_C_SUB
        last = _rd_muted("last output ") + _wd_c(_wd_fmt_dur(q) + " ago", col)
    lg = wk.log
    right: list[str] = []
    if lg.tokens_in is not None:
        right.append(_rd_kv("in", _wd_fmt_tok(lg.tokens_in)))
    if lg.tokens_out is not None:
        right.append(_rd_kv("out", _wd_fmt_tok(lg.tokens_out)))
    if lg.ctx_tokens is not None:
        right.append(_rd_kv("ctx", _wd_fmt_tok(lg.ctx_tokens)))
    if wk.cpu is not None:
        right.append(_rd_kv("cpu", f"{wk.cpu:.0f}%"))
    if wk.rss is not None:
        right.append(_rd_kv("mem", _wd_fmt_mem(wk.rss)))
    if wk.nproc is not None:
        right.append(_rd_sub(str(wk.nproc)) + _rd_dim(" procs"))
    if lg.cost is not None:
        right.append(_rd_sub(_wd_fmt_money(lg.cost)))
    if lg.tokens_cache is not None:
        right.append(_rd_kv("cache", _wd_fmt_tok(lg.tokens_cache)))
    lw = _wd_width(last)

    def spark_w() -> int:
        used = _wd_width("  ".join(right)) + 2 if right else 0
        return min(cap, cw - lw - 3 - used)

    while right and spark_w() < 8:
        right.pop()
    sw = spark_w()
    if sw < 6:
        sw = 0
    left = (_wd_spark(wk.activity, sw) + "   " + last) if sw else last
    return _wd_lr(left, "  ".join(right), cw, 2)


def _rd_has_counts(wk: Worker) -> bool:
    return wk.log.errors > 0 or any(wk.log.tool_counts.get(cat, 0) > 0 for cat in TOOL_CATS)


def _rd_counts_row(wk: Worker) -> str:
    lg = wk.log
    left_parts = [_rd_kv(cat, str(lg.tool_counts[cat])) for cat in TOOL_CATS
                  if lg.tool_counts.get(cat, 0) > 0]
    if lg.errors > 0:
        left_parts.append(_rd_kv("err", str(lg.errors), _wd_C_RED))
    return "  ".join(left_parts)


def _rd_step_row(st: Step, cw: int, newest: bool) -> str:
    base = _wd_C_TEXT if newest else _wd_C_SUB
    if st.kind == "error":
        kind, kcol = "error", _wd_C_RED
    elif st.kind == "text":
        kind, kcol = "text", base
    else:
        kind, kcol = _wd_trunc(st.tool or "tool", 7), base
    pad = " " * max(0, 8 - _wd_width(kind))
    text = _wd_trunc(st.text, max(1, cw - 10))
    return _wd_c("› ", _wd_C_MUTED) + _wd_c(kind, kcol) + pad + _wd_c(text, base)


_rd_VERBS = {"read": "reading", "edit": "editing", "shell": "running", "search": "searching"}


_rd_HOME = os.path.expanduser("~")
_rd_SHELL_WRAP = re.compile(r"""^(?:/usr/bin/|/bin/)?(?:ba|z)?sh\s+-l?c\s+(["'])(.*?)(?:\1\s*)?$""", re.DOTALL)
# tools whose step has no useful target: say what they do instead of repeating the tool name
_rd_TOOL_WORDS = {"webfetch": "fetching a web page", "websearch": "searching the web", "glob": "finding files",
                  "skill": "using a skill", "execute": "running a command", "todowrite": "updating its to-do list",
                  "task": "starting a sub-task"}


def _rd_tail(text: str, w: int) -> str:
    """A long path keeps its end, where the file name is, cut at a folder boundary; home shows as ~."""
    if text.startswith(_rd_HOME):
        text = "~" + text[len(_rd_HOME):]
    if _wd_width(text) <= w:
        return text
    tail = text[-(max(w, 2) - 1):]
    cut = tail.find("/")
    return "…" + (tail[cut:] if 0 <= cut < len(tail) - 1 else tail)


def _rd_command(text: str) -> str:
    """The command itself, without a `bash -lc "..."` wrapper (codex wraps every command in one)."""
    m = _rd_SHELL_WRAP.match(text.strip())
    return (m.group(2) if m else text).strip()


def _rd_now_words(wk: Worker, w: int) -> str:
    """What a live worker is doing, as one plain sentence no wider than w: the verb and target of its newest log step."""
    if not wk.log.steps:
        return _wd_trunc("working (output arrives when it finishes)" if _rd_empty_log(wk) else "working", w)
    st = wk.log.steps[-1]
    if st.kind == "text":
        return "thinking"
    if st.kind == "error":
        return _wd_trunc("error: " + st.text, w)
    cat = tool_category(st.tool)
    verb = _rd_VERBS.get(cat, "using " + (st.tool or "a tool"))
    tool = (st.tool or "").lower()
    text = st.text.strip()
    if tool in _rd_TOOL_WORDS and (not text or text.lower() == tool):
        return _wd_trunc(_rd_TOOL_WORDS[tool], w)
    if text.lower() == tool:
        text = ""
    if cat in ("read", "edit"):
        target = _rd_tail(text, w - _wd_width(verb) - 1)
    else:
        target = (_rd_command(text) if cat == "shell" else text).replace(_rd_HOME, "~")
    return _wd_trunc(f"{verb} {target}" if target else verb, w)


def _rd_now_row(wk: Worker, cw: int) -> str:
    """The now-line of a live card: "now  editing src/render.py". The stats row below carries the output age."""
    head = _wd_c("now", _wd_C_MUTED) + "  "
    words = _rd_now_words(wk, max(cw - _wd_width(head), 1))
    return head + _wd_c(words, _wd_C_TEXT)


def _rd_hero(wk: Worker, lvl: int) -> bool:
    """A live card carries its now-line at every level but the smallest: there the name and one step must fit first."""
    return _rd_is_live(wk) and lvl < len(_rd_LEVELS) - 1


def _rd_card_height(wk: Worker, lvl: int) -> int:
    steps, counts, stats, title = _rd_LEVELS[lvl]
    h = 1 + (1 if _rd_hero(wk, lvl) else 0) + (1 if stats else 0) + (1 if counts and _rd_has_counts(wk) else 0)
    if title and (wk.title.strip() or wk.goal.strip()):
        h += 1
    return h + max(1, min(steps, len(wk.log.steps)))


def _rd_card(wk: Worker, lvl: int, w: int, now: float, t: float, sel: bool,
             fl: tuple[_wd_Rgb, float] | None, eta: str = "") -> list[str]:
    """Card rows at width w. The selected card carries the life-spine in column 0; others keep the accent bar."""
    steps, counts, stats, title = _rd_LEVELS[lvl]
    cw = w - 3
    lines = [_rd_name_line(wk, now, w - 1, t, sel, fl, eta)]
    if _rd_hero(wk, lvl):
        lines.append(_rd_now_row(wk, cw))
    if title and (wk.title.strip() or wk.goal.strip()):
        lines.append(_rd_title_row(wk, cw))
    if stats:
        lines.append(_rd_stats_row(wk, cw, 60 if w >= 120 else 30))
    if counts and _rd_has_counts(wk):
        lines.append(_rd_counts_row(wk))
    shown = list(reversed(wk.log.steps))[:steps]
    if not shown:
        lines.append(_rd_muted("› waiting for output"))
    for i, st in enumerate(shown):
        lines.append(_rd_step_row(st, cw, i == 0))
    return _rd_gutter(_rd_edge(wk, t, fl, len(lines), sel), lines)


# --- finished rows -------------------------------------------------------------------------------
def _rd_fin_text(wk: Worker) -> str:
    if wk.state == NOREPORT:
        return _wd_c("finished · no report", _wd_C_AMBER)
    if wk.state == FAILED:
        for st in reversed(wk.log.steps):
            if st.kind == "error":
                return _wd_c(_wd_clean(st.text), _wd_C_RED)
        return _wd_c("failed", _wd_C_RED)
    for line in wk.summary:
        if line.strip():
            return _rd_muted(_wd_clean(line).strip())
    return ""


def _rd_worker_tok(lg: LogState) -> int | None:
    """A worker's own window now (ctx) when its log carries it, else what it has read and written."""
    if lg.ctx_tokens is not None:
        return lg.ctx_tokens
    toks = [x for x in (lg.tokens_in, lg.tokens_out) if x is not None]
    return sum(toks) if toks else None


def _rd_fin_tok(wk: Worker) -> str:
    lg = wk.log
    tok = _rd_worker_tok(lg)
    parts: list[str] = []
    if tok is not None:
        parts.append(_wd_fmt_tok(tok) + " tok")
    if lg.cost is not None:
        parts.append(_wd_fmt_money(lg.cost))
    return "  ".join(parts)


def _rd_fin_rows(fin: list[tuple[int, Worker]], sel: int, w: int, ui: UIState, t: float) -> list[list[str]]:
    """One single-line item per finished worker, columns aligned across the group."""
    nw = min(18, max((_wd_width(_wd_clean(wk.name)) for _, wk in fin), default=4))
    mw = min(16, max((_wd_width(_wd_clean(display_model(wk.model))) for _, wk in fin), default=0))
    toks = [_rd_fin_tok(wk) for _, wk in fin]
    tw = max((len(x) for x in toks), default=0)
    dw = 7
    rest = w - (2 + nw + 2 + mw + 2 + dw + 2 + tw + 2)
    if rest < 12 and tw:
        tw, rest = 0, rest + tw + 2
    if rest < 12 and mw:
        mw, rest = 0, rest + mw + 2
    rows: list[list[str]] = []
    for (idx, wk), tok in zip(fin, toks):
        is_sel = idx == sel
        fl = _rd_flash(ui, wk, t)
        name_c = _rd_tint(fl, _wd_C_BLUE if is_sel else _wd_C_TEXT)
        mark_col = _rd_tint(fl, _rd_mark_color(wk))
        name = _wd_pad(_wd_c(_wd_trunc(wk.name, nw), name_c, bold=is_sel), nw)
        parts = [_wd_c(_rd_mark_char(wk), mark_col) + " " + name]
        if mw:
            mcol = _wd_family_color(wk.family)
            parts.append(_wd_pad(_wd_c(_wd_trunc(display_model(wk.model), mw), mcol), mw))
        dur = _wd_fmt_dur(max(0.0, wk.end - wk.start)) if wk.end is not None and wk.start > 0 else ""
        parts.append(_wd_pad(_rd_sub(dur), dw))
        if tw:
            parts.append(_wd_pad(_rd_sub(tok), tw))
        parts.append(_wd_clip(_rd_fin_text(wk), max(rest, 1)))
        line = _wd_clip("  ".join(parts), w)
        rows.append([_wd_bg_line(line, _wd_C_SELBG, w) if is_sel else line])
    return rows


def _rd_mark_char(wk: Worker) -> str:
    return "✔" if wk.state == DONE else "✖"


def _rd_newest_first(fin: list[tuple[int, Worker]]) -> list[tuple[int, Worker]]:
    """Finished workers newest first: by end time, then start time, whatever order the snapshot gave."""
    return sorted(fin, key=lambda item: (item[1].end or 0.0, item[1].start), reverse=True)


def _rd_workers_word(n: int) -> str:
    return "worker" if n == 1 else "workers"


def _rd_group_head(group: Group, t: float, w: int) -> str:
    """The current group's header above its rows: label, bar, done/total and when it started."""
    width = min(20, max(5, w // 4))
    bar = _wd_gauge_bar(group.done / group.total, width, _wd_C_BLUE, shimmer=int(t * 3) % width if group.live else -1)
    head = _wd_c(_wd_clean(group.label), _wd_C_TEXT, bold=True) + "  " + bar
    rest = _rd_sub(f"{group.done}/{group.total} {_rd_workers_word(group.total)} · started {_wd_fmt_clock(group.started)}")
    return _wd_fit(_wd_clip(head + "  " + rest, w), w)


def _rd_group_line(snap: Snapshot, group: Group, opened: bool, selected: bool, now: float, w: int) -> str:
    """An earlier group on one line: ▸ folded or ▾ open, its label, worker count, ✔ or ✖ with the time it ended (or
    ● running), then how long it ran."""
    failed = any(wk.state == FAILED for wk in snap.workers if wk.name in group.workers)
    mark = _wd_c("▾ " if opened else "▸ ", _wd_C_BLUE if selected else _wd_C_MUTED)
    name = _wd_c(_wd_clean(group.label), _wd_C_BLUE if selected else _wd_C_TEXT, bold=True)
    if group.finished:
        clock = _wd_fmt_clock(group.finished_at) if group.finished_at is not None else ""
        end = _wd_c(f"{'✖' if failed else '✔'} {clock}", _wd_C_RED if failed else _wd_C_GREEN)
        dur = _wd_fmt_dur(group.finished_at - group.started) if group.finished_at is not None else ""
    else:
        end = _wd_c(f"● {group.live} running", _wd_C_GREEN)
        dur = _wd_fmt_dur(now - group.started)
    text = mark + name + _rd_muted(f" · {group.total} {_rd_workers_word(group.total)}") + " " + end + ("  " + _rd_sub(dur) if dur else "")
    return _wd_fit(_wd_clip(text, w), w)


def _rd_earlier_line(snap: Snapshot, earlier: list, opened: bool, selected: bool, w: int) -> str:
    """The one line that folds every earlier group: ▸ earlier · N groups · N workers ✔ done ✖ failed (● running)."""
    wks = [wk for _, g_live, g_fin in earlier for _, wk in g_live + g_fin]
    done = sum(1 for wk in wks if wk.state == DONE)
    failed = sum(1 for wk in wks if wk.state == FAILED)
    noreport = sum(1 for wk in wks if wk.state == NOREPORT)
    live = sum(1 for wk in wks if wk.state in LIVE_STATES)
    mark = _wd_c("▾ " if opened else "▸ ", _wd_C_BLUE if selected else _wd_C_MUTED)
    name = _wd_c("earlier", _wd_C_BLUE if selected else _wd_C_TEXT, bold=True)
    groups = len(earlier)
    text = mark + name + _rd_muted(f" · {groups} group{'s' if groups != 1 else ''} · {len(wks)} {_rd_workers_word(len(wks))}")
    text += "  " + _wd_c(f"✔ {done}", _wd_C_GREEN)
    if failed:
        text += "  " + _wd_c(f"✖ {failed}", _wd_C_RED)
    if noreport:
        text += "  " + _wd_c(f"· {noreport} no report", _wd_C_MUTED)
    if live:
        text += "  " + _wd_c(f"● {live} running", _wd_C_GREEN)
    return _wd_fit(_wd_clip(text, w), w)


def _rd_block_at(blocks: list[tuple[int | str | None, list[str]]], owner: int | str | None,
                 fallback: int | None = None) -> int | None:
    """Index of the block that belongs to owner, else fallback."""
    k = next((k for k, (o, _) in enumerate(blocks) if o == owner), None)
    return k if k is not None else fallback


# --- workers panel -------------------------------------------------------------------------------
def _rd_workers_panel(snap: Snapshot, ui: UIState, sel: int, w: int, h: int, t: float) -> list[str]:
    iw, ih = w - 2, h - 2
    live = [(i, wk) for i, wk in enumerate(snap.workers) if _rd_is_live(wk)]
    fin = _rd_newest_first([(i, wk) for i, wk in enumerate(snap.workers) if not _rd_is_live(wk)])
    running = sum(1 for _, wk in live if wk.state == RUNNING)
    extra = f"{running} running" if running else (f"{len(live)} live" if live else "")
    title = _rd_title_main("workers", extra)
    if ui.density == "full":
        return _rd_workers_full(snap, ui, sel, w, h, t, title)
    service_rows = 1 if snap.services else 0
    content_h = max(0, ih - service_rows)
    if not snap.workers:
        msg = [_rd_muted("all workers resting"), _rd_muted(".stage/ has no workers in this session")]
        top = max(0, (ih - 2) // 2)
        body = [""] * top + [_wd_center(m, iw) for m in msg]
        if snap.services:
            body = body[:content_h]
            body += [""] * max(0, content_h - len(body))
            body.append(_rd_services(snap.services))
        return _wd_box(body, w, h, title)
    nl, nf = len(live), len(fin)

    def total(lvl: int, gaps: bool, fin_rows: int) -> int:
        n = sum(_rd_card_height(wk, lvl) for _, wk in live) + (nl - 1 if gaps and nl > 1 else 0)
        if nf:
            n += fin_rows + (1 if fin_rows < nf else 0) + (1 if nl else 0)
        return n

    fin3 = min(nf, 3)
    configs: list[tuple[int, bool, int]] = [(0, True, nf)]
    configs += [(1, True, k) for k in range(nf, fin3 - 1, -1)]
    configs += [(lv, True, fin3) for lv in range(2, len(_rd_LEVELS))]
    configs += [(len(_rd_LEVELS) - 1, True, k) for k in range(fin3 - 1, -1, -1)]
    configs.append((len(_rd_LEVELS) - 1, False, 0))
    chosen = next((c for c in configs if total(*c) <= content_h), None)
    scroll = chosen is None
    lvl, gaps, fin_rows = chosen if chosen else configs[-1]
    while chosen and fin_rows < nf and total(lvl, gaps, fin_rows + 1) <= content_h:
        fin_rows += 1
    now = snap.now
    items: list[tuple[list[str], int | None]] = []   # (lines, worker index or None)
    for k, (i, wk) in enumerate(live):
        if k and gaps:
            items.append(([""], None))
        card = _rd_card(wk, lvl, iw, now, t, i == sel, _rd_flash(ui, wk, t), _rd_eta_label(snap, wk, now))
        if i == sel:
            card[0] = _wd_bg_line(card[0], _wd_C_SELBG, iw)
        items.append((card, i))
    if nf:
        rows = _rd_fin_rows(fin, sel, iw, ui, t)
        if live:
            items.append(([_wd_c("─" * iw, _wd_C_FAINT)], None))
        if scroll:
            shown = list(range(nf))
        else:
            sel_pos = next((p for p, (i, _) in enumerate(fin) if i == sel), 0)
            first = 0 if sel_pos < fin_rows else min(sel_pos - fin_rows + 1, nf - fin_rows)
            shown = list(range(first, first + fin_rows))
        for p in shown:
            items.append((rows[p], fin[p][0]))
        if not scroll and fin_rows < nf:
            items.append(([_rd_muted(f"+{nf - fin_rows} more finished")], None))
    off = ui.pscroll.get("workers")
    if not scroll and off is None:
        body = [ln for lines, _ in items for ln in lines]
        own = [i for lines, i in items for _ in lines]
        ui.ptop["workers"] = 0
    else:
        body, own, ui.ptop["workers"] = _rd_scroll(items, sel, iw, content_h, off)
        if off is not None:
            ui.pscroll["workers"] = ui.ptop["workers"]
    return _rd_workers_box(snap, ui, body, own, w, h, content_h, title)


def _rd_workers_box(snap: Snapshot, ui: UIState, body: list[str], own: list[int | str | None], w: int, h: int,
                    content_h: int, title: str) -> list[str]:
    """The workers box: rows from the panel, the services line pinned under them, and the hit map of the rows."""
    if snap.services:
        body, own = body[:content_h], own[:content_h]
        body += [""] * max(0, content_h - len(body))
        own += [None] * max(0, content_h - len(own))
        body.append(_rd_services(snap.services))
        own.append(None)
    # Shrink the box to its content: trailing empty rows would otherwise stretch the panel
    # (and push `services` to the bottom) leaving a dead void. The box only keeps its full
    # height when the rows genuinely fill it or the body scrolls.
    trimmed = len(body)
    while trimmed > 0 and not body[trimmed - 1].strip():
        trimmed -= 1
    scrolling = trimmed >= len(body)   # body already fills the box: leave the height alone
    if not scrolling:
        body, own = body[:trimmed], own[:trimmed]
        h = min(h, trimmed + 2)
    _rd_hit(ui, 0, h - 1, 0, w - 1, "scroll", "workers")
    for k, owner in enumerate(own[:h - 2]):
        if isinstance(owner, str):   # an earlier group's line
            _rd_hit(ui, k + 1, k + 1, 1, w - 2, "group", owner[len(_RD_GROUP):])
        elif owner is not None:
            _rd_hit(ui, k + 1, k + 1, 1, w - 2, "select", snap.workers[owner].name)
    return _wd_box(body, w, h, title)


def _rd_services(services: list[tuple[str, bool]]) -> str:
    parts = [_rd_muted("services")]
    for name, up in services:
        dot = _wd_c("●", _wd_C_GREEN) if up else _wd_c("○", _wd_C_MUTED)
        parts.append(dot + " " + _rd_muted(_wd_clean(name)))
    return "  ".join(parts)


def _rd_md_wrap(text: str, width: int, prefix: str = "") -> list[str]:
    spans = _wd_markdown(text)
    plain = "".join(x[0] for x in spans)
    wrapped = _wd_wrap(plain, max(1, width - _wd_width(prefix)))
    out: list[str] = []
    pos = 0
    for idx, line in enumerate(wrapped):
        wanted = line.lstrip()
        if idx:
            pos = plain.find(wanted, pos)
        else:
            pos = plain.find(wanted, pos)
        end = pos + len(wanted)
        span_start = 0
        pieces: list[str] = []
        for value, color, bold in spans:
            span_end = span_start + len(value)
            a, b = max(pos, span_start), min(end, span_end)
            if b > a:
                pieces.append(_wd_c(value[a - span_start:b - span_start], color or _wd_C_SUB, bold=bold))
            span_start = span_end
        out.append((prefix if idx == 0 else " " * _wd_width(prefix)) + "".join(pieces))
        pos = end
    return out or [prefix]


def _rd_finished_block(wk: Worker, selected: bool, now: float, width: int) -> list[str]:
    mark = _rd_mark_char(wk)
    color = _rd_mark_color(wk)
    name_color = _wd_C_BLUE if selected else _wd_C_TEXT
    name = _wd_c(_wd_clean(wk.name), name_color, bold=selected)
    model_text = _wd_clean(display_model(wk.model))
    duration = _wd_fmt_dur(max(0, wk.end - wk.start)) if wk.end is not None else ""
    endtime = _wd_fmt_clock(wk.end) if wk.end is not None else ""
    time_text = duration + (" · " + endtime if endtime else "")
    token_text = _rd_fin_tok(wk)
    avail = max(1, width - 4)
    head = _wd_c(mark, color) + " " + name
    fixed = _wd_width(head) + 4 + _wd_width(time_text)
    include_tokens = bool(token_text) and fixed + 2 + _wd_width(token_text) < avail
    if include_tokens:
        fixed += 2 + _wd_width(token_text)
    model_room = max(0, avail - fixed)
    model = _wd_c(_wd_trunc(model_text, model_room), _wd_family_color(wk.family))
    right = "  ".join(x for x in (model, _rd_sub(time_text),
                                   _rd_dim(token_text) if include_tokens else "") if x)
    if right:
        head = _wd_lr(head, right, avail, 2)
    if selected:
        head = _wd_bg_line(head, _wd_C_SELBG, avail)
    out = ["  " + _wd_fit(head, avail)]
    if wk.title:
        out.append("    " + _wd_c(_wd_clean(wk.title), _wd_C_SUB))
    if wk.state == FAILED:
        error = next((s.text for s in reversed(wk.log.steps) if s.kind == "error"), "failed")
        out.extend("    " + _wd_c(x, _wd_C_RED) for x in _wd_wrap(_wd_clean(error), max(1, width - 6)))
    elif wk.state == NOREPORT:
        out.append("    " + _wd_c("finished · no report", _wd_C_AMBER))
    else:
        for line in wk.summary:
            out.extend(_rd_md_wrap(line, width - 2, _rd_sub("    • ")))
    return out


_rd_NAME_W = 20              # grid name column; the widest name wins up to this
_rd_MODEL_W = 22             # grid model column
_rd_TIME_W = 8               # grid elapsed slot, right-aligned
_rd_MONEY_W = 27             # elapsed 8 + tokens 8 + cost 7, with two gaps of 2
_rd_SUMMARY_MIN = 12         # narrowest space that still shows a finished summary
_rd_DETAIL_LINES = 3         # lines under an expanded finished row (title first)
_rd_STATE_TEXT = {RUNNING: ("running", _wd_C_SUB), QUIET: ("quiet", _wd_C_AMBER), STALLED: ("stalled", _wd_C_RED),
                  DONE: ("done", _wd_C_GREEN), FAILED: ("failed", _wd_C_RED), NOREPORT: ("no report", _wd_C_SUB)}


def _rd_rjust(s: str, n: int) -> str:
    return " " * max(0, n - _wd_width(s)) + s


def _rd_grid_mode(w: int, nw: int, mw: int) -> tuple[bool, bool, bool]:
    """(model, tokens and cost, summary) shown at content width w. Dropped in this order: summary, model, tokens and cost."""
    for model, money, summary in ((True, True, True), (True, True, False), (False, True, False), (False, False, False)):
        need = 2 + nw + ((2 + mw) if model and mw else 0) + (_rd_MONEY_W if money else _rd_TIME_W)
        if summary:
            need += 4 + _rd_SUMMARY_MIN
        if need <= w:
            return model, money, summary
    return False, False, False


def _rd_grid_flex(wk: Worker, show_sum: bool, fw: int, eta: str = "") -> str:
    """Summary (or error) for a finished row when the mode shows it; otherwise the state word, then a live worker's ETA."""
    if show_sum and wk.state == FAILED:
        error = next((s.text for s in reversed(wk.log.steps) if s.kind == "error"), "failed")
        return _wd_clip(_wd_c(_wd_clean(error), _wd_C_RED), fw)
    if show_sum and wk.state == DONE:
        line = next((_wd_clean(s).strip() for s in wk.summary if s.strip()), "")
        if line:
            return _wd_clip(_rd_muted(line), fw)
    if eta and wk.state == RUNNING:
        return _wd_clip(eta, fw)
    word, col = _rd_STATE_TEXT[wk.state]
    return _wd_clip(_wd_c(word, col) + (_rd_dot() + eta if eta else ""), fw)


def _rd_grid_row(wk: Worker, t: float, now: float, nw: int, mw: int, mode: tuple[bool, bool, bool],
                 w: int, sel: bool, fl: tuple[_wd_Rgb, float] | None, eta: str = "") -> str:
    """One worker on the grid, live or finished: mark, name, model, summary or state and ETA, then elapsed, tokens, cost."""
    show_model, show_money, show_sum = mode
    name_c = _rd_tint(fl, _wd_C_BLUE if sel else _wd_C_TEXT)
    left = _rd_mark(wk, t) + " " + _wd_pad(_wd_c(_wd_trunc(_wd_clean(wk.name), nw), name_c, bold=sel), nw)
    if show_model and mw:
        left += "  " + _wd_pad(_wd_c(_wd_trunc(_wd_clean(display_model(wk.model)), mw), _wd_family_color(wk.family)), mw)
    lg = wk.log
    elapsed = _wd_fmt_dur(_rd_elapsed(wk, now)) if wk.start > 0 else ""
    cluster = _rd_rjust(_rd_sub(elapsed), _rd_TIME_W)
    if show_money:
        wtok = _rd_worker_tok(lg)
        tok = _wd_fmt_tok(wtok) if wtok is not None else ""
        cost = _wd_fmt_money(lg.cost) if lg.cost is not None else ""
        cluster += "  " + _rd_rjust(_rd_sub(tok), 8) + "  " + _rd_rjust(_rd_sub(cost), 7)
    room = w - _wd_width(left) - _wd_width(cluster)
    if room - 4 >= 4:
        flex = _rd_grid_flex(wk, show_sum, room - 4, eta)
        return _wd_clip(left + "  " + _wd_pad(flex, room - 4) + "  " + cluster, w)
    return _wd_clip(left + " " * max(room, 0) + cluster, w)


def _rd_grid_detail(wk: Worker, w: int) -> list[str]:
    """Lines under an expanded finished row: the summary, or the error, wrapped to the row."""
    cw = max(1, w - 4)
    body: list[str] = []
    if wk.state == FAILED:
        error = next((s.text for s in reversed(wk.log.steps) if s.kind == "error"), "failed")
        body += [_wd_c(x, _wd_C_RED) for x in _wd_wrap(_wd_clean(error), cw)]
    elif wk.state == NOREPORT:
        body.append(_wd_c("finished · no report", _wd_C_AMBER))
    else:
        for line in wk.summary:
            if line.strip():
                body += [_rd_muted(x) for x in _wd_wrap(_wd_clean(line).strip(), cw)]
    return ["    " + x for x in body[:_rd_DETAIL_LINES]]


def _rd_fin_window(blocks: list[tuple[int | str | None, list[str]]], sel_k: int | None,
                   room: int, tail: str = "more finished") -> list[tuple[int | str | None, str]]:
    """Finished rows that fit in room, newest first, each with the worker it belongs to. The selected row stays in
    view; the last line counts the rest."""
    if room <= 0 or not blocks:
        return []
    if sum(len(b) for _, b in blocks) <= room:
        return [(owner, ln) for owner, b in blocks for ln in b]
    cap = room - 1
    start = 0
    if sel_k is not None and sum(len(b) for _, b in blocks[:sel_k + 1]) > cap:
        start = sel_k
    out: list[tuple[int | str | None, str]] = []
    shown = 0
    for k in range(start, len(blocks)):
        owner, block = blocks[k]
        block = block[:1] if k == start and len(block) > cap else block
        if len(out) + len(block) > cap:
            break
        out += [(owner, ln) for ln in block]
        shown += 1
    return out + [(None, _rd_muted(f"+{len(blocks) - shown} {tail}"))]


def _rd_worker_lines(snap: Snapshot, ui: UIState, sel: int, iw: int, avail: int,
                     t: float) -> tuple[list[str], list[int | str | None]]:
    """Workers panel body and, for each row, the worker index it belongs to, the "group:<key>" of an earlier group's
    line, or None (rules, headers, counts). The current group comes first: its header, its live rows (cards when open)
    and its finished rows. Earlier groups follow, newest first, one line each until the user opens one. Workers in
    ui.expanded show their card (live) or summary (finished); every other worker is one aligned row."""
    now = snap.now
    current, live, fin, earlier = _rd_plan(snap, ui)
    cursor = _rd_cursor_stop(snap, ui)
    nw = min(_rd_NAME_W, max(_wd_width(_wd_clean(wk.name)) for wk in snap.workers))
    mw = min(_rd_MODEL_W, max(_wd_width(_wd_clean(display_model(wk.model))) for wk in snap.workers))
    w = iw - 1                      # content width; the first cell is the rail
    mode = _rd_grid_mode(w, nw, mw)
    rail = _wd_c("▎", _wd_C_BLUE)

    def row(i: int, wk: Worker, bar: str = " ", expanded: bool = False) -> str:
        # an expanded finished row shows its state here; its summary is the detail below
        m = (mode[0], mode[1], False) if expanded else mode
        line = _rd_grid_row(wk, t, now, nw, mw, m, w, i == sel, _rd_flash(ui, wk, t), _rd_eta_label(snap, wk, now))
        if i == sel:
            line = _wd_bg_line(line, _wd_C_SELBG, w)
        return bar + line

    def worker_block(i: int, wk: Worker) -> list[str]:
        # a finished worker shows its summary below its row while it is in ui.expanded
        opened = wk.name in ui.expanded
        block = [row(i, wk, rail if i == sel else " ", opened)]
        if opened:
            block += [rail + ln for ln in _rd_grid_detail(wk, w)]
        return block

    body: list[str] = []
    own: list[int | str | None] = []

    def put(lines: list[str], owner: int | str | None) -> None:
        body.extend(lines)
        own.extend([owner] * len(lines))

    for group in current:
        put([_rd_group_head(group, t, iw)], None)
    keep = 1 if fin else 0          # the "+N more finished" line
    # the richest card level at which every open live card and every other row still fit
    room = avail - len(body) - keep
    open_live = [(i, wk) for i, wk in live if wk.name in ui.expanded]
    flashes = {i: _rd_flash(ui, wk, t) for i, wk in open_live}
    rests: dict[int, tuple[list[str], tuple[_wd_Rgb, float] | None]] = {}
    for lvl in range(1, len(_rd_LEVELS)):
        rests = {i: (_rd_card(wk, lvl, iw, now, t, True, flashes[i]), flashes[i]) for i, wk in open_live}
        if sum(len(rest) for rest, _ in rests.values()) + len(live) - len(open_live) <= room:
            break
    # open cards first, then the one-line rows; when the rows do not all fit, the tail becomes "+N more running"
    live_lines: list[tuple[int, list[str]]] = []
    for i, wk in [(i, wk) for i, wk in live if i in rests] + [(i, wk) for i, wk in live if i not in rests]:
        if i in rests:
            rest, fl = rests[i]
            # the grid row replaces the card's name row and takes its spine glyph, timed for this card's height
            live_lines.append((i, [row(i, wk, _rd_spine(wk, t, len(rest), fl)[0])] + rest[1:]))
        else:
            live_lines.append((i, [row(i, wk, rail if i == sel else " ")]))
    if sum(len(lines) for _, lines in live_lines) <= room:
        for owner, lines in live_lines:
            put(lines, owner)
    else:
        show = 0
        used = 0
        for _, lines in live_lines:   # one row stays free for the "+N more running" line
            if used + len(lines) > room - 1:
                break
            used += len(lines)
            show += 1
        for owner, lines in live_lines[:show]:
            put(lines, owner)
        if room >= 1:
            put([_rd_muted(f"+{len(live_lines) - show} more running")], None)
    room = avail - len(body)
    if live and fin and room >= 2:   # a rule between the live rows and the finished ones
        put([_wd_c("─" * iw, _wd_C_FAINT)], None)
        room -= 1
    fin_blocks = [(i, worker_block(i, wk)) for i, wk in fin]
    earlier_blocks: list[tuple[int | str | None, list[str]]] = []
    if earlier:
        all_key = _RD_GROUP + _RD_EARLIER
        earlier_blocks.append((all_key, [_rd_earlier_line(snap, earlier, _RD_EARLIER in ui.group_open,
                                                          cursor == all_key, iw)]))
    for group, g_live, g_fin in earlier if _RD_EARLIER in ui.group_open else []:
        key = _RD_GROUP + group.key
        opened = group.key in ui.group_open
        earlier_blocks.append((key, [_rd_group_line(snap, group, opened, cursor == key, now, iw)]))
        if opened:
            earlier_blocks += [(i, worker_block(i, wk)) for i, wk in g_live + g_fin]
    sel_owner: int | str | None = int(cursor[1:]) if cursor.startswith("w") else cursor
    open_k = next((k for k, (_, wk) in enumerate(fin) if wk.name in ui.expanded), None)
    # the current group's finished rows keep at most half of what is left; the earlier groups get the rest
    f_need = sum(len(block) for _, block in fin_blocks)
    e_need = sum(len(block) for _, block in earlier_blocks)
    f_room = room if f_need + e_need <= room else max(min(f_need, room // 2), room - e_need)
    for owner, line in _rd_fin_window(fin_blocks, _rd_block_at(fin_blocks, sel_owner, open_k), f_room):
        put([line], owner)
    room = avail - len(body)
    if earlier_blocks and room >= 2:
        put([_wd_c("─" * iw, _wd_C_FAINT)], None)
        room -= 1
    for owner, line in _rd_fin_window(earlier_blocks, _rd_block_at(earlier_blocks, sel_owner), room, "more"):
        put([line], owner)
    return body[:avail], own[:avail]


def _rd_workers_full(snap: Snapshot, ui: UIState, sel: int, w: int, h: int, t: float, title: str) -> list[str]:
    iw, ih = w - 2, h - 2
    content_h = max(0, ih - (1 if snap.services else 0))
    if snap.workers:
        body, own = _rd_scroll_rows(ui, "workers", lambda n: _rd_worker_lines(snap, ui, sel, iw, n, t), content_h)
    else:
        body = [_rd_muted("all workers resting"), _rd_muted(".stage/ has no workers in this session")]
        own = [None] * len(body)
    return _rd_workers_box(snap, ui, body, own, w, h, content_h, title)


def _rd_scroll(items: list[tuple[list[str], int | None]], sel: int, iw: int, ih: int,
               off: int | None) -> tuple[list[str], list[int | None], int]:
    """Window over the items that keeps the selected worker visible, with `▲ n more` / `▼ n more`.
    With off (the wheel's row offset) it is a plain window over every row. Returns the rows, their workers, the top row."""
    flat = [(ln, i) for lines, i in items for ln in lines]
    if off is not None:
        top = min(max(off, 0), max(0, len(flat) - ih))
        return [ln for ln, _ in flat[top:top + ih]], [i for _, i in flat[top:top + ih]], top
    sel_item = next((k for k, (_, i) in enumerate(items) if i == sel), 0)

    def window(start: int) -> tuple[int, bool]:
        budget = ih - (1 if start > 0 else 0)
        used = 0
        end = start
        while end < len(items) and used + len(items[end][0]) <= budget:
            used += len(items[end][0])
            end += 1
        if end < len(items):   # something hidden below: reserve the `▼` line
            while end > start and used + 1 > budget:
                end -= 1
                used -= len(items[end][0])
        return end, end < len(items)

    start = 0
    while start < sel_item:
        end, _ = window(start)
        if sel_item < end:
            break
        start += 1
    end, more_below = window(start)
    end = max(end, start + 1)
    top = sum(len(items[k][0]) for k in range(start))
    out: list[str] = []
    own: list[int | None] = []
    hidden_up = sum(1 for _, i in items[:start] if i is not None)
    if start > 0:
        out.append(_rd_muted(f"▲ {hidden_up} more"))
        own.append(None)
    for lines, i in items[start:end]:
        out.extend(lines)
        own.extend([i] * len(lines))
    if more_below:
        hidden_dn = sum(1 for _, i in items[end:] if i is not None)
        out, own = out[:ih - 1], own[:ih - 1]
        out.append(_rd_muted(f"▼ {hidden_dn} more"))
        own.append(None)
    return out[:ih], own[:ih], top


# --- timeline ------------------------------------------------------------------------------------
class _rd_Item:
    """One timeline bar: a worker or a review."""

    def __init__(self, start: float, end: float, obj: Worker | Review, live: bool) -> None:
        self.start = start
        self.end = end
        self.obj = obj
        self.live = live
        self.review: Review | None = None   # a review of the same name folded into a worker's lane
        self.xs = 0
        self.xe = 0
        self.label = ""


def _rd_tl_range(snap: Snapshot) -> tuple[float, float]:
    now = snap.now
    s0 = snap.session_start
    if s0 <= 0 or s0 >= now:
        starts = [wk.start for wk in snap.workers if wk.start > 0]
        starts += [r.start for r in snap.reviews if r.start > 0]
        s0 = min(starts) if starts else now - 60
        if s0 >= now:
            s0 = now - 60
    return s0, max(now - s0, 1.0)


def _rd_review_counts(r: Review) -> tuple[int, int, int]:
    return (sum(1 for f in r.findings if f.prio == 1), sum(1 for f in r.findings if f.prio == 2),
            sum(1 for f in r.findings if f.prio >= 3))


def _rd_tl_items(snap: Snapshot, iw: int) -> list[_rd_Item]:
    s0, span = _rd_tl_range(snap)
    now = snap.now
    items: list[_rd_Item] = []
    folded: set[str] = set()
    for wk in snap.workers:
        it = _rd_Item(wk.start, wk.end if wk.end is not None else now, wk, wk.end is None)
        if wk.is_review and wk.name not in folded:
            it.review = next((r for r in snap.reviews if r.name == wk.name), None)
            if it.review is not None:
                folded.add(wk.name)
        items.append(it)
    for r in snap.reviews:
        if r.name not in folded:
            items.append(_rd_Item(r.start, now if r.running else r.end, r, r.running))
    items.sort(key=lambda it: it.start)
    last = max(iw - 1, 0)
    for it in items:
        st = max(it.start, s0)
        en = min(max(it.end, st), now)
        it.xs = min(last, max(0, int((st - s0) / span * last)))
        it.xe = min(last, max(it.xs, int((en - s0) / span * last)))
        lab = _wd_trunc(it.obj.name, 16)
        rev = it.obj if isinstance(it.obj, Review) else it.review
        if rev is not None:
            lab += " " + "·".join(str(n) for n in _rd_review_counts(rev))
        it.label = lab
    return items


def _rd_tl_pack(items: list[_rd_Item], cap: int) -> tuple[list[list[_rd_Item]], int]:
    """Lanes of items (one per lane when they fit, else packed greedily); drops the oldest when needed."""
    if len(items) <= cap:
        return [[it] for it in items], 0
    kept = list(items)
    while True:
        free: list[int] = []
        lanes: list[list[_rd_Item]] = []
        for it in kept:
            end = it.xe + 2 + _wd_width(it.label)
            for k, f in enumerate(free):
                if f < it.xs:
                    free[k] = end
                    lanes[k].append(it)
                    break
            else:
                free.append(end)
                lanes.append([it])
        if len(lanes) <= cap or len(kept) <= 1:
            return lanes[:cap], len(items) - len(kept)
        kept.remove(min(kept, key=lambda it: it.end))   # the item whose activity ended longest ago


def _rd_tick_step(span: float, iw: int) -> int:
    for minutes in (1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440):
        if minutes * 60 / span * max(iw - 1, 1) >= 12:
            return minutes
    return 1440


def _rd_timeline_panel(snap: Snapshot, ui: UIState, sel: int, w: int, h: int, t: float,
                       lanes: list[list[_rd_Item]], dropped: int) -> list[str]:
    if "timeline" in ui.folded:
        count = sum(len(lane) for lane in lanes) + dropped
        _rd_hit(ui, 0, 0, 0, w - 1, "toggle_panel", "timeline")
        return [_rd_fold_line("timeline", count, w, _rd_panel_sel(snap, ui) == "timeline")]
    iw, ih = w - 2, h - 2
    s0, span = _rd_tl_range(snap)
    now = snap.now
    extra = f"{_wd_fmt_clock(s0)} → now ({_wd_fmt_mins(span) if span >= 60 else _wd_fmt_dur(span)})"
    if dropped:
        extra += f"  +{dropped} earlier"
    title = _rd_title_main("timeline", extra)
    last = max(iw - 1, 1)
    # axis
    axis = [" "] * iw
    step = _rd_tick_step(span, iw) * 60
    try:
        off = time.localtime(s0).tm_gmtoff or 0
    except (OverflowError, OSError, ValueError):
        off = 0
    tick = math.ceil((s0 + off) / step) * step - off
    while tick <= now:
        x = int((tick - s0) / span * last)
        label = "│" + _wd_fmt_clock(tick)
        if x + len(label) <= iw:
            for k, ch in enumerate(label):
                axis[x + k] = _wd_c(ch, _wd_C_MUTED)
        tick += step
    # milestones
    mrow = [" "] * iw
    for ms in snap.milestones:
        if ms.ts is not None and s0 <= ms.ts <= now:
            mrow[int((ms.ts - s0) / span * last)] = _wd_c("⚑", _wd_C_VIOLET)
    lines = ["".join(axis), "".join(mrow)]
    sel_obj = snap.workers[sel] if 0 <= sel < len(snap.workers) else None
    head = "╸" if int(t * 2) % 2 == 0 else "▶"
    cap = max(ih - 2, 0)
    off = min(max(ui.pscroll.get("timeline", 0), 0), max(0, len(lanes) - cap))   # wheel offset, in lanes
    if "timeline" in ui.pscroll:
        ui.pscroll["timeline"] = off
    ui.ptop["timeline"] = off
    for lane in lanes[off:off + cap]:
        cells: list[str | None] = [None] * iw
        spans: list[tuple[int, int]] = []
        for it in lane:
            _rd_tl_bar(cells, it, it.obj is sel_obj, head)
            spans.append((it.xs, it.xe))
        for it in lane:
            lw = _wd_width(it.label)
            sel_it = it.obj is sel_obj
            col = _wd_C_TEXT if sel_it else _wd_C_SUB
            rev = it.obj if isinstance(it.obj, Review) else it.review
            tag = " " + _rd_review_tag(rev) if rev is not None else ""
            name = _wd_trunc(it.obj.name, 16)
            lab = _wd_c(name, col, bold=sel_it) + tag
            x0 = -1
            for cand in (it.xe + 2, it.xs - 1 - lw):
                if 0 <= cand and cand + lw <= iw and all(cells[x] is None for x in range(cand, cand + lw)):
                    x0 = cand
                    break
            if x0 < 0:
                if it.xe - it.xs + 1 < lw + 4:
                    continue
                # fits neither side: write it over the left end of the bar
                x0 = it.xs + 1
                lab = _wd_c(name, _wd_C_TEXT, bold=True) + tag
                cells[x0 - 1] = " "
                cells[x0 + lw] = " "
            cells[x0] = lab
            for x in range(x0 + 1, x0 + lw):
                cells[x] = ""
        lines.append("".join(" " if c is None else c for c in cells))
    _rd_hit(ui, 0, h - 1, 0, w - 1, "scroll", "timeline")
    _rd_hit(ui, 0, 0, 0, w - 1, "toggle_panel", "timeline")
    return _wd_box(lines, w, h, title, _wd_C_BLUE if _rd_panel_sel(snap, ui) == "timeline" else _wd_C_BORDER)


def _rd_review_tag(r: Review) -> str:
    p1, p2, p3 = _rd_review_counts(r)
    dot = _wd_c("·", _wd_C_MUTED)
    return _wd_c(str(p1), _wd_C_RED) + dot + _wd_c(str(p2), _wd_C_AMBER) + dot + _wd_c(str(p3), _wd_C_GREEN)


def _rd_tl_bar(cells: list[str | None], it: _rd_Item, selected: bool, head: str) -> None:
    obj = it.obj
    fam = _wd_family_color(obj.family)
    if it.live:
        col = _wd_lerp(fam, _wd_C_TEXT, 0.35) if selected else fam
        for x in range(it.xs, it.xe):
            cells[x] = _wd_c("━", col)
        cells[it.xe] = _wd_c(head, col)
        return
    col = fam if selected else _wd_lerp(fam, _wd_C_DIM, 0.4)
    for x in range(it.xs, it.xe):
        cells[x] = _wd_c("━", col, bold=selected)
    if isinstance(obj, Worker):
        mark_col = _rd_mark_color(obj)
        ch = _rd_mark_char(obj)
    else:
        mark_col, ch = _wd_C_GREEN, "✔"
    cells[it.xe] = _wd_c(ch, mark_col)


def _rd_timeline_compact(snap: Snapshot, w: int) -> str:
    s0, span = _rd_tl_range(snap)
    n = max(8, min(40, w - 20))
    spans = [(wk.start, wk.end if wk.end is not None else snap.now) for wk in snap.workers]
    counts = []
    for i in range(n):
        a = s0 + span * i / n
        b = s0 + span * (i + 1) / n
        counts.append(sum(1 for st, en in spans if st < b and en >= a))
    spark = _wd_spark_levels(_wd_levels(counts, 7.0))
    length = _wd_fmt_mins(span) if span >= 60 else _wd_fmt_dur(span)
    return _rd_muted("timeline ") + spark + " " + _rd_muted(length)


# --- reviews -------------------------------------------------------------------------------------
_rd_PRIO_COLORS = {1: _wd_C_RED, 2: _wd_C_AMBER, 3: _wd_C_GREEN}


def _rd_prio_color(p: int) -> _wd_Rgb:
    return _rd_PRIO_COLORS.get(min(max(p, 1), 3), _wd_C_GREEN)


def _rd_sorted_findings(r: Review) -> list[Finding]:
    return sorted(r.findings, key=lambda f: f.prio)


def _rd_reviews_natural(snap: Snapshot) -> int:
    return 2 + sum(1 + len(r.findings) for r in snap.reviews)


def _rd_reviews_panel(snap: Snapshot, ui: UIState, w: int, h: int, t: float) -> list[str]:
    revs = snap.reviews
    if "reviews" in ui.folded:
        _rd_hit(ui, 0, 0, 0, w - 1, "toggle_panel", "reviews")
        return [_rd_fold_line("reviews", len(revs), w, _rd_panel_sel(snap, ui) == "reviews")]
    iw, ih = w - 2, h - 2
    if "reviews" in ui.pscroll:   # wheel: every line, windowed from the offset
        lines = _rd_reviews_lines(snap, iw, _RD_TALL, t)
        off = min(max(ui.pscroll["reviews"], 0), max(0, len(lines) - ih))
        ui.pscroll["reviews"] = ui.ptop["reviews"] = off
        lines = lines[off:off + ih]
    else:
        lines = _rd_reviews_lines(snap, iw, ih, t)
        ui.ptop["reviews"] = 0
    color = _wd_C_BLUE if _rd_panel_sel(snap, ui) == "reviews" else _wd_C_BORDER
    ts = ui.flashes.get("reviews")
    if ts is not None and 0 <= t - ts < 3:
        color = _wd_lerp(_wd_C_RED, _wd_C_BORDER, (t - ts) / 3)
    _rd_hit(ui, 0, h - 1, 0, w - 1, "scroll", "reviews")
    _rd_hit(ui, 0, 0, 0, w - 1, "toggle_panel", "reviews")
    return _wd_box(lines, w, h, _rd_title_main("reviews", str(len(revs))), color)


def _rd_reviews_lines(snap: Snapshot, iw: int, ih: int, t: float) -> list[str]:
    """Review rows for a panel `ih` rows tall: a header per review, as many findings as the room allows."""
    revs = snap.reviews
    # lines per review: header + as many findings as the space allows
    alloc = [0] * len(revs)
    room = ih - len(revs)
    k = 0
    while room > 0 and any(alloc[i] < len(revs[i].findings) for i in range(len(revs))):
        i = k % len(revs)
        if alloc[i] < len(revs[i].findings):
            alloc[i] += 1
            room -= 1
        k += 1
    lines: list[str] = []
    shown_revs = revs if ih >= len(revs) else revs[:max(ih - 1, 0)]
    bars = _rd_review_bars_fit(revs, iw, t)
    for ri, r in enumerate(shown_revs):
        lines.append(_rd_review_head(r, iw, t, bars))
        fs = _rd_sorted_findings(r)
        a = alloc[ri]
        if a and a < len(fs):
            hidden = len(fs) - (a - 1)
            fs, tail = fs[:a - 1], _rd_muted(f"  +{hidden} more")
        else:
            fs, tail = fs[:a], ""
        for f in fs:
            col = _rd_prio_color(f.prio)
            tag = _wd_c(f"P{min(max(f.prio, 1), 9)}", col)
            lines.append("  " + tag + " " + _rd_sub(_wd_trunc(f.text, iw - 5)))
        if tail:
            lines.append(tail)
    if len(shown_revs) < len(revs):
        lines.append(_rd_muted(f"+{len(revs) - len(shown_revs)} more"))
    return lines


def _rd_review_when(r: Review, t: float) -> str:
    if r.running:
        return _wd_c(_wd_spinner(t), _wd_family_color(r.family)) + _rd_sub(" running")
    return _rd_dim(_wd_fmt_clock(r.end))


def _rd_review_bars(r: Review) -> str:
    """The outcome at a glance: "clean" in green, else "P1 n  P2 n  P3 n" (P1 red, P2 amber). Blank while a review runs
    with no finding yet."""
    counts = _rd_review_counts(r)
    if not any(counts):
        return "" if r.running else _wd_c("clean", _wd_C_GREEN)
    return "  ".join(_wd_c(f"P{pr} {n}", _rd_prio_color(pr)) for pr, n in zip((1, 2, 3), counts))


def _rd_review_bars_fit(revs: list[Review], iw: int, t: float) -> bool:
    """True when every review keeps its time and counts with a (cut) name; decided once per panel."""
    for r in revs:
        need = min(_wd_width(_wd_clean(r.name)), 10) + 2 + _wd_width(_rd_review_when(r, t))
        if need + 2 + _wd_width(_rd_review_bars(r)) > iw:
            return False
    return True


def _rd_review_head(r: Review, iw: int, t: float, bars: bool = True) -> str:
    when = _rd_review_when(r, t)
    right = _rd_review_bars(r) if bars else ""
    room = iw - (_wd_width(right) + 2 if right else 0) - _wd_width(when) - 2   # for name + model
    name = _wd_clean(r.name)
    nw = min(_wd_width(name), max(room, 1))
    model = _wd_clean(display_model(r.model))
    mw = room - nw - 2
    parts = [_wd_c(_wd_trunc(name, nw), _wd_C_TEXT, bold=True)]
    if model and mw >= 3:
        parts.append(_wd_c(_wd_trunc(model, mw), _wd_family_color(r.family)))
    left = _rd_join(parts + [when], "  ")
    pad = max(iw - _wd_width(left) - _wd_width(right), 0)
    return left + " " * pad + right


def _rd_reviews_compact(snap: Snapshot) -> str:
    p = [0, 0, 0]
    for r in snap.reviews:
        for f in r.findings:
            p[min(max(f.prio, 1), 3) - 1] += 1
    parts = [_rd_muted("reviews ") + _rd_sub(str(len(snap.reviews)))]
    for i, n in enumerate(p):
        if n:
            parts.append(_wd_c(f"P{i + 1} {n}", _rd_prio_color(i + 1)))
    return _rd_join(parts, _rd_dot())


# --- quota ---------------------------------------------------------------------------------------
def _rd_tightest(acct: QuotaAccount) -> tuple[str, float, float | None, bool] | None:
    if not acct.windows:
        return None
    win = max(acct.windows, key=lambda x: x.used)
    return win.name, win.used, win.pace, win.stale


_RD_WINDOW_SECS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400, "thirty_day": 30 * 86400,
                   "month_total": 30 * 86400, "month_auto": 30 * 86400, "month_api": 30 * 86400}


def _rd_window_secs(win: QuotaWindow) -> float:
    """Rough length of a window in seconds, for the 'shortest wins a tie' rule. Unknown -> weekly."""
    key = win.window or win.name.lower()
    return float(_RD_WINDOW_SECS.get(key, 7 * 86400))


def _rd_quota_winner(acct: QuotaAccount) -> QuotaWindow | None:
    """The window that constrains the account right now (binding constraint), layered:
    a capped window first, then the earliest projected run-out, then the highest use, then the
    shortest window (5h over weekly). None when the account has no windows."""
    wins = acct.windows
    if not wins:
        return None
    capped = [w for w in wins if w.used >= 100.0]
    if capped:
        return max(capped, key=lambda w: w.used)
    projected = [w for w in wins if w.runs_out_at is not None]
    if projected:
        return min(projected, key=lambda w: w.runs_out_at)   # type: ignore[arg-type,return-value]
    return max(wins, key=lambda w: (w.used, -_rd_window_secs(w)))


def _rd_ttr(resets_at: float | None, now: float) -> str:
    """Time to reset, compact two units: `2h14m`, `3d4h`, `45m`, `<1m`, `now` once elapsed.
    Blank when there is no reset time."""
    if resets_at is None:
        return ""
    secs = resets_at - now
    if secs <= 0:
        return "now"
    if secs < 60:
        return "<1m"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 24:
        rem = mins % 60
        return f"{hours}h" + (f"{rem}m" if rem else "")
    days = hours // 24
    rem = hours % 24
    return f"{days}d" + (f"{rem}h" if rem else "")


def _rd_openrouter_worker(wk: Worker) -> bool:
    """True when the worker runs on OpenRouter (deepseek-or): log model prefix or picker id."""
    raw = (wk.log.model or wk.model or "").lower()
    return raw.startswith(("openrouter/", "deepseek-or@")) or raw == "deepseek-or"


def _rd_spend_chip(snap: SpendSnapshot) -> tuple[str, bool, bool]:
    """(text without ANSI, alert colour, stale) for the masthead usage line."""
    name = snap.provider or OPENROUTER_ACCOUNT
    if snap.balance is not None:
        txt = f"{name} ${snap.balance:.2f}"
        if snap.runs_out_at:
            txt += " · out " + time.strftime("%b %d", time.gmtime(snap.runs_out_at))
        alert = snap.low or (snap.floor_usd is not None and snap.balance < snap.floor_usd)
        return (txt, alert, snap.stale)
    if snap.month_to_date_usd is not None:
        return (f"{name} ${snap.month_to_date_usd:.2f} mtd", False, snap.stale)
    if name == OPENROUTER_ACCOUNT:
        return (f"{OPENROUTER_ACCOUNT} –", False, snap.stale)
    return (f"{name} –", False, snap.stale)


def _rd_openrouter_chip(snap: SpendSnapshot) -> tuple[str, bool, bool]:
    return _rd_spend_chip(snap)


def _rd_spend_chip_paint(snap: SpendSnapshot) -> str:
    text, alert, stale = _rd_spend_chip(snap)
    body = _wd_c(text, _wd_C_RED if alert else _wd_C_TEXT, bold=True)
    if stale:
        body += _rd_dim(" stale")
    return body


def _rd_openrouter_chip_paint(snap: SpendSnapshot) -> str:
    return _rd_spend_chip_paint(snap)


def _rd_spend_masthead_chip(snap: SpendSnapshot) -> bool:
    return snap.balance is not None or snap.month_to_date_usd is not None


def _rd_credit_text(c: QuotaCredit) -> str:
    if c.balance is None:
        return "no balance"
    txt = f"${c.balance:.2f} left"
    if c.burn_per_day:
        txt += f" · ${c.burn_per_day:.2f}/d"
    if c.runs_out_at:
        txt += " · out " + time.strftime("%b %d", time.localtime(c.runs_out_at))
    return txt


def _rd_credit_line(dot: str, name: str, c: QuotaCredit) -> str:
    txt = _rd_credit_text(c)
    return dot + " " + name + " " + (_wd_c(txt + " LOW", _wd_C_RED, bold=True) if c.low else _rd_muted(txt))


def _rd_extra_text(e: QuotaExtra) -> str:
    if e.usd is None:
        return "capped"
    return f"capped · extra ${e.usd:.2f}" + (" est" if e.method == "estimated" else "")


def _rd_quota_compact(snap: Snapshot) -> str:
    parts: list[str] = []
    for a in snap.quota:
        nm = _wd_c(_wd_clean(a.name), _wd_C_TEXT if a.in_use else _wd_C_DIM)
        tw = _rd_tightest(a)
        if a.credit is not None:
            parts.append(nm + " " + (_wd_c(f"${a.credit.balance:.2f}", _wd_C_RED if a.credit.low else _wd_C_DIM)
                                     if a.credit.balance is not None else _rd_dim("–")))
        elif a.stale or tw is None:
            parts.append(nm + _rd_dim(" stale" if a.stale else " –"))
        else:
            parts.append(nm + " " + _wd_c(f"{tw[1]:.0f}%", _wd_pct_color(tw[1])))
    return _rd_dim("quota  ") + _rd_join(parts, _rd_dot())


def _rd_pct_cell(used: float, stale: bool) -> str:
    txt = f"{min(max(used, 0), 999):.0f}%".rjust(4)
    return _wd_c(txt, _wd_C_DIM if stale else _wd_pct_color(used))


def _rd_quota_bar(used: float, pace: float | None, bw: int, stale: bool) -> str:
    return _wd_bar(used / 100.0, bw, lambda x: _wd_pct_color(x * 100), None if pace is None else pace / 100.0,
                   _wd_C_DIM if stale else None)


def _rd_quota_panel(snap: Snapshot, w: int, h: int) -> list[str]:
    iw, ih = w - 2, h - 2
    accts = snap.quota
    shown = accts if len(accts) <= ih else accts[:max(ih - 1, 0)]
    nw = min(14, max((_wd_width(_wd_clean(a.name)) for a in accts), default=4))
    rows = [(a, _rd_tightest(a)) for a in shown]
    ww = min(6, max((len(tw[0]) for _, tw in rows if tw), default=2))
    extra = 6 if any(a.stale for a in accts) else 0
    bw = iw - (2 + nw + 1 + 1 + 4 + 1 + ww + extra)
    bw = min(max(bw, 8), 36)
    lines: list[str] = []
    for a, tw in rows:
        dot = _wd_c("●", _wd_C_BLUE) if a.in_use else _wd_c("·", _wd_C_FAINT)
        name_c = _wd_C_TEXT if a.in_use else _wd_C_DIM
        if a.stale:
            name_c = _wd_C_DIM
        name = _wd_pad(_wd_c(_wd_trunc(a.name, nw), name_c), nw)
        if a.credit is not None:
            lines.append(_rd_credit_line(dot, name, a.credit))
            continue
        if tw is None:
            lines.append(dot + " " + name + " " + _rd_muted("no data"))
            continue
        stale = a.stale or tw[3]
        bar = _rd_quota_bar(tw[1], tw[2], bw, stale)
        line = (dot + " " + name + " " + bar + " " + _rd_pct_cell(tw[1], stale)
                + " " + _rd_muted(_wd_pad(_wd_trunc(tw[0], ww), ww)))
        if stale:
            line += _rd_muted(" stale")
        if a.extra is not None:
            line += " " + _wd_c(_rd_extra_text(a.extra), _wd_C_RED)
        lines.append(line)
    if len(shown) < len(accts):
        lines.append(_rd_muted(f"+{len(accts) - len(shown)} more"))
    return _wd_box(lines, w, h, _rd_title_main("quota"))


def _rd_quota_full(snap: Snapshot, w: int, h: int, now: float) -> list[str]:
    iw, ih = w - 2, h - 2
    title = _rd_title_main("quota", "all windows")
    if not snap.quota_ok:
        msg = _rd_dim("quota unavailable (STATUS_QUOTA_CMD missing or not answering)")
        return _wd_box([""] * max(0, (ih - 1) // 2) + [_wd_center(msg, iw)], w, h, title)
    ww = min(8, max((len(win.name) for a in snap.quota for win in a.windows), default=2))
    bw = min(max(iw - (2 + ww + 1 + 1 + 4 + 2 + 9), 8), 50)
    blocks: list[list[str]] = []
    for a in snap.quota:
        dot = _wd_c("●", _wd_C_BLUE) if a.in_use else _wd_c("·", _wd_C_FAINT)
        name_c = _wd_C_TEXT if a.in_use and not a.stale else _wd_C_DIM
        head = dot + " " + _wd_c(_wd_clean(a.name), name_c, bold=True)
        if a.stale:
            head += _rd_dim("  stale")
        blk = [head]
        if a.credit is not None:
            blk.append("  " + (_wd_c(_rd_credit_text(a.credit) + " LOW", _wd_C_RED, bold=True) if a.credit.low
                               else _rd_muted(_rd_credit_text(a.credit))))
        if a.extra is not None:
            blk.append("  " + _wd_c(_rd_extra_text(a.extra), _wd_C_RED) + _rd_dim(
                " since " + time.strftime("%b %d %H:%M", time.localtime(a.extra.since))))
        for win in a.windows:
            stale = a.stale or win.stale
            pace = _rd_dim("pace " + f"{win.pace:.0f}%") if win.pace is not None else ""
            ttr = _rd_ttr(win.resets_at, now)
            blk.append("  " + _rd_dim(_wd_pad(_wd_trunc(win.name, ww), ww)) + " "
                       + _rd_quota_bar(win.used, win.pace, bw, stale) + " " + _rd_pct_cell(win.used, stale)
                       + ("  " + pace if pace else "")
                       + ("  " + _rd_muted(ttr) if ttr else ""))
        blocks.append(blk)
    lines: list[str] = []
    for k, blk in enumerate(blocks):
        need = len(blk) + (1 if lines else 0)
        left_after = len(blocks) - k - 1
        if len(lines) + need + (1 if left_after else 0) > ih:
            lines.append(_rd_dim(f"+{len(blocks) - k} more"))
            break
        if lines:
            lines.append("")
        lines.extend(blk)
    return _wd_box(lines, w, h, title)


# --- detail view ---------------------------------------------------------------------------------
def _rd_md_line(line: str, kind: str) -> str:
    """Light Markdown styling for one (already wrapped) line."""
    if kind == "h":
        return _wd_c(line, _wd_C_BLUE, bold=True)
    out: list[str] = []
    parts = line.split("`")
    for i, part in enumerate(parts):
        if i % 2 == 1 and i < len(parts) - 1:
            out.append(_wd_c(part, _wd_C_CYAN))
        elif i % 2 == 1:
            out.append(_wd_c("`" + part, _wd_C_TEXT))
        else:
            if part.lstrip().startswith("Goal:") and kind == "n":
                pre, _, rest = part.partition("Goal:")
                out.append(_wd_c(pre + "Goal:", _wd_C_VIOLET) + _wd_c(rest, _wd_C_TEXT))
            else:
                out.append(_wd_c(part, _wd_C_TEXT))
    return "".join(out)


def _rd_md_lines(text: list[str], w: int) -> list[str]:
    out: list[str] = []
    for raw in text:
        line = _wd_clean(raw).rstrip()
        stripped = line.lstrip()
        if stripped.startswith("#"):
            kind = "h"
        else:
            kind = "n"
        indent = len(line) - len(stripped)
        if kind == "n" and stripped[:2] in ("- ", "* "):
            body = " " * indent + "• " + stripped[2:]
            wrapped = _wd_wrap(body, w)
            for k, seg in enumerate(wrapped):
                out.append(_rd_md_line(seg if k == 0 else "  " + seg.lstrip(), "n"))
            continue
        for seg in _wd_wrap(line, w):
            out.append(_rd_md_line(seg, kind))
    return out


def _rd_feed_lines(wk: Worker, w: int) -> list[str]:
    out: list[str] = []
    tw = max(1, w - 18)
    for st in wk.log.steps:
        stamp = _rd_dim(_rd_hms(st.ts)) if st.ts else " " * 8
        if st.kind == "error":
            kind, kcol = "error", _wd_C_RED
        elif st.kind == "text":
            kind, kcol = "text", _wd_C_SUB
        else:
            kind, kcol = _wd_trunc(st.tool or "tool", 7), _wd_C_CYAN
        head = stamp + "  " + _wd_c(kind, kcol) + " " * max(0, 7 - _wd_width(kind)) + " "
        for k, seg in enumerate(_wd_wrap(st.text, tw)):
            out.append((head if k == 0 else " " * 18) + _wd_c(seg.strip() if k else seg, _wd_C_TEXT))
    return out


def _rd_detail_card(wk: Worker, now: float, iw: int, t: float, ui: UIState, avail: int) -> list[str]:
    cw = iw - 3
    fl = _rd_flash(ui, wk, t)
    out = [("name", _rd_name_line(wk, now, iw - 1, t, True, fl))]
    title = _wd_clean(wk.title).strip()
    if title:
        out.append(("title", _rd_sub(_wd_trunc(title, cw))))
    goal = _wd_clean(wk.goal).strip()
    if goal:
        for k, seg in enumerate(_wd_wrap("goal: " + goal, cw)[:2]):
            out.append((f"goal{k}", _rd_dim(seg)))
    out.append(("stats", _rd_stats_row(wk, cw, 60 if iw >= 120 else 30)))
    if _rd_has_counts(wk):
        out.append(("counts", _rd_counts_row(wk)))
    for drop in ("counts", "stats", "goal1", "goal0", "title"):
        if len(out) <= avail:
            break
        out = [x for x in out if x[0] != drop]
    lines = [ln for _, ln in out]
    return _rd_gutter(_rd_edge(wk, t, fl, len(lines), True), lines)[:max(avail, 1)]


def _rd_detail(snap: Snapshot, ui: UIState, sel: int, cols: int, rows: int, t: float) -> list[str]:
    wk = snap.workers[sel]
    h = rows - 2
    iw = cols - 2
    card = _rd_detail_card(wk, snap.now, iw, t, ui, 6)
    card_h = len(card) + 2
    tabs = True
    body_h = h - card_h - (2 if tabs else 0)
    while body_h < 5 and len(card) > 1:
        card = _rd_detail_card(wk, snap.now, iw, t, ui, len(card) - 1)
        card_h = len(card) + 2
        body_h = h - card_h - 2
    out: list[str] = []
    if body_h >= 5:
        state = _rd_dim(wk.state)
        name = _wd_c(_wd_clean(wk.name), _wd_C_BLUE, bold=True)
        out += _wd_box(card, cols, card_h, name + _rd_dim(" ── ") + state)
        out += _rd_tab_bar(ui.tab, cols, ui, len(out))
    else:
        body_h = h
    top = len(out)
    out += _rd_detail_body(wk, ui, cols, body_h, t)
    _rd_hit(ui, top, top + max(body_h, 1) - 1, 0, cols - 1, "scroll", "detail")
    return out


def _rd_tab_bar(active: str, cols: int, ui: UIState | None = None, row: int = 0) -> list[str]:
    """The Live / Brief / Report tabs; with ui, each label is a click target at row."""
    labels = ""
    rule = ""
    col = 0
    for key, name in _rd_TABS:
        seg = f" {name} "
        if ui is not None and col + len(seg) <= cols:
            _rd_hit(ui, row, row + 1, col, col + len(seg) - 1, "tab", key)
        col += len(seg) + 2
        if key == active:
            labels += _wd_c(seg, _wd_C_BLUE, bold=True)
            rule += _wd_c("━" * len(seg), _wd_C_BLUE)
        else:
            labels += _wd_c(seg, _wd_C_MUTED)
            rule += _wd_c("─" * len(seg), _wd_C_FAINT)
        labels += "  "
        rule += _wd_c("──", _wd_C_FAINT)
    return [_wd_fit(labels, cols), _wd_fit(rule + _wd_c("─" * cols, _wd_C_FAINT), cols)]


def _rd_detail_body(wk: Worker, ui: UIState, cols: int, h: int, t: float) -> list[str]:
    iw, ih = cols - 2, h - 2
    tab = ui.tab if ui.tab in ("live", "brief", "report") else "live"
    title = _wd_c(dict(_rd_TABS)[tab].lower(), _wd_C_BLUE)
    if tab == "live":
        lines = _rd_feed_lines(wk, iw - 1)
    else:
        if not any(x.strip() for x in ui.detail_text):
            msg = _rd_dim("no brief found" if tab == "brief" else "no report yet")
            return _wd_box([""] * max(0, (ih - 1) // 2) + [_wd_center(msg, iw)], cols, h, title)
        lines = _rd_md_lines(ui.detail_text, iw - 1)
    total = len(lines)
    top_max = max(0, total - ih)
    note = ""
    if tab == "live":
        if ui.follow:
            first = top_max
        else:
            first = top_max - min(max(int(ui.scroll), 0), top_max)
            note = _rd_dim("▼ following paused · G to resume")
    else:
        first = min(max(int(ui.scroll), 0), top_max)
    window = lines[first:first + ih]
    return _wd_box(window, cols, h, title, note=note, thumb=_wd_thumb(total, ih, first))


# --- station pages: lead, cost, changed files (opened with L, C, F from the station) --------------
_rd_PAGES = ("lead", "cost", "files")
_rd_PAGE_KEYS = (("Esc", "back"), ("↑↓", "scroll"), ("r", "reload"), ("L", "lead"), ("C", "cost"), ("F", "files"))
_rd_PAGE_COLS = (12, 11, 6, 6, 7)   # name, model, in, out, cost; one space between them


def _rd_page_row(name: str, model: str, tin: str, tout: str, cost: str, bar: str = "") -> str:
    cells = [_wd_pad(_wd_trunc(name, _rd_PAGE_COLS[0]), _rd_PAGE_COLS[0]),
             _wd_pad(_wd_trunc(model, _rd_PAGE_COLS[1]), _rd_PAGE_COLS[1]),
             _rd_rjust(tin, _rd_PAGE_COLS[2]), _rd_rjust(tout, _rd_PAGE_COLS[3]), _rd_rjust(cost, _rd_PAGE_COLS[4])]
    return " ".join(cells) + (" " + bar if bar else "")


def _rd_cost_lines(snap: Snapshot, iw: int) -> list[str]:
    """Cost page: one row per worker, most expensive first; then the totals per model with a bar scaled to the top cost."""
    def tok(n: float | None) -> str:
        return _wd_fmt_tok(n) if n is not None else "–"

    def money(x: float | None) -> str:
        return _wd_fmt_money(x) if x is not None else "–"

    lines = [_rd_sub(_rd_page_row("worker", "model", "in", "out", "cost"))]
    for wk in sorted(snap.workers, key=lambda w: -(w.log.cost or 0.0)):
        model = display_model(wk.model or wk.log.model) or "–"
        lines.append(_rd_page_row(_wd_clean(wk.name), model, tok(wk.log.tokens_in), tok(wk.log.tokens_out),
                                  money(wk.log.cost)))
    if not snap.workers:
        lines.append(_rd_dim("no workers in this station"))
    per: dict[str, list[float]] = {}   # model -> tokens in, tokens out, cost, and 1 when some worker reported a cost
    for wk in snap.workers:
        tot = per.setdefault(display_model(wk.model or wk.log.model) or "–", [0.0, 0.0, 0.0, 0.0])
        tot[0] += wk.log.tokens_in or 0
        tot[1] += wk.log.tokens_out or 0
        tot[2] += wk.log.cost or 0.0
        tot[3] = tot[3] or (wk.log.cost is not None)
    top = max((tot[2] for tot in per.values()), default=0.0)
    bw = max(4, min(30, iw - sum(_rd_PAGE_COLS) - len(_rd_PAGE_COLS)))
    lines += ["", _rd_sub(_rd_page_row("model", "", "in", "out", "cost"))]
    for model, (tin, tout, cost, priced) in sorted(per.items(), key=lambda kv: -kv[1][2]):
        fill = min(bw, round(bw * cost / top)) if top > 0 else 0
        bar = _wd_c("█" * fill, _wd_C_BLUE) + _wd_c("░" * (bw - fill), _wd_C_FAINT)
        lines.append(_rd_page_row(model, "", tok(tin), tok(tout), money(cost if priced else None), bar))
    return lines


def _rd_page_body(snap: Snapshot, ui: UIState, cols: int, h: int) -> list[str]:
    """The open page in a box: the cost table, or the lead and files text loaded on open. Wheel and ↑↓ scroll it."""
    iw, ih = cols - 2, h - 2
    lines = _rd_cost_lines(snap, iw) if ui.view == "cost" else \
        [_rd_sub(x) if x and not x.startswith(" ") else x for x in ui.detail_text]
    top_max = max(0, len(lines) - ih)
    first = min(max(int(ui.scroll), 0), top_max)
    ui.scroll = first   # the renderer clamps: End and the wheel can overshoot
    _rd_hit(ui, 0, max(h, 1) - 1, 0, cols - 1, "scroll", "page")
    return _wd_box(lines[first:first + ih], cols, h, _rd_title_main(ui.view), thumb=_wd_thumb(len(lines), ih, first))


def _rd_columns(left: list[str], right: list[str]) -> list[str]:
    return [a + b for a, b in zip(left, right)]


def _rd_blank(w: int, h: int) -> list[str]:
    return [" " * w] * max(h, 0)


def _rd_overview(snap: Snapshot, ui: UIState, sel: int, cols: int, rows: int, t: float) -> list[str]:
    if ui.density == "full":
        return _rd_overview_full(snap, ui, sel, cols, rows, t)
    body_h = rows - 2
    live_n = sum(1 for wk in snap.workers if _rd_is_live(wk))
    fin_n = len(snap.workers) - live_n
    has_q = snap.quota_ok and bool(snap.quota)
    has_rev = bool(snap.reviews)
    has_tl = bool(snap.workers or snap.reviews)
    mode = "wide" if cols >= 130 else "medium" if cols >= 84 else "narrow"
    cap = max(2, min(8, (rows - 14) // 3))
    items = _rd_tl_items(snap, cols - 2) if has_tl else []
    lanes, dropped = _rd_tl_pack(items, cap)
    tl_folded = has_tl and "timeline" in ui.folded
    rv_folded = has_rev and "reviews" in ui.folded
    tl_h = 1 if tl_folded else 4 + max(1, len(lanes))
    q_h = 2 + min(len(snap.quota), 8)
    rev_nat = (1 if rv_folded else _rd_reviews_natural(snap)) if has_rev else 0
    rev_need = 1 if rv_folded else 2 + len(snap.reviews)
    qtxt = _rd_quota_compact(snap) if has_q else ""
    rtxt = _rd_reviews_compact(snap) if has_rev else ""
    need = 2 + 3 * live_n + max(0, live_n - 1)
    if fin_n:
        need += min(fin_n, 3) + (1 if live_n else 0)
    both_one_line = has_q and has_rev and _wd_width(qtxt) + 3 + _wd_width(rtxt) <= cols

    def plan(qc: bool, rc: bool, tc: bool) -> tuple[bool, int, int, int]:
        """(fits, workers height, compact lines, bottom/top panel height used)."""
        rc, tc = rc and not rv_folded, tc and not tl_folded   # a folded panel is its title line, not the compact form
        qline, rline = has_q and qc, has_rev and rc
        n_comp = 0
        if qline or rline:
            n_comp = 2 if (qline and rline and not both_one_line) else 1
        tline = 1 if has_tl and tc else 0
        top = tl_h if has_tl and not tc else 0
        if mode == "wide":
            avail = body_h - n_comp - tline - top
            right = (rev_need if has_rev and not rc else 0) + (q_h if has_q and not qc else 0)
            return avail >= need and avail >= right, avail, n_comp + tline, top
        if mode == "medium":
            bottom = max(min(rev_nat, 10) if has_rev and not rc else 0, q_h if has_q and not qc else 0)
            avail = body_h - n_comp - tline - top - bottom
            return avail >= need, avail, n_comp + tline, top
        avail = body_h - n_comp - tline
        return avail >= need, avail, n_comp + tline, 0

    states = [(False, False, False), (True, False, False), (True, True, False), (True, True, True)]
    if mode == "narrow":
        states = states[-1:]
    qc, rc, tc = states[-1]
    for st in states:
        if plan(*st)[0]:
            qc, rc, tc = st
            break
    rc, tc = rc and not rv_folded, tc and not tl_folded
    _, avail, _, _ = plan(qc, rc, tc)
    qline, rline = has_q and qc, has_rev and rc
    out: list[str] = []
    comp: list[str] = []
    if qline and rline and both_one_line:
        comp.append(qtxt + _rd_dot() + rtxt)
    else:
        if qline:
            comp.append(qtxt)
        if rline:
            comp.append(rtxt)
    if has_tl and tc:
        comp.append(_rd_timeline_compact(snap, cols))
    comp = comp[:max(body_h - 3, 0)]
    out += [_wd_fit(_wd_clip(x, cols), cols) for x in comp]
    avail = body_h - len(out)
    if has_tl and not tc:
        with _rd_at(ui, len(out), 0):
            out += _rd_timeline_panel(snap, ui, sel, cols, tl_h, t, lanes, dropped)
        avail -= tl_h
    show_rev = has_rev and not rc
    show_q = has_q and not qc
    if mode == "wide" and (show_rev or show_q):
        lw = int(cols * 0.64)
        rw = cols - lw - 1
        base = len(out)
        right: list[str] = []
        qh = q_h if show_q else 0
        if show_rev:
            with _rd_at(ui, base, lw + 1):
                right += _rd_reviews_panel(snap, ui, rw, max(min(rev_nat, avail - qh), 3), t)
        if show_q:
            right += _rd_quota_panel(snap, rw, qh)
        right += _rd_blank(rw, avail - len(right))
        right = right[:avail]
        with _rd_at(ui, base, 0):
            workers = _rd_workers_panel(snap, ui, sel, lw, avail, t)
        out += [x + " " + y for x, y in zip(workers, right)]
    elif mode == "medium" and (show_rev or show_q):
        bottom = max(min(rev_nat, 10) if show_rev else 0, q_h if show_q else 0)
        wh = avail - bottom
        with _rd_at(ui, len(out), 0):
            out += _rd_workers_panel(snap, ui, sel, cols, wh, t)
        if show_rev and show_q:
            lw = cols // 2
            with _rd_at(ui, len(out), 0):
                left = _rd_reviews_panel(snap, ui, lw, bottom, t)
            out += _rd_columns(left, _rd_quota_panel(snap, cols - lw, bottom))
        elif show_rev:
            with _rd_at(ui, len(out), 0):
                out += _rd_reviews_panel(snap, ui, cols, bottom, t)
        else:
            out += _rd_quota_panel(snap, cols, bottom)
    else:
        with _rd_at(ui, len(out), 0):
            out += _rd_workers_panel(snap, ui, sel, cols, avail, t)
    return out


def _rd_lead_strip(snap: Snapshot, cols: int, opened: bool, max_lines: int) -> list[str]:
    """Newest milestone on one line. Opened lists newer entries first, capped at `max_lines`."""
    stones = snap.milestones
    if not stones or cols < 8 or max_lines <= 0:
        return []

    def row(ms: Milestone, mark: str, suffix: str) -> str:
        stamp = _wd_fmt_clock(ms.ts) if ms.ts is not None else "     "
        text = _wd_markdown(ms.text, plain=True)
        assert isinstance(text, str)
        tail = f" +{suffix}" if suffix else ""
        prefix = f"{mark} {stamp} "
        room = max(1, cols - len(prefix) - len(tail))
        return (_rd_muted(mark) + " " + _wd_c(stamp, _wd_C_VIOLET) + " "
                + _rd_sub(_wd_trunc(text, room)) + _rd_muted(tail))

    if not opened:
        hidden = len(stones) - 1
        return [row(stones[-1], "▸", str(hidden) if hidden else "")]
    shown = list(reversed(stones))[:max_lines]
    return [row(ms, "▾" if i == 0 else " ", "") for i, ms in enumerate(shown)]


_rd_LANES = 8                # timeline lanes before "+N earlier"
_rd_MIN_WORKERS = 6          # the workers panel keeps at least this many rows, or half the room


def _rd_overview_full(snap: Snapshot, ui: UIState, sel: int, cols: int, rows: int, t: float) -> list[str]:
    """Station view, full density, one column: lead line, workers, timeline, reviews, quota.
    Workers keep half the room; the panels below shrink to one line when they do not fit: quota, then timeline, then reviews."""
    body_h = max(0, rows - 2)
    lead: list[str] = []
    if snap.milestones and body_h >= 10:
        cap = max(1, body_h - max(8, body_h // 2)) if ui.lead_open else 1
        lead = _rd_lead_strip(snap, cols, ui.lead_open, cap)
    if lead:
        _rd_hit(ui, 0, len(lead) - 1, 0, cols - 1, "lead", "")
    room = body_h - len(lead)
    has_tl = bool(snap.workers or snap.reviews)
    has_rev = bool(snap.reviews)
    has_q = snap.quota_ok and bool(snap.quota)
    lanes, dropped = _rd_tl_pack(_rd_tl_items(snap, cols - 2), _rd_LANES) if has_tl else ([], 0)
    # (full height, one-line height) per panel, in the order they shrink
    tl_folded = has_tl and "timeline" in ui.folded
    rv_folded = has_rev and "reviews" in ui.folded
    full = {"quota": 2 + min(len(snap.quota), 8) if has_q else 0,
            "reviews": 1 if rv_folded else min(_rd_reviews_natural(snap), 10) if has_rev else 0,
            "timeline": 1 if tl_folded else 4 + max(1, len(lanes)) if has_tl else 0}
    one = {"quota": 1 if has_q else 0, "reviews": 1 if has_rev else 0, "timeline": 1 if has_tl else 0}
    shrink = tuple(k for k in ("quota", "timeline", "reviews") if not (k == "timeline" and tl_folded)
                   and not (k == "reviews" and rv_folded))   # folded panels are already one line
    level = {k: 0 if full[k] else 2 for k in ("quota", "timeline", "reviews")}   # 0 full, 1 one line, 2 hidden

    def used() -> int:
        return sum(0 if level[k] == 2 else full[k] if level[k] == 0 else one[k] for k in full)

    want = max(_rd_MIN_WORKERS, room // 2)
    for k in shrink:
        while room - used() < want and level[k] < 2:
            level[k] += 1
    out = list(lead)
    wh = room - used()
    if wh > 0:
        with _rd_at(ui, len(out), 0):
            out += _rd_workers_panel(snap, ui, sel, cols, wh, t)
    compact = {"timeline": lambda: _rd_timeline_compact(snap, cols),
               "reviews": lambda: _rd_reviews_compact(snap),
               "quota": lambda: _rd_quota_compact(snap)}
    for k in ("timeline", "reviews", "quota"):
        if level[k] == 0:
            with _rd_at(ui, len(out), 0):
                if k == "timeline":
                    out += _rd_timeline_panel(snap, ui, sel, cols, full[k], t, lanes, dropped)
                elif k == "reviews":
                    out += _rd_reviews_panel(snap, ui, cols, full[k], t)
                else:
                    out += _rd_quota_panel(snap, cols, full[k])
        elif level[k] == 1:
            out.append(_wd_fit(_wd_clip(compact[k](), cols), cols))
    return out[:body_h]


# --- frame ---------------------------------------------------------------------------------------
def _rd_build(snap: Snapshot, ui: UIState, cols: int, rows: int, t: float, stage: Stage | None = None) -> list[str]:
    crumb = ui.gpage in ("stage", "worker") and ui.view != "quota"   # a station opened from the global list
    header = _rd_header(snap, ui, cols, t, crumb, _rd_g_label(stage) if crumb and stage is not None else "")
    if crumb:
        _rd_hit(ui, 0, 0, 0, _wd_width(_RD_CRUMB) - 1, "back", "")
    _rd_attention_hit(ui, header, snap)
    if cols < 40 or rows < 8:
        return [header]
    sel = _rd_clamp_sel(snap, ui)
    view = ui.view
    mark = len(ui.hits)
    with _rd_at(ui, 1, 0):   # the body starts under the header row
        if view == "detail" and snap.workers:
            body = _rd_detail(snap, ui, sel, cols, rows, t)
            keys: tuple[tuple[str, str], ...] | list[tuple[str, str]] = _rd_DETAIL_KEYS
        elif view == "quota":
            body = usage_screen(snap.quota, snap.quota_ok, snap.openrouter, snap.openrouter_ok, ui.scroll,
                                cols, rows - 2, t, snap.usage_history, spend=snap.spend, spend_ok=snap.spend_ok)
            keys = _rd_QUOTA_KEYS
        elif view in _rd_PAGES:
            body = _rd_page_body(snap, ui, cols, rows - 2)
            keys = _rd_PAGE_KEYS
        else:
            worker_sel = -1 if _rd_panel_sel(snap, ui) else sel   # a panel row leaves no worker highlighted
            body = _rd_overview(snap, ui, worker_sel, cols, rows, t)
            keys = _rd_station_keys(snap, ui, sel, stage)
    _rd_clip_hits(ui, mark, rows - 2, cols - 1)
    if ui.note and rows >= 8:   # the last action's result: the body's last row makes room for it
        body = body[:rows - 3] + [_wd_fit(_rd_sub(ui.note), cols)]
    footer = _rd_keyline(keys, cols, ui, rows - 1)
    out = [header] + body[:rows - 2]
    out += [" " * cols] * (rows - 1 - len(out))
    out.append(footer)
    return out


def _rd_clip_hits(ui: UIState, mark: int, last_row: int, last_col: int) -> None:
    """Drop hit regions outside the frame area, from index mark on: rows past last_row or columns past last_col."""
    ui.hits[mark:] = [(r0, min(r1, last_row), c0, min(c1, last_col), act, tgt)
                      for r0, r1, c0, c1, act, tgt in ui.hits[mark:] if r0 <= last_row and c0 <= last_col]


def _rd_publish_hits(ui: UIState) -> None:
    """Hit map for the frame just drawn, 1-based like SGR mouse reports. Nothing is clickable under the ? overlay."""
    ui.hits = [] if ui.help else [(r0 + 1, r1 + 1, c0 + 1, c1 + 1, act, tgt) for r0, r1, c0, c1, act, tgt in ui.hits]


def render_frame(snap: Snapshot, ui: UIState, cols: int, rows: int, t: float, stage: Stage | None = None) -> list[str]:
    """One frame of the station view. stage is the station's record in the global view (its lead); None in a single station."""
    cols, rows = max(1, int(cols)), max(1, int(rows))
    ui.hits = []
    ui.title_place = (0, 0)
    try:
        lines = _rd_build(snap, ui, cols, rows, t, stage)
        if ui.help:
            lines = _rd_help_overlay(lines, cols, rows, ui)
        lines = tut_overlay(lines, ui, cols, rows)   # the station pages are drawn here, also in the tutorial
    except Exception:   # a render bug must never take the dashboard down
        ui.hits = []
        lines = [_wd_c("render error", _wd_C_RED)]
    _rd_publish_hits(ui)
    lines = lines[:rows] + [""] * (rows - len(lines))
    result = [_wd_fit(ln, cols) for ln in lines]
    if os.environ.get("NO_COLOR") is not None and ui.view == "quota":
        return [_wd_SGR_RE.sub("", line) for line in result]
    return result


# --- plain text ----------------------------------------------------------------------------------
def render_plain(snap: Snapshot, cols: int = 100, rows: int | None = None) -> str:
    cols = max(1, int(cols))
    n = _rd_counts(snap)
    names = {RUNNING: "running", QUIET: "quiet", STALLED: "stalled", DONE: "done", FAILED: "failed",
             NOREPORT: "no report"}
    counts = " · ".join(f"{n[k]} {names[k]}" for k in names if n.get(k))
    older = f"+{snap.hidden_older} older" if snap.hidden_older > 0 and not snap.all_history else ""
    totals = ""
    if snap.tokens_total is not None:
        totals = f"Σ {_wd_fmt_tok(snap.tokens_total)} tok"
        if snap.cost_total is not None:
            totals += f" · {_wd_fmt_money(snap.cost_total)}"
    elif snap.cost_total is not None:
        totals = f"Σ {_wd_fmt_money(snap.cost_total)}"
    clock = _wd_fmt_clock(snap.now, True)
    station = _wd_clean(snap.title).strip()
    title = f"◆ {_RD_HEADING} · {station}" if station else f"◆ {_RD_HEADING}"
    # the clock is never cut: drop the totals first, then the counts, then the "older" note
    head = ""
    for mid in ((counts, older, totals), (counts, older), (older,), ()):
        head = "   ".join(x for x in (title, *mid, clock) if x)
        if _wd_width(head) <= cols:
            break
    else:
        head = _wd_trunc(title, cols - len(clock) - 1) + " " + clock
    def milestone_block(ms: object) -> list[str]:
        stamp = _wd_fmt_clock(ms.ts) if ms.ts is not None else "     "
        text = _wd_markdown(ms.text, plain=True)
        assert isinstance(text, str)
        wrapped = _wd_wrap(text, max(1, cols - 8))
        return [stamp + " " + wrapped[0], *("      " + line for line in wrapped[1:])]

    def worker_block(wk: Worker) -> list[str]:
        state = names.get(wk.state, wk.state)
        row = f"{_rd_plain_mark(wk)} {_wd_clean(wk.name)}  {state}  {_wd_clean(display_model(wk.model))}  "
        row += _wd_fmt_dur(_rd_elapsed(wk, snap.now)) if wk.start > 0 else ""
        block = [row.rstrip()]
        if _rd_is_live(wk):
            if wk.log.steps:
                st = wk.log.steps[-1]
                kind = "error" if st.kind == "error" else "text" if st.kind == "text" else (st.tool or "tool")
                block.append(f"    › {kind}  {_wd_clean(st.text)}")
            block.append(f"    quiet {_wd_fmt_dur(wk.quiet_s)}")
        elif wk.state == NOREPORT:
            block.append("    finished · no report")
        elif wk.state == FAILED:
            err = next((s.text for s in reversed(wk.log.steps) if s.kind == "error"), "failed")
            wrapped = _wd_wrap(_wd_clean(err), max(1, cols - 4))
            block.extend("    " + line for line in wrapped)
        else:
            if wk.title:
                title_lines = _wd_wrap(_wd_clean(wk.title), max(1, cols - 4))
                block.extend("    " + line for line in title_lines)
            for summary in wk.summary:
                clean = _wd_markdown(summary, plain=True)
                assert isinstance(clean, str)
                lines = _wd_wrap(clean, max(1, cols - 6))
                block.append("    • " + lines[0].lstrip())
                block.extend("      " + x.lstrip() for x in lines[1:])
        return block

    live = [wk for wk in snap.workers if _rd_is_live(wk)]
    finished = [wk for wk in snap.workers if not _rd_is_live(wk)]
    milestone_blocks = [milestone_block(ms) for ms in reversed(snap.milestones)]
    review_blocks: list[list[str]] = []
    for r in snap.reviews:
        p = _rd_review_counts(r)
        when = "running" if r.running else _wd_fmt_clock(r.end)
        tally = " ".join(f"P{i + 1} {c}" for i, c in enumerate(p) if c) or ("" if r.running else "clean")
        block = [f"  {_wd_clean(r.name)}  {_wd_clean(display_model(r.model))}  {when}  {tally}".rstrip()]
        for f in _rd_sorted_findings(r):
            block.append(f"    P{f.prio} {_wd_clean(f.text)}")
        review_blocks.append(block)
    quota_blocks: list[list[str]] = []
    for a in snap.quota if snap.quota_ok else []:
        wins = " · ".join(f"{x.name} {x.used:.0f}%" for x in a.windows) or "no data"
        flags = (" (in use)" if a.in_use else "") + (" stale" if a.stale else "")
        quota_blocks.append([f"  {_wd_clean(a.name)}{flags}  {wins}"])
    service_block = (["services: " + ", ".join(f"{_wd_clean(n)} {'up' if up else 'down'}"
                                                for n, up in snap.services)] if snap.services else [])

    ordered: list[tuple[str, list[str], list[str]]] = []
    # Prioritize live workers, the newest finished worker, then recent milestones.
    ordered.extend(("live", worker_block(wk), worker_block(wk)[:1]) for wk in live)
    fin_units = [("finished", block, block[:1]) for block in (worker_block(wk) for wk in finished)]
    ordered.extend(fin_units[:1])
    ms_units = [("milestone", b, b[:1]) for b in milestone_blocks]
    ordered.extend(ms_units[:3])
    ordered.extend(fin_units[1:])
    ordered.extend(ms_units[3:])
    ordered.extend(("review", b, b[:1]) for b in review_blocks)
    ordered.extend(("quota", b, b[:1]) for b in quota_blocks)
    selected: list[str] = [head]
    if live or finished:
        selected.extend(["", "workers"])
    omitted = {"live": 0, "finished": 0, "milestone": 0, "review": 0, "quota": 0}
    cap = None if rows is None else max(0, rows)
    for index, (kind, block, fallback) in enumerate(ordered):
        block = [part for line in block for part in _wd_wrap(line, cols)]
        fallback = [part for line in fallback for part in _wd_wrap(line, cols)]
        first_kind = kind not in {x[0] for x in ordered[:index]}
        section_name = {"milestone": "lead", "review": "reviews", "quota": "quota"}.get(kind, "")
        separator = (["", section_name] if first_kind and section_name else [])
        candidate = selected + separator + block
        if cap is None or len(candidate) <= cap:
            selected = candidate
            continue
        candidate = selected + separator + fallback
        if cap is None or len(candidate) <= cap:
            selected = candidate
        else:
            omitted[kind] += 1
    if service_block:
        service_block = [part for line in service_block for part in _wd_wrap(line, cols)]
        if cap is None or len(selected) + len(service_block) <= cap:
            selected.extend(service_block)
        else:
            omitted["service"] = 1
    labels = {"live": "more live workers", "finished": "more finished", "milestone": "earlier milestones",
              "review": "more reviews", "quota": "more quota accounts", "service": "services"}
    notices = [_wd_trunc(f"+{count} {labels[kind]}", cols) for kind, count in omitted.items() if count]
    if cap is not None and notices:
        while len(selected) + len(notices) > cap and ordered:
            removable = next((i for i in range(len(selected) - 1, 0, -1)
                              if selected[i].startswith(("✔ ", "✖ ", "◌ ", "▲ ", "● ", "    ", "      "))), None)
            if removable is None:
                break
            removed = selected.pop(removable)
            if removed.startswith(("✔ ", "✖ ")):
                omitted["finished"] += 1
        notices = [_wd_trunc(f"+{count} {labels[kind]}", cols) for kind, count in omitted.items() if count]
        selected = selected[:max(0, cap - len(notices))] + notices[:cap]
    cleaned = [ln if _wd_width(ln) <= cols else part for ln in selected
               for part in (_wd_wrap(ln, cols) if _wd_width(ln) > cols else [ln])]
    if cap is not None:
        cleaned = cleaned[:cap]
    return "\n".join(cleaned) + ("\n" if cleaned else "")


def _rd_plain_mark(wk: Worker) -> str:
    return {RUNNING: "●", QUIET: "◌", STALLED: "▲", DONE: "✔", FAILED: "✖",
            NOREPORT: "○"}.get(wk.state, "○")


# --- global tree -------------------------------------------------------------------------------
_rd_G_ANIM: OrderedDict[tuple[str, str], tuple[str, float, float]] = OrderedDict()


_rd_G_RATE: OrderedDict[tuple[str, str], tuple[float, int, float]] = OrderedDict()
_rd_G_RATE_FULL = 40_000.0   # tokens per minute that fills the meter


def _rd_g_tokens(wk: Worker) -> int | None:
    if wk.log.tokens_in is None and wk.log.tokens_out is None:
        return None
    return (wk.log.tokens_in or 0) + (wk.log.tokens_out or 0)


def _rd_g_load(wk: Worker, key: tuple[str, str] | None = None, now: float | None = None) -> float:
    """Token burn rate as 0-100, log-scaled against _rd_G_RATE_FULL tokens per minute."""
    tokens = _rd_g_tokens(wk)
    if tokens is None:
        return 0.0
    if key is None or now is None:
        prev = _rd_G_RATE.get(key) if key is not None else None
        rate = prev[2] if prev else 0.0
    else:
        prev = _rd_G_RATE.get(key)
        rate = prev[2] if prev else 0.0
        if prev is None or tokens < prev[1]:
            _rd_G_RATE[key] = (now, tokens, 0.0)
        elif tokens > prev[1] and now - prev[0] >= 1.0:
            per_min = (tokens - prev[1]) / (now - prev[0]) * 60
            rate = per_min if prev[2] == 0 else .5 * prev[2] + .5 * per_min
            _rd_G_RATE[key] = (now, tokens, rate)
        elif tokens == prev[1] and now - prev[0] >= 10.0:
            # nothing new for a while: let the meter decay
            rate = prev[2] * .5
            _rd_G_RATE[key] = (now, tokens, rate)
        _rd_G_RATE.move_to_end(key)
        while len(_rd_G_RATE) > 2048:
            _rd_G_RATE.popitem(last=False)
    return min(100.0, 100.0 * math.log1p(max(rate, 0.0)) / math.log1p(_rd_G_RATE_FULL))


def _rd_g_load_spark(values: list[float]) -> str:
    glyphs = "▁▂▃▄▅▆▇█"
    out = []
    for value in values[-_rd_G_SPARK_LEN:]:
        level = min(7, max(0, round(min(max(value, 0.0), 100.0) / 100 * 7)))
        out.append(_wd_c(glyphs[level], _wd_C_AMBER if value > 75 else _wd_C_BLUE))
    return "".join(out)


def _rd_g_stage_progress(snap: Snapshot) -> tuple[int, int, float]:
    total = len(snap.workers)
    done = sum(w.state in (DONE, FAILED, NOREPORT) for w in snap.workers)
    return done, total, done / total if total else 0.0


def _rd_g_activity_level(item: Worker | Stage) -> int:
    """Classify recent worker output as still, cool, warm, or hot."""
    if isinstance(item, Worker):
        if item.state not in LIVE_STATES:
            return 0
        if item.quiet_s <= 10:
            return 3
        if item.quiet_s < 60:
            return 2
        return 1
    workers = [w for w in item.snap.workers if w.state in LIVE_STATES]
    live = item.live or len(workers)
    if not live:
        return 0
    if live >= 2 and any(w.quiet_s <= 10 for w in workers):
        return 3
    if any(w.quiet_s < 60 for w in workers):
        return 2
    return 1


def _rd_g_worker_timing(stage: Stage, wk: Worker, t: float) -> tuple[float, float, _wd_Rgb | None]:
    key = (stage.path, wk.name)
    entry = _rd_G_ANIM.get(key)
    if entry is None:
        entry = (wk.state, t, t)
    else:
        old_state, first_seen, changed = entry
        if old_state != wk.state:
            changed = t
        entry = (wk.state, first_seen, changed)
    _rd_G_ANIM[key] = entry
    _rd_G_ANIM.move_to_end(key)
    while len(_rd_G_ANIM) > 2048:
        _rd_G_ANIM.popitem(last=False)
    _, first_seen, changed = entry
    fade = min(1.0, max(0.0, (t - first_seen)))
    age = t - changed
    flash = (_wd_C_GREEN if wk.state == DONE else _wd_C_RED) if wk.state in (DONE, FAILED) and 0 <= age < 1.5 else None
    return fade, age, flash


_rd_G_SPARK_LEN = 7            # load sparkline cells
_rd_G_SPARK: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()   # station path ("" = all) -> (last sample, loads)
_rd_LEAD_NAMES = {"claude": "Claude Code", "claude bg": "Claude background", "t3 code": "T3 Code",
                  "cursor": "Cursor", "opencode": "opencode", "lead.json": "lead",
                  "claude work": "Claude Code (work)", "codex": "Codex"}


def _rd_g_live(stage: Stage) -> int:
    return sum(_rd_is_live(wk) for wk in stage.snap.workers)


def _rd_g_label(stage: Stage) -> str:
    return _wd_clean(stage.label).strip() or _wd_clean(stage.path)


def _rd_g_lead(stage: Stage) -> str:
    lead = stage.lead
    return " · ".join(_wd_clean(x) for x in ((display_model(lead.model), lead.phase, lead.stream) if lead else ()) if x)


def _rd_g_counts(snap: Snapshot) -> str:
    n = _rd_counts(snap)
    labels = ((RUNNING, "running"), (QUIET, "quiet"), (STALLED, "stalled"), (DONE, "done"),
              (FAILED, "failed"), (NOREPORT, "no report"))
    return _rd_join([f"{n[k]} {label}" for k, label in labels if n.get(k)], " · ")


def _rd_g_totals(snap: Snapshot, lead_ctx: int | None) -> str:
    parts = []
    if lead_ctx is not None:
        parts.append(_wd_fmt_tok(lead_ctx) + " ctx")
    if snap.cost_total is not None:
        parts.append(_wd_fmt_money(snap.cost_total))
    return " ".join(parts)


def _rd_g_spend(tokens: int | None, cost: float | None, unit: str = "tok") -> str:
    parts = []
    if tokens is not None:
        parts.append(_wd_fmt_tok(tokens) + " " + unit)
    if cost is not None:
        parts.append(_wd_fmt_money(cost))
    return _rd_sub("  ".join(parts)) if parts else ""


def _rd_g_review_summary(reviews: list[Review]) -> str:
    counts = [0, 0, 0]
    for review in reviews:
        for finding in review.findings:
            counts[min(max(finding.prio, 1), 3) - 1] += 1
    parts = [f"P{i + 1} {n}" for i, n in enumerate(counts) if n]
    return f"reviews {len(reviews)}" + ((" · " + "  ".join(parts)) if parts else "")


def _rd_g_spark(key: str, value: float | None, t: float) -> str:
    """Load sparkline for a station ("" = all). Samples about once a second; None = nothing live."""
    if value is None:
        _rd_G_SPARK.pop(key, None)
        return ""
    last, values = _rd_G_SPARK.get(key, (-math.inf, []))
    if t < last:
        last, values = -math.inf, []
    if not values or t - last >= 1.0:
        values = (values + [value])[-_rd_G_SPARK_LEN:]
        last = t
    _rd_G_SPARK[key] = (last, values)
    _rd_G_SPARK.move_to_end(key)
    while len(_rd_G_SPARK) > 256:
        _rd_G_SPARK.popitem(last=False)
    return _rd_g_load_spark(values[:-1] + [value])


def _rd_g_lead_age(sec: float) -> str:
    """How long a lead has run, short for a title row: 45m, 17h, 3d."""
    if sec < 3600:
        return f"{int(sec // 60)}m"
    if sec < 48 * 3600:
        return f"{int(sec // 3600)}h"
    return f"{int(sec // 86400)}d"


# --- global overview: the Board layout (masthead, filter chips, cursor rows that open into workers) ---
_rd_G_FILTERS = ("all", "working", "lagging", "problems")
_rd_G_SECTIONS = ("active", "idle", "closed")   # the overview's sections, top to bottom; active has no heading
_rd_G_HEADED = ("idle", "closed")               # the sections with a heading: a fold row, a hide key, `+N more` past the fold
_rd_G_FOLD = 12                # stations listed under a heading before "+N more <section>"
_rd_G_UNLINKED = 20            # workers an unlinked ("session unknown") group lists before its `+N more` row
_rd_G_UNLINKED_MORE = "unlinked"   # the unlinked group's fold row name (the finished folds use "finished:<root>")
_rd_G_SECTION_KEYS = {"idle": "h", "closed": "H"}   # the key that hides each headed section
_rd_G_STEPS = 3                # recent log steps shown in an open worker
_rd_G_KEYS = (("↑↓", "move"), ("Enter", "fold"), ("Space", "open"), ("w", "station"), ("/", "find"), ("1-4", "filter"),
              ("h", "hide idle"), ("H", "hide closed"), ("e/c", "all"), ("a", "history"))


def _rd_g_keys(ui: UIState) -> tuple[tuple[str, str], ...]:
    """The overview's key line; h and H say what they do now."""
    hidden = {"h": ui.ghide_idle, "H": ui.ghide_closed}
    return tuple((k, ("show " + ("idle" if k == "h" else "closed")) if hidden.get(k) else v) for k, v in _rd_G_KEYS)
_rd_G_WORKER_ORDER = {RUNNING: 0, QUIET: 1, STALLED: 1, FAILED: 2, DONE: 3, NOREPORT: 3}
_rd_G_FLAGS = {QUIET: ("quiet", _wd_C_AMBER), STALLED: ("stalled", _wd_C_RED), FAILED: ("failed", _wd_C_RED),
               NOREPORT: ("no report", _wd_C_SUB)}


@dataclass
class _rd_GRow:
    stage: Stage
    active: bool                 # the active section: open here and active in the last five minutes, or a worker running
    live: int
    finished: int
    lagging: int
    problems: int
    workers: list[Worker]
    solo: bool = False           # an open session's live lead with no workers at all; a row with workers is never solo
    section: str = "active"      # "active" | "idle" (open, quiet) | "closed" (not open)
    child: bool = False          # listed under its launcher in the same project and section
    depth: int = 0
    launcher: str = ""          # a shown launcher in another project
    folded_kids: int = 0         # spawned sessions hidden under this row because it is folded (set by `_rd_g_unfolded`)
    folded_problems: int = 0     # problems among those hidden sessions: a fold never hides a failure


def _rd_g_plain(text: str) -> str:
    """One line of report text without its markdown (bold, code, link syntax)."""
    return str(_wd_markdown(_wd_clean(text), plain=True))


def _rd_g_section(stage: Stage) -> str:
    """The section a session row sits in. A stage the collector has not filled (`state` empty) goes by its live
    workers or lead process: active when either runs, otherwise closed."""
    if stage.state in _rd_G_SECTIONS:
        return stage.state
    return "active" if _rd_g_live(stage) > 0 or stage.lead_state != "gone" else "closed"


def _rd_g_activity(stage: Stage) -> float:
    return stage.activity or stage.last_activity


def _rd_g_is_working(stage: Stage) -> bool:
    """A session (row) is working when a worker runs in it, or its lead is live or in the background in the active
    section. The masthead, the filter chips and --plain all count with this one."""
    return any(wk.state == RUNNING for wk in stage.snap.workers) or (
        stage.lead_state in ("live", "background") and _rd_g_section(stage) == "active")


def _rd_g_rowdata(stage: Stage) -> _rd_GRow:
    snap = stage.snap
    workers = sorted(snap.workers, key=lambda wk: _rd_G_WORKER_ORDER.get(wk.state, 3))   # stable: newest first kept
    p1 = sum(1 for r in snap.reviews for f in r.findings if f.prio == 1)
    section = _rd_g_section(stage)
    return _rd_GRow(
        stage=stage, active=section == "active",
        live=sum(wk.state == RUNNING for wk in workers),
        finished=sum(wk.state in (DONE, FAILED, NOREPORT) for wk in workers),
        lagging=sum(wk.state in (QUIET, STALLED) for wk in workers),
        problems=sum(wk.state == FAILED for wk in workers) + p1, workers=workers,
        solo=stage.solo and not workers and section != "closed", section=section)


def _rd_g_match(row: _rd_GRow, flt: str) -> bool:
    return {"working": _rd_g_is_working(row.stage), "lagging": row.lagging > 0, "problems": row.problems > 0}.get(flt, True)


def _rd_g_query(row: _rd_GRow, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    lead = row.stage.lead
    fields = [_rd_g_label(row.stage), row.stage.session_name, row.stage.tag, lead.model if lead else "",
              lead.phase if lead else "", lead.stream if lead else ""] + [wk.model for wk in row.workers]
    return any(needle in f.lower() for f in fields if f)


def _rd_g_show_unattached(ui: UIState) -> bool:
    """Whether `A` has the unattached runs shown; off by default."""
    return ui.gunattached


def _rd_g_shown(gs: GlobalSnapshot, ui: UIState) -> GlobalSnapshot:
    """The snapshot without its unattached runs, unless `A` shows them."""
    if _rd_g_show_unattached(ui) or not any(s.unattached for s in gs.stages):
        return gs
    return replace(gs, stages=[s for s in gs.stages if not s.unattached])


def _rd_g_unattached_n(gs: GlobalSnapshot) -> int:
    """The workers in unattached runs: GlobalSnapshot.unattached, or counted from those stages when that is 0."""
    return gs.unattached or sum(len(s.snap.workers) for s in gs.stages if s.unattached) or sum(s.unattached for s in gs.stages)


def _rd_g_all_rows(gs: GlobalSnapshot) -> list[_rd_GRow]:
    """Every session, by section (active, idle, closed). Inside each: needs attention first, then working, then most
    recently active (an idle section's age order)."""
    rows = [_rd_g_rowdata(stage) for stage in gs.stages]
    return sorted(rows, key=lambda r: (_rd_G_SECTIONS.index(r.section), r.problems == 0, not _rd_g_is_working(r.stage),
                                       -_rd_g_activity(r.stage), r.stage.path))


def _rd_g_nest(rows: list[_rd_GRow]) -> list[_rd_GRow]:
    """Nest shown launch chains within a project and section; leave cyclic links at the root."""
    by_id = {r.stage.session_id: r for r in rows if r.stage.session_id}
    kids: dict[int, list[_rd_GRow]] = {}
    for r in rows:
        r.child, r.depth, r.launcher = False, 0, ""
        parent = by_id.get(r.stage.parent)
        seen = {id(r)}
        node = parent
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            node = by_id.get(node.stage.parent)
        if node is not None or parent is None:
            continue
        if _rd_g_root(parent.stage) != _rd_g_root(r.stage):
            r.launcher = parent.stage.session_name or _rd_g_label(parent.stage)
        elif parent.section == r.section:
            r.child = True
            kids.setdefault(id(parent), []).append(r)
    out = []
    pending = [(r, 0) for r in reversed(rows) if not r.child]
    while pending:
        r, r.depth = pending.pop()
        out.append(r)
        pending.extend((kid, r.depth + 1) for kid in reversed(kids.get(id(r), [])))
    return out


def _rd_g_rows(gs: GlobalSnapshot, ui: UIState) -> list[_rd_GRow]:
    return _rd_g_nest([r for r in _rd_g_all_rows(_rd_g_shown(gs, ui))
                       if _rd_g_match(r, ui.gfilter) and _rd_g_query(r, ui.gquery)])


def _rd_g_starts_open(stage: Stage) -> bool:
    """A session or project starts open only while live: a running worker, or the active section."""
    return _rd_g_live(stage) > 0 or _rd_g_section(stage) == "active"


def _rd_g_is_open(ui: UIState, row: _rd_GRow) -> bool:
    key = "s:" + (row.stage.session_id or row.stage.path)
    if key in ui.gopen_keys:
        return True
    if key in ui.gshut_keys:
        return False
    if (row.stage.path, "stage", "") in ui.gopen:   # older path-keyed choice, still honoured
        return True
    if row.stage.path in ui.gfolded:
        return False
    return _rd_g_starts_open(row.stage)


def _rd_g_set_open(ui: UIState, row: _rd_GRow, value: bool) -> None:
    key = "s:" + (row.stage.session_id or row.stage.path)
    ui.gopen_keys.discard(key)
    ui.gshut_keys.discard(key)
    (ui.gopen_keys if value else ui.gshut_keys).add(key)
    if value and row.section == "idle":
        ui.gpin_keys.add(key)
    elif not value:
        ui.gpin_keys.discard(key)


def _rd_g_project_open(ui: UIState, root: str, rows: list[_rd_GRow]) -> bool:
    key = "p:" + root
    if key in ui.gopen_keys:
        return True
    if key in ui.gshut_keys:
        return False
    if any(fold.endswith("|" + root) for fold in ui.gfolded):   # older group fold, still honoured
        return False
    return any(_rd_g_starts_open(r.stage) for r in rows)


def _rd_g_set_project_open(ui: UIState, root: str, value: bool) -> None:
    key = "p:" + root
    ui.gopen_keys.discard(key)
    ui.gshut_keys.discard(key)
    (ui.gopen_keys if value else ui.gshut_keys).add(key)


def _rd_g_by_root(rows: list[_rd_GRow]) -> dict[str, list[_rd_GRow]]:
    """Every row by project root, built once so a section walk costs nothing per project."""
    out: dict[str, list[_rd_GRow]] = {}
    for row in rows:
        out.setdefault(_rd_g_root(row.stage), []).append(row)
    return out


def _rd_g_closed_recent(gs: GlobalSnapshot, row: _rd_GRow) -> bool:
    """A closed session quiet under IDLE_LIST_S stays under its project; older ones are only counted.
    History mode (`a`) lists every closed session as before."""
    if gs.all_history:
        return True
    activity_at = _rd_g_activity(row.stage)
    return bool(activity_at) and gs.now - activity_at < IDLE_LIST_S


def _rd_g_needs_sweep(stage: Stage) -> bool:
    """Work a human still has to finish: uncommitted, unmerged, or unpushed."""
    badge = stage.work.badge() if stage.work is not None else ""
    return badge in ("uncommitted", "unmerged") or badge.endswith(" unpushed")


def _rd_g_section_folded(ui: UIState, name: str) -> bool:
    return name in ui.gfolded   # a headed section's fold is keyed by its name; no station path is a bare name


def _rd_g_section_hidden(ui: UIState, name: str) -> bool:
    return ui.ghide_idle if name == "idle" else ui.ghide_closed if name == "closed" else False


def _rd_g_is_section_row(kind: str, name: str) -> bool:
    """A heading row, or a `+N more` row under one: they carry no station path."""
    return kind in _rd_G_HEADED or kind in ("group", "closed-count") or (kind == "more" and (name in _rd_G_HEADED or name.startswith("finished:")))


def _rd_g_task_list(row: _rd_GRow) -> list[Group]:
    """A session's tasks, newest first. Workers that no task holds (a hand-built snapshot) form one more task."""
    groups = list(row.stage.snap.groups)
    held = {name for group in groups for name in group.workers}
    loose = [wk for wk in row.workers if wk.name not in held]
    if loose:
        live = [wk for wk in loose if wk.state in LIVE_STATES]
        extra = Group(label="workers", workers=[wk.name for wk in loose], started=min(wk.start for wk in loose),
                      done=len(loose) - len(live), total=len(loose), live=len(live),
                      current=bool(live) or not any(group.current for group in groups))
        groups = [extra] + groups if live else groups + [extra]
    return groups


def _rd_g_tasks(row: _rd_GRow) -> tuple[list[tuple[Group, list[Worker]]], int]:
    """A session's tasks, newest first, each with its workers (live first, then finished, newest first), and how many
    of them list their workers: a task with a live or failed worker (kept first, in order). A finished task is one line that
    Enter opens (the user 2026-10-24: done workers must not sit open); the older ones fold under `+N done`."""
    by_name = {wk.name: wk for wk in row.workers}
    nested = {wk.name for wk in row.workers if wk.level and wk.parent in by_name}   # listed under their launcher, not in a task of their own
    kids: dict[str, list[Worker]] = {}
    for wk in row.workers:
        if wk.name in nested:
            kids.setdefault(wk.parent, []).append(wk)

    def with_kids(wk: Worker, seen: frozenset[str]) -> list[Worker]:
        out = [wk]
        for kid in kids.get(wk.name, ()):
            if kid.name not in seen:
                out += with_kids(kid, seen | {wk.name})
        return out
    tasks = []
    for group in _rd_g_task_list(row):
        members = [by_name[name] for name in dict.fromkeys(group.workers) if name in by_name and name not in nested]   # a re-run brief lists its name twice
        if not members:
            continue
        live = [wk for wk in members if wk.state in LIVE_STATES]
        done = sorted((wk for wk in members if wk.state not in LIVE_STATES), key=lambda wk: wk.end or wk.start, reverse=True)
        # a re-run brief lists its name twice in the group, so count the workers listed, not the group's own tally
        group = replace(group, total=len(members), live=len(live), done=len(done))
        tasks.append((group, [m for wk in live + done for m in with_kids(wk, frozenset())]))
    keep = {i for i, (_, members) in enumerate(tasks)
            if any(wk.state in LIVE_STATES or wk.state == FAILED for wk in members)}   # a failure stays in sight
    tasks = [t for i, t in enumerate(tasks) if i in keep] + [t for i, t in enumerate(tasks) if i not in keep]
    return tasks, len(keep)


def _rd_g_task_split(tasks: list[tuple[Group, list[Worker]]], shown: int) -> tuple[list, list]:
    """The tasks after the `shown` open ones: the newest finished ones as their own one-line rows, and the rest, which
    fold under `+N done`."""
    rest = tasks[shown:]
    return rest[:_rd_G_TASKS_SHOWN], rest[_rd_G_TASKS_SHOWN:]


def _rd_g_task_head(group: Group, w: int, depth: int = 0) -> str:
    """A task's one-line label above its workers, shown only when the session has more than one task."""
    pad = "   " + "  " * depth
    return _wd_clip(pad + _rd_dim(f"{_wd_clean(group.label)} · {group.live} live · {group.done} done"), w)


def _rd_g_task_line(group: Group, last: bool, selected: bool, opened: bool, now: float, w: int, depth: int = 0) -> str:
    """An older task folded to one line: ▸ label · done/total · age. Enter opens its workers."""
    cur = _wd_c("▌", _wd_C_BLUE) if selected else " "
    branch = _wd_c("└─ " if last else "├─ ", _wd_C_FAINT)
    age = _rd_g_lead_age(max(0.0, now - (group.finished_at or group.started)))
    pad = "   " + "  " * depth
    text = cur + pad + branch + _rd_muted(("▾ " if opened else "▸ ") + _wd_trunc(_wd_clean(group.label), max(8, w - 40))
                                          + f" · {group.done}/{group.total} · {age}")
    return _wd_bg_line(text, _wd_C_SELBG, w) if selected else text


def _rd_g_buckets(section: str, rows: list[_rd_GRow]) -> list[tuple[str, list[_rd_GRow], list[tuple[str, str, list[_rd_GRow]]]]]:
    """One project per normalised root, in row order: (project key, its sessions, no sub-groups).
    Crons and chats share the one openclaw project; there are no `·cron` sub-groups any more."""
    order: list[str] = []
    by_root: dict[str, list[_rd_GRow]] = {}
    for r in rows:
        root = _rd_g_root(r.stage)
        if root not in by_root:
            by_root[root] = []
            order.append(root)
        by_root[root].append(r)
    return [(f"p:{root}", by_root[root], []) for root in order]


def _rd_g_capped(ui: UIState, group: list[_rd_GRow], name: str) -> list[_rd_GRow]:
    """Idle projects are all represented by their collapsed headings."""
    return group


def _rd_g_group_keys(gs: GlobalSnapshot, ui: UIState) -> set[str]:
    """Every live or idle project heading key in the full tree (for e/c all), not only the active filter."""
    rows = _rd_g_all_rows(_rd_g_shown(gs, ui))
    return {f"p:{root}" for root in dict.fromkeys(_rd_g_root(r.stage) for r in rows if r.section != "closed")}


@dataclass
class _rd_GNode:
    """One selectable node of the console tree, with the prefix `_ot_prefixes` gave it."""
    row: _rd_GRow
    kind: str
    name: str
    depth: int = 0
    prefix: str = ""
    cont: str = ""
    rows: list[_rd_GRow] = field(default_factory=list)


def _rd_g_closed_shown(gs: GlobalSnapshot, ui: UIState) -> bool:
    return not _rd_g_section_hidden(ui, "closed")


def _rd_g_order(rows: list[_rd_GRow]) -> list[_rd_GRow]:
    """Rows depth-first with each sibling list stable except a project's crons after its chats."""
    roots: list[tuple[_rd_GRow, list]] = []
    stack: list[tuple[_rd_GRow, list]] = []
    for row in rows:
        node: tuple[_rd_GRow, list] = (row, [])
        while stack and stack[-1][0].depth >= row.depth:
            stack.pop()
        (stack[-1][1] if stack else roots).append(node)
        stack.append(node)
    out: list[_rd_GRow] = []

    def emit(items: list[tuple[_rd_GRow, list]]) -> None:
        for row, kids in sorted(items, key=lambda item: 1 if item[0].stage.cluster == "cron" else 0):
            out.append(row)
            emit(kids)
    emit(roots)
    return out


def _rd_g_unlinked_split(row: _rd_GRow) -> tuple[list[Worker], list[Worker]]:
    """An unlinked group's _rd_G_UNLINKED newest workers (by start) and the rest its `+N more` row folds away.
    Every other row, and an unlinked group that fits, keeps all its workers."""
    if not row.stage.unlinked or len(row.workers) <= _rd_G_UNLINKED:
        return row.workers, []
    ranked = sorted(row.workers, key=lambda wk: wk.start, reverse=True)
    return ranked[:_rd_G_UNLINKED], ranked[_rd_G_UNLINKED:]


def _rd_g_station_nodes(ui: UIState, row: _rd_GRow, depth: int) -> list[_rd_GNode]:
    """A session node and, when it is open, its workers, older-task folds and task rows."""
    nodes = [_rd_GNode(row, "stage", "", depth=depth)]
    if row.solo or not _rd_g_is_open(ui, row):
        return nodes
    kept, folded = _rd_g_unlinked_split(row)
    if folded:   # a session-unknown group lists its newest workers; Enter on the fold shows the rest
        nodes.extend(_rd_GNode(row, "worker", wk.name, depth=depth + 1 + max(0, wk.level)) for wk in kept)
        nodes.append(_rd_GNode(row, "more", _rd_G_UNLINKED_MORE, depth=depth + 1))
        if (row.stage.path, "more", _rd_G_UNLINKED_MORE) in ui.gopen:
            nodes.extend(_rd_GNode(row, "worker", wk.name, depth=depth + 1 + max(0, wk.level)) for wk in folded)
        return nodes
    tasks, shown = _rd_g_tasks(row)
    for _group, members in tasks[:shown]:
        nodes.extend(_rd_GNode(row, "worker", wk.name, depth=depth + 1 + max(0, wk.level)) for wk in members)

    def task_nodes(pairs: list[tuple[Group, list[Worker]]]) -> None:
        for group, members in pairs:
            nodes.append(_rd_GNode(row, "task", group.key, depth=depth + 1))
            if (row.stage.path, "task", group.key) in ui.gopen:
                nodes.extend(_rd_GNode(row, "worker", wk.name, depth=depth + 2 + max(0, wk.level)) for wk in members)
    direct, folded = _rd_g_task_split(tasks, shown)
    task_nodes(direct)   # a finished task: one line, Enter lists its workers
    if folded:
        nodes.append(_rd_GNode(row, "more", "", depth=depth + 1))
        if (row.stage.path, "more", "") in ui.gopen:
            task_nodes(folded)
    return nodes


def _rd_g_finished_nodes(gs: GlobalSnapshot, ui: UIState, root: str, rows: list[_rd_GRow]) -> list[_rd_GNode]:
    """The project's recent closed sessions: one `finished N` fold, its sessions when the fold is opened."""
    recent = [r for r in rows if r.section == "closed" and _rd_g_closed_recent(gs, r)]
    if not recent or not _rd_g_closed_shown(gs, ui):
        return []
    nodes = [_rd_GNode(recent[0], "more", "finished:" + root, depth=0, rows=recent)]
    if ("", "more", "finished:" + root) in ui.gopen:
        for row in _rd_g_order(recent):
            nodes.extend(_rd_g_station_nodes(ui, row, 1))
    return nodes


def _rd_g_has_live(rows: list[_rd_GRow]) -> bool:
    return any(r.section == "active" for r in rows)


def _rd_g_unfolded(ui: UIState, ordered: list[_rd_GRow]) -> list[_rd_GRow]:
    """`ordered` (depth-first) without the sessions spawned under a folded row: folding a lead folds its spawns into it.
    The folded row keeps their count in `folded_kids` for its `▸ N spawns` tail."""
    out: list[_rd_GRow] = []
    cut: _rd_GRow | None = None
    for i, row in enumerate(ordered):
        row.folded_kids = row.folded_problems = 0
        if cut is not None and row.depth > cut.depth:
            cut.folded_kids += 1
            cut.folded_problems += row.problems
            continue
        cut = None
        out.append(row)
        if i + 1 < len(ordered) and ordered[i + 1].depth > row.depth and not _rd_g_is_open(ui, row):
            cut = row
    return out


def _rd_g_nodes(gs: GlobalSnapshot, ui: UIState) -> list[_rd_GNode]:
    """Every selectable row in screen order: live projects with all their sessions, the idle divider and its
    collapsed projects, recent finished folds, then the closed count. Fill rows are added later, never here."""
    rows = _rd_g_rows(gs, ui)
    by_root_all = _rd_g_by_root(_rd_g_all_rows(_rd_g_shown(gs, ui)))
    groups = _rd_g_buckets("", rows)
    live = [(key[2:], prows) for key, prows, _ in groups if _rd_g_has_live(prows)]
    idle = [(key[2:], prows) for key, prows, _ in groups
            if not _rd_g_has_live(prows) and any(r.section == "idle" for r in prows)]
    done = [(key[2:], prows) for key, prows, _ in groups
            if not _rd_g_has_live(prows) and not any(r.section == "idle" for r in prows)]
    blocks: list[list[_rd_GNode]] = []
    for root, prows in live:
        block = [_rd_GNode(prows[0], "group", "p:" + root, rows=prows)]
        if _rd_g_project_open(ui, root, by_root_all.get(root, prows)):
            for row in _rd_g_unfolded(ui, _rd_g_order([r for r in prows if r.section != "closed"])):
                block.extend(_rd_g_station_nodes(ui, row, row.depth))
            block.extend(_rd_g_finished_nodes(gs, ui, root, prows))
        blocks.append(block)
    if idle and not _rd_g_section_hidden(ui, "idle"):
        blocks.append([_rd_GNode(idle[0][1][0], "idle", "")])
        if not _rd_g_section_folded(ui, "idle"):
            for root, prows in idle:
                block = [_rd_GNode(prows[0], "group", "p:" + root, rows=prows)]
                if _rd_g_project_open(ui, root, by_root_all.get(root, prows)):
                    for row in _rd_g_unfolded(ui, _rd_g_order([r for r in prows if r.section != "closed"])):
                        block.extend(_rd_g_station_nodes(ui, row, row.depth))
                    block.extend(_rd_g_finished_nodes(gs, ui, root, prows))
                blocks.append(block)
    for root, prows in done:
        block = _rd_g_finished_nodes(gs, ui, root, prows)
        if block:
            blocks.append(block)
    closed = [r for r in rows if r.section == "closed" and not _rd_g_closed_recent(gs, r)]
    if (closed or gs.dismissed) and _rd_g_closed_shown(gs, ui):
        first = closed[0] if closed else (rows[0] if rows else _rd_g_rowdata(Stage("", "")))
        blocks.append([_rd_GNode(first, "closed-count", "", rows=closed)])
    nodes: list[_rd_GNode] = []
    for block in blocks:
        for node, (prefix, cont) in zip(block, _ot_prefixes([n.depth for n in block])):
            node.prefix, node.cont = prefix, cont
            nodes.append(node)
    return nodes


def _rd_g_layout(gs: GlobalSnapshot, ui: UIState) -> list[tuple[_rd_GRow, str, str]]:
    """Selectable session, worker, task, project and fold rows in screen order. Fill rows are not here."""
    return [(node.row, node.kind, node.name) for node in _rd_g_nodes(gs, ui)]


def global_rows(gs: GlobalSnapshot, ui: UIState) -> list[tuple[str, str, str]]:
    """Selectable rows, top to bottom: (stage path, kind, name). A heading and its fold row have no path."""
    return [("" if _rd_g_is_section_row(node.kind, node.name) else node.row.stage.path, node.kind, node.name)
            for node in _rd_g_nodes(gs, ui)]


def _rd_g_section_head(name: str, n: int, folded: bool, cols: int, selected: bool) -> str:
    """The idle divider (the closed section has only its count line now)."""
    return _og_idle_divider(_og_plan(cols), n, selected)


def _rd_g_group_head(key: str, label: str, n: str, folded: bool, cols: int, selected: bool) -> str:
    """A project heading, for callers that hold its label and counts rather than the rows."""
    return _og_project_head(_og_plan(cols), label, not folded, not folded, n, "", "", selected)


def _rd_g_counts_text(rows: list[_rd_GRow]) -> str:
    """`2 live · 1 idle · 6 done`: the project heading's session counts."""
    n_live = sum(r.section == "active" for r in rows)
    n_idle = sum(r.section == "idle" for r in rows)
    n_done = sum(r.section == "closed" for r in rows)
    parts = [f"{n} {word}" for n, word in ((n_live, "live"), (n_idle, "idle"), (n_done, "done")) if n]
    return " · ".join(parts) or "0 live"


def _rd_g_rollup_badge(rows: list[_rd_GRow]) -> str:
    """The worst work-state badge in a project, rolled up since the heading has one badge."""
    works = [r.stage.work for r in rows if r.stage.work is not None]
    order = (lambda w: bool(w.dirty), lambda w: bool(w.ahead),
             lambda w: bool(w.unmerged), lambda w: w.unmerged is False and bool(w.branch) and w.dirty is False)
    for test in order:
        for work in works:
            if test(work):
                return _og_badge(work)
    return ""


def _rd_g_cost(rows: list[_rd_GRow]) -> str:
    """The project's known spend; empty when no session reports a cost (never a made-up $0.00)."""
    costs = [r.stage.lead_cost for r in rows if r.stage.lead_cost is not None]
    return _wd_fmt_money(sum(costs)) if costs else ""


def _rd_g_metric_cells(nodes: list[_rd_GNode], now: float) -> list[_og_Cells]:
    """Metric values for every session, worker and project heading in the tree (scroll does not change this)."""
    out: list[_og_Cells] = []
    for node in nodes:
        if node.kind == "group":
            out.append(_rd_g_project_totals(node.rows))
        elif node.kind == "stage":
            out.append(_og_stage_cells(node.row.stage, now))
        elif node.kind == "worker":
            wk = next(x for x in node.row.workers if x.name == node.name)
            out.append(_og_worker_cells(wk, now))
    return out


def _rd_g_project_totals(rows: list[_rd_GRow]) -> _og_Cells:
    """A project's roll-up for its heading's TOKENS, COST and TOOLS cells; a column no session reports stays None."""
    def total(values: list) -> float | int | None:
        known = [v for v in values if v is not None]
        return sum(known) if known else None
    return _og_Cells(tokens=total([r.stage.lead_tokens for r in rows]), cost=total([r.stage.lead_cost for r in rows]),
                     tools=total([r.stage.lead_tools for r in rows]))


def _rd_g_project_age(rows: list[_rd_GRow], now: float) -> str:
    """How long a project has been quiet, for an idle heading's right side."""
    stamps = [_rd_g_activity(r.stage) for r in rows if _rd_g_activity(r.stage)]
    return _og_age(max(0.0, now - max(stamps))) + " ago" if stamps else ""


def _rd_g_group_info(ui: UIState, rows: list[_rd_GRow]) -> dict[str, tuple[str, str]]:
    """One project heading per normalised root, with its rolled-up session counts."""
    counts: dict[str, tuple[str, str]] = {}
    short = _rd_g_short_labels([r.stage for r in rows])
    for pkey, prows, _ in _rd_g_buckets("", rows):
        counts[pkey] = (short.get(_rd_g_root(prows[0].stage), ""), _rd_g_counts_text(prows))
    return counts


_rd_G_SOLO = "solo · no workers"                                  # a live lead with nothing delegated
_rd_G_MARKS = {"active": "◆", "idle": "◐", "closed": "◇"}         # a session's mark by section; solo is ◇
_rd_G_MARK_COLORS = {"active": _wd_C_GREEN, "idle": _wd_C_AMBER, "closed": _wd_C_FAINT}   # the mark's colour by state
_rd_G_CTX_WARN, _rd_G_CTX_BAD = 150, 250          # orchestrator context, thousands of tokens: amber from WARN, red above BAD


def _rd_g_fit_words(text: str, n: int) -> str:
    """The text cut to n columns at a word boundary, with … after the last whole word kept; a single word too long
    for the column is cut as it must be."""
    if _wd_width(text) <= n:
        return text
    out = ""
    for word in text.split():
        cand = f"{out} {word}" if out else word
        if _wd_width(cand) + 1 > n:
            break
        out = cand
    return out + "…" if out else _wd_trunc(text, n)


def _rd_g_project_dir(path: str) -> str:
    """A directory as its project sees it: no `#...` suffix, no trailing `/`, and a `/.stage` folder counts as the project."""
    path = path.split("#", 1)[0].rstrip("/")
    return path.removesuffix("/.stage")


def _rd_g_root(stage: Stage) -> str:
    """The stage's project directory: `dir` or `path`, as `_rd_g_project_dir` reads it."""
    return _rd_g_project_dir(stage.dir or stage.path)


def _rd_g_short_dir(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _rd_g_path_line(stage: Stage, w: int) -> str:
    """`↳ started in ~/x · workers in ~/Projects/y, ~/Projects/z` under a session whose start or worker dirs differ
    from its project label; empty when neither differs. Two dirs at most, then +N."""
    root = _rd_g_root(stage)
    parts = []
    cwd = _rd_g_project_dir(stage.cwd)
    if cwd and cwd != root:
        parts.append("started in " + _rd_g_short_dir(cwd))
    others = list(dict.fromkeys(d for d in (_rd_g_project_dir(x) for x in stage.workdirs) if d and d != root))
    if others:
        parts.append("workers in " + ", ".join(_rd_g_short_dir(d) for d in others[:2]) + (f" +{len(others) - 2}" if len(others) > 2 else ""))
    if not parts:
        return ""
    return _wd_clip("     " + _rd_dim("↳ " + " · ".join(parts)), w)


def _rd_g_doing_lines(stage: Stage, w: int, now: float) -> list[str]:
    """An open solo session's goal, then its latest actions, oldest first, each `HH:MM  Edit render.py`; the newest is the
    brightest and carries its age. With no actions yet, the stage's doing_note says why, else the transcript line does."""
    out = [_wd_clip("     " + _rd_dim("┊ goal  ") + _rd_muted(_rd_g_plain(stage.goal)), w)] if stage.goal else []
    if not stage.doing:
        followed = stage.lead_kind.startswith(("claude", "opencode", "openclaw"))   # the kinds whose transcripts are read
        why = stage.doing_note or ("nothing in its transcript yet" if followed else "no readable activity (only Claude, OpenCode and OpenClaw sessions are followed)")
        return out + [_wd_clip("     " + _rd_dim("┊ " + why), w)]
    for i, (ts, text) in enumerate(stage.doing):
        newest = i == len(stage.doing) - 1
        paint = _rd_muted if newest else _rd_dim
        age = _rd_dim("  " + _wd_fmt_dur(max(0.0, now - ts)) + " ago") if newest else ""
        out.append(_wd_clip("     " + _rd_dim("┊ " + _wd_fmt_clock(ts) + "  ") + paint(_rd_g_plain(text)) + age, w))
    return out


def _rd_g_ctx_limits() -> tuple[int, int]:
    """The amber and red limits of the orchestrator context, in tokens: STATUS_CTX_WARN and STATUS_CTX_BAD (thousands),
    150 and 250 when unset or not a number."""
    def limit(name: str, default: int) -> int:
        try:
            value = int(float(os.environ.get(name, "")) * 1000)
        except (ValueError, OverflowError):
            return default * 1000
        return value if value >= 1000 else default * 1000   # zero or negative would turn every session amber
    return limit("STATUS_CTX_WARN", _rd_G_CTX_WARN), limit("STATUS_CTX_BAD", _rd_G_CTX_BAD)


def _rd_g_ctx_text(tokens: int | None) -> str:
    """`229k ctx`; empty when the session's context is unknown."""
    return "" if tokens is None else _wd_fmt_tok(tokens) + " ctx"


def _rd_g_ctx_cell(tokens: int | None, w: int) -> str:
    """The context figure in its column, padded to `w`: normal under the warn limit, amber up to the bad one, red above."""
    if tokens is None or not w:
        return ""
    warn, bad = _rd_g_ctx_limits()
    color = _wd_C_RED if tokens > bad else _wd_C_AMBER if tokens >= warn else _wd_C_MUTED
    return _wd_c(_wd_pad(_rd_g_ctx_text(tokens), w), color)


# --- quips: one short phrase per station, picked by what one of its workers is doing ------------------------
# The built-in phrases are placeholders. Put your own in ~/.config/station/quips.md (headings + bullets; see
# docs/QUIPS.md) or quips.json ({"working": [...], ...}), or name a file with STATUS_QUIPS. Empty lists keep the defaults.
_rd_QUIP_STATES = ("working", "lagging", "idle", "failed")
_rd_QUIPS_DEFAULT: dict[str, list[str]] = {
    "working": ["on task", "making progress", "steady output"],
    "lagging": ["slower than usual", "quiet for a while", "may be stuck"],
    "idle": ["standing by", "nothing queued", "waiting for work"],
    "failed": ["ended with an error", "needs a retry", "did not finish"],
}
_rd_QUIP_ROTATE_S = 90         # a station keeps one phrase this long, then moves to the next
_rd_QUIPS: dict[str, list[str]] | None = None


def _rd_quips_paths() -> list[str]:
    env = os.environ.get("STATUS_QUIPS")
    if env:
        return [env]
    base = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "station")
    return [os.path.join(base, "quips.md"), os.path.join(base, "quips.json")]


def _rd_quips_from_markdown(text: str) -> dict[str, list[str]]:
    """`## working` style headings, each followed by `- phrase` bullets. Empty bullets and <!-- comments --> are skipped."""
    out: dict[str, list[str]] = {}
    state = ""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    for line in text.splitlines():
        head = re.match(r"^#{2,3}\s+(\w+)", line)
        if head:
            word = head.group(1).lower()
            state = word if word in _rd_QUIP_STATES else ""
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*\S)\s*$", line)
        if state and bullet:
            out.setdefault(state, []).append(bullet.group(1))
    return out


def _rd_g_quips() -> dict[str, list[str]]:
    """Phrase pools by state: the defaults, with any non-empty list from the quips file replacing its state's default."""
    global _rd_QUIPS
    if _rd_QUIPS is None:
        pools = {k: list(v) for k, v in _rd_QUIPS_DEFAULT.items()}
        for path in _rd_quips_paths():
            try:
                with open(path, encoding="utf-8") as stream:
                    raw = stream.read()
                data = _rd_quips_from_markdown(raw) if path.endswith(".md") else json.loads(raw)
                for key in _rd_QUIP_STATES:
                    lines = [_wd_clean(x).strip() for x in data.get(key, []) if isinstance(x, str) and x.strip()]
                    if lines:
                        pools[key] = lines
                break
            except (OSError, ValueError, AttributeError, TypeError):
                continue
        _rd_QUIPS = pools
    return _rd_QUIPS


def _rd_g_quip_state(wk: Worker) -> str:
    if wk.state == RUNNING:
        return "working"
    if wk.state in (QUIET, STALLED):
        return "lagging"
    if wk.state == FAILED:
        return "failed"
    return "idle"   # done and finished with no report both stand by: neutral, never "ended with an error"


def _rd_g_quip_worker(row: _rd_GRow) -> Worker | None:
    """The one worker who speaks for a station: someone failed or lagging first, then a running one, then the
    newest finished one."""
    for states in ((FAILED,), (QUIET, STALLED), (RUNNING,), (DONE, NOREPORT)):
        pick = next((wk for wk in row.workers if wk.state in states), None)
        if pick is not None:
            return pick
    return None


def _rd_g_quip(row: _rd_GRow, now: float) -> tuple[Worker, str, str] | None:
    """(speaker, state, phrase) for a station. Stable inside a _rd_QUIP_ROTATE_S window, then rotates."""
    wk = _rd_g_quip_worker(row)
    if wk is None:
        return None
    state = _rd_g_quip_state(wk)
    if state == "idle" and row.stage.lead_state in ("live", "background"):
        return None   # a live lead is not "standing by" because one of its workers finished
    pool = _rd_g_quips()[state]
    seed = sum(ord(c) for c in row.stage.path + wk.name) + int(now // _rd_QUIP_ROTATE_S)
    return wk, state, pool[seed % len(pool)]


_rd_QUIP_COLORS = {"working": _wd_C_GREEN, "lagging": _wd_C_AMBER, "idle": _wd_C_DIM, "failed": _wd_C_RED}


def _rd_g_quip_text(row: _rd_GRow, now: float) -> str:
    """`name “phrase”`, dim, coloured by state; empty when the station has no worker to speak."""
    quip = _rd_g_quip(row, now)
    if quip is None:
        return ""
    wk, state, phrase = quip
    return _wd_c(_wd_trunc(_wd_clean(wk.name), 18), _wd_C_DIM) + " " + _wd_c("“" + phrase + "”", _rd_QUIP_COLORS[state])


def _rd_g_legend(w: int) -> list[str]:
    """Key lines: who is who. A team is a ◆ row with workers under it on ├─; a solo lead is a teal ◇ row with none."""
    team = (_wd_c("◆ team", _wd_C_GREEN) + _rd_dim("  ") + _wd_c("◇ solo", _wd_C_TEAL)
            + _rd_dim("   a team has workers under it; a solo lead has none"))
    lead = (_wd_c("◐", _wd_C_AMBER) + _rd_dim(" idle  ") + _wd_c("○", _wd_C_FAINT) + _rd_dim(" closed"))
    work = (_wd_c("├─ ", _wd_C_FAINT) + _rd_sub("worker") + "  " + _wd_c("⠋", _wd_C_SUB) + _rd_dim(" running  ")
            + _wd_c("◌", _wd_C_AMBER) + _rd_dim(" quiet  ") + _wd_c("▲", _wd_C_RED) + _rd_dim(" stalled  ")
            + _wd_c("✔", _wd_C_GREEN) + _rd_dim(" done  ") + _wd_c("✖", _wd_C_RED) + _rd_dim(" failed  ")
            + _wd_c("○", _wd_C_AMBER) + _rd_dim(" no report"))
    quip = _rd_dim("“…” a worker's remark: ") + "  ".join(
        _wd_c(s, _rd_QUIP_COLORS[s]) for s in _rd_QUIP_STATES) + _rd_dim("   (t hides, i hides this key)")
    lines = [team, lead, work, quip]
    return [_wd_clip(x, w) for x in lines]


def _rd_g_worker_line(row: _rd_GRow, wk: Worker, last: bool, w: int, t: float, selected: bool,
                      opened: bool, now: float) -> tuple[list[str], int | None]:
    """The worker's row, then its detail when opened. Also the index of the detail's Files link line, if any.
    Callers that know the tree prefix use `_rd_g_worker_row` directly."""
    prefix = "└─ " if last else "├─ "
    cont = "   " if last else "│  "
    return _rd_g_worker_row(row, wk, prefix, cont, _og_plan(w), w, t, selected, opened, now)


def _rd_g_name_tint(wk: Worker, fade: float, age: float, flash: _wd_Rgb | None, heat: float) -> _wd_Rgb | None:
    """The name's colour when motion wants one: fade in from FAINT, flash the result, brighten to CYAN on a tool call."""
    if wk.state in LIVE_STATES:
        base = _wd_lerp(_wd_C_FAINT, _wd_C_TEXT, min(1.0, fade))
        if flash is not None:
            base = _wd_lerp(flash, base, min(1.0, age / 1.5))
        if heat > 0:
            base = _wd_lerp(base, _wd_C_CYAN, min(1.0, heat))
        return base if base != _wd_C_TEXT else None
    if flash is not None and 0 <= age < 1.5:
        return _wd_lerp(flash, _wd_C_MUTED, min(1.0, age / 1.5))
    return None


def _rd_g_worker_row(row: _rd_GRow, wk: Worker, prefix: str, cont: str, plan: _og_Plan, cols: int, t: float,
                     selected: bool, opened: bool, now: float, cc_expanded: bool = False) -> tuple[list[str], int | None]:
    """One worker via `_og_row`, with the fade-in, result flash and tool-call pulse on its name; detail when opened."""
    cells = _og_worker_cells(wk, now)
    if opened or plan.control_center:
        cells.now, cells.sub = "", False   # the detail below already shows the step in full; no copy on a sub-line
    fade, age, flash = _rd_g_worker_timing(row.stage, wk, t)   # also records the worker for the fade-in and result flash
    tint = _rd_g_name_tint(wk, fade, age, flash, _og_pulse(wk, now))
    lines = _og_row(plan, prefix, cont, _rd_mark(wk, t), _wd_clean(wk.name), cells, now,
                    why="" if plan.control_center else _os_why(wk), selected=False)
    if tint is not None:
        name_w = max(0, plan.cols - plan.fixed - _wd_width(prefix) - 2)
        shown = _wd_trunc(_wd_clean(wk.name), name_w)
        lines[0] = lines[0].replace(shown, _wd_c(shown, tint, bold=cells.live), 1)
    if selected:
        lines = [_wd_bg_line(line, _wd_C_SELBG, plan.cols) for line in lines]
    link_at = None
    if opened:
        changes = worker_changes(row.stage.dir or row.stage.path, wk, now)
        detail, detail_link = _rd_g_worker_detail(wk, cols, now, t, changes, plan.control_center and cc_expanded)
        link_at = len(lines) + detail_link
        lines += detail
    return lines, link_at


_rd_G_FILES_LINK = "f files →"


def _rd_g_changes(changes: WorkerChanges | None, now: float, iw: int) -> str:
    """The changes line of an opened worker: what it has changed and how recently, then the link to its Files page."""
    link = _wd_c(_rd_G_FILES_LINK, _wd_C_BLUE)
    label = _rd_muted("changes  ")
    if changes is None:
        body = _rd_dim("not known: git did not answer")
    elif changes.read_only:
        body = _rd_dim("read-only: no file changes expected")
    elif changes.files == 0:
        body = _rd_muted("no changes yet")
    else:
        parts = [_rd_sub(f"{changes.files} file{'s' if changes.files != 1 else ''}")]
        if changes.scope == "git":
            parts.append(_wd_c(f"+{changes.added}", _wd_C_GREEN) + " " + _wd_c(f"−{changes.removed}", _wd_C_RED))
        else:
            parts.append(_rd_dim("in this folder"))
        if changes.last_edit is not None:
            age = now - changes.last_edit
            color = _wd_C_GREEN if age < 60 else _wd_C_AMBER if age < 300 else _wd_C_DIM
            parts.append(_wd_c("last edit " + _wd_fmt_dur(age) + " ago", color))
        body = _rd_dot().join(parts)
    room = max(0, iw - _wd_width("changes  ") - _wd_width(_rd_G_FILES_LINK) - 3)
    return label + _wd_clip(body, room) + "   " + link


def _rd_g_worker_detail(wk: Worker, w: int, now: float, t: float,
                        changes: WorkerChanges | None, command_above: bool = False) -> tuple[list[str], int]:
    """The opened worker's detail lines, and the index of its changes line (the Files link).
    `command_above`: the control center already printed the newest step in full above the card, so the list skips it."""
    pad, iw = "         ", max(10, w - 12)
    bar = _wd_c("│ ", _wd_C_FAINT)
    out = []
    goal = wk.goal or wk.title
    goal_w = max(10, iw - 6)
    parts = _wd_wrap(_wd_clean(goal), goal_w) if goal else ["no goal recorded"]
    if len(parts) > 3:      # the fourth line on: mark that the sentence continues
        parts = parts[:3]
        parts[-1] = _wd_trunc(parts[-1] + " …", goal_w)
    for k, part in enumerate(parts):
        out.append(pad + bar + (_rd_dim("goal  ") if k == 0 else "      ") + _rd_sub(part))
    facts = [_rd_kv("model", _wd_clean(display_model(wk.model or wk.log.model) or "?"))]
    if wk.pid:
        facts.append(_rd_kv("pid", str(wk.pid)))
    tokens = _rd_g_tokens(wk)
    if tokens is not None or wk.log.cost is not None:
        facts.append(_rd_kv("spend", _rd_g_spend(tokens, wk.log.cost).strip()))
    if wk.state in LIVE_STATES:
        facts.append(_rd_kv("output", _wd_fmt_dur(max(0.0, wk.quiet_s)) + " ago" if wk.log.steps else "none yet"))
        if wk.cpu is not None:
            facts.append(_rd_kv("cpu", f"{wk.cpu:.0f}%"))
    out.append(pad + bar + _wd_clip("   ".join(facts), iw))
    link_at = len(out)
    out.append(pad + bar + _rd_g_changes(changes, now, iw))
    if wk.state in LIVE_STATES or not wk.summary:
        steps = wk.log.steps[:-1] if command_above else wk.log.steps
        for step in steps[-_rd_G_STEPS:]:
            tag = _wd_pad(step.tool or step.kind, 6)
            out.append(pad + bar + _rd_dim("› " + tag) + _rd_muted(_wd_trunc(_wd_clean(step.text), max(1, iw - 9))))
    else:
        lines = [x for x in wk.summary if x.strip()]
        for i, line in enumerate(lines[:_rd_G_STEPS]):
            text = _rd_g_plain(line)
            if i == _rd_G_STEPS - 1 and len(lines) > _rd_G_STEPS:   # more report follows: mark the cut
                text = _wd_trunc(text + " …", iw)
            else:
                text = _wd_trunc(text, iw)
            out.append(pad + bar + _rd_muted(text))
    return out, link_at


_rd_G_TASKS_SHOWN = 2   # tasks listed when a session opens; the fold line shows the rest
_rd_G_MILES_MAX = 40   # milestones listed when a session's list is expanded


def _rd_g_meta(row: _rd_GRow, w: int, expanded: bool = False, depth: int = 0) -> list[str]:
    """A session's latest milestone, with the review counts at the right; `M` expands it to every milestone, newest first.
    No lines at all when it has no milestone."""
    snap = row.stage.snap
    if not snap.milestones:
        return []
    shown = list(reversed(snap.milestones))[:_rd_G_MILES_MAX] if expanded else [snap.milestones[-1]]
    counts = [0, 0, 0]
    for r in snap.reviews:
        for f in r.findings:
            counts[min(max(f.prio, 1), 3) - 1] += 1
    right = ""
    if snap.reviews or sum(counts):
        right = _rd_muted(f"reviews {sum(counts)} · ") + "  ".join(
            _wd_c(f"P{i + 1} {n}", _rd_prio_color(i + 1) if n else _wd_C_FAINT) for i, n in enumerate(counts))
    pad = "     " + "  " * depth
    out = []
    for i, m in enumerate(shown):
        stamp = _wd_fmt_clock(m.ts) + " " if m.ts is not None else ""
        left = _wd_c("⚑ " if i == 0 else "  ", _wd_C_VIOLET) + (_rd_muted if i == 0 else _rd_dim)(_rd_g_plain(stamp + m.text))
        tail = right if i == 0 else ""
        if i == 0 and not expanded and len(snap.milestones) > 1:
            tail = (tail + "  " if tail else "") + _rd_dim(f"M all {len(snap.milestones)}")
        out.append(pad + _wd_lr(left, tail, w - 5, 2))
    return out


def _rd_g_quota_line(gs: GlobalSnapshot, w: int, now: float) -> str:
    items: list[tuple[str, str]] = []   # (account text, countdown or "")
    ranked = []
    quota = gs.quota if gs.quota_ok else []   # spend chips still show when the quota command is down
    for acct in quota:
        if not acct.windows:
            continue
        win = _rd_quota_winner(acct)
        if win is None:
            continue
        ranked.append((win.used, acct, win))
    for used, acct, win in sorted(ranked, key=lambda x: -x[0]):
        color = _wd_C_RED if used >= 80 else _wd_C_AMBER if used >= 60 else _wd_C_TEXT
        text = (_rd_dim(_wd_clean(acct.name)) + " " + _wd_c(f"{round(used)}%", color, bold=True)
                + (" " + _wd_c("+$" + (f"{acct.extra.usd:.2f}" if acct.extra.usd is not None else "?"), _wd_C_RED)
                   if acct.extra is not None else ""))
        items.append((text, _rd_ttr(win.resets_at, now)))
    spend_shown = {pid for pid in SPEND_PROVIDER_ORDER if gs.spend_ok.get(pid) and gs.spend.get(pid)}
    for pid in SPEND_PROVIDER_ORDER:
        snap = gs.spend.get(pid)
        if not gs.spend_ok.get(pid) or snap is None or not _rd_spend_masthead_chip(snap):
            continue
        items.append((_rd_spend_chip_paint(snap), ""))
    for acct in quota:
        if acct.name in spend_shown:
            continue
        if acct.credit is not None and acct.credit.balance is not None:
            items.append((_rd_dim(_wd_clean(acct.name)) + " "
                          + _wd_c(f"${acct.credit.balance:.2f}", _wd_C_RED if acct.credit.low else _wd_C_TEXT, bold=True), ""))
    if not items:
        return _rd_dim("quota offline")

    def build(with_ttr: bool, n: int) -> str:
        parts = [text + (" " + _rd_muted(ttr) if with_ttr and ttr else "") for text, ttr in items[:n]]
        more = _rd_dim(f"  +{len(items) - n}") if n < len(items) else ""
        return "   ".join(parts) + more

    if _wd_width(build(True, len(items))) <= w:          # everything fits with countdowns
        return _wd_clip(build(True, len(items)), w)
    if _wd_width(build(False, len(items))) <= w:         # drop the countdowns before any account
        return _wd_clip(build(False, len(items)), w)
    shown = len(items)
    while shown > 1 and _wd_width(build(False, shown)) > w:
        shown -= 1
    return _wd_clip(build(False, shown), w)


def _rd_g_chips(gs: GlobalSnapshot, ui: UIState, w: int, y: int) -> str:
    rows = [r for r in _rd_g_all_rows(gs) if _rd_g_query(r, ui.gquery)]
    short = {"all": "all", "working": "work", "lagging": "lag", "problems": "prob"}

    def build(label: Callable[[str], str]) -> tuple[list[str], list[int]]:
        out: list[str] = []
        widths: list[int] = []
        for i, name in enumerate(_rd_G_FILTERS):
            n = sum(_rd_g_match(r, name) for r in rows)
            count = "" if w <= 50 else f" {n}"   # a phone has no room for the counts: the filter box must fit
            text = f"{i + 1} {label(name)}{count}"
            if ui.gfilter == name:
                out.append(_wd_c("[", _wd_C_BLUE) + _wd_c(text, _wd_C_TEXT, bold=True) + _wd_c("]", _wd_C_BLUE))
            else:
                out.append(" " + _rd_dim(f"{i + 1} ") + _rd_muted(label(name)) + _rd_dim(count) + " ")
            widths.append(len(text) + 2)
        return out, widths
    parts, widths = build(lambda n: n)
    if sum(widths) + 3 + 10 > w:
        parts, widths = build(lambda n: short[n])
    x = 0
    for i, width in enumerate(widths):
        _rd_hit(ui, y, y, x, x + width - 1, "key", str(i + 1))
        x += width + 1
    usage_label = "5 usage"      # the usage screen: quota windows plus one section per spend provider
    if ui.view == "quota":
        usage_chip = _wd_c("[", _wd_C_BLUE) + _wd_c(usage_label, _wd_C_TEXT, bold=True) + _wd_c("]", _wd_C_BLUE)
    else:
        usage_chip = " " + _rd_dim("5 ") + _rd_muted("usage") + " "
    usage_w = len(usage_chip) + 2
    if x + usage_w + 10 <= w:
        _rd_hit(ui, y, y, x, x + usage_w - 1, "key", "5")
        parts.append(usage_chip)
        widths.append(usage_w)
        x += usage_w + 1
    if ui.gedit:
        box = _wd_c("/ ", _wd_C_BLUE) + _wd_c(ui.gquery, _wd_C_TEXT) + _wd_c("▌", _wd_C_BLUE)
    elif ui.gquery:
        box = _wd_c("/ ", _wd_C_BLUE) + _wd_c(ui.gquery, _wd_C_TEXT) + _rd_dim("  esc clears")
    else:
        box = _rd_dim("/ filter")
    room = w - x - 2
    if room < 3:
        return _wd_clip(" ".join(parts), w)
    _rd_hit(ui, y, y, x + 1, min(w - 1, x + 24), "key", "/")
    return _wd_clip(" ".join(parts) + "  " + _wd_clip(box, room), w)


_rd_TITLE_ID = 51   # kitty graphics image id of the title
_rd_TITLE_ROWS = {4: 24, 3: 20, 2: 16}   # image height in text rows -> the terminal rows needed to afford it (art + stats, quota, keys, a few body rows)


def _rd_g_title_cells(ui: UIState, w: int, rows: int) -> list[str] | None:
    """Blank rows for the title picture, which the app places over them (kitty graphics), as many text rows as fit; the size
    is left in `ui.title_place`. None when the terminal shows no images or the room is short."""
    ui.title_place = (0, 0)
    if ui.title_aspect <= 0:
        return None
    for height, need in _rd_TITLE_ROWS.items():
        cols = round(height * ui.title_aspect)
        if rows >= need and cols + 2 <= w:
            ui.title_place = (cols, height)
            return [""] * (height + 1)   # one spare row of air under the picture
    return None


_rd_G_RULE_MIN = (60, 30)   # columns, rows: the heading gets a thin rule underneath only on a terminal at least this big


def _rd_g_masthead(gs: GlobalSnapshot, ui: UIState, w: int, rows: int, t: float, ntotal: int) -> list[str]:
    live = sum(_rd_g_is_working(s) for s in gs.stages)
    lag = sum(wk.state in (QUIET, STALLED) for s in gs.stages for wk in s.snap.workers)
    title_text = _RD_HEADING.upper()
    spaced = "   ".join(" ".join(word) for word in title_text.split(" "))
    title = spaced if len(spaced) + 3 <= w else title_text
    caret = _wd_c("▌", _wd_C_BLUE) if int(t * 1.6) % 2 == 0 else " "
    out = _rd_g_title_cells(ui, w, rows)   # the picture shrinks with the window; it only goes when even the smallest does not fit
    compact = rows < 45 or w < 70   # a short or phone screen without the picture: the title collapses to one line
    if out is None and compact:
        out = [_wd_c("◆ Station", _wd_C_BLUE, bold=True) + " " + caret]
    elif out is None:
        out = [_wd_c(title, _wd_C_BLUE, bold=True) + " " + caret]
        if w >= _rd_G_RULE_MIN[0] and rows >= _rd_G_RULE_MIN[1]:   # a thin rule under the title
            out.append(_wd_c("▔" * min(_wd_width(title), w), _wd_C_BLUE))
    sep = "  "
    attn = sum(r.problems > 0 for r in _rd_g_all_rows(gs))
    n_active = sum(_rd_g_section(s) == "active" for s in gs.stages)
    n_idle = sum(_rd_g_section(s) == "idle" for s in gs.stages)
    n_fin = sum(_rd_g_section(s) == "closed" for s in gs.stages) + gs.hidden_idle + gs.dismissed
    working = _wd_c(f"{live} working", _wd_C_GREEN if live else _wd_C_SUB)
    lagging = _wd_c(f"{lag} lagging", _wd_C_AMBER if lag else _wd_C_SUB)
    problems = _wd_c(f"{attn} problem", _wd_C_RED if attn else _wd_C_SUB)
    live_s = _wd_c(f"{n_active} live", _wd_C_GREEN if n_active else _wd_C_SUB)
    idle_s = _wd_c(f"{n_idle} idle", _wd_C_AMBER if n_idle else _wd_C_SUB)
    fin_s = _rd_dim(f"{n_fin} finished")
    cost_s = (_wd_c(_wd_fmt_money(gs.cost_total), _wd_C_TEXT) if gs.cost_total is not None
              else _wd_c("—", _wd_C_FAINT)) + _wd_c(" today", _wd_C_MUTED)
    bar = _wd_c("│", _wd_C_BORDER)
    loading = _rd_sub("loading…") if getattr(gs, "partial", False) else ""   # the quick first pass: no full pass yet
    lead = (loading + sep) if loading else ""
    full = lead + sep.join((live_s, idle_s, fin_s)) + sep + bar + sep + sep.join((working, lagging, problems)) + sep + bar + sep + cost_s
    mid = lead + sep.join((live_s, idle_s)) + sep + bar + sep + sep.join((working, lagging, problems)) + sep + bar + sep + cost_s
    lean = lead + sep.join((live_s, idle_s)) + sep + bar + sep + sep.join((working, problems)) + sep + bar + sep + cost_s
    short = lead + sep.join((working, problems)) + sep + cost_s
    dot = _wd_c("●" if live else "○", _wd_C_BLUE if live and int(t * 1.4) % 2 == 0 else _wd_C_DIM)
    clock = dot + " " + _rd_sub(_rd_hms(t))
    for stats in (full, mid, lean, short, lead + working):
        if _wd_width(stats) + 2 + _wd_width(clock) <= w:
            out.append(_wd_lr(stats, clock, w, 2))
            break
    else:
        out.append(_wd_clip(lead + working, w))   # a phone keeps the working count
    if rows >= 22:
        out.append(_rd_g_quota_line(gs, w, t))
    return out


def _rd_g_strip_suffix(label: str) -> str:
    """A stage label without the collector's disambiguation suffixes: the text before the first ` · `
    (`sample-project · claude bg`), then a trailing ` #<digits>` (`sample-project #2`)."""
    return re.sub(r" #\d+$", "", label.split(" · ", 1)[0]).strip()


def _rd_g_short_labels(stages: list[Stage]) -> dict[str, str]:
    """Each project root's name, cut to the shortest trailing path part that is still unique among the roots:
    `Projects/openclaw` -> `openclaw`. Rows of one root (a real stage and its virtual row) share one name, the root's longest
    label with its disambiguation suffixes stripped; path parts stay only when two different directories share a
    basename. Keyed by `_rd_g_root`."""
    full: dict[str, str] = {}
    for stage in stages:
        label = _rd_g_strip_suffix(_rd_g_label(stage)) or _rd_g_label(stage)
        if stage.path.startswith("execution:") and _rd_g_root(stage).startswith("/"):
            label = os.path.basename(_rd_g_root(stage)) or label   # a headless run's title is not its project's name
        if len(label) > len(full.get(_rd_g_root(stage), "")):
            full[_rd_g_root(stage)] = label
    names = list(full.values())   # one per root: two roots with the same label count twice
    out: dict[str, str] = {}
    for root, label in full.items():
        parts = [x for x in label.replace("\\", "/").split("/") if x] or [label]
        for k in range(1, len(parts) + 1):
            cand = "/".join(parts[-k:])
            if sum(1 for other in names if other == cand or other.endswith("/" + cand)) <= 1 or k == len(parts):
                out[root] = cand
                break
    return out


def _rd_g_session_names(stages: list[Stage]) -> dict[str, str]:
    """Each session row's own name, unique within its project root: `{stage.path: name}`.
    The base is the stage's `session_name` (cleaned); when empty, the goal's first 4 words, else empty (the row keeps no name).
    Two or more sessions of one root with the same base (case-insensitive) all get ` #` plus the first 4 characters
    of their session_id (or of a hash of the path when there is none); unique bases stay as they are.
    An unlinked group (no session of its own) keeps an empty name, as its row shows only the project name."""
    out: dict[str, str] = {}
    bases: dict[str, tuple[Stage, str, str]] = {}   # path -> (stage, root, base)
    for stage in stages:
        if stage.unlinked:
            out[stage.path] = ""
            continue
        base = _wd_clean(stage.session_name).strip()
        if not base:
            base = " ".join(_wd_clean(stage.goal).split()[:4])
        if not base:
            out[stage.path] = ""   # nothing to show: the row keeps its old empty name column
            continue
        bases[stage.path] = (stage, _rd_g_root(stage), base)
    counts: dict[tuple[str, str], int] = {}
    for _, root, base in bases.values():
        counts[(root, base.lower())] = counts.get((root, base.lower()), 0) + 1
    for path, (stage, root, base) in bases.items():
        if counts[(root, base.lower())] < 2:
            out[path] = base
            continue
        key = stage.session_id or hashlib.sha1(stage.path.encode("utf-8")).hexdigest()
        out[path] = f"{base} #{key[:4]}"
    return out


_rd_G_TOUCH = (("Filter", "f"), ("Open", "e"), ("Close", "c"), ("Page", "Space"), ("▲", "Up"), ("▼", "Down"))


def _rd_g_touch_bar(cols: int, ui: UIState, y: int, t: float) -> list[str]:
    """Three rows of big buttons for a finger: a padding row, the labels, a padding row. Each is a click target."""
    n = len(_rd_G_TOUCH)
    gap = 1
    width = (cols - gap * (n - 1)) // n
    rows = ["", "", ""]
    x = 0
    for label, key in _rd_G_TOUCH:
        face = _wd_C_SELBG
        text = label if label not in ("Filter",) else f"Filter {_rd_G_FILTERS.index(ui.gfilter) + 1}" if ui.gfilter != "all" else "Filter"
        cell_mid = _wd_c(_wd_center(text, width), _wd_C_TEXT if label in ("Filter", "Page") else _wd_C_SUB, bold=True)
        for r, body in enumerate((" " * width, cell_mid, " " * width)):
            rows[r] += _wd_bg_line(body, face, width) + (" " if x + width < cols - 1 else "")
        _rd_hit(ui, y, y + 2, x, x + width - 1, "key", key)
        x += width + gap
    return [_wd_fit(r, cols) for r in rows]


def _rd_g_column_rule(cols: int, marks: list[tuple[int, str]]) -> str:
    """The rule under the chips, carrying the column headings: orchestrator, stream, lead, workers."""
    out, at = _wd_c("─ ", _wd_C_BORDER), 2
    for x, label in sorted(marks):
        x = max(x, 2)
        if x < at or x + len(label) + 1 > cols:
            continue
        out += _wd_c(("─" * (x - at - 1) + " ") if x > at else "", _wd_C_BORDER) + _rd_dim(label) + _wd_c(" ", _wd_C_BORDER)
        at = x + len(label) + 1
    return out + _wd_c("─" * max(0, cols - at), _wd_C_BORDER)


def _rd_g_openrouter_lines(gs: GlobalSnapshot, iw: int) -> list[str]:
    or_data = gs.openrouter
    if not gs.openrouter_ok or or_data is None:
        return [_rd_dim("OpenRouter unavailable (quota openrouter --json failed)")]
    lines: list[str] = []
    if or_data.balance is None:
        head = _rd_muted("no balance recorded")
    else:
        head = _wd_c(f"${or_data.balance:.2f}", _wd_C_RED if or_data.low else _wd_C_TEXT, bold=True)
        bits = []
        if or_data.burn_per_day is not None:
            bits.append(f"${or_data.burn_per_day:.2f}/day")
        if or_data.runs_out_at:
            bits.append("out " + time.strftime("%Y-%m-%d", time.localtime(or_data.runs_out_at)))
        if or_data.floor_usd is not None:
            bits.append(f"floor ${or_data.floor_usd:.2f}")
        if bits:
            head += "  " + _rd_muted(" · ".join(bits))
        if or_data.stale:
            head += _rd_dim(" stale")
    lines.append(_rd_sub("OpenRouter") + "  " + head)
    if not or_data.per_day and not or_data.per_model:
        lines.append("")
        lines.append(_rd_dim("no OpenRouter spend yet"))
        return lines
    total = sum(row.usd for row in or_data.per_model)
    lines.append("")
    lines.append(_rd_sub(_wd_pad("day (UTC)", 12) + _rd_rjust("$", 8) + "  " + _rd_rjust("req", 5)))
    for row in sorted(or_data.per_day, key=lambda r: r.date, reverse=True):
        lines.append(_wd_pad(row.date, 12) + _rd_rjust(f"${row.usd:.2f}", 8) + "  " + _rd_rjust(str(row.requests), 5))
    lines.append("")
    lines.append(_rd_sub(_wd_pad("model", iw - 18) + _rd_rjust("$", 8) + "  " + _rd_rjust("share", 6)))
    for row in sorted(or_data.per_model, key=lambda r: r.usd, reverse=True):
        share = f"{100 * row.usd / total:.0f}%" if total > 0 else "–"
        model = _wd_trunc(row.model, max(8, iw - 26))
        lines.append(_wd_pad(model, max(8, iw - 26)) + _rd_rjust(f"${row.usd:.2f}", 8) + "  " + _rd_rjust(share, 6))
    if or_data.since:
        lines.append("")
        lines.append(_rd_dim(f"since {or_data.since} UTC"))
    return lines


def _rd_g_openrouter(gs: GlobalSnapshot, ui: UIState, cols: int, rows: int) -> list[str]:
    h = max(3, rows - 2)
    iw = cols - 2
    lines = _rd_g_openrouter_lines(gs, iw)
    ih = h - 2
    top_max = max(0, len(lines) - ih)
    first = min(max(int(ui.scroll), 0), top_max)
    ui.scroll = first
    _rd_hit(ui, 0, max(h, 1) - 1, 0, cols - 1, "scroll", "openrouter")
    body = _wd_box(lines[first:first + ih], cols, h, _rd_title_main("openrouter"), thumb=_wd_thumb(len(lines), ih, first))
    footer = _rd_keyline(_rd_OPENROUTER_KEYS, cols, ui, rows - 1)
    return body + [footer]


def _rd_g_session_mark(row: _rd_GRow, t: float) -> str:
    """◆ live (GREEN, breathing with its workers), ◐ idle (AMBER), ◇ closed; a solo lead is teal."""
    if row.solo:
        return _wd_c("◇", _wd_C_TEAL)
    if row.section == "active":
        low = _wd_lerp(_wd_C_GREEN, _wd_C_DIM, 0.5)
        breath = _wd_lerp(low, _wd_C_GREEN, (1 + math.sin(2 * math.pi * t / 1.4)) / 2)
        return _wd_c("◆", breath if row.live else _wd_C_GREEN)
    return _wd_c(_rd_G_MARKS[row.section], _rd_G_MARK_COLORS[row.section])


def _rd_g_session_name(row: _rd_GRow, names: dict[str, str], short: dict[str, str]) -> str:
    """The row's own name: the unique session name, then the openclaw display name, then the short project label."""
    stage = row.stage
    if stage.unlinked:   # workers whose launching session is unknown: say so, not a project-named LEAD with nothing in it
        return "session unknown"
    return (names.get(stage.path) or _wd_clean(stage.display_name)
            or short.get(_rd_g_root(stage)) or _rd_g_label(stage))


def _rd_g_swarm_workers(row: _rd_GRow) -> list[Worker]:
    """The workers of the session's current task, when there are enough of them for a swarm band."""
    tasks, _shown = _rd_g_tasks(row)
    if not tasks:
        return []
    current = next((members for group, members in tasks if group.current), max(tasks, key=lambda t: t[0].started)[1])   # no current task: the newest
    return list(current) if len(current) >= OG_SWARM_MIN else []


def _rd_g_suffix(line: str, suffix: str, cols: int) -> str:
    """Put a suffix at the right of a fitted row, over its blank tail."""
    room = max(0, cols - _wd_width(suffix) - 1)
    head = _wd_clip(line, room, ell=False)
    head = re.sub(r" +((?:\x1b\[[0-9;]*m)*)$", r"\1", head)
    return head + " " + suffix


def _rd_g_session_lines(plan: _og_Plan, node: _rd_GNode, names: dict[str, str], short: dict[str, str],
                        ui: UIState, t: float, now: float, selected: bool, swarm: list[Worker]) -> list[str]:
    """A session row; an open swarm's band under it, a folded swarm's `SWARM hive N/M` closing its name column."""
    row = node.row
    cells = _og_stage_cells(row.stage, now)
    opened = _rd_g_is_open(ui, row)
    tail = ""
    if row.folded_kids:     # folded over spawned sessions: say how many are inside, shorter as the name column narrows
        room = plan.name_w - _wd_width(node.prefix) - 2 - 1 - 8      # what the tail may take and still leave 8 cells of name
        n, bad = row.folded_kids, row.folded_problems
        plural = "s" if n != 1 else ""
        options = [(f"▸ {n} spawn{plural}", f" · ▲ {bad}"), (f"▸{n}", f" ▲{bad}")]
        tail = ""
        for word, flag in options:
            if _wd_width(word + (flag if bad else "")) <= room:
                tail = _wd_c(word, _wd_C_VIOLET) + (_wd_c(flag, _wd_C_RED) if bad else "")
                break
        else:   # no room: a problem still shows, a plain count may go
            tail = _wd_c(f"▲{bad}", _wd_C_RED) if bad else ""
    elif swarm and not opened:
        done = sum(wk.state not in LIVE_STATES for wk in swarm)
        word = _wd_c("SWARM ", _wd_C_VIOLET) if plan.name_w >= 44 else ""   # a phone keeps the hive and the count
        tail = word + _og_hive(swarm, t, now) + _wd_c(f" {done}/{len(swarm)}", _wd_C_SUB)
    lines = _og_row(plan, node.prefix, node.cont, _rd_g_session_mark(row, t),
                    _rd_g_session_name(row, names, short), cells, now, selected=selected, tail=tail)
    if swarm and opened:
        lines.insert(1, _wd_pad(_og_swarm_band(plan, node.cont, swarm, now, t), plan.cols))
    return lines


def _rd_g_project_line(plan: _og_Plan, node: _rd_GNode, ui: UIState, short: dict[str, str], now: float,
                       selected: bool, fill_open: set[str]) -> str:
    """A project heading: live projects carry the rolled-up spend, idle ones how long they have been quiet."""
    root = node.name[2:]
    prows = node.rows
    opened = _rd_g_project_open(ui, root, prows) or root in fill_open
    live = _rd_g_has_live(prows)
    right = "" if live else _rd_g_project_age(prows, now)   # the spend goes in the COST column
    return _og_project_head(plan, short.get(root, root), opened, live, _rd_g_counts_text(prows),
                            _rd_g_rollup_badge(prows), right, selected, _rd_g_project_totals(prows))


def _rd_g_closed_text(gs: GlobalSnapshot, closed: list[_rd_GRow]) -> str:
    """`31 closed · 4 need sweep · a shows closed · session-sweep cleans up`, plus `· N dismissed` when any.
    The tree's count line and --plain share this one wording."""
    needs = sum(1 for row in closed if _rd_g_needs_sweep(row.stage))
    text = f"{len(closed)} closed · {needs} need sweep  ·  a shows closed  ·  session-sweep cleans up"
    if gs.dismissed:
        text += f"  ·  {gs.dismissed} dismissed"
    return text


def _rd_g_closed_line_text(gs: GlobalSnapshot, nodes: list[_rd_GNode], plan: _og_Plan, selected: bool) -> str:
    """The closed sessions' one line, with the count and the sweep reminder."""
    closed = next((node.rows for node in nodes if node.kind == "closed-count"), [])
    line = _wd_c(_wd_trunc(_rd_g_closed_text(gs, closed), plan.cols), _wd_C_MUTED)
    return _og_selected(_og_fit_line(line, plan.cols), plan.cols, selected)


def _rd_g_fill(gs: GlobalSnapshot, ui: UIState, nodes: list[_rd_GNode], plan: _og_Plan, cols: int, budget: int,
               now: float, t: float, names: dict[str, str],
               short: dict[str, str]) -> tuple[dict[int, list[str]], set[str], int]:
    """Spend the body's spare rows, in order: steps under live workers, then a short recent-finished list.
    Idle projects never open here (the user 2026-10-24): only a live project or an explicit toggle opens one. Display-only: nothing here writes an expand key, and every row is recomputed each frame."""
    extra: dict[int, list[str]] = {}
    fill_open: set[str] = set()
    used = 0
    if budget <= 0:
        return extra, fill_open, used
    for i, node in enumerate(nodes):   # (1) the last few steps under each live worker
        if used >= budget:
            break
        if node.kind != "worker":
            continue
        if (node.row.stage.path, "worker", node.name) in ui.gopen or (plan.control_center and node.name in ui.expanded):
            continue   # an opened worker's detail already lists its steps; no second copy below it
        wk = next((x for x in node.row.workers if x.name == node.name), None)
        if wk is None or wk.state not in LIVE_STATES or not wk.log.steps:
            continue
        width = max(10, cols - _wd_width(node.cont) - 2)
        lines = [_wd_c(node.cont, _wd_C_BORDER) + "  " + _os_step_line(step, width, now, live=wk.state == RUNNING)
                 for step in list(reversed(wk.log.steps))[:_rd_G_STEPS]]
        take = lines[:budget - used]
        if take:
            extra[i] = take
            used += len(take)
    if used < budget:   # (2) a short recent-finished list under its project
        for i, node in enumerate(nodes):
            if used >= budget:
                break
            if node.kind != "more" or not node.name.startswith("finished:"):
                continue
            if ("", "more", node.name) in ui.gopen:
                continue
            recent = _rd_g_order([r for r in node.rows if r.section == "closed" and _rd_g_closed_recent(gs, r)])[:3]
            lines = []
            for row, (prefix, cont) in zip(recent, _ot_prefixes([1 for _ in recent])):
                fake = _rd_GNode(row, "stage", "", depth=1, prefix=prefix, cont=cont)
                lines += _rd_g_session_lines(plan, fake, names, short, ui, t, now, False, [])
            take = lines[:budget - used]
            if take:
                extra.setdefault(i, []).extend(take)
                used += len(take)
    return extra, fill_open, used


def _rd_g_tree(gs: GlobalSnapshot, ui: UIState, cols: int, rows: int, t: float,
               control_center: bool = False) -> list[str]:
    has_unattached = any(s.unattached for s in gs.stages)
    unattached_n = _rd_g_unattached_n(gs)
    gs = _rd_g_shown(gs, ui)     # the masthead, chips and rows count only what is shown
    now = max(gs.now, t)   # wall time keeps live durations and ages ticking between collector polls
    nodes = _rd_g_nodes(gs, ui)
    plan = _og_plan(cols, control_center, _rd_g_metric_cells(nodes, now))
    sel = max(0, min(int(ui.gsel), len(nodes) - 1)) if nodes else 0
    head = _rd_g_masthead(gs, ui, cols, rows, t, len(gs.stages))
    chips_y = len(head)
    head.append(_rd_g_chips(gs, ui, cols, chips_y))
    if ui.glegend and rows >= 26:
        head += _rd_g_legend(cols)
    head.append(_rd_g_column_rule(cols, [(2, "session · harness · model · ctx · now")] if ui.glegend else []))
    head.append(_og_header(plan))   # the one width plan's column header, always under the chips rule
    top = len(head)
    touch = cols < 90 and rows >= 24                # a phone: big buttons at the bottom, air between stations
    visible_rows = _rd_g_rows(gs, ui)
    count = {name: sum(1 for r in visible_rows if r.section == name) for name in _rd_G_HEADED}
    notes = []
    if ui.note:   # the last key's result, e.g. "<label>: moved to idle"; a key clears it
        notes.append(_rd_sub(ui.note))
    if has_unattached:
        notes.append(_rd_dim(f"{unattached_n} unattached runs (A to {'hide' if _rd_g_show_unattached(ui) else 'show'})"))
    for name in _rd_G_HEADED:
        if _rd_g_section_hidden(ui, name) and count[name]:
            notes.append(_rd_dim(f"{name} hidden · {_rd_G_SECTION_KEYS[name]} shows"))
    note = "   ".join(notes)
    foot = 3 if touch else 1
    avail = max(0, rows - top - foot - (1 if note else 0))
    names = _rd_g_session_names(gs.stages)
    short = _rd_g_short_labels(gs.stages)
    idle_projects = sum(1 for _key, prows, _subs in _rd_g_buckets("", _rd_g_rows(gs, ui))
                        if not _rd_g_has_live(prows) and any(r.section == "idle" for r in prows))
    heads: dict[tuple[str, str], Group] = {}     # (station path, worker name) -> the task whose label goes above that worker
    for node in nodes:
        if node.kind == "stage" and not node.row.solo and _rd_g_is_open(ui, node.row):
            tasks, shown = _rd_g_tasks(node.row)
            if len(tasks) > 1:
                heads.update({(node.row.stage.path, members[0].name): group
                              for group, members in tasks[:shown] if members})
    blocks: list[list] = []      # [layout index, lines, the row of the selectable line]
    cc_command_hits: dict[int, int] = {}
    links: dict[int, tuple[int, str, str]] = {}        # layout index -> (row in its block of the Files link, stage, worker)
    for i, node in enumerate(nodes):
        r, selected = node.row, i == sel
        lines: list[str] = []
        if node.kind == "group":
            lines.append(_rd_g_project_line(plan, node, ui, short, now, selected, set()))
        elif node.kind == "idle":
            lines.append(_og_idle_divider(plan, idle_projects, selected))
        elif node.kind == "closed-count":
            lines.append(_rd_g_closed_line_text(gs, nodes, plan, selected))
        elif node.kind == "stage":
            lines += _rd_g_session_lines(plan, node, names, short, ui, t, now, selected, _rd_g_swarm_workers(r))
        elif node.kind == "worker":
            wk = next(x for x in r.workers if x.name == node.name)
            if (r.stage.path, node.name) in heads:
                lines.append(_rd_g_task_head(heads[(r.stage.path, node.name)], cols, node.depth))
            cc_expanded = control_center and node.name in ui.expanded
            wl, link_at = _rd_g_worker_row(r, wk, node.prefix, node.cont, plan, cols, t, selected,
                                           (r.stage.path, "worker", node.name) in ui.gopen or cc_expanded, now, cc_expanded)
            if control_center:
                finished = wk.state not in LIVE_STATES and wk.state != FAILED   # the user 2026-10-24: a done worker is one line
                shown = cc_expanded or (r.stage.path, "worker", node.name) in ui.gopen   # opened by click or by Enter
                command = [] if finished and not shown else command_lines(
                    wk, cols, cc_expanded, node.cont, plan.cols - plan.fixed - 1, 2)
                if command:
                    wl[1:1] = command
                    cc_command_hits[i] = len(lines) + 1
                if link_at is not None:   # the command lines went in above the card, so its Files link moved down
                    link_at += len(command)
            if link_at is not None:
                links[i] = (len(lines) + link_at, r.stage.path, node.name)
            lines += wl
        elif node.kind == "task":
            group = next(g for g, _ in _rd_g_tasks(r)[0] if g.key == node.name)
            lines.append(_rd_g_task_line(group, True, selected, (r.stage.path, "task", node.name) in ui.gopen,
                                         now, cols, node.depth))
        else:   # a `+N done` fold under its session, or the project's finished fold
            if node.name.startswith("finished:"):
                recent = [x for x in node.rows if x.section == "closed" and _rd_g_closed_recent(gs, x)]
                last = max((_rd_g_activity(x.stage) for x in recent), default=None)
                lines.append(_og_finished_line(plan, node.prefix, len(recent), max(0.0, now - last) if last else None,
                                               ("", "more", node.name) in ui.gopen, selected))
            elif node.name == _rd_G_UNLINKED_MORE:   # a session-unknown group's folded older workers
                _kept, folded = _rd_g_unlinked_split(r)
                opened = (r.stage.path, "more", node.name) in ui.gopen
                label = "− fewer" if opened else f"+{len(folded)} more (Enter shows all)"
                text = _wd_c(node.prefix, _wd_C_BORDER) + _wd_c("▾ " if opened else "▸ ", _wd_C_MUTED) + _rd_muted(label)
                lines.append(_og_selected(_og_fit_line(text, plan.cols), plan.cols, selected))
            else:
                tasks, shown = _rd_g_tasks(r)
                older = _rd_g_task_split(tasks, shown)[1]
                opened = (r.stage.path, "more", "") in ui.gopen
                label = "− fewer" if opened else f"+{sum(group.total for group, _ in older)} done"
                text = _wd_c(node.prefix, _wd_C_BORDER) + _wd_c("▾ " if opened else "▸ ", _wd_C_MUTED) + _rd_muted(label)
                lines.append(_og_selected(_og_fit_line(text, plan.cols), plan.cols, selected))
        blocks.append([i, lines, 0])
    for i, node in enumerate(nodes):   # an open session's milestone and reviews close its last row
        if node.kind != "stage" or not _rd_g_is_open(ui, node.row):
            continue
        end = i
        while end + 1 < len(nodes) and nodes[end + 1].row is node.row:
            end += 1
        blocks[end][1].extend(_rd_g_meta(node.row, cols, (node.row.stage.path, "miles", "") in ui.gopen, node.row.depth))
    hit_end = [len(block[1]) for block in blocks]   # fill rows below are display-only: no click target
    extra, fill_open, _used = _rd_g_fill(gs, ui, nodes, plan, cols,
                                         max(0, avail - sum(len(block[1]) for block in blocks)), now, t, names, short)
    for i, lines in extra.items():
        blocks[i][1].extend(lines)
    for i, node in enumerate(nodes):   # a project the fill opened shows its arrow open
        if node.kind == "group" and node.name[2:] in fill_open:
            blocks[i][1][0] = _rd_g_project_line(plan, node, ui, short, now, i == sel, fill_open)
    if touch:   # spend spare rows on air between stations so every row is an easy target
        spare = avail - sum(len(block[1]) for block in blocks)
        ends = [i for i, node in enumerate(nodes)
                if node.kind not in ("group", "idle", "closed-count")
                and (i + 1 == len(nodes) or nodes[i + 1].kind in ("group", "idle", "closed-count"))]
        per_gap = min(2, spare // max(1, len(ends))) if spare > 0 else 0
        for i in ends:
            blocks[i][1].extend([""] * per_gap)
            if i not in extra:
                hit_end[i] += per_gap
    marker = ""
    if not note and sum(len(block[1]) for block in blocks) > avail:   # scrolling: a footer row carries "↑ n more · ↓ n more"
        avail = max(0, avail - 1)
        note = " "
    _rd_hit(ui, top, rows - 2, 0, cols - 1, "scroll", "cards")
    body: list[str] = []
    if not nodes:
        msg = "no stations match this filter" if (ui.gfilter != "all" or ui.gquery) else "no orchestrators with recent activity"
        body = [""] * max(0, (avail - 1) // 2) + [_wd_center(_rd_dim(msg), cols)]
        if gs.errors:
            body.append(_wd_c(_wd_trunc("collector error: " + gs.errors[-1], cols), _wd_C_RED))
        ui.ptop["cards"] = 0
    else:
        heights = [len(block[1]) for block in blocks]
        starts = [sum(heights[:k]) for k in range(len(blocks))]
        flat = [(k if block[2] <= j < hit_end[k] else None, ln) for k, block in enumerate(blocks) for j, ln in enumerate(block[1])]
        off = ui.pscroll.get("cards")
        if off is None:   # follow the cursor, moving the view only as far as it takes to keep the cursor row in sight
            a0, b0 = starts[sel] + blocks[sel][2], starts[sel] + heights[sel]
            prev = ui.ptop.get("cards", 0)
            if b0 - a0 > avail or a0 < prev:
                start = a0
            elif b0 > prev + avail:
                start = b0 - avail
            else:
                start = prev
            start = min(max(start, 0), max(0, len(flat) - avail))
        else:             # wheel: a row offset
            start = min(max(off, 0), max(0, len(flat) - avail))
            ui.pscroll["cards"] = start
        ui.ptop["cards"] = start
        shown = flat[start:start + avail]
        above = sum(1 for k in range(len(blocks)) if starts[k] + heights[k] <= start)
        below = sum(1 for k in range(len(blocks)) if starts[k] >= start + avail)
        if above or below:
            marker = _rd_dim(("↑ " + str(above) + " more" if above else "") + ("  " if above and below else "")
                             + ("↓ " + str(below) + " more" if below else ""))
        shown = shown[:avail]
        body = [ln for _, ln in shown]
        spans: dict[int, list[int]] = {}
        for r_, (k, _) in enumerate(shown):
            if k is not None:
                spans.setdefault(k, [r_, r_])[1] = r_
        for k, (r0, r1) in spans.items():
            _rd_hit(ui, top + r0, top + r1, 0, cols - 1, "nav", str(k))
        for k, offset in cc_command_hits.items():
            y = starts[k] + offset - start
            if 0 <= y < len(shown):
                _rd_hit(ui, top + y, top + y, 0, cols - 1, "ccrow", nodes[k].name)
        for k, (off_, stage_path, name) in links.items():   # after the rows: the changes line's click opens its Files page
            y = starts[k] + off_ - start
            if 0 <= y < len(shown):
                _rd_hit(ui, top + y, top + y, 0, cols - 1, "files", f"{stage_path}\t{name}")
        for k, node in enumerate(nodes):   # a heading's `h hide` is its own target
            if node.kind in _rd_G_HEADED and k in spans:
                y, key = top + spans[k][0], _rd_G_SECTION_KEYS[node.kind]
                _rd_hit(ui, y, y, cols - _wd_width(f"{key} hide") - 1, cols - 1, "key", key)
    body += [""] * max(0, avail - len(body))
    if note.strip() or marker:
        note = marker + ("   " if marker and note.strip() else "") + note.strip()
    lines = head + body[:avail] + ([_wd_clip(note, cols)] if note else [])
    lines = (lines + [""] * max(0, rows - foot - len(lines)))[:rows - foot]
    return lines + (_rd_g_touch_bar(cols, ui, rows - 3, t) if touch else [_rd_keyline(_rd_g_keys(ui), cols, ui, rows - 1)])


def _rd_g_page(gs: GlobalSnapshot, ui: UIState, cols: int, rows: int, t: float) -> list[str]:
    stage = next((s for s in gs.stages if s.path == ui.gstage), None)
    if stage is not None and ui.gpage == "openclaw":
        body = _ov_page(stage, ui, cols, max(0, rows - 1), t)
        return body[:max(0, rows - 1)] + [_rd_keyline(OV_KEYS, cols, ui, rows - 1)]
    if stage is not None and ui.gpage == "sess":
        body = _sv_page(stage, ui, cols, max(0, rows - 1), t)
        return body[:max(0, rows - 1)] + [_rd_keyline(SV_KEYS, cols, ui, rows - 1)]
    if stage is not None and ui.gpage == "wk":
        wk = next((x for x in stage.snap.workers if x.name == ui.gworker), None)
        if wk is not None:
            body = _wv_page(stage, wk, ui, cols, max(0, rows - 1), t)
            return body[:max(0, rows - 1)] + [_rd_keyline(WV_KEYS, cols, ui, rows - 1)]
    if stage is None or ui.gpage not in ("milestones", "reviews"):
        return _rd_g_tree(gs, ui, cols, rows, t)
    label = _wd_clean(stage.label)
    title = f"‹ {label} / {ui.gpage}"
    header = _wd_fit(_wd_lr(_wd_c(title, _wd_C_BLUE, bold=True), _rd_sub(_rd_hms(t)), cols, 2), cols)
    content: list[str] = []
    if ui.gpage == "milestones":
        for milestone in reversed(stage.snap.milestones):
            stamp = _wd_fmt_clock(milestone.ts) if milestone.ts is not None else "     "
            plain = _wd_markdown(milestone.text, plain=True)
            content.extend(_wd_wrap(stamp + " " + str(plain), cols))
    else:
        for review in stage.snap.reviews:
            content.append(_wd_clip(_rd_review_head(review, cols, t), cols))
            for finding in _rd_sorted_findings(review):
                prefix = f"  P{min(max(finding.prio, 1), 9)} "
                content.extend(_rd_md_wrap(finding.text, cols, _wd_c(prefix, _rd_prio_color(finding.prio))))
    height = max(0, rows - 2)
    start = max(0, min(int(ui.scroll), max(0, len(content) - height)))
    _rd_hit(ui, 1, rows - 2, 0, cols - 1, "scroll", "page")
    footer = _rd_keyline((("↑↓", "scroll"), ("Esc", "back")), cols, ui, rows - 1)
    body = content[start:start + height]
    return [header] + body + [""] * max(0, rows - 2 - len(body)) + [footer]


def _rd_close_box(lines: list[str], gs: GlobalSnapshot, ui: UIState, cols: int, rows: int) -> list[str]:
    """The end-session confirm box, centred over the frame. [y] and [n] are click targets; the rest of the box eats clicks."""
    path, pid = ui.close_ask or ("", 0)
    stage = next((s for s in gs.stages if s.path == path), None)
    if stage is None:
        return lines
    what = _rd_LEAD_NAMES.get(stage.lead_kind, "lead")
    age = _rd_g_lead_age(max(0.0, gs.now - stage.lead_since))
    question = _wd_wrap(f"End {what} session in {_rd_g_label(stage)}? (pid {pid}, running {age})", max(10, min(cols - 6, 72)))
    keys = _wd_c("[y]", _wd_C_BLUE, bold=True) + _rd_sub(" end") + "   " + _wd_c("[n]", _wd_C_BLUE, bold=True) + _rd_sub(" cancel")
    body = question + ["", keys]
    w = min(cols, max(_wd_width(x) for x in body) + 4)
    h = min(rows, len(body) + 2)
    box = _wd_box(body[:max(h - 2, 0)], w, h, _rd_title_main("end session"))
    top, left = max(0, (rows - h) // 2), max(0, (cols - w) // 2)
    _rd_hit(ui, top, top + h - 1, left, left + w - 1, "box", "")
    key_row = top + 1 + len(question) + 1
    _rd_hit(ui, key_row, key_row, left + 1, left + 7, "key", "y")          # "[y] end", after the left border
    _rd_hit(ui, key_row, key_row, left + 11, left + 20, "key", "n")        # "[n] cancel"
    out = list(lines)
    for k, row in enumerate(box):
        if top + k < len(out):
            out[top + k] = " " * left + row + " " * max(0, cols - left - w)
    return out


def _rd_g_cc(gs: GlobalSnapshot, ui: UIState, cols: int, rows: int, t: float) -> list[str]:
    """The master-detail control center (cclayout) when it applies; [] makes the caller draw the classic tree."""
    ui.cc_active = False
    if not (ui.cc and ui.view == "overview" and cc_applies(cols, rows)):
        return []
    nodes = global_rows(gs, ui)
    sel = nodes[max(0, min(int(ui.gsel), len(nodes) - 1))] if nodes else None
    lines = cc_frame(gs, ui, cols, rows, t, sel, lambda w, h: _rd_g_tree(gs, ui, w, h, t, True))
    ui.cc_active = bool(lines)
    return lines


def render_global(gs: GlobalSnapshot, ui: UIState, cols: int, rows: int, t: float) -> list[str]:
    cols, rows = max(1, int(cols)), max(1, int(rows))
    ui.hits = []
    ui.title_place = (0, 0)   # only the tree's masthead draws the title picture, and sets this again
    try:
        if ui.view == "openrouter" and not ui.gpage:
            lines = _rd_g_openrouter(gs, ui, cols, rows)
        elif ui.gpage:
            lines = _rd_g_page(gs, ui, cols, rows, t)
        else:
            lines = _rd_g_cc(gs, ui, cols, rows, t) or _rd_g_tree(gs, ui, cols, rows, t)
        if gs.errors:
            lines.insert(max(0, len(lines) - 1), _wd_c(_wd_trunc("collector error: " + gs.errors[-1], cols), _wd_C_RED))
        if ui.close_ask is not None and not ui.gpage:
            lines = _rd_close_box(lines, gs, ui, cols, rows)
        if ui.help:
            lines = _rd_help_overlay(lines, cols, rows, ui)
        lines = tut_overlay(lines, ui, cols, rows)
    except Exception:
        ui.hits = []
        lines = [_wd_c("render error", _wd_C_RED)]
    _rd_publish_hits(ui)
    result = [_wd_fit(line, cols) for line in (lines[:rows] + [""] * max(0, rows - len(lines)))]
    if os.environ.get("NO_COLOR") is not None:
        return [_wd_SGR_RE.sub("", line) for line in result]
    return result


def render_global_plain(gs: GlobalSnapshot, cols: int = 100, rows: int | None = None) -> str:
    cols = max(1, int(cols))
    title = "◆ " + _RD_HEADING
    working = sum(_rd_g_is_working(s) for s in gs.stages)
    head_parts = [title, f"{working} working", f"{len(gs.stages)} sessions"]
    if gs.cost_total is not None:
        head_parts.append(_wd_fmt_money(gs.cost_total))
    head = _wd_trunc("   ".join(head_parts + [_wd_fmt_clock(gs.now, True)]), cols)
    output = [head]
    for stage in gs.stages:
        lead = stage.lead
        prefix = " · ".join(x for x in ((display_model(lead.model) if lead else ""), (lead.phase if lead else ""),
                                         (lead.stream if lead else "")) if x)
        parts = [_wd_clean(stage.label), stage.lead_state]
        if stage.unattached:
            parts.append("unattached")
        if stage.solo:
            parts.append("solo")
        if prefix:
            parts.append(prefix)
        counts = _rd_g_counts(stage.snap)
        if counts:
            parts.append(counts)
        totals = _rd_g_totals(stage.snap, stage.lead_ctx)
        if totals:
            parts.append(totals)
        if stage.last_activity > 0:
            parts.append(_wd_fmt_dur(max(0.0, gs.now - stage.last_activity)))
        output.append(_wd_trunc("   ".join(parts), cols))
        snap = stage.snap
        if snap.milestones:
            m = snap.milestones[-1]
            stamp = _wd_fmt_clock(m.ts) if m.ts is not None else "     "
            output.append(_wd_trunc("  " + stamp + " " + _wd_clean(m.text), cols))
        if snap.reviews:
            output.append(_wd_trunc("  " + _rd_g_review_summary(snap.reviews), cols))
        live = [w for w in snap.workers if w.state in LIVE_STATES]
        failed = [w for w in snap.workers if w.state in (FAILED, NOREPORT)]
        done = [w for w in snap.workers if w.state == DONE]
        shown = done if stage.path in () else done[:DONE_SHOWN]
        for wk in live + failed + shown:
            state = {RUNNING: "running", QUIET: "quiet", STALLED: "stalled", DONE: "done", FAILED: "failed",
                     NOREPORT: "no report"}.get(wk.state, wk.state)
            age = _wd_fmt_dur(_rd_elapsed(wk, gs.now)) if wk.start > 0 else ""
            output.append(_wd_trunc(f"  {_rd_plain_mark(wk)} {_wd_clean(wk.name)}  {_wd_clean(display_model(wk.model))}  {state}  {age}", cols))
        if len(done) > len(shown):
            output.append(f"  +{len(done) - len(shown)} more finished")
        output.append("")
    closed = [r for r in _rd_g_all_rows(gs) if r.section == "closed" and not _rd_g_closed_recent(gs, r)]
    if closed or gs.dismissed:
        output.append(_rd_g_closed_text(gs, closed))
    for pid in SPEND_PROVIDER_ORDER:
        snap = gs.spend.get(pid)
        if gs.spend_ok.get(pid) and snap is not None:
            chip, _, stale = _rd_spend_chip(snap)
            output.append(chip + (" stale" if stale else ""))
    output.extend(f"collector error: {error}" for error in gs.errors)
    if output and not output[-1]:
        output.pop()
    if rows is not None and len(output) > max(0, rows):
        cap = max(0, rows)
        omitted = len(output) - cap + 1
        output = output[:max(0, cap - 1)] + [_wd_trunc(f"+{omitted} more lines", cols)] if cap else []
    return "\n".join(_wd_trunc(line, cols) for line in output) + ("\n" if output else "")
