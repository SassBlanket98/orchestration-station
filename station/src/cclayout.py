"""Control center: the master-detail frame. Composes the hierarchy table (drawn by render.py's tree, passed in as
`table`), the detail pane and the bottom strip. Imports model, ccpane, ccstrip and widgets only; render.py calls it."""
from __future__ import annotations

from collections.abc import Callable

from cchint import hint_line
from ccpane import pane_lines, pane_max_scroll, pane_tab_bar
from ccstrip import strip_lines, strip_tab_bar
from model import GlobalSnapshot, UIState
from widgets import _wd_c, _wd_C_BLUE, _wd_C_DIM, _wd_center, _wd_fit, _wd_lr, _wd_width

MIN_COLS = 120       # under this the classic full-screen pages are used
MIN_ROWS = 24
TABLE_MIN = 64       # the table keeps at least this many columns
WIDE_COLS = 190      # ultra-wide: cap the master table and give surplus to the detail pane
TABLE_MAX = 128      # readable master width at WIDE_COLS and above (pane open only)


def cc_applies(cols: int, rows: int) -> bool:
    """True when the master-detail frame fits the terminal."""
    return cols >= MIN_COLS and rows >= MIN_ROWS


def cc_frame(gs: GlobalSnapshot, ui: UIState, cols: int, rows: int, t: float, sel: tuple[str, str, str] | None,
             table: Callable[[int, int], list[str]]) -> list[str]:
    """The whole frame as `rows` lines of `cols` cells, or [] to make render.py draw the classic tree instead.
    `sel` is the selected tree row (stage path, kind, worker name) or None; `table(width, height)` draws the tree at that
    size and sets its click hits (so the table must start at screen row 1, column 1). Adds hits for pane tabs
    ("cctab", name) and strip tabs ("ccstrip", name) with 1-based screen coordinates."""
    if not cc_applies(cols, rows):
        return []
    try:
        strip_open = bool(ui.cc_strip_open and rows >= 30)
        strip_h = (8 if rows >= 40 else 6) if strip_open else 0
        table_h = rows - strip_h - 1

        pane_open = ui.cc_pane_open
        left_w = cols
        pane_w = 0
        if pane_open:
            left_w = max(TABLE_MIN, round(cols * 0.58))
            if cols >= WIDE_COLS:
                left_w = min(left_w, TABLE_MAX)
            if cols - left_w - 1 < 40:
                left_w = cols - 41
            pane_w = cols - left_w - 1

        # The tree's final row is its key footer. Clip or pad to the shared pane height.
        left = table(left_w, table_h)
        left = left[:table_h]
        left.extend([_wd_fit("", left_w)] * max(0, table_h - len(left)))

        path, kind, name = sel if sel is not None else ("", "", "")
        stage = next((item for item in gs.stages if item.path == path), None) if sel is not None else None
        if stage is None and name.startswith("p:"):   # a project heading: show its live (else newest) session
            root = name[2:]
            kin = [item for item in gs.stages if (item.cwd or item.dir or item.path).startswith(root)]
            stage = next((item for item in kin if item.lead_state == "live" or item.live), kin[0] if kin else None)
        worker = None
        if stage is not None and kind == "worker":
            worker = next((item for item in stage.snap.workers if item.name == name), None)

        close_label = _wd_c("✕  d", _wd_C_DIM)
        # the tabs get the width left of the close label, so their click spans never run under it
        pane_bar, pane_spans = pane_tab_bar(ui.cc_tab, max(0, pane_w - _wd_width(close_label) - 1)) if pane_open else ("", [])
        if pane_open:
            pane_bar = _wd_lr(pane_bar, close_label, pane_w)
        if pane_open:
            pane_h = table_h - 1
            if stage is None:
                body = [_wd_fit("", pane_w) for _ in range(max(0, pane_h))]
                if pane_h:
                    message = _wd_c("select a session or worker", _wd_C_DIM)
                    body[pane_h // 2] = _wd_fit(_wd_center(message, pane_w), pane_w)
            else:
                ui.cc_pane_scroll = min(max(0, ui.cc_pane_scroll),
                                        pane_max_scroll(stage, worker, ui.cc_tab, pane_w, pane_h))
                body = pane_lines(stage, worker, ui.cc_tab, pane_w, pane_h, ui.cc_pane_scroll, t)
        ui.cc_strip_scroll = max(0, ui.cc_strip_scroll)

        frame = []
        for row in range(table_h):
            if pane_open:
                accent = ui.cc_focus == "pane"
                divider = _wd_c("│", _wd_C_BLUE if accent else _wd_C_DIM)
                frame.append(_wd_fit(left[row], left_w) + divider + _wd_fit(
                    pane_bar if row == 0 else body[row - 1] if row - 1 < len(body) else "", pane_w))
            else:
                frame.append(_wd_fit(left[row], cols))

        if table_h:
            if not pane_open:
                label = _wd_c("▸ details  d", _wd_C_DIM)
                label_width = _wd_width(label)
                frame[0] = _wd_lr(frame[0], label, cols)
                ui.hits.append((0, 0, cols - label_width, cols - 1, "ccpane", ""))
            else:
                label_width = _wd_width(close_label)
                ui.hits.append((0, 0, left_w + 1 + pane_w - label_width, left_w + pane_w,
                                "ccpane", ""))

        # Tree hits are set by table(); keep them and append tabs in zero-based frame coordinates.
        if pane_open:
            ui.hits.extend((0, 0, left_w + 1 + c0, left_w + 1 + c1, "cctab", name)
                           for c0, c1, name in pane_spans)
        if strip_open:
            rule_color = _wd_C_BLUE if ui.cc_focus == "strip" else _wd_C_DIM
            frame.append(_wd_fit(_wd_c("─" * cols, rule_color), cols))
            _strip_bar, strip_spans = strip_tab_bar(ui.cc_strip, cols)
            strip_body_h = strip_h - 1
            strip = strip_lines(gs, ui.cc_strip, stage, worker, cols, strip_body_h,
                                ui.cc_strip_scroll, t)
            strip_row = len(frame)
            frame.extend(strip[:strip_body_h])
            frame.extend([_wd_fit("", cols)] * max(0, strip_body_h - len(strip)))
            ui.hits.extend((strip_row, strip_row, c0, c1, "ccstrip", tab)
                           for c0, c1, tab in strip_spans)
        frame.append(_wd_fit(hint_line(ui, cols), cols))
        return frame if len(frame) == rows else []
    except Exception:  # noqa: BLE001 - the frame must fall back on every rendering error.
        return []
