from __future__ import annotations

import argparse
import fcntl
import json
import os
import select
import shutil
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import traceback
import tty
from collections.abc import Callable
from dataclasses import dataclass

from ccactions import cx_set_demo
from cckeys import cc_key, cc_pointer
from collect import Collector, GlobalCollector, files_page, lead_page, set_override
from demo import DemoCollector, DemoGlobalCollector, demo_page_lines
from model import (
    DONE,
    FAILED,
    LIVE_STATES,
    NOREPORT,
    STALLED,
    GlobalSnapshot,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from openclawview import _ov_key, _ov_opens
from render import (
    _RD_GROUP,
    _rd_attention_first,
    _rd_cursor_stop,
    _rd_g_activity_level,
    _rd_g_all_rows,
    _rd_G_ANIM,
    _rd_g_by_root,
    _rd_G_FILTERS,
    _rd_g_group_keys,
    _rd_G_HEADED,
    _rd_g_is_open,
    _rd_g_project_open,
    _rd_g_root,
    _rd_g_rowdata,
    _rd_g_rows,
    _rd_g_set_open,
    _rd_g_set_project_open,
    _rd_g_starts_open,
    _rd_g_swarm_workers,
    _rd_PAGES,
    _rd_panel_keys,
    _rd_panel_sel,
    _rd_sel_on_new_start,
    _rd_stops,
    _rd_TITLE_ID,
    _rd_wstop,
    global_rows,
    render_frame,
    render_global,
    render_global_plain,
    render_plain,
)
from sessionview import _sv_key
from titleart import _ta_PNG_B64, _ta_SIZE
from tutorial import tut_after_key, tut_key, tut_start
from web import run_serve
from webdata import (
    contract_document,
    execution_detail,
    global_payload,
    single_payload,
    stage_payload,
    worker_detail,
)
from widgets import _wd_c, _wd_C_RED, _wd_center, _wd_THEME, _wd_trunc
from workerview import _wv_key

_AP_VERSION = "1.0"
_AP_FAR = 1_000_000          # "scrolled to the far end"; the renderer clamps it
_AP_TABS = ("live", "brief", "report")
_AP_SYNC_ON = b"\x1b[?2026h"
_AP_SYNC_OFF = b"\x1b[?2026l"
_AP_TERM_SETUP = b"\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?1000h\x1b[?1006h\x1b[2J"
_AP_TERM_RESTORE = b"\x1b[0m\x1b[?1006l\x1b[?1000l\x1b[?2026l\x1b[?7h\x1b[?25h\x1b[?1049l"


def _ap_title_enabled() -> bool:
    """Show the masthead title as a picture: on terminals that speak the kitty graphics protocol (Ghostty, kitty), not inside tmux.
    STATUS_TITLE_IMAGE=0 turns it off, =1 forces it on."""
    forced = os.environ.get("STATUS_TITLE_IMAGE", "").strip().lower()
    if forced in ("0", "off", "no", "false"):
        return False
    if forced in ("1", "on", "yes", "true"):
        return True
    if os.environ.get("TMUX") or os.environ.get("STY"):
        return False
    term = os.environ.get("TERM", "") + " " + os.environ.get("TERM_PROGRAM", "")
    return "ghostty" in term.lower() or "kitty" in term.lower() or bool(os.environ.get("KITTY_WINDOW_ID"))


def _ap_title_aspect(fd: int) -> float:
    """The title image's width/height in cells for this terminal's cell shape (2:1 tall when it will not say)."""
    try:
        rows, cols, xpix, ypix = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
        cell = (ypix / rows) / (xpix / cols) if rows and cols and xpix and ypix else 2.0
    except (OSError, ValueError, ZeroDivisionError):
        cell = 2.0
    width, height = _ta_SIZE
    return width / height * cell


def _ap_title_send() -> bytes:
    """Transmit the title PNG to the terminal as image `_rd_TITLE_ID` (chunked base64, quiet)."""
    data = _ta_PNG_B64.get(_wd_THEME) or next(iter(_ta_PNG_B64.values()))
    chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)]
    out = []
    for n, chunk in enumerate(chunks):
        head = f"a=t,f=100,t=d,i={_rd_TITLE_ID},q=2," if n == 0 else "q=2,"
        out.append(f"\x1b_G{head}m={0 if n == len(chunks) - 1 else 1};{chunk}\x1b\\")
    return "".join(out).encode("ascii")


def _ap_title_place(cols: int, rows: int) -> bytes:
    """Place the title image `cols` x `rows` cells big with its corner at row 1, column 2 (the masthead's blank rows). The
    placement id makes a repeat replace the old one."""
    return f"\x1b[1;2H\x1b_Ga=p,i={_rd_TITLE_ID},p=1,c={cols},r={rows},C=1,q=2\x1b\\".encode("ascii")


def _ap_title_test() -> int:
    """`station --title-test`: send the title picture and print what the terminal answers, to find out why it does not show."""
    print(f"TERM={os.environ.get('TERM')} TERM_PROGRAM={os.environ.get('TERM_PROGRAM')} TMUX={'yes' if os.environ.get('TMUX') else 'no'}")
    print(f"enabled automatically: {_ap_title_enabled()}   (STATUS_TITLE_IMAGE={os.environ.get('STATUS_TITLE_IMAGE', '')!r})")
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError as exc:
        print("no terminal:", exc)
        return 1
    def ask(data: bytes) -> str:
        os.write(fd, data)
        reply = b""
        while select.select([fd], [], [], 1.0)[0]:
            reply += os.read(fd, 256)
            if reply.endswith(b"\\"):
                break
        return reply.decode("ascii", "replace").replace("\x1b", "ESC")

    old = None
    try:
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        sent = ask(_ap_title_send().replace(b"q=2", b"q=0"))
        aspect = _ap_title_aspect(fd)
        cols = round(3 * aspect)
        put = ask(b"\r\n\r\n" + _ap_title_place(cols, 3).replace(b"\x1b[1;2H", b"").replace(b"q=2", b"q=0") + b"\r\n\r\n\r\n\r\n")
    except (OSError, termios.error) as exc:
        os.close(fd)
        print("terminal not usable:", exc)
        return 1
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print(f"transmit reply: {sent or '(none)'}")
    print(f"placement reply: {put or '(none)'}   ({cols}x3 cells, cell aspect gives {aspect:.1f})")
    print("An 'OK' on both and a title picture above means the terminal is fine.")
    try:
        input("Press Enter to remove the picture and quit. ")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        os.write(fd, _AP_TITLE_DELETE)
        os.close(fd)
    return 0


_AP_TITLE_UNPLACE = f"\x1b_Ga=d,d=i,i={_rd_TITLE_ID},p=1,q=2\x1b\\".encode("ascii")   # the placement only; the image stays uploaded
_AP_TITLE_DELETE = f"\x1b_Ga=d,d=I,i={_rd_TITLE_ID},q=2\x1b\\".encode("ascii")
_AP_CSI_FINAL = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "Z": "btab"}
_AP_CSI_TILDE = {"1": "home", "7": "home", "4": "end", "8": "end", "5": "pgup", "6": "pgdn"}
_AP_DEFAULT_ROOTS = ("~",)
_AP_PAGE_OPEN = {"L": "lead", "C": "cost", "F": "files"}   # station keys that open a page


def _ap_has_stage_files(path: str) -> bool:
    try:
        if os.path.isdir(os.path.join(path, "briefs")) or os.path.isfile(os.path.join(path, "PROGRESS.md")):
            return True
        return any(name.endswith(".pid") for name in os.listdir(path))
    except OSError:
        return False


def _ap_global_mode(args: argparse.Namespace, script_dir: str) -> bool:
    has_global_request = args.global_view or bool(getattr(args, "roots", None)) or (not args.stage and not _ap_has_stage_files(script_dir))
    return bool(not args.demo and has_global_request)


def _ap_roots(args: argparse.Namespace) -> list[str]:
    if args.roots:
        return args.roots
    env = os.environ.get("STATUS_ROOTS", "")
    return [x for x in env.split(":") if x] if env else list(_AP_DEFAULT_ROOTS)


def _ap_help_key(ui: UIState, key: str) -> bool:
    """The ? overlay: ? opens it and any key closes it. True when the key was used here; ctrl-c always quits."""
    if key == "ctrl-c":
        return False
    if ui.help:
        ui.help = False
        return True
    if key == "?":
        ui.help = True
        return True
    return False


def _ap_global_key(ui: UIState, key: str, gs: GlobalSnapshot, rows: int) -> str:
    """One key in the global view. In the tutorial the step card sees it first."""
    if ui.tut >= 0 and key != "ctrl-c" and tut_key(ui, key, lambda: global_rows(gs, ui)):
        return ""
    action = _ap_global_key_inner(ui, key, gs, rows)
    tut_after_key(ui, key)
    return action


