"""Interactive tutorial (`station --tutorial`): a step card drawn over the real control center on demo data.
Imports model, widgets, ccpane and ccstrip only. app.py offers every key to `tut_key` first, then calls `tut_after_key`;
render.py calls `tut_overlay`. Nothing moves on by itself: a step's goal ticks it done, and only the next key moves on.
A step that asks for a menu is done only once you have been in that menu (`UIState.tut_seen` remembers where you went)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ccpane import PANE_TABS
from ccstrip import STRIP_TABS
from model import UIState
from widgets import (
    _wd_box,
    _wd_c,
    _wd_C_AMBER,
    _wd_C_BLUE,
    _wd_C_GREEN,
    _wd_C_MUTED,
    _wd_C_SUB,
    _wd_C_TEXT,
    _wd_pad,
    _wd_wrap,
)

_tu_NEXT, _tu_BACK = (".", ">"), (",", "<")
_tu_RESTART, _tu_SKIP, _tu_QUIT = "R", "S", "Q"
_tu_CARD_W = 80
_tu_Rows = Callable[[], list[tuple[str, str, str]]]   # the tree rows now: (stage path, kind, name)


def _tu_snapshot(ui: UIState) -> tuple:
    """The state steps compare against, taken when the step starts."""
    return (ui.gsel, frozenset(ui.gopen_keys), frozenset(ui.gshut_keys), frozenset(ui.gfolded), frozenset(ui.gopen))


def _tu_pick(rows: _tu_Rows, ui: UIState, kind: str, path_has: str = "") -> None:
    """Put the cursor on the first row of this kind (whose stage path holds `path_has`); stays put when there is none."""
    for i, (path, k, _name) in enumerate(rows()):
        if k == kind and path_has in path:
            ui.gsel = i
            return


def _tu_clean(ui: UIState, rows: _tu_Rows) -> None:
    """Every step starts from the same screen: the control center, nothing open, nothing typed, default folds."""
    ui.cc = True
    ui.gpage, ui.view, ui.scroll, ui.detail_text = "", "overview", 0, []
    ui.gturn, ui.gworker, ui.gsess, ui.sel = "", "", False, 0
    ui.help, ui.gedit, ui.gquery, ui.gfilter = False, False, "", "all"
    ui.cc_pane_open, ui.cc_focus, ui.cc_tab = False, "table", "Overview"
    ui.cc_strip_open, ui.cc_strip, ui.cc_strip_scroll, ui.cc_pane_scroll = True, "Usage", 0, 0
    ui.cc_confirm, ui.cc_spawn, ui.cc_spawn_model, ui.cc_input, ui.cc_spawn_row = None, False, "", "", ""
    ui.close_ask, ui.note = None, ""
    ui.gopen_keys, ui.gshut_keys, ui.gfolded, ui.gopen = set(), set(), set(), set()
    ui.ghide_idle = ui.ghide_closed = False
    ui.gsel = 0


def _tu_setup(*extra: Callable[[UIState, _tu_Rows], None]) -> Callable[[UIState, _tu_Rows], None]:
    def run(ui: UIState, rows: _tu_Rows) -> None:
        _tu_clean(ui, rows)
        for fn in extra:
            fn(ui, rows)
    return run


def _tu_on_stage(ui: UIState, rows: _tu_Rows) -> None:
    _tu_pick(rows, ui, "stage", "demo-project")


def _tu_on_worker(ui: UIState, rows: _tu_Rows) -> None:
    _tu_pick(rows, ui, "worker")


def _tu_on_closed(ui: UIState, rows: _tu_Rows) -> None:
    ui.gopen.add(("", "more", "finished:/demo/old-spike"))   # finished sessions sit behind a one-line fold; open the one the step needs
    _tu_pick(rows, ui, "stage", "old-spike")


def _tu_in_station(ui: UIState, rows: _tu_Rows) -> None:
    """Start inside the station view of the demo project, its live workers open (what w does from the list)."""
    for path, kind, _name in rows():
        if kind == "stage" and "demo-project" in path:
            ui.gpage, ui.gstage, ui.view, ui.tab, ui.follow = "stage", path, "overview", "live", True
            ui.expanded = {name for p, k, name in rows() if p == path and k == "worker"}
            return


def _tu_pane_open(ui: UIState, rows: _tu_Rows) -> None:
    ui.cc_pane_open = True
    ui.tut_seen.add(ui.cc_tab)


def _tu_strip_seen(ui: UIState, rows: _tu_Rows) -> None:
    ui.tut_seen.add(ui.cc_strip)


# -- goals ---------------------------------------------------------------------------------------------------------
def _tu_moved(ui: UIState) -> bool:
    return ui.gsel != ui.tut_base[0]


def _tu_folded(ui: UIState) -> bool:
    return (frozenset(ui.gopen_keys), frozenset(ui.gshut_keys), frozenset(ui.gfolded), frozenset(ui.gopen)) != ui.tut_base[1:5]


def _tu_visit(page: str) -> Callable[[UIState, str], None]:
    """Track: note when the page was open."""
    def track(ui: UIState, key: str) -> None:
        if ui.gpage == page:
            ui.tut_seen.add("in")
    return track


def _tu_back_from_page(ui: UIState) -> bool:
    return "in" in ui.tut_seen and ui.gpage == ""


def _tu_track_station(ui: UIState, key: str) -> None:
    if ui.gpage == "stage" and ui.view in ("lead", "cost", "files"):
        ui.tut_seen.add(ui.view)


def _tu_track_tabs(ui: UIState, key: str) -> None:
    if ui.cc_pane_open:
        ui.tut_seen.add(ui.cc_tab)


def _tu_track_strip(ui: UIState, key: str) -> None:
    if ui.cc_strip_open:
        ui.tut_seen.add(ui.cc_strip)


def _tu_checklist(names: tuple[str, ...]) -> Callable[[UIState], str]:
    def line(ui: UIState) -> str:
        return "   ".join(("✔ " if n in ui.tut_seen else "· ") + n for n in names)
    return line


def _tu_track_quota(ui: UIState, key: str) -> None:
    if ui.view == "quota":
        ui.tut_seen.add("in")


def _tu_track_keys(*wanted: str) -> Callable[[UIState, str], None]:
    def track(ui: UIState, key: str) -> None:
        if key in wanted:
            ui.tut_seen.add(key)
    return track


def _tu_track_note(**prefixes: str) -> Callable[[UIState, str], None]:
    """Track: ui.note starting with one of the prefixes (what the action reported) marks that name seen."""
    def track(ui: UIState, key: str) -> None:
        for name, prefix in prefixes.items():
            if ui.note.startswith(prefix):
                ui.tut_seen.add(name)
    return track


def _tu_track_end(ui: UIState, key: str) -> None:
    if key == "x":
        ui.tut_seen.add("x")
    elif "x" in ui.tut_seen and key in ("y", "n", "esc"):
        ui.tut_seen.add("answered")


def _tu_all(*names: str) -> Callable[[UIState], bool]:
    return lambda ui: all(n in ui.tut_seen for n in names)


@dataclass(frozen=True)
class TutStep:
    title: str
    text: tuple[str, ...]
    ask: str = ""                                                    # what to do; "" = a reading step, next works at once
    done: Callable[[UIState], bool] | None = None                    # the goal; once true it stays true for the step
    track: Callable[[UIState, str], None] | None = None              # runs after every key: remembers where you went
    progress: Callable[[UIState], str] | None = None                 # an extra line (a checklist)
    setup: Callable[[UIState, _tu_Rows], None] = _tu_clean           # the screen the step starts from
    at: str = "bottom"                                               # where the card sits: "bottom" or "top"


_tu_STEPS: tuple[TutStep, ...] = (
    TutStep("Welcome",
            ("This is the real monitor on made-up sessions, so nothing here can touch your own work.",
             "Nothing moves on by itself. Each step asks you to do one thing and ticks when you have; then press . for the next step.",
             "If you get lost, R puts the step back the way it started. S skips a step, , goes back, Q leaves.",
             "Tip: w opens the station view, the fullest page for one session. It has its own step.")),
    TutStep("How the screen is laid out",
            ("Top: counts of live, idle and finished sessions, then the quota bar for each account, then the filter chips.",
             "Middle: one block per project. Each session is a row; a session that started helpers (workers) lists them underneath.",
             "Bottom: a strip of usage, events, logs and metrics, then the key line. The key line changes with where you are.")),
    TutStep("Reading a row",
            ("LEAD is the session you talk to. WORKER is a helper it started. HARN is the tool (CC·P Claude personal, CDX Codex, OCode, Cursr).",
             "The mark in front of a worker is its state: a spinner is running, ○ quiet for a while, ✖ failed, ✔ finished.",
             "CTX and TOKENS say how full the context is and what it has used; COST is money spent; TOOLS counts tool calls.")),
    TutStep("Move the cursor",
            ("The highlighted row is the cursor. Almost everything you do acts on the row under it.",),
            "Press ↑ or ↓ (j and k also work)", _tu_moved),
    TutStep("Fold and unfold",
            ("A live session shows its workers; a quiet one is a single line. Fold and unfold to show more or less.",
             "Enter flips the row under the cursor. → opens it and ← shuts it."),
            "Press Enter on a project or session row", _tu_folded),
    TutStep("The station view",
            ("w opens the station view of the session under the cursor: a live card for every worker with what it is doing now, a timeline of activity, and the reviews.",
             "It is the fullest picture of one session. Move with ↑↓, Enter folds a card, Esc goes back to the list. On a project heading, w opens its first session."),
            "Press w, look around, then press Esc",
            _tu_back_from_page, _tu_visit("stage"), None, _tu_setup(_tu_on_stage)),
    TutStep("Pages inside the station view",
            ("From the station view, L opens the lead page (the session's own prompt and recent actions), C the cost page (tokens and money per worker) and F the files page (the files each worker changed, with the diff size). The lead and files pages show example data in the tutorial.",
             "Esc comes back to the station view. t folds the timeline and r the reviews. Visit L, C and F."),
            "Press L, then Esc, then C, then Esc, then F",
            _tu_all("lead", "cost", "files"), _tu_track_station, _tu_checklist(("lead", "cost", "files")),
            _tu_setup(_tu_in_station)),
    TutStep("The session page",
            ("Space opens a full page for the session: its goal, what it is doing now, every worker and the milestones.",
             "On that page ↑↓ scroll and Enter opens a worker. Esc goes back to the list."),
            "Press Space on the highlighted session, look around, then press Esc",
            _tu_back_from_page, _tu_visit("sess"), None, _tu_setup(_tu_on_stage)),
    TutStep("The worker page",
            ("Put the cursor on a worker (the indented rows) and press Space for its page: brief, result, and every step it took.",
             "There b opens the brief, r the report and f the files it touched. Esc goes back."),
            "Press ↓ to a WORKER row, press Space, then Esc",
            _tu_back_from_page, _tu_visit("wk"), None, _tu_setup(_tu_on_worker)),
    TutStep("The details pane",
            ("d opens a pane on the right with everything about the selected row, so you do not have to leave the list.",
             "It follows the cursor: move up and down and the pane changes with you."),
            "Press d", lambda ui: ui.cc_pane_open),
    TutStep("The six pane tabs",
            ("The pane has six tabs: Overview, Prompt (what the session was asked), Tools (every call), Files, Events and Metrics.",
             "Tab moves focus into the pane. Then ← → step through the tabs, or press 1 to 6 to jump to one. Visit all six."),
            "Press Tab, then use ← → or 1-6",
            _tu_all(*PANE_TABS), _tu_track_tabs, _tu_checklist(PANE_TABS), _tu_setup(_tu_on_worker, _tu_pane_open)),
    TutStep("The bottom strip",
            ("The strip under the list has four tabs: Usage (quota per account), Events (what just happened), Logs and Metrics.",
             "[ and ] switch tab. E hides or shows the strip. Tab moves focus into it so the arrows scroll it. Visit all four."),
            "Press ] (or [) until you have seen every tab",
            _tu_all(*STRIP_TABS), _tu_track_strip, _tu_checklist(STRIP_TABS), _tu_setup(_tu_strip_seen), "top"),
    TutStep("Search",
            ("/ searches by project, stream, lead or model. Type a word, then Enter keeps it. Try api or docs.",
             "Esc clears the search again."),
            "Press /, type a word, press Enter", lambda ui: bool(ui.gquery) and not ui.gedit),
    TutStep("Filter",
            ("Filters narrow the list to a kind of session: 1 all, 2 working, 3 lagging (quiet too long), 4 problems.",
             "f cycles through them. Esc goes back to all."),
            "Press 2, 3 or 4", lambda ui: ui.gfilter != "all"),
    TutStep("Quota",
            ("u opens the quota page: for each account, how much of the 5-hour and weekly allowance is used, and when it resets.",
             "Press u again, or Esc, to come back."),
            "Press u, then u or Esc to come back",
            lambda ui: "in" in ui.tut_seen and ui.view == "overview", _tu_track_quota),
    TutStep("Show more or less",
            ("c shuts every session and project. e opens them all, idle and closed ones too.",
             "h hides the idle section, H the closed one, a shows all history."),
            "Press c, then e", _tu_all("c", "e"), _tu_track_keys("c", "e"), _tu_checklist(("c", "e"))),
    TutStep("Stop and kill (asks first)",
            ("s on a session or worker asks 'Stop … ?'. Answer y to send it. Anything else cancels. Stop asks it to quit nicely.",
             "K kills it for good, but only after a stop was sent and a few seconds have passed. That is the real question; in the tutorial y sends nothing and tells you what would have happened.",
             "So: press s, then y. Wait three seconds, press K, then y."),
            "Press s then y, then wait 3 seconds, then K and y",
            _tu_all("stop", "kill"), _tu_track_note(stop="demo: stop", kill="demo: kill"),
            _tu_checklist(("stop", "kill")), _tu_setup(_tu_on_worker)),
    TutStep("Resume a closed session",
            ("A session that has ended can be picked up again. r asks 'Resume … in a new terminal?' and shows the exact command.",
             "It only works on a closed session. The cursor is on one now: old-spike, under the project's finished line."),
            "Press r, then y", _tu_all("resume"), _tu_track_note(resume="demo: resume"),
            None, _tu_setup(_tu_on_closed)),
    TutStep("Spawn a new worker",
            ("n starts something new in the project under the cursor. First you pick a model with ↑↓ and Enter. Greyed ones are out of quota, and it says why.",
             "Then you type one line of task, Enter, and confirm with y. Keys such as . are typed as text while you type the task."),
            "Press n, pick a model, type a task, Enter, then y",
            _tu_all("spawn"), _tu_track_note(spawn="demo: spawn"), None, _tu_setup(_tu_on_stage)),
    TutStep("End a session",
            ("x ends the lead session of the row under the cursor, after a box asking you to confirm.",
             "y ends it, anything else cancels. Here y ends nothing."),
            "Press x, then y or n", _tu_all("answered"), _tu_track_end, None, _tu_setup(_tu_on_stage)),
    TutStep("Classic view",
            ("v flips between this control center and the older plain tree with no side pane or strip. The same sessions show in both.",
             "Look around, then press . for the next step, which puts you back in the control center."),
            "Press v", lambda ui: not ui.cc),
    TutStep("The key list",
            ("? shows every key in one box, grouped. It is the quickest way to find a key you forgot.",
             "Any key closes it."),
            "Press ?", lambda ui: ui.help),
    TutStep("You are set",
            ("That is every screen. Remember w for the station view and ? for the key list. The mouse works too: click a row, double-click to open it, wheel to scroll.",
             "Not in the demo: OpenClaw sessions have a turn-by-turn page, and 5 shows OpenRouter spend when it is set up.",
             "Run `station --tutorial` any time to come back. Press ? for the full key list. Press . to finish.")),
)


def tut_start(ui: UIState, rows: _tu_Rows) -> None:
    ui.tut = 0
    _tu_enter(ui, rows)


def _tu_enter(ui: UIState, rows: _tu_Rows) -> None:
    ui.tut_seen, ui.tut_ok = set(), False
    _tu_STEPS[ui.tut].setup(ui, rows)
    ui.tut_base = _tu_snapshot(ui)
    if _tu_STEPS[ui.tut].done is None:
        ui.tut_ok = True


def _tu_go(ui: UIState, index: int, rows: _tu_Rows) -> None:
    if index >= len(_tu_STEPS):
        ui.tut = -1
        ui.note = "tutorial finished"
        return
    ui.tut = max(0, index)
    _tu_enter(ui, rows)


def tut_active(ui: UIState) -> bool:
    return 0 <= ui.tut < len(_tu_STEPS)


def tut_key(ui: UIState, k: str, rows: _tu_Rows) -> bool:
    """True when the tutorial used the key. Typing in the search box or a task, and the open ? box, keep their own keys."""
    if not tut_active(ui) or ui.gedit or ui.help or (ui.cc_spawn and ui.cc_spawn_model):
        return False
    if k in _tu_NEXT:
        if ui.tut_ok:
            _tu_go(ui, ui.tut + 1, rows)
        else:
            ui.note = "do the step first (the ▶ line), or press S to skip it"
        return True
    if k in _tu_BACK:
        _tu_go(ui, ui.tut - 1, rows)
        return True
    if k == _tu_RESTART:
        _tu_enter(ui, rows)
        return True
    if k == _tu_SKIP:
        _tu_go(ui, ui.tut + 1, rows)
        return True
    if k == _tu_QUIT:
        ui.tut = -1
        ui.note = "tutorial left"
        return True
    return False


def tut_after_key(ui: UIState, key: str = "") -> None:
    """Call after the app handled a key: remember where you went, and tick the step when its goal shows."""
    if not tut_active(ui):
        return
    step = _tu_STEPS[ui.tut]
    if step.track is not None:
        step.track(ui, key)
    if not ui.tut_ok and step.done is not None and step.done(ui):
        ui.tut_ok = True


def tut_overlay(lines: list[str], ui: UIState, cols: int, rows: int) -> list[str]:
    """The step card over the frame: bottom left by default, top left for steps whose subject is at the bottom."""
    if not tut_active(ui) or cols < 30 or rows < 10:
        return lines
    step = _tu_STEPS[ui.tut]
    inner = min(_tu_CARD_W, cols) - 4
    body: list[str] = []
    for para in step.text:
        body.extend(" " + _wd_c(row, _wd_C_TEXT) for row in _wd_wrap(para, inner - 1))
    body.append("")
    if step.progress is not None:
        body.append(" " + _wd_c(step.progress(ui), _wd_C_SUB))
    if step.ask:
        body.append(" " + (_wd_c("✔ Done. Press . for the next step", _wd_C_GREEN, bold=True) if ui.tut_ok
                           else _wd_c("▶ " + step.ask, _wd_C_AMBER, bold=True)))
    keys = ["R restart", "S skip", ", back", "Q leave"] if not ui.tut_ok and step.ask else (
        [". next", ", back", "R restart", "Q leave"] if step.ask else [". next", ", back", "Q leave"])
    body.append(" " + _wd_c("   ".join(keys), _wd_C_MUTED))
    w = min(cols, _tu_CARD_W)
    h = min(rows - 2, len(body) + 2)
    title = _wd_c(f"tutorial {ui.tut + 1}/{len(_tu_STEPS)}", _wd_C_BLUE, bold=True) + _wd_c(" ── " + step.title, _wd_C_SUB)
    box = _wd_box([_wd_pad(x, w - 2) for x in body[:h - 2]], w, h, title, _wd_C_GREEN if ui.tut_ok else _wd_C_BLUE)
    top = 5 if step.at == "top" else max(0, rows - 1 - h)
    out = list(lines)
    for i, row in enumerate(box):
        if top + i < len(out):
            out[top + i] = row + " " * max(0, cols - w)
    return out
