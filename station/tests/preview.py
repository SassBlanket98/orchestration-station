"""Not part of the product: print one frame of the busy snapshot.

python3 preview.py [cols] [rows] [view] [--strip] [--tab live|brief|report] [--sel N]
"""
from __future__ import annotations

import re
import sys

from test_render import NOW, busy

from model import UIState
from render import render_frame

SGR = re.compile(r"\x1b\[[0-9;]*m")


def main(argv: list[str]) -> int:
    strip = "--strip" in argv
    tab, sel = "live", 0
    args: list[str] = []
    it = iter([a for a in argv if a != "--strip"])
    for a in it:
        if a == "--tab":
            tab = next(it, "live")
        elif a == "--sel":
            sel = int(next(it, "0"))
        else:
            args.append(a)
    cols = int(args[0]) if args else 110
    rows = int(args[1]) if len(args) > 1 else 35
    view = args[2] if len(args) > 2 else "overview"
    text = ["# Brief: accounts API", "Goal: add `GET /api/accounts` with validation", "",
            "- read the existing routes", "- add a handler for `POST /api/accounts`", "* write tests", "",
            ("Some longer paragraph text that has to be wrapped to the width of the panel so that we can "
             "see how the wrapping behaves on a narrow terminal.")]
    ui = UIState(view=view, tab=tab, sel=sel, detail_text=text if tab != "live" else [])
    for line in render_frame(busy(), ui, cols, rows, NOW + 0.3):
        print(SGR.sub("", line) if strip else line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