def _ap_global_key_inner(ui: UIState, key: str, gs: GlobalSnapshot, rows: int) -> str:
    if key == "ctrl-c":
        return "quit"
    ui.note = ""
    if ui.close_ask is not None:   # the end-session box takes every key but ctrl-c until y or n
        if not ui.actions_on():
            ui.close_ask = None
            return ""
        if key == "y":
            return "end_lead"
        if key in ("n", "esc"):
            ui.close_ask = None
        return ""
    if ui.gedit:   # typing into the overview filter: every key but ctrl-c is text until Enter or Esc
        if key in ("enter", "esc"):
            ui.gedit = False
            if key == "esc":
                ui.gquery = ""
        elif key == "backspace":
            ui.gquery = ui.gquery[:-1]
        elif key == "space":
            ui.gquery += " "
        elif len(key) == 1 and key.isprintable():
            ui.gquery += key
        ui.gsel = 0
        ui.pscroll.pop("cards", None)
        return ""
    if _ap_help_key(ui, key):
        return ""
    if key == "v" and not ui.cc and not ui.gpage and ui.view == "overview":   # back from the classic view
        ui.cc = True
        return ""
    if ui.cc and not ui.gpage and ui.view == "overview":   # the control center takes its keys first (cckeys.cc_key)
        cc_rows = global_rows(gs, ui)
        handled = cc_key(ui, key, gs, rows, cc_rows[max(0, min(int(ui.gsel), len(cc_rows) - 1))] if cc_rows else None)
        if handled is not None:
            return handled
    if key == "d" and not ui.gpage and ui.view == "overview":    # cckeys took it when the pane is on screen
        ui.note = "details need the control center and a wider window" if ui.cc else "details: press v for the control center"
        return ""
    if key == "q":
        return "quit"
    if key == "m":
        ui.muted = not ui.muted
        return ""
    if key == "a":
        return "toggle_all"
    if key == "u":
        ui.view = "quota" if ui.view != "quota" else "overview"
        ui.scroll = 0
        return ""
    if ui.view == "quota":
        return _ap_handle_key(ui, key, None, rows)
    if ui.view == "openrouter":
        if key in ("esc", "backspace", "5"):
            ui.view, ui.scroll = "overview", 0
            return ""
        if key in ("up", "k"):
            ui.scroll = max(0, ui.scroll - 1)
            return ""
        if key in ("down", "j"):
            ui.scroll += 1
            return ""
        if key == "pgup":
            ui.scroll = max(0, ui.scroll - max(rows - 8, 1))
            return ""
        if key == "pgdn":
            ui.scroll += max(rows - 8, 1)
            return ""
        return ""
    if key == "5" and not ui.gpage:
        ui.view = "quota" if ui.view != "quota" else "overview"
        ui.scroll = 0
        return ""
    if key == "x" and not ui.gpage:
        return "close"
    if ui.gpage in ("milestones", "reviews"):
        if key in ("esc", "left", "backspace"):
            ui.gpage, ui.scroll = "", 0
        elif key in ("up", "k"):
            ui.scroll = max(0, ui.scroll - 1)
        elif key in ("down", "j"):
            ui.scroll += 1
        elif key == "pgup":
            ui.scroll = max(0, ui.scroll - max(rows - 8, 1))
        elif key == "pgdn":
            ui.scroll += max(rows - 8, 1)
        return ""
    if ui.gpage == "sess":   # the session page: ↑↓ pick a worker, Enter opens its page, Esc back to the tree
        stage = next((s for s in gs.stages if s.path == ui.gstage), None)
        if key in ("esc", "left", "backspace") or stage is None:
            ui.gpage, ui.gturn, ui.gworker, ui.scroll, ui.sel = "", "", "", 0, 0
            return ""
        action = _sv_key(ui, stage, key, rows)
        if action.startswith("worker:"):
            ui.gpage, ui.gworker, ui.scroll, ui.gsess = "wk", action[7:], 0, True
        return ""
    if ui.gpage in ("openclaw", "wk"):   # the ops pages: Esc goes back to the tree; the page's module takes the rest
        stage = next((s for s in gs.stages if s.path == ui.gstage), None)
        wk = next((x for x in stage.snap.workers if x.name == ui.gworker), None) if stage is not None else None
        if key in ("esc", "left", "backspace") or stage is None or (ui.gpage == "wk" and wk is None):
            back = ui.gpage == "wk" and stage is not None and wk is not None and ui.gsess   # back to the session page
            ui.gpage, ui.gturn, ui.gworker, ui.scroll, ui.gsess = ("sess" if back else ""), "", "", 0, False
            return ""
        if ui.gpage == "openclaw":
            _ov_key(ui, stage, key, rows)
            return ""
        assert wk is not None
        return _ap_wk_action(ui, gs, stage, wk, _wv_key(ui, stage, wk, key, rows))
    if ui.gpage in ("worker", "stage"):
        if ui.gpage == "worker" and key in ("L", "C", "F"):   # the station pages belong to the station only
            return ""
        if ui.gpage == "worker" and key in ("esc", "left", "backspace"):
            ui.gpage, ui.view, ui.scroll = "", "overview", 0
            ui.detail_text = []
            return ""
        if key in ("esc", "left", "backspace") and ui.view == "overview":
            ui.gpage, ui.view, ui.scroll = "", "overview", 0
            return ""
        action = _ap_handle_key(ui, key, _ap_global_stage_snap(gs, ui), rows)
        if ui.gpage == "worker" and ui.view == "overview":
            ui.gpage, ui.scroll = "", 0
        return action
    tree = global_rows(gs, ui)
    if key in ("1", "2", "3", "4"):
        ui.gfilter = _rd_G_FILTERS[int(key) - 1]
        ui.gsel = 0
        ui.view, ui.scroll = "overview", 0
        ui.pscroll.pop("cards", None)
        return ""
    if key == "/":
        ui.gedit = True
        return ""
    if key == "f" and tree and tree[min(ui.gsel, len(tree) - 1)][1] == "worker":   # a worker row: its Files page
        path, _, name = tree[min(ui.gsel, len(tree) - 1)]
        return _ap_open_files(ui, gs, path, name)
    if key == "f":   # the touch bar's Filter button: all, working, lagging, problems, around again
        ui.gfilter = _rd_G_FILTERS[(_rd_G_FILTERS.index(ui.gfilter) + 1) % len(_rd_G_FILTERS)]
        ui.gsel = 0
        ui.pscroll.pop("cards", None)
        return ""
    if key == "t":
        ui.gquips = not ui.gquips
        return ""
    if key == "i":
        ui.glegend = not ui.glegend
        return ""
    if key == "w":   # the station view: live worker cards, timeline and reviews of this session
        _ap_open_station_from_row(ui, gs, tree)
        return ""
    if key == "p":   # pin the selected station idle or active, or back to automatic; the main loop runs it (it needs the collector)
        return "pin" if global_rows(gs, ui) else ""
    if key == "esc" and (ui.gquery or ui.gfilter != "all"):
        ui.gquery, ui.gfilter, ui.gsel = "", "all", 0
        return ""
    if not tree:
        ui.gsel = 0
        return ""
    ui.gsel = min(max(ui.gsel, 0), len(tree) - 1)
    path, kind, name = tree[ui.gsel]
    if key in _AP_MOVES or key in ("home", "g", "end", "G"):
        ui.pscroll.pop("cards", None)
    if key in ("up", "k"):
        ui.gsel = max(ui.gsel - 1, 0)
    elif key in ("down", "j"):
        ui.gsel = min(ui.gsel + 1, len(tree) - 1)
    elif key == "pgup":
        ui.gsel = max(ui.gsel - 5, 0)
    elif key == "pgdn":
        ui.gsel = min(ui.gsel + 5, len(tree) - 1)
    elif key in ("home", "g"):
        ui.gsel = 0
    elif key in ("end", "G"):
        ui.gsel = len(tree) - 1
    elif key in ("enter", "o") and kind == "stage" and _ov_opens(next((s for s in gs.stages if s.path == path), Stage(path="", label=""))):
        ui.gpage, ui.gstage, ui.gturn, ui.scroll, ui.sel = "openclaw", path, "", 0, 0   # an openclaw session: its turn page (→/← fold it)
    elif key in ("enter", "right", "l", "left"):
        _ap_global_toggle(ui, gs, tree, key)
    elif key == "A":     # the unattached runs shown or hidden; the cursor stays on its row when that row is still listed
        ui.gunattached = not ui.gunattached
        _ap_global_follow_selection(ui, gs, (path, kind, name))
    elif key in ("h", "H"):   # the idle or closed section hidden or shown; the cursor stays on its row when that row is still listed
        if key == "h":
            ui.ghide_idle = not ui.ghide_idle
        else:
            ui.ghide_closed = not ui.ghide_closed
        _ap_global_follow_selection(ui, gs, (path, kind, name))
    elif key == "M":     # the selected session's milestones: the latest only, or all of them
        marker = (path, "miles", "")
        if path and marker in ui.gopen:
            ui.gopen.discard(marker)
        elif path:
            ui.gopen.add(marker)
    elif key == "e":     # expand live and idle sessions and their projects
        all_rows = _rd_g_all_rows(gs)
        for row in all_rows:
            if row.section != "closed":
                _rd_g_set_open(ui, row, True)
                if row.section == "idle":
                    ui.gpin_keys.add("s:" + (row.stage.session_id or row.stage.path))
        for root, prows in _rd_g_by_root([r for r in all_rows if r.section != "closed"]).items():
            _rd_g_set_project_open(ui, root, True)
            if not any(_ap_stage_live(r.stage) for r in prows):
                ui.gpin_keys.add("p:" + root)
        ui.gfolded.discard("idle")
    elif key == "c":     # shut every session and project
        all_rows = _rd_g_all_rows(gs)
        for row in all_rows:
            _rd_g_set_open(ui, row, False)
        for root in _rd_g_by_root(all_rows):
            _rd_g_set_project_open(ui, root, False)
            ui.gpin_keys.discard("p:" + root)
        ui.gopen = {k for k in ui.gopen if k[1] == "more" and not k[2].startswith("finished:")}
        ui.gfolded |= set(_rd_G_HEADED) | _rd_g_group_keys(gs, ui)
        ui.gsel = next((i for i, row in enumerate(global_rows(gs, ui)) if row[0] == path and row[1] == "stage"), 0)
    elif key in ("o", "space") and (
        (kind == "more" and name.startswith("finished:")) or kind in _rd_G_HEADED or kind == "group"
    ):   # finished fold, section heading, or project heading: o or Space like Enter
        _ap_global_toggle(ui, gs, tree, "enter")
    elif key in ("o", "space") and kind == "worker":   # double click or o on a worker: its page
        ui.gpage, ui.gstage, ui.gworker, ui.scroll, ui.gsess = "wk", path, name, 0, False
    elif key in ("o", "space") and kind == "stage" and _ov_opens(next((s for s in gs.stages if s.path == path), Stage(path="", label=""))):
        ui.gpage, ui.gstage, ui.gturn, ui.scroll, ui.sel = "openclaw", path, "", 0, 0
    elif key in ("o", "space") and kind == "stage":   # double click or o on a session: its overview page
        ui.gpage, ui.gstage, ui.gworker, ui.scroll, ui.sel, ui.gsess = "sess", path, "", 0, 0, False
        stage = next((s for s in gs.stages if s.path == path), None)
        ui.expanded = {wk.name for wk in stage.snap.workers if wk.state in LIVE_STATES} if stage else set()
        ui.pinned.clear()
    elif key in ("o", "space") and kind != "more":
        _ap_open_station(ui, gs, path, name if kind == "worker" else "")
    return ""


def _ap_open_station(ui: UIState, gs: GlobalSnapshot, path: str, worker: str = "") -> None:
    """The station view of the session at `path`: live worker cards, timeline, reviews; L lead, C cost, F files inside.
    Esc goes back to the list. `worker` puts the cursor on that worker's card."""
    ui.gpage, ui.gstage, ui.scroll = "stage", path, 0
    ui.view, ui.tab, ui.follow = "overview", "live", True
    ui.pscroll.clear()
    stage = next((s for s in gs.stages if s.path == path), None)
    ui.expanded = {wk.name for wk in stage.snap.workers if wk.state in LIVE_STATES} if stage else set()
    ui.pinned = set()   # opening a station: its live workers open, nothing is pinned yet
    if stage is not None and worker:
        ui.sel = next((i for i, wk in enumerate(stage.snap.workers) if wk.name == worker), 0)


def _ap_open_station_from_row(ui: UIState, gs: GlobalSnapshot, tree: list[tuple[str, str, str]]) -> None:
    """w on any row: the station view of the row's session. A project heading opens its first session."""
    if not tree:
        return
    i = min(max(ui.gsel, 0), len(tree) - 1)
    path, kind, name = tree[i]
    if not path:   # a heading: the first session row below it
        path, kind, name = next((row for row in tree[i:] if row[0] and row[1] in ("stage", "worker")), ("", "", ""))
    if path and any(s.path == path for s in gs.stages):
        _ap_open_station(ui, gs, path, name if kind == "worker" else "")
    else:
        ui.note = "w: nothing to open on this row"


def _ap_wk_action(ui: UIState, gs: GlobalSnapshot, stage: Stage, wk: Worker, action: str) -> str:
    """What the worker page asked for: Files, or the station page on that worker with its brief or report tab."""
    if action == "files":
        return _ap_open_files(ui, gs, stage.path, wk.name)
    if action in ("brief", "report"):
        names = [x.name for x in stage.snap.workers]
        ui.gpage, ui.gstage, ui.gworker, ui.scroll, ui.detail_text = "stage", stage.path, "", 0, []
        ui.view, ui.tab, ui.follow = "detail", action, True
        ui.sel = names.index(wk.name) if wk.name in names else 0
        ui.panel = ""
        ui.pscroll.clear()
        return "load_detail"
    return ""


def _ap_open_files(ui: UIState, gs: GlobalSnapshot, path: str, name: str) -> str:
    """f on a worker row of the overview: the station's Files page with that worker selected. Esc goes back to the station."""
    stage = next((s for s in gs.stages if s.path == path), None)
    if stage is None:
        return ""
    names = [wk.name for wk in stage.snap.workers]
    ui.gpage, ui.gstage, ui.view, ui.scroll, ui.detail_text = "stage", path, "files", 0, []
    ui.sel = names.index(name) if name in names else 0
    ui.panel = ""
    ui.pscroll.clear()
    return "load_page"


def _ap_global_toggle(ui: UIState, gs: GlobalSnapshot, tree: list[tuple[str, str, str]], key: str) -> None:
    """Enter/→/← on the overview: open or close the row under the cursor. ← on a worker goes back to its station."""
    path, kind, name = tree[ui.gsel]
    grow = key in ("right", "l")
    shrink = key == "left"
    rows = {row.stage.path: row for row in _rd_g_rows(gs, ui)}
    if kind == "group":
        root = name[2:] if name.startswith("p:") else name.split("|", 1)[-1]
        project_rows = [r for r in _rd_g_all_rows(gs) if _rd_g_root(r.stage) == root]
        opened = _rd_g_project_open(ui, root, project_rows)
        if (grow and not opened) or (shrink and opened) or (not grow and not shrink):
            _rd_g_set_project_open(ui, root, not opened)
            key_for_pin = "p:" + root
            if not opened and not any(_ap_stage_live(r.stage) for r in project_rows):
                ui.gpin_keys.add(key_for_pin)
            elif opened:
                ui.gpin_keys.discard(key_for_pin)
        return
    row = rows.get(path)
    if kind in _rd_G_HEADED:
        fold_key = kind
        folded = fold_key in ui.gfolded
        if (grow and folded) or (shrink and not folded) or (not grow and not shrink):
            ui.gfolded.discard(fold_key) if folded else ui.gfolded.add(fold_key)
        return
    if kind == "more":
        marker = (path, "more", name)   # a session's fold (name ""), or a headed section's `+N more` (path "")
        if marker in ui.gopen and not grow:
            ui.gopen.discard(marker)
        elif not shrink:
            ui.gopen.add(marker)
        return
    if kind == "task":   # an older task's line: Enter opens its workers under it
        marker = (path, "task", name)
        if marker in ui.gopen and not grow:
            ui.gopen.discard(marker)
        elif not shrink:
            ui.gopen.add(marker)
        return
    if row is None:
        return
    if kind == "stage":
        opened = _rd_g_is_open(ui, row)
        if (grow and not opened) or (shrink and opened) or (not grow and not shrink):
            _rd_g_set_open(ui, row, not opened)
        return
    marker = (path, "worker", name)
    if marker in ui.gopen:
        if not grow:
            ui.gopen.discard(marker)
    elif shrink:
        ui.gsel = next((i for i, r in enumerate(tree) if r[0] == path and r[1] == "stage"), ui.gsel)
    else:
        ui.gopen.add(marker)


def _ap_launch_ui(density: str) -> UIState:
    """The overview starts with idle headings visible and each non-live row collapsed."""
    return UIState(density=density, cc=True)


def _ap_ui_from_args(args: argparse.Namespace, density: str) -> UIState:
    """Create the interactive UI with the parsed process-action opt-in applied."""
    ui = _ap_launch_ui(density)
    ui.allow_actions = bool(getattr(args, "allow_actions", False))
    return ui


def _ap_expand_path(gc: GlobalCollector) -> str:
    return gc._co_overrides_path


def _ap_load_expand(ui: UIState, path: str) -> None:
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        expand = data.get("expand", {}) if isinstance(data, dict) else {}
        opened = expand.get("open", []) if isinstance(expand, dict) else []
        shut = expand.get("shut", []) if isinstance(expand, dict) else []
        ui.gopen_keys = {key for key in opened if isinstance(key, str)}
        ui.gshut_keys = {key for key in shut if isinstance(key, str)}
        ui.gexpand_order = [key for key in opened + shut if isinstance(key, str)]
    except (OSError, ValueError, TypeError, AttributeError):
        return


def _ap_save_expand(ui: UIState, path: str) -> None:
    try:
        try:
            with open(path, encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        keys = ui.gopen_keys | ui.gshut_keys
        order = [key for key in ui.gexpand_order if key in keys]
        order.extend(sorted(keys - set(order)))
        order = order[-1000:]
        ui.gexpand_order = order
        data["expand"] = {"open": [k for k in order if k in ui.gopen_keys][-500:],
                           "shut": [k for k in order if k in ui.gshut_keys][-500:]}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except OSError:
        pass


def _ap_expand_key_changed(ui: UIState, before_open: set[str], before_shut: set[str]) -> bool:
    changed = before_open != ui.gopen_keys or before_shut != ui.gshut_keys
    if changed:
        removed = (before_open | before_shut) - (ui.gopen_keys | ui.gshut_keys)
        ui.gexpand_order = [key for key in ui.gexpand_order if key not in removed]
        changed_state = {key for key in before_open | before_shut | ui.gopen_keys | ui.gshut_keys
                         if (key in before_open, key in before_shut) !=
                         (key in ui.gopen_keys, key in ui.gshut_keys)}
        ui.gexpand_order = [key for key in ui.gexpand_order if key not in changed_state]
        for key in sorted(changed_state & (ui.gopen_keys | ui.gshut_keys)):
            ui.gexpand_order.append(key)
    return changed


def _ap_stage_live(stage: Stage) -> bool:
    return _rd_g_starts_open(stage)


def _ap_reconcile_expansion(ui: UIState, before: GlobalSnapshot | None, after: GlobalSnapshot) -> bool:
    """Forget an unpinned open choice when its session or project becomes idle."""
    old_by_session = {(stage.session_id or stage.path): stage for stage in before.stages} if before else {}
    new_by_session = {(stage.session_id or stage.path): stage for stage in after.stages}
    roots = {_rd_g_root(stage) for stage in after.stages}
    if before is None:
        for stage in after.stages:
            if not _ap_stage_live(stage):
                key = "s:" + (stage.session_id or stage.path)
                if key in ui.gopen_keys:
                    ui.gpin_keys.add(key)
        for root in roots:
            project_key = "p:" + root
            if project_key in ui.gopen_keys and not any(
                _ap_stage_live(stage) for stage in after.stages if _rd_g_root(stage) == root
            ):
                ui.gpin_keys.add(project_key)
        return False
    changed = False
    for sid, old in old_by_session.items():
        current = new_by_session.get(sid)
        if current is not None and _ap_stage_live(old) and not _ap_stage_live(current):
            key = "s:" + sid
            if key in ui.gopen_keys and key not in ui.gpin_keys:
                ui.gopen_keys.discard(key)
                ui.gshut_keys.discard(key)
                changed = True
    old_roots = {_rd_g_root(stage) for stage in before.stages}
    for root in old_roots & roots:
        was_live = any(_ap_stage_live(s) for s in before.stages if _rd_g_root(s) == root)
        is_live = any(_ap_stage_live(s) for s in after.stages if _rd_g_root(s) == root)
        key = "p:" + root
        if was_live and not is_live and key in ui.gopen_keys and key not in ui.gpin_keys:
            ui.gopen_keys.discard(key)
            ui.gshut_keys.discard(key)
            changed = True
    return changed


def _ap_global_follow_selection(ui: UIState, gs: GlobalSnapshot,
                                selected: tuple[str, str, str] | None) -> tuple[str, str, str] | None:
    rows = global_rows(gs, ui)
    if selected in rows:
        ui.gsel = rows.index(selected)
    else:
        ui.gsel = min(max(ui.gsel, 0), max(0, len(rows) - 1))
    return rows[ui.gsel] if rows else None


def _ap_global_validate_page(ui: UIState, gs: GlobalSnapshot, worker_name: str = "") -> bool:
    if not ui.gpage:
        return True
    stage = next((item for item in gs.stages if item.path == ui.gstage), None)
    valid = stage is not None
    if ui.gpage == "worker" and stage is not None:
        valid = any(worker.name == worker_name for worker in stage.snap.workers)
    if ui.gpage == "wk" and stage is not None:
        valid = any(worker.name == ui.gworker for worker in stage.snap.workers)
    if ui.gpage == "sess" and stage is not None:
        valid = True
    if not valid:
        ui.gpage, ui.view, ui.scroll = "", "overview", 0
    return valid


def _ap_global_stage_snap(gs: GlobalSnapshot, ui: UIState) -> Snapshot | None:
    stage = next((s for s in gs.stages if s.path == ui.gstage), None)
    if stage is None:
        return None
    stage.snap.quota, stage.snap.quota_ok = gs.quota, gs.quota_ok
    return stage.snap


def _ap_close_selected(ui: UIState, gs: GlobalSnapshot, gc: GlobalCollector) -> None:
    """x on a card. A station with no lead and nothing running is dismissed at once; a running lead asks first.
    A station whose lead is gone but whose workers still run stays as it is.
    A daemon-hosted lead (no pid) is dismissed at once too, since there is nothing to signal."""
    rows_data = global_rows(gs, ui)
    if not rows_data:
        return
    path = rows_data[min(ui.gsel, len(rows_data) - 1)][0]
    stage = next((s for s in gs.stages if s.path == path), None)
    if stage is None:
        return
    if stage.lead_state == "background" and not stage.lead_pid:
        if stage.live == 0:   # daemon-hosted session: no process to end, so hide it like a gone one
            gc.dismiss(path)
    elif stage.lead_state != "gone":
        if ui.actions_on():
            ui.close_ask = (path, stage.lead_pid or 0)
        else:
            ui.note = "ending a session needs --allow-actions"
    elif stage.live == 0:
        gc.dismiss(path)


def _ap_pin_selected(ui: UIState, gs: GlobalSnapshot, gc: GlobalCollector) -> bool:
    """p on the selected station: moved to idle, moved to active, or automatic again. Nothing selected, or a heading row, does nothing.
    Returns True when the toggle ran, so the main loop wakes the collector and the row changes section in the next frame."""
    rows_data = global_rows(gs, ui)
    if not rows_data:
        return False
    path = rows_data[min(ui.gsel, len(rows_data) - 1)][0]
    stage = next((s for s in gs.stages if s.path == path), None)
    if stage is None:
        return False
    state = gc.toggle_idle(path)
    ui.note = {"idle": stage.label + ": moved to idle", "active": stage.label + ": moved to active"}.get(state, stage.label + ": automatic again")
    return True


def _ap_control(gc: GlobalCollector, path: str, action: str, now: float | None = None) -> dict[str, object]:
    """POST /api/control: the same pin or dismiss as the TUI's p and x, with an explicit idle, active or clear.
    The pin is keyed as toggle_idle keys it (the station's dir). Raises OSError when the file cannot be written."""
    if action == "dismiss":
        if not gc.dismiss(path):
            raise OSError("cannot write the dismissed file")
        return {"pinned": "", "dismissed": True}
    key = next(((s.dir or s.path) for s in gc._co_last_stages if s.path == path), path)
    state = None if action == "clear" else action
    set_override(gc._co_overrides_path, key, state, time.time() if now is None else now)
    return {"pinned": state or "", "dismissed": False}


def _ap_end_lead(ui: UIState, gc: GlobalCollector) -> None:
    """y in the confirm box: SIGTERM the lead, but only after a fresh /proc read still finds that pid as the station's lead."""
    if not ui.actions_on():
        ui.close_ask = None
        ui.note = "ending a session needs --allow-actions"
        return
    if ui.close_ask is None:
        return
    path, pid = ui.close_ask
    ui.close_ask = None
    if ui.demo:   # the tutorial's rows have made-up pids: never signal them
        ui.note = "demo: the session was not ended. A real y would have ended it."
        return
    lead = gc.current_lead(path)
    if pid <= 0 or lead is None or lead.pid != pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    ui.closing.add(path)


def _ap_settle_closing(ui: UIState, gs: GlobalSnapshot, gc: GlobalCollector) -> None:
    """Dismiss each station that was ended, once its lead is gone and nothing in it still runs."""
    for path in list(ui.closing):
        stage = next((s for s in gs.stages if s.path == path), None)
        if stage is None or stage.lead_state == "gone" and stage.live == 0 and gc.dismiss(path):
            ui.closing.discard(path)


def _ap_global_stage(gs: GlobalSnapshot, ui: UIState) -> Stage | None:
    return next((s for s in gs.stages if s.path == ui.gstage), None)


def _ap_page_key(ui: UIState, key: str, rows: int) -> str:
    """Keys on a station page: L, C, F switch page; r reloads; ↑↓ and the wheel scroll; Esc goes back to the station."""
    page = max(rows - 8, 1)
    if key in _AP_PAGE_OPEN:
        ui.view, ui.scroll, ui.detail_text = _AP_PAGE_OPEN[key], 0, []
        return "load_page"
    if key in ("esc", "backspace", "left"):
        ui.view, ui.scroll, ui.detail_text = "overview", 0, []
    elif key == "r":
        return "load_page"
    elif key in ("up", "k"):
        ui.scroll = max(ui.scroll - 1, 0)
    elif key in ("down", "j"):
        ui.scroll += 1
    elif key == "pgup":
        ui.scroll = max(ui.scroll - page, 0)
    elif key == "pgdn":
        ui.scroll += page
    elif key in ("home", "g"):
        ui.scroll = 0
    elif key in ("end", "G"):
        ui.scroll = _AP_FAR
    return ""


def _ap_load_page(ui: UIState, stage_dir: str, stage: Stage | None, snap: Snapshot | None, now: float) -> None:
    """Read what the open page shows: once on open and on r, never per frame. The cost page is drawn from the snapshot."""
    if ui.demo:   # the tutorial's rows have no lead.json, stream file or git to read
        name = _ap_sel_name(ui, snap) if ui.view == "files" else ""
        ui.detail_text = demo_page_lines(ui.view, name)
    elif ui.view == "lead":
        ui.detail_text = lead_page(stage_dir, stage, now)
    elif ui.view == "files":
        name = _ap_sel_name(ui, snap)
        worker = next((wk for wk in snap.workers if wk.name == name), None) if snap is not None else None
        ui.detail_text = files_page(stage_dir, worker)


def _ap_global_events(prev: GlobalSnapshot | None, new: GlobalSnapshot) -> list[_ap_Event]:
    if prev is None or prev.all_history != new.all_history:
        return []
    old = {stage.path: stage for stage in prev.stages}
    events: list[_ap_Event] = []
    for stage in new.stages:
        before = old.get(stage.path)
        if before is None:
            continue
        for event in _ap_detect_events(before.snap, stage.snap):
            events.append(_ap_Event(event.kind, event.key, f"{stage.label}: {event.summary}",
                                    event.body, event.urgency))
    return events


# ---------------------------------------------------------------- keys

def _ap_parse_keys(data: bytes, final: bool = False) -> tuple[list[str], bytes]:
    """Turn raw bytes into key names. Returns (keys, unconsumed incomplete tail).

    With final=True a lone ESC becomes "esc" and any other incomplete sequence is dropped."""
    keys: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == 0x1B:
            if i + 1 >= n:
                if final:
                    keys.append("esc")
                    i += 1
                    continue
                return keys, data[i:]
            nxt = data[i + 1]
            if nxt == 0x4F:  # SS3: ESC O <final>
                if i + 2 >= n:
                    if final:
                        break
                    return keys, data[i:]
                key = _AP_CSI_FINAL.get(chr(data[i + 2]))
                if key:
                    keys.append(key)
                i += 3
            elif nxt == 0x5B:  # CSI: ESC [ params intermediates final
                j = i + 2
                while j < n and 0x30 <= data[j] <= 0x3F:
                    j += 1
                while j < n and 0x20 <= data[j] <= 0x2F:
                    j += 1
                if j >= n:
                    if final:
                        break
                    return keys, data[i:]
                fin = chr(data[j])
                params = data[i + 2:j].decode("ascii", "ignore")
                if params.startswith("<") and fin in "Mm":
                    if fin == "M":   # presses only; releases (m) are dropped
                        keys += _ap_mouse_key(params[1:])
                    i = j + 1
                    continue
                key = _AP_CSI_TILDE.get(params.split(";")[0]) if fin == "~" else _AP_CSI_FINAL.get(fin)
                if key:
                    keys.append(key)
                i = j + 1
            else:
                keys.append("esc")
                i += 1
            continue
        if b in (0x0D, 0x0A):
            keys.append("enter")
        elif b == 0x09:
            keys.append("tab")
        elif b in (0x7F, 0x08):
            keys.append("backspace")
        elif b == 0x03:
            keys.append("ctrl-c")
        elif 0x20 <= b < 0x7F:
            keys.append("space" if b == 0x20 else chr(b))
        i += 1
    return keys, b""


_AP_MOD_BITS = 4 | 8 | 16     # SGR button bits for shift, meta and ctrl; they do not change the button


def _ap_mouse_key(report: str) -> list[str]:
    """One SGR mouse press ("button;col;row") as a key: click:col:row for the left button, wheel:up|down:col:row.
    Motion reports and other buttons give no key."""
    bits = report.split(";")
    if len(bits) != 3:
        return []
    try:
        button, col, row = int(bits[0]), int(bits[1]), int(bits[2])
    except ValueError:
        return []
    if button & 32:   # motion
        return []
    kind = button & ~_AP_MOD_BITS
    if kind == 0:
        return [f"click:{col}:{row}"]
    if kind in (64, 65):
        return [f"wheel:{'up' if kind == 64 else 'down'}:{col}:{row}"]
    return []


def _ap_read_keys(fd: int) -> list[str] | None:
    """Read pending input and parse it. None means stdin is gone."""
    buf = b""
    keys: list[str] = []
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
        got, buf = _ap_parse_keys(buf)
        keys += got
        if not buf:
            return keys
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            got, _ = _ap_parse_keys(buf, final=True)
            return keys + got


def _ap_handle_key(ui: UIState, key: str, snap: Snapshot | None, rows: int) -> str:
    """Apply one key to the UI state. Returns "", "quit", "load_detail" or "toggle_all"."""
    if key == "ctrl-c":
        return "quit"
    if _ap_help_key(ui, key):
        return ""
    if key == "q":
        return "quit"
    if key == "m":
        ui.muted = not ui.muted
        return ""
    ui.note = ""
    if ui.view in _rd_PAGES:
        return _ap_page_key(ui, key, rows)
    if ui.view == "overview" and key == "d":
        ui.density = "compact" if ui.density == "full" else "full"
        _ap_save_density(ui.density)
        return ""
    if ui.view == "overview" and key == "l":
        ui.lead_open = not ui.lead_open
        return ""
    if ui.view == "quota":
        if key in ("esc", "backspace", "u", "5"):
            ui.view, ui.scroll = "overview", 0
        elif key in ("up", "k"):
            ui.scroll = max(0, ui.scroll - 1)
        elif key in ("down", "j"):
            ui.scroll += 1
        elif key == "pgup":
            ui.scroll = max(0, ui.scroll - max(rows - 8, 1))
        elif key == "pgdn":
            ui.scroll += max(rows - 8, 1)
        return ""
    if key == "u":
        ui.view = "quota"
        return ""
    if key == "!" and snap is not None and ui.view == "overview":   # the header's ⚠ count: first attention item
        kind, idx = _rd_attention_first(snap)
        if kind == "worker":
            ui.panel, ui.sel = "", idx
        elif kind == "reviews":
            ui.panel = "reviews"
        return ""
    if ui.view == "detail":
        return _ap_handle_detail_key(ui, key, rows)
    return _ap_station_key(ui, key, snap, rows)


_AP_MOVES = {"up": -1, "k": -1, "down": 1, "j": 1, "pgup": -5, "pgdn": 5}


def _ap_cursor(ui: UIState, snap: Snapshot) -> int:
    """Cursor position over the station rows: the walk render._rd_stops gives (workers, group lines, panel rows)."""
    stops = _rd_stops(snap, ui)
    cur = _rd_cursor_stop(snap, ui)
    return stops.index(cur) if cur in stops else 0


def _ap_go(ui: UIState, stop: str) -> None:
    """Put the cursor on a station row: "w<index>" is a worker; anything else is a panel or an earlier group's line."""
    if stop.startswith("w"):
        ui.panel, ui.sel = "", int(stop[1:])
    else:
        ui.panel = stop


def _ap_toggle_expanded(ui: UIState, wk: Worker) -> None:
    """Enter on a worker: open or close its card or summary. Opening a finished worker is your own choice, so pin it."""
    if wk.name in ui.expanded:
        ui.expanded.discard(wk.name)
        ui.pinned.discard(wk.name)
    else:
        ui.expanded.add(wk.name)
        if wk.state not in LIVE_STATES:
            ui.pinned.add(wk.name)


def _ap_sync_expanded(ui: UIState, prev: Snapshot | None, snap: Snapshot) -> None:
    """Keep the open set in step with the workers. A worker that starts opens; one that finishes closes unless the user pinned it."""
    before = {w.name: w.state for w in prev.workers} if prev is not None else {}
    for wk in snap.workers:
        was = before.get(wk.name)
        if wk.state in LIVE_STATES:
            if was not in LIVE_STATES:
                ui.expanded.add(wk.name)
                ui.pinned.discard(wk.name)
        elif was in LIVE_STATES and wk.name not in ui.pinned:
            ui.expanded.discard(wk.name)
    names = {w.name for w in snap.workers}
    ui.expanded &= names
    ui.pinned &= names
    ui.group_open &= {group.key for group in snap.groups} | {"*"}


def _ap_station_key(ui: UIState, key: str, snap: Snapshot | None, rows: int) -> str:
    """Station view. The cursor walks the worker rows, the earlier groups' lines, then the timeline and reviews title rows.
    Enter opens or closes the row under the cursor: a worker (any number may be open), an earlier group's line
    (its workers) or a panel (fold); Space opens a worker's page; t and r fold the panels."""
    if key in _AP_PAGE_OPEN:
        ui.view, ui.scroll, ui.detail_text = _AP_PAGE_OPEN[key], 0, []
        return "load_page"
    panels = _rd_panel_keys(snap) if snap is not None else []
    if key in ("t", "r"):
        name = "timeline" if key == "t" else "reviews"
        if name in panels:
            if name in ui.folded:
                ui.folded.discard(name)
            else:
                ui.folded.add(name)
        return ""
    if key == "a":
        return "toggle_all"
    stops = _rd_stops(snap, ui) if snap is not None else []
    if not stops or snap is None:
        return ""
    panel = _rd_panel_sel(snap, ui)
    ui.panel = panel
    cur = _ap_cursor(ui, snap)
    stop = stops[cur]
    if key in _AP_MOVES or key in ("home", "g", "end", "G"):
        for name in ("workers", "timeline", "reviews"):   # the cursor takes the view back from the wheel
            ui.pscroll.pop(name, None)
        if key in ("home", "g"):
            new = 0
        elif key in ("end", "G"):
            new = len(stops) - 1
        else:
            new = min(max(cur + _AP_MOVES[key], 0), len(stops) - 1)
        _ap_go(ui, stops[new])
    elif key == "enter":
        if stop.startswith(_RD_GROUP):
            _ap_toggle_group(ui, stop[len(_RD_GROUP):])
        elif stop.startswith("w"):
            _ap_go(ui, stop)
            _ap_toggle_expanded(ui, snap.workers[ui.sel])
        elif panel:
            if panel in ui.folded:
                ui.folded.discard(panel)
            else:
                ui.folded.add(panel)
    elif key == "space" and stop.startswith("w"):
        _ap_go(ui, stop)
        ui.view = "detail"
        ui.tab = "live"
        ui.follow = True
        ui.scroll = 0
        ui.detail_text = []
        return "load_detail"
    return ""


def _ap_toggle_group(ui: UIState, key: str) -> None:
    """Enter or a click on an earlier group's line: fold or unfold its workers."""
    if key in ui.group_open:
        ui.group_open.discard(key)
    else:
        ui.group_open.add(key)


_AP_LABEL_KEYS = {"Esc": "esc", "Tab": "tab", "Enter": "enter", "Enter/Space": "enter", "Space": "space", "Up": "up", "Down": "down"}
_AP_DOUBLE_S = 0.4          # two left presses this close together on one row are a double-click
_AP_WHEEL_ROWS = 3          # rows per wheel notch


def _ap_hit(ui: UIState, col: int, row: int, scroll: bool = False) -> tuple[str, str] | None:
    """The region under a 1-based screen cell, from the frame on screen. Clicks take the last region drawn
    (rows sit over their panel); a wheel takes the scroll region."""
    for r0, r1, c0, c1, action, target in reversed(ui.hits):
        if (action == "scroll") == scroll and r0 <= row <= r1 and c0 <= col <= c1:
            return action, target
    return None


def _ap_moves(delta: int) -> list[str]:
    """Cursor moves that take it delta steps: the same keys the keyboard uses."""
    return ["down"] * delta if delta > 0 else ["up"] * -delta


def _ap_wheel(ui: UIState, target: str, delta: int) -> None:
    """Scroll the panel under the pointer by delta rows; negative is up, towards the top."""
    if target == "detail":
        _ap_scroll(ui, -delta)                       # positive = further up the content
    elif target == "page":
        ui.scroll = max(ui.scroll + delta, 0)
    else:
        ui.pscroll[target] = max(ui.ptop.get(target, 0) + delta, 0)


def _ap_pointer_keys(ui: UIState, key: str, gs: GlobalSnapshot | None, snap: Snapshot | None,
                     now: float) -> list[str]:
    """A mouse event as the keys it stands for, run through the normal key path. Clicks use the hit map of the
    frame on screen; wheel scrolls are applied here. gs is set in global mode, snap in the station view."""
    parts = key.split(":")
    col, row = int(parts[-2]), int(parts[-1])
    if parts[0] == "wheel":
        hit = None if ui.help else _ap_hit(ui, col, row, scroll=True)
        if hit is not None:
            _ap_wheel(ui, hit[1], -_AP_WHEEL_ROWS if parts[1] == "up" else _AP_WHEEL_ROWS)
        return []
    if ui.help:   # any click closes the ? overlay
        ui.help = False
        return []
    last_row, last_t, last_action, last_target = ui.last_click
    double = last_row == row and now - last_t <= _AP_DOUBLE_S
    hit = _ap_hit(ui, col, row)
    if double and last_action in ("select", "nav"):   # the layout may have moved under the first press: open that row
        hit = (last_action, last_target)
    ui.last_click = (0, now, "", "") if double else (row, now, *(hit or ("", "")))
    if hit is None:
        return []
    action, target = hit
    if action in ("cctab", "ccstrip", "ccpane", "ccrow"):   # the control center's tab labels, pane button, worker commands
        return cc_pointer(ui, action, target)
    if action == "key":
        k = _AP_LABEL_KEYS.get(target, target if len(target) == 1 else "")   # ↑↓ is no single key
        return [k] if k else []
    if action == "tab":         # a Live / Brief / Report label: step there with tab presses
        if target not in _AP_TABS or ui.tab not in _AP_TABS:
            return []
        return ["tab"] * ((_AP_TABS.index(target) - _AP_TABS.index(ui.tab)) % len(_AP_TABS))
    if action == "svw" and ui.gpage == "sess":   # a worker row on the session page
        j = int(target) if target.isdigit() else -1
        return ["enter"] if double and j == ui.sel else _ap_moves(j - ui.sel)
    if action == "back":        # the breadcrumb: back to the stations list
        return ["esc", "esc"]    # esc climbs one level at a time; the second is a no-op on the list
    if action == "lead":
        return ["l"]
    if action == "toggle_panel":
        return ["t" if target == "timeline" else "r"]
    if action in ("select", "group"):   # a station worker row, or an earlier group's line: select; Enter toggles
        station = _ap_global_stage_snap(gs, ui) if gs is not None else snap
        if station is None:
            return []
        if action == "select":
            names = [wk.name for wk in station.workers]
            if target not in names:
                return []
            stop = _rd_wstop(names.index(target))
        else:
            stop = _RD_GROUP + target
        stops = _rd_stops(station, ui)
        if stop not in stops:
            return []
        j, cur = stops.index(stop), _ap_cursor(ui, station)
        if action == "select":
            if j == cur:
                return ["space"] if double else ["enter"]
            return _ap_moves(j - cur)
        if double:   # the second press of a double click has already toggled the line
            return []
        return _ap_moves(j - cur) + ["enter"]
    if action == "nav" and gs is not None:   # an overview row: put the cursor there; on the cursor row, open or close it
        j = int(target) if target.isdigit() else -1
        if not 0 <= j < len(global_rows(gs, ui)):
            return []
        if double:   # the first press already toggled the row: put it back, then open the page
            return ["enter", "space"] if j == ui.gsel else _ap_moves(j - ui.gsel) + ["space"]
        return ["enter"] if j == ui.gsel else _ap_moves(j - ui.gsel) + ["enter"]
    if action == "files" and gs is not None:   # a worker's changes line: the cursor onto that worker, then f
        path, _, name = target.partition("\t")
        j = next((i for i, row in enumerate(global_rows(gs, ui)) if row == (path, "worker", name)), -1)
        return _ap_moves(j - ui.gsel) + ["f"] if j >= 0 else []
    if action == "close" and gs is not None:  # the ✕ on a card's title: select that card, then close it as x does
        paths = [path for path, _, _ in global_rows(gs, ui)]
        if target not in paths:
            return []
        return _ap_moves(paths.index(target) - ui.gsel) + ["x"]
    return []


def _ap_scroll(ui: UIState, delta: int) -> None:
    """Move the view by delta lines; positive = further up the content, whichever tab is shown."""
    if ui.tab == "live":  # scroll counts up from the bottom
        ui.scroll = max(ui.scroll + delta, 0)
        ui.follow = ui.scroll == 0
    else:  # scroll counts down from the top
        ui.scroll = max(ui.scroll - delta, 0)


def _ap_handle_detail_key(ui: UIState, key: str, rows: int) -> str:
    page = max(rows - 8, 1)
    if key in ("up", "k"):
        _ap_scroll(ui, 1)
    elif key in ("down", "j"):
        _ap_scroll(ui, -1)
    elif key == "pgup":
        _ap_scroll(ui, page)
    elif key == "pgdn":
        _ap_scroll(ui, -page)
    elif key in ("home", "g"):
        _ap_scroll(ui, _AP_FAR)
        if ui.tab == "live":
            ui.scroll = _AP_FAR
    elif key in ("end", "G"):
        _ap_scroll(ui, -_AP_FAR)
        if ui.tab != "live":
            ui.scroll = _AP_FAR
        ui.follow = True
    elif key in ("tab", "right", "l", "btab", "left", "h"):
        step = 1 if key in ("tab", "right", "l") else -1
        ui.tab = _AP_TABS[(_AP_TABS.index(ui.tab) + step) % len(_AP_TABS)]
        ui.scroll = 0
        ui.follow = True
        return "load_detail"
    elif key in ("esc", "backspace"):
        ui.view = "overview"
        ui.scroll = 0
        ui.follow = True
        ui.detail_text = []
    return ""


def _ap_follow_selection(ui: UIState, name: str, snap: Snapshot) -> str:
    """Point ui.sel at the worker called `name`; if it is gone, clamp. Returns the selected name."""
    workers = snap.workers
    if not workers:
        ui.sel = 0
        return ""
    for i, w in enumerate(workers):
        if w.name == name:
            ui.sel = i
            return name
    ui.sel = min(max(ui.sel, 0), len(workers) - 1)
    return workers[ui.sel].name


def _ap_sel_name(ui: UIState, snap: Snapshot | None) -> str:
    """Name of the worker under the cursor; "" on a panel row or when no worker is there."""
    if snap is None or _rd_panel_sel(snap, ui) or not 0 <= ui.sel < len(snap.workers):
        return ""
    return snap.workers[ui.sel].name


def _ap_started(prev: Snapshot | None, new: Snapshot) -> bool:
    """True when a live worker shows up that the previous snapshot did not have."""
    if prev is None:
        return False
    seen = {w.name for w in prev.workers}
    return any(w.state in LIVE_STATES and w.name not in seen for w in new.workers)


def _ap_follow_snapshot(ui: UIState, snap: Snapshot, sel_name: str, started: bool) -> str:
    """Keep the cursor on its worker by name. A new live worker may move it (slice-1 rule). Returns the selected name."""
    if not _rd_panel_sel(snap, ui):
        _ap_follow_selection(ui, sel_name, snap)
    name = _ap_sel_name(ui, snap)
    if started:
        ui.panel = ""
        ui.sel = _rd_sel_on_new_start(snap, ui.sel if name else -1)
        name = _ap_sel_name(ui, snap)
    return name


# -------------------------------------------------------------- events

@dataclass
class _ap_Event:
    kind: str       # "done" | "failed" | "noreport" | "stalled" | "p1"
    key: str        # ui.flashes key
    summary: str
    body: str
    urgency: str    # "low" | "normal" | "critical"


def _ap_eta(snap: Snapshot, wk: Worker) -> float | None:
    """Expected run time: median of finished workers on the same model (3+), else of all finished (3+)."""
    done = [w for w in snap.workers if w.state == DONE and w.end is not None and w.start > 0]
    same = [w.end - w.start for w in done if w.model and w.model == wk.model]  # type: ignore[operator]
    pool = same if len(same) >= 3 else [w.end - w.start for w in done]  # type: ignore[operator]
    return statistics.median(pool) if len(pool) >= 3 else None


def _ap_detect_events(prev: Snapshot | None, new: Snapshot) -> list[_ap_Event]:
    if prev is None:
        return []
    events: list[_ap_Event] = []
    old = {w.name: w for w in prev.workers}
    for w in new.workers:
        before = old.get(w.name)
        if before is None or before.state not in LIVE_STATES:
            continue
        text = w.title or (w.summary[0] if w.summary else "")
        if w.state == DONE:
            events.append(_ap_Event("done", w.name, f"✔ {w.name} finished", text, "normal"))
        elif w.state == FAILED:
            events.append(_ap_Event("failed", w.name, f"✖ {w.name} failed", text, "critical"))
        elif w.state == NOREPORT:
            summary = f"· {w.name} finished without a report"
            events.append(_ap_Event("noreport", w.name, summary, text, "low"))
        elif w.state in LIVE_STATES and w.start > 0 and (eta := _ap_eta(new, w)) is not None:
            if prev.now - w.start < eta <= new.now - w.start:
                body = f"expected about {int(eta // 60)}m {int(eta % 60):02d}s, still running"
                events.append(_ap_Event("overdue", w.name, f"⏰ {w.name} is past its estimated time", body, "normal"))
            if w.state == STALLED and before.state != STALLED:
                body = f"no output for {int(w.quiet_s // 60)} min"
                events.append(_ap_Event("stalled", w.name, f"▲ {w.name} stalled", body, "critical"))
        elif w.state == STALLED and before.state != STALLED:
            body = f"no output for {int(w.quiet_s // 60)} min"
            events.append(_ap_Event("stalled", w.name, f"▲ {w.name} stalled", body, "critical"))
    before_p1 = {r.name: sum(1 for f in r.findings if f.prio == 1) for r in prev.reviews}
    for r in new.reviews:
        p1 = [f for f in r.findings if f.prio == 1]
        if len(p1) > before_p1.get(r.name, 0):
            summary = f"{r.name}: {len(p1)} P1 finding(s)"
            events.append(_ap_Event("p1", "reviews", summary, p1[0].text, "critical"))
    return events


class _ap_Notifier:
    def __init__(self) -> None:
        self.procs: list[subprocess.Popen[bytes]] = []

    def send(self, summary: str, body: str, urgency: str) -> None:
        if shutil.which("notify-send") is None:
            return
        self.procs = [p for p in self.procs if p.poll() is None]  # reap finished ones
        try:
            self.procs.append(subprocess.Popen(
                ["notify-send", "-a", "workers", "-u", urgency, summary, body],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
        except OSError:
            pass


# ------------------------------------------------------------- drawing

def _ap_fit(lines: list[str], rows: int, cols: int) -> list[str]:
    out = [x if isinstance(x, str) else str(x) for x in lines[:rows]]
    out += [" " * cols] * (rows - len(out))
    return out


def _ap_diff_frame(prev: list[str] | None, new: list[str], full: bool) -> bytes:
    """Cursor-addressed writes for the rows that changed (all rows when full or no previous frame)."""
    parts: list[str] = []
    for i, line in enumerate(new):
        if full or prev is None or i >= len(prev) or prev[i] != line:
            parts.append(f"\x1b[{i + 1};1H{line}\x1b[0m")
    return "".join(parts).encode("utf-8", "replace")


def _ap_scanning_lines(rows: int, cols: int, stage: str, error: str) -> list[str]:
    name = os.path.basename(stage.rstrip("/")) or stage
    text = _wd_trunc(f"scanning {name} ...", cols)
    lines = [""] * rows
    mid = rows // 2
    lines[mid] = _wd_center(text, cols)
    if error and mid + 1 < rows:
        lines[mid + 1] = _wd_center(_wd_trunc(error, cols), cols)
    return lines


def _ap_safe_text(text: str, cols: int) -> str:
    """Text that did not come from render_frame: control characters out, each line cut to `cols`."""
    return "\n".join(_wd_trunc(line, cols) for line in text.splitlines())


def _ap_term_size(fd: int) -> tuple[int, int]:
    try:
        size = os.get_terminal_size(fd)
        if size.columns > 0 and size.lines > 0:
            return size.columns, size.lines
    except OSError:
        pass
    fallback = shutil.get_terminal_size((100, 40))
    return fallback.columns, fallback.lines


def _ap_write(fd: int, data: bytes) -> bool:
    """Write everything; False if the terminal is gone."""
    view = memoryview(data)
    while view:
        try:
            sent = os.write(fd, view)
        except BlockingIOError:
            select.select([], [fd], [], 0.5)
            continue
        except OSError:
            return False
        view = view[sent:]
    return True


def _ap_animating(snap: Snapshot | GlobalSnapshot | None, ui: UIState, now: float) -> bool:
    if any(t > now - 4 for t in ui.flashes.values()):
        return True
    if isinstance(snap, GlobalSnapshot):
        active = any(_rd_g_activity_level(stage) >= 2 or any(_rd_g_activity_level(w) >= 2
                                                              for w in stage.snap.workers)
                     for stage in snap.stages)
        timed = any(0 <= now - changed < 1.5 or 0 <= now - first < 1.0
                    for _, first, changed in _rd_G_ANIM.values())
        swarm = any(_rd_g_swarm_workers(_rd_g_rowdata(stage)) for stage in snap.stages)
        return active or timed or swarm
    return snap is not None and any(w.state in LIVE_STATES for w in snap.workers)


# ----------------------------------------------------------- collector

class _ap_Shared:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.snap: Snapshot | GlobalSnapshot | None = None
        self.seq = 0
        self.error = ""
        self.all_history = False
        self.poke: Callable[[], None] | None = None   # wakes the draw loop when a snapshot lands


def _ap_collector_loop(collector: Collector | GlobalCollector, shared: _ap_Shared, stop: threading.Event,
                       wake: threading.Event, interval: float) -> None:
    quick = isinstance(collector, GlobalCollector)   # first pass skips the slow native scan; the next pass is full at once
    while not stop.is_set():
        with shared.lock:
            all_history = shared.all_history
        try:
            snap = collector.snapshot(all_history=all_history, quick=True) if quick else collector.snapshot(all_history=all_history)
        except Exception as exc:
            with shared.lock:
                shared.error = f"{type(exc).__name__}: {exc}"
        else:
            if quick:
                snap.partial = True   # the quick pass is not the full one: the masthead says loading until the next pass lands
            with shared.lock:
                shared.snap = snap
                shared.seq += 1
                shared.error = ""
            if shared.poke is not None:
                shared.poke()   # every snapshot wakes the draw loop, so the screen follows the data without a key press
        if quick:
            quick = False
            continue
        wake.wait(interval)
        wake.clear()


def _ap_load_detail(ui: UIState, collector: Collector, snap: Snapshot | None, error: str) -> None:
    if ui.view != "detail" or snap is None or ui.tab == "live" or not 0 <= ui.sel < len(snap.workers):
        if ui.tab == "live" and ui.view not in ("lead", "files"):   # those pages are read once on open; a snapshot must not blank them
            ui.detail_text = []
        return
    try:
        ui.detail_text = list(collector.detail(snap.workers[ui.sel].name, ui.tab))
    except Exception as exc:
        ui.detail_text = [f"error: {type(exc).__name__}: {exc}"]
    if not ui.detail_text and error:
        ui.detail_text = [f"collector error: {error}"]


def _ap_load_global_detail(ui: UIState, collector: GlobalCollector, path: str, name: str) -> None:
    if ui.tab == "live":
        ui.detail_text = []
        return
    try:
        ui.detail_text = list(collector.detail(path, name, ui.tab))
    except Exception as exc:
        ui.detail_text = [f"error: {type(exc).__name__}: {exc}"]


# ----------------------------------------------------------- main loop

def _ap_interactive(collector: Collector | GlobalCollector, stage: str, fps: int, interval: float,
                    notify: bool, all_history: bool, density: str = "full",
                    global_mode: bool = False, tutorial: bool = False, *, ui: UIState) -> int:
    fd_in, fd_out = sys.stdin.fileno(), sys.stdout.fileno()
    stop = threading.Event()
    wake = threading.Event()
    resized = threading.Event()
    shared = _ap_Shared()
    shared.all_history = all_history

    def poke() -> None:
        try:
            os.write(pipe_w, b"\0")
        except OSError:
            pass
    shared.poke = poke
    notifier = _ap_Notifier()
    old_attr = termios.tcgetattr(fd_in)
    pipe_r, pipe_w = os.pipe()
    os.set_blocking(pipe_r, False)
    os.set_blocking(pipe_w, False)
    old_handlers: dict[int, object] = {}
    old_wakeup = -1
    entered = False
    title_on = _ap_title_enabled()
    title_send = _ap_title_send() if title_on else b""   # the picture goes in after the alternate screen is entered (images live per screen) and again after every clear (ESC[2J deletes it)
    title_del = _AP_TITLE_DELETE if title_on else b""
    thread: threading.Thread | None = None
    trace = ""
    suspend_requested = threading.Event()
    continued = threading.Event()
    ui.density, ui.cc = density, global_mode   # the caller's UIState carries the opt-ins (allow_actions); do not replace it
    expand_path = _ap_expand_path(collector) if global_mode else ""
    expand_save_at = 0.0

    def on_stop(_signum: int, _frame: object) -> None:
        stop.set()

    def on_winch(_signum: int, _frame: object) -> None:
        resized.set()

    def on_tstp(_signum: int, _frame: object) -> None:
        suspend_requested.set()

    def on_cont(_signum: int, _frame: object) -> None:
        continued.set()

    try:
        try:
            for sig, handler in ((signal.SIGINT, on_stop), (signal.SIGTERM, on_stop),
                                 (signal.SIGHUP, on_stop), (signal.SIGWINCH, on_winch)):
                old_handlers[sig] = signal.signal(sig, handler)
            if hasattr(signal, "SIGTSTP"):
                old_handlers[signal.SIGTSTP] = signal.signal(signal.SIGTSTP, on_tstp)
            if hasattr(signal, "SIGCONT"):
                old_handlers[signal.SIGCONT] = signal.signal(signal.SIGCONT, on_cont)
            old_wakeup = signal.set_wakeup_fd(pipe_w, warn_on_full_buffer=False)
        except ValueError:
            pass  # not the main thread: no signal handling
        entered = True
        _ap_write(fd_out, _AP_TERM_SETUP + title_send)
        tty.setcbreak(fd_in)
        thread = threading.Thread(target=_ap_collector_loop, args=(collector, shared, stop, wake, interval),
                                  daemon=True)
        thread.start()

        if global_mode:
            _ap_load_expand(ui, expand_path)
        ui.glevel, ui.glevel_all = _ap_load_levels()
        if tutorial:
            ui.demo = True
            tut_start(ui, lambda: [])
        saved_levels = (dict(ui.glevel), ui.glevel_all)
        prev_lines: list[str] | None = None
        prev_snap: Snapshot | None = None
        snap: Snapshot | None = None
        prev_global: GlobalSnapshot | None = None
        gs: GlobalSnapshot | None = None
        seen = 0
        sel_name = ""
        stage_sel_name = ""
        global_selected: tuple[str, str, str] | None = None
        global_worker_name = ""
        cols, rows = _ap_term_size(fd_out)
        ui.title_aspect = _ap_title_aspect(fd_out) if title_on else 0.0
        placed = (0, 0)
        full = True
        while not stop.is_set():
            if suspend_requested.is_set():
                suspend_requested.clear()
                _ap_write(fd_out, title_del + _AP_TERM_RESTORE)
                termios.tcsetattr(fd_in, termios.TCSAFLUSH, old_attr)
                if hasattr(signal, "SIGTSTP"):
                    signal.signal(signal.SIGTSTP, signal.SIG_DFL)
                    os.kill(os.getpid(), signal.SIGTSTP)
                    signal.signal(signal.SIGTSTP, on_tstp)
                tty.setcbreak(fd_in)
                _ap_write(fd_out, _AP_TERM_SETUP + title_send)
                placed = (0, 0)
                continued.clear()
                prev_lines = None
                full = True
            elif continued.is_set():
                continued.clear()
                tty.setcbreak(fd_in)
                _ap_write(fd_out, _AP_TERM_SETUP + title_send)
                placed = (0, 0)
                prev_lines = None
                full = True
            now = time.time()
            with shared.lock:
                new_snap, seq, error = shared.snap, shared.seq, shared.error
            if new_snap is not None and seq != seen:
                seen = seq
                if global_mode:
                    gs = new_snap if isinstance(new_snap, GlobalSnapshot) else None
                    if gs is not None:
                        if _ap_reconcile_expansion(ui, prev_global, gs):
                            expand_save_at = now + 0.4
                        for ev in _ap_global_events(prev_global, gs):
                            ui.flashes[ev.key] = now
                            if notify and not ui.muted:
                                notifier.send(ev.summary, ev.body, ev.urgency)
                        selected_path = global_selected
                        if ui.gpage == "worker":
                            global_worker_name = global_worker_name or next(
                                (row[2] for row in global_rows(gs, ui)
                                 if row[0] == ui.gstage and row[1] == "worker"), "")
                        global_selected = _ap_global_follow_selection(ui, gs, selected_path)
                        _ap_settle_closing(ui, gs, collector)
                        if not _ap_global_validate_page(ui, gs, global_worker_name):
                            global_worker_name = ""
                        stage_data = next((item for item in gs.stages if item.path == ui.gstage), None)
                        if ui.gpage == "stage" and stage_data is not None:
                            same_history = prev_global is not None and prev_global.all_history == gs.all_history
                            if not same_history:
                                stage_sel_name = ""
                            before = None
                            if same_history and prev_global is not None:
                                before = next((s for s in prev_global.stages if s.path == ui.gstage), None)
                            started = _ap_started(before.snap if before else None, stage_data.snap)
                            _ap_sync_expanded(ui, before.snap if before else None, stage_data.snap)
                            stage_sel_name = _ap_follow_snapshot(ui, stage_data.snap, stage_sel_name, started)
                            if ui.view == "detail" and not stage_sel_name:
                                ui.gpage, ui.view, ui.scroll = "", "overview", 0
                        prev_global = gs
                        if ui.gpage == "worker" and stage_data is not None:
                            ui.sel = next((i for i, worker in enumerate(stage_data.snap.workers)
                                           if worker.name == global_worker_name), 0)
                            if ui.view == "detail" and ui.tab != "live":
                                _ap_load_global_detail(ui, collector, ui.gstage, global_worker_name)
                        elif ui.gpage == "stage" and stage_data is not None:
                            stage_collector = collector.collector_for(ui.gstage)
                            if stage_collector is not None:
                                _ap_load_detail(ui, stage_collector, stage_data.snap, error)
                else:
                    snap = new_snap if isinstance(new_snap, Snapshot) else None
                    if snap is not None:
                        if prev_snap is not None and prev_snap.all_history != snap.all_history:
                            prev_snap = None  # history toggled: the worker set changed, not a real event
                        for ev in _ap_detect_events(prev_snap, snap):
                            ui.flashes[ev.key] = now
                            if notify and not ui.muted:
                                notifier.send(ev.summary, ev.body, ev.urgency)
                        started = _ap_started(prev_snap, snap)
                        _ap_sync_expanded(ui, prev_snap, snap)
                        prev_snap = snap
                        sel_name = _ap_follow_snapshot(ui, snap, sel_name, started)
                        _ap_load_detail(ui, collector, snap, error)  # type: ignore[arg-type]
            ui.flashes = {k: t for k, t in ui.flashes.items() if now - t <= 10}
            if global_mode and expand_save_at and now >= expand_save_at:
                _ap_save_expand(ui, expand_path)
                expand_save_at = 0.0

            size = _ap_term_size(fd_out)
            if resized.is_set() or size != (cols, rows):
                resized.clear()
                cols, rows = size
                ui.title_aspect = _ap_title_aspect(fd_out) if title_on else 0.0
                full = True
            if global_mode and gs is None:
                lines = _ap_scanning_lines(rows, cols, "all orchestrators", error)
            elif global_mode and gs is not None:
                stage_snap = _ap_global_stage_snap(gs, ui) if ui.gpage in ("worker", "stage") else None
                if ui.view == "quota":
                    quota_snap = Snapshot(quota=gs.quota, quota_ok=gs.quota_ok, spend=gs.spend, spend_ok=gs.spend_ok,
                                          openrouter=gs.openrouter, openrouter_ok=gs.openrouter_ok,
                                          usage_history=gs.usage_history)
                    lines = render_frame(quota_snap, ui, cols, rows, now)
                elif stage_snap is not None and ui.gpage in ("worker", "stage"):
                    if ui.gpage == "worker" and ui.view == "detail":
                        ui.sel = next((i for i, worker in enumerate(stage_snap.workers)
                                       if worker.name == global_worker_name), 0)
                    lines = render_frame(stage_snap, ui, cols, rows, now, _ap_global_stage(gs, ui))
                else:
                    lines = render_global(gs, ui, cols, rows, now)
            elif snap is None:
                lines = _ap_scanning_lines(rows, cols, stage, error)
            else:
                try:
                    lines = render_frame(snap, ui, cols, rows, now)
                except Exception as exc:
                    lines = [_wd_trunc(f"render error: {type(exc).__name__}: {exc}", cols)]
            if error and (gs is not None if global_mode else snap is not None) and lines:
                lines[-1] = _wd_c(_wd_trunc(f"collector error: {error}", cols), _wd_C_RED)
            lines = _ap_fit(lines, rows, cols)
            payload = _ap_diff_frame(prev_lines, lines, full)
            place = b""
            if title_on and ui.title_place != (0, 0):
                rewritten = prev_lines is None or any(i >= len(prev_lines) or prev_lines[i] != lines[i] for i in range(ui.title_place[1]))
                if full or rewritten or ui.title_place != placed:   # the picture goes on top of the text just written
                    place = _ap_title_place(*ui.title_place)
            if title_on and ui.title_place == (0, 0) and placed != (0, 0):   # left the tree: take the picture off the screen
                place = _AP_TITLE_UNPLACE
            placed = ui.title_place
            if payload:
                clear = b"\x1b[2J" if full else b""
                if not _ap_write(fd_out, _AP_SYNC_ON + clear + (title_send if clear else b"") + payload + place + _AP_SYNC_OFF):
                    break
            prev_lines, full = lines, False

            anim_snap = gs if global_mode else snap
            timeout = 1.0 / fps if _ap_animating(anim_snap, ui, now) else 1.0
            ready, _, _ = select.select([fd_in, pipe_r], [], [], timeout)
            if pipe_r in ready:
                try:
                    os.read(pipe_r, 4096)
                except OSError:
                    pass
            if fd_in in ready:
                keys = _ap_read_keys(fd_in)
                if keys is None:
                    break
                pending = list(keys)
                while pending:
                    key = pending.pop(0)
                    if key.startswith(("click:", "wheel:")):
                        pending[:0] = _ap_pointer_keys(ui, key, gs if global_mode else None,
                                                       None if global_mode else snap, time.monotonic())
                        continue
                    if global_mode:
                        before_open, before_shut = set(ui.gopen_keys), set(ui.gshut_keys)
                        action = _ap_global_key(ui, key, gs, rows) if gs is not None else ""
                        if _ap_expand_key_changed(ui, before_open, before_shut):
                            expand_save_at = time.time() + 0.4
                        if (ui.glevel, ui.glevel_all) != saved_levels:
                            _ap_save_levels(ui.glevel, ui.glevel_all)
                            saved_levels = (dict(ui.glevel), ui.glevel_all)
                        if action == "load_detail" and gs is not None and ui.gpage == "worker":
                            selected_rows = global_rows(gs, ui)
                            if selected_rows:
                                global_worker_name = selected_rows[min(ui.gsel, len(selected_rows) - 1)][2]
                            _ap_load_global_detail(ui, collector, ui.gstage, global_worker_name)
                        elif action == "load_detail" and gs is not None and ui.gpage == "stage":
                            stage_snap = _ap_global_stage_snap(gs, ui)
                            stage_collector = collector.collector_for(ui.gstage)
                            if stage_collector is not None:
                                _ap_load_detail(ui, stage_collector, stage_snap, error)
                        if gs is not None and not ui.gpage:
                            rows_data = global_rows(gs, ui)
                            global_selected = (rows_data[ui.gsel] if rows_data else None)
                        elif gs is not None and ui.gpage == "stage":
                            current_stage = next((item for item in gs.stages if item.path == ui.gstage), None)
                            stage_sel_name = _ap_sel_name(ui, current_stage.snap if current_stage is not None else None)
                    else:
                        action = _ap_handle_key(ui, key, snap, rows)
                    if action == "quit":
                        stop.set()
                        break
                    if not global_mode and snap is not None:
                        sel_name = _ap_sel_name(ui, snap)
                    if action == "toggle_all":
                        with shared.lock:
                            shared.all_history = not shared.all_history
                        wake.set()
                    elif action == "close" and gs is not None:
                        _ap_close_selected(ui, gs, collector)  # type: ignore[arg-type]
                    elif action == "pin" and gs is not None:
                        if _ap_pin_selected(ui, gs, collector):  # type: ignore[arg-type]
                            wake.set()   # the collector refreshes now, so the row moves section in the next frame
                    elif action == "end_lead" and gs is not None:
                        _ap_end_lead(ui, collector)  # type: ignore[arg-type]
                    elif action == "load_page":
                        if gs is not None:
                            station = _ap_global_stage(gs, ui)
                            if station is not None:
                                _ap_load_page(ui, station.dir or station.path, station, station.snap, now)
                        else:
                            _ap_load_page(ui, stage, None, snap, now)
                    elif action == "load_detail" and not global_mode:
                        _ap_load_detail(ui, collector, snap, error)
    except Exception:
        trace = traceback.format_exc()
    finally:
        if global_mode and expand_save_at:
            _ap_save_expand(ui, expand_path)
        stop.set()
        wake.set()
        if entered:
            _ap_write(fd_out, title_del + _AP_TERM_RESTORE)
        try:
            termios.tcsetattr(fd_in, termios.TCSAFLUSH, old_attr)
        except termios.error:
            pass
        if old_wakeup != -1 or old_handlers:
            try:
                signal.set_wakeup_fd(-1)
            except ValueError:
                pass
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]
        os.close(pipe_r)
        os.close(pipe_w)
        if thread is not None:
            thread.join(1.0)
    if trace:
        sys.stderr.write(_ap_safe_text(trace, _ap_term_size(fd_out)[0]) + "\n")
        return 1
    return 0


# ----------------------------------------------------------------- cli

def _ap_fps(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer from 1 to 30") from None
    if not 1 <= value <= 30:
        raise argparse.ArgumentTypeError("must be an integer from 1 to 30")
    return value


def _ap_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="station", description="Dashboard for orchestrator worker agents.")
    p.add_argument("--demo", action="store_true", help="show generated demo data")
    p.add_argument("--tutorial", action="store_true", help="interactive tutorial on demo data")
    p.add_argument("--serve", action="store_true", help="serve the read-only web monitor")
    p.add_argument("--port", type=int, default=8743, help="web monitor port")
    p.add_argument("--host", default="127.0.0.1", help="web monitor bind address")
    p.add_argument("--plain", action="store_true", help="print one plain-text snapshot and exit")
    p.add_argument("--json", action="store_true", help="print one JSON snapshot (schema status.visibility) and exit")
    p.add_argument("--execution", metavar="ID", help="with --json: print the full detail of one execution")
    density = p.add_mutually_exclusive_group()
    density.add_argument("--compact", action="store_true", help="use compact overview density")
    density.add_argument("--full", action="store_true", help="use full overview density")
    p.add_argument("--everything", action="store_true", help="show all plain output without a row limit")
    p.add_argument("--all", action="store_true", help="start with all history shown")
    p.add_argument("--stage", metavar="DIR", help="stage directory (default: next to this script)")
    p.add_argument("--global", dest="global_view", action="store_true", help="show every orchestrator")
    p.add_argument("--roots", nargs="+", metavar="DIR", help="roots to scan in global mode")
    p.add_argument("--fps", type=_ap_fps, default=10, metavar="N", help="frame rate while animating, 1-30")
    p.add_argument("--no-notify", action="store_true", help="no desktop notifications")
    p.add_argument("--allow-actions", action="store_true",
                   help="enable confirmed process actions (stop, kill, resume, spawn, ending sessions)")
    p.add_argument("--title-test", action="store_true", help="draw the masthead picture once and print the terminal's replies")
    p.add_argument("--version", action="version", version=f"station {_AP_VERSION}")
    p.add_argument("-v", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("-c", action="store_true", help=argparse.SUPPRESS)
    return p


def _ap_density_path() -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
                        "station", "density")


def _ap_levels_path() -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
                        "station", "levels.json")


def _ap_load_levels() -> tuple[dict[str, int], int]:
    """Per-station disclosure levels and the all-cards level, as left by the last run."""
    try:
        with open(_ap_levels_path(), encoding="utf-8") as stream:
            data = json.load(stream)
        per = {str(k): min(max(int(v), 0), 2) for k, v in dict(data.get("stations", {})).items()}
        return per, min(max(int(data.get("all", -1)), -1), 2)
    except (OSError, ValueError, TypeError, AttributeError):
        return {}, -1


def _ap_save_levels(per: dict[str, int], every: int) -> None:
    try:
        path = _ap_levels_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump({"stations": per, "all": every}, stream)
    except OSError:
        pass


def _ap_load_density() -> str:
    try:
        with open(_ap_density_path(), encoding="utf-8") as stream:
            value = stream.read().strip()
    except (OSError, UnicodeError):
        return "full"
    return value if value in ("full", "compact") else "full"


def _ap_save_density(value: str) -> None:
    try:
        path = _ap_density_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(value + "\n")
    except OSError:
        pass


def _ap_plain_size() -> tuple[int, int] | None:
    width = height = None
    try:
        width = int(os.environ["STATUS_COLS"])
    except (KeyError, ValueError):
        pass
    try:
        height = int(os.environ["STATUS_ROWS"])
    except (KeyError, ValueError):
        pass
    if width is not None and height is not None:
        return width, height
    for fd in (2, 0):
        try:
            size = os.get_terminal_size(fd)
            return width or size.columns, height or size.lines
        except OSError:
            continue
    try:
        with open("/dev/tty", "rb") as tty_file:
            size = os.get_terminal_size(tty_file.fileno())
            return width or size.columns, height or size.lines
    except OSError:
        return (width or 100, height or 40) if width is not None or height is not None else None


def _ap_plain(collector: Collector, all_history: bool, everything: bool = False) -> int:
    snap = collector.snapshot(all_history=all_history)
    deadline = time.monotonic() + 3.0
    while not snap.quota_ok and time.monotonic() < deadline:
        time.sleep(0.1)
        snap = collector.snapshot(all_history=all_history)
    size = _ap_plain_size()
    if everything:
        cols, rows = size[0] if size is not None else 100, None
    elif size is None:
        cols, rows = 100, None
    else:
        cols, height = size
        rows = max(0, height - 2)
    sys.stdout.write(render_plain(snap, cols, rows).rstrip("\n") + "\n")
    sys.stdout.flush()
    return 0


def _ap_json(args: argparse.Namespace, script_dir: str) -> int:
    """One-shot JSON snapshot (or one execution's detail) on stdout; no listener, no model."""
    if _ap_global_mode(args, script_dir):
        collector_g = GlobalCollector(_ap_roots(args), quota=False, openclaw_home=os.path.expanduser("~"))
        try:
            gs = collector_g.snapshot(all_history=args.all)
        finally:
            collector_g.close()
        doc = global_payload(gs, title="all orchestrators", poll_ms=3000)
        diagnostics = list(gs.errors)
        workers = [worker for stage in gs.stages for worker in stage.snap.workers]
        stage = None
    else:
        local_stage = os.path.join(script_dir, ".stage")
        stage = args.stage or (local_stage if _ap_has_stage_files(local_stage) else script_dir)
        collector = DemoCollector(stage) if args.demo else Collector(stage)
        try:
            snap = collector.snapshot(all_history=args.all)
        finally:
            collector.close()
        base = os.path.basename(os.path.abspath(stage))
        label = (os.path.basename(os.path.dirname(os.path.abspath(stage))) if base == ".stage" else base) or base
        doc = single_payload(snap, stage_dir=stage, label=label, poll_ms=3000)
        diagnostics = []
        workers = snap.workers
    unlinked = sum(1 for worker in workers if not worker.execution_id)
    if unlinked:
        diagnostics.append(f"{unlinked} worker(s) without execution identity")
    out = contract_document(doc, diagnostics=diagnostics)
    if args.execution:
        detail = execution_detail(workers, args.execution, stage_dir=stage)
        if detail is None:
            out = {"schema": out["schema"], "schema_version": out["schema_version"],
                   "diagnostics": diagnostics, "error": f"unknown execution: {args.execution}"}
            sys.stdout.write(json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n")
            return 1
        out = contract_document({"execution": detail}, diagnostics=diagnostics)
    sys.stdout.write(json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


def _ap_tutorial(args: argparse.Namespace) -> int:
    """`--tutorial`: the control center on fake sessions. Every file the app would write (expand choices, levels,
    density) goes to a scratch directory that is removed afterwards, so the tutorial leaves nothing behind."""
    if not (sys.stdout.isatty() and sys.stdin.isatty()) or os.environ.get("TERM", "") == "dumb":
        sys.stderr.write("station --tutorial needs an interactive terminal\n")
        return 2
    scratch = tempfile.mkdtemp(prefix="status-tutorial-")
    cx_set_demo(True)
    for name in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        os.environ[name] = scratch
    os.environ["HOME"] = scratch   # anything that still expands ~ lands in the scratch directory too
    try:
        collector = DemoGlobalCollector(os.path.join(scratch, "overrides.json"))
        return _ap_interactive(collector, "tutorial", args.fps, 0.5, False, False, global_mode=True, tutorial=True,  # type: ignore[arg-type]
                               ui=_ap_launch_ui("full"))
    finally:
        cx_set_demo(False)
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _ap_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    script_dir = os.path.dirname(os.path.realpath(sys.argv[0]))
    if args.title_test:
        return _ap_title_test()
    if args.tutorial:
        return _ap_tutorial(args)
    global_mode = _ap_global_mode(args, script_dir)
    if args.json or args.execution:
        return _ap_json(args, script_dir)
    if args.serve:
        if global_mode:
            collector_g = GlobalCollector(_ap_roots(args), quota=False, openrouter_only=True, openclaw_home=os.path.expanduser("~"))
            last_global = {"snapshot": None}
            def global_web_payload():
                gs = collector_g.snapshot(all_history=args.all)
                last_global["snapshot"] = gs
                return global_payload(gs, title="all orchestrators", poll_ms=3000)
            def global_stage(path):
                gs = last_global["snapshot"]
                return stage_payload(gs, path) if gs is not None else None
            try:
                return run_serve(
                    global_web_payload,
                    lambda stage, name, since=None: worker_detail(stage, name, since),
                    global_stage,
                    host=args.host, port=args.port,
                    control_fn=lambda path, action: _ap_control(collector_g, path, action),
                )
            finally:
                collector_g.close()
        local_stage = os.path.join(script_dir, ".stage")
        stage = args.stage or (local_stage if _ap_has_stage_files(local_stage) else script_dir)
        collector = DemoCollector(stage) if args.demo else Collector(stage)
        try:
            base = os.path.basename(os.path.abspath(stage))
            label = (os.path.basename(os.path.dirname(os.path.abspath(stage))) if base == ".stage" else base) or base
            last_single = {"snap": None}
            def single_web_payload():
                snap = collector.snapshot(all_history=args.all)
                last_single["snap"] = snap
                return single_payload(snap, stage_dir=stage, label=label, poll_ms=3000)
            def single_stage(path):
                snap = last_single["snap"]
                if path != stage or snap is None:
                    return None
                live = sum(worker.state in LIVE_STATES for worker in snap.workers)
                wrapped = Stage(path=stage, label=label, snap=snap, live=live, last_activity=0.0)
                return stage_payload(GlobalSnapshot(now=snap.now, stages=[wrapped]), stage)
            return run_serve(
                single_web_payload,
                lambda stage, name, since=None: worker_detail(stage, name, since),
                single_stage,
                host=args.host, port=args.port,
            )
        finally:
            collector.close()
    if global_mode:
        collector_g = GlobalCollector(_ap_roots(args), openclaw_home=os.path.expanduser("~"))
        try:
            size = _ap_plain_size()
            cols, height = size if size else (100, 40)
            rows = None if args.everything else max(0, height - 2)
            interactive = (not args.plain and sys.stdout.isatty() and sys.stdin.isatty()
                           and os.environ.get("TERM", "") != "dumb")
            if interactive:   # no snapshot first: the collector thread paints a quick one, then the full one
                return _ap_interactive(collector_g, "all orchestrators", args.fps, 6.0,
                                       not args.no_notify, args.all, global_mode=True,
                                       ui=_ap_ui_from_args(args, "full"))
            gs = collector_g.snapshot(all_history=args.all)
            sys.stdout.write(render_global_plain(gs, cols, rows).rstrip("\n") + "\n")
            return 0
        finally:
            collector_g.close()
    local_stage = os.path.join(script_dir, ".stage")
    stage = args.stage or (local_stage if _ap_has_stage_files(local_stage) else script_dir)
    collector = DemoCollector(stage) if args.demo else Collector(stage)
    try:
        interactive = (not args.plain and sys.stdout.isatty() and sys.stdin.isatty()
                       and os.environ.get("TERM", "") != "dumb")
        if not interactive:
            return _ap_plain(collector, args.all, args.everything)
        density = "compact" if args.compact else "full" if args.full else _ap_load_density()
        return _ap_interactive(collector, stage, args.fps, 0.5 if args.demo else 2.0,
                               not (args.no_notify or args.demo), args.all, density,
                               ui=_ap_ui_from_args(args, density))
    finally:
        try:
            collector.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
