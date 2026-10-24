from __future__ import annotations

import unittest

from test_legibility import NOW, EnvPatch, plain, session

from model import DONE, FAILED, RUNNING, GlobalSnapshot, UIState, Worker
from render import global_rows, render_global

TITLE = "S T A T I O N"


def rows_of(gs: GlobalSnapshot, cols: int, rows: int) -> list[str]:
    return plain(render_global(gs, UIState(), cols, rows, NOW))


class Banner(EnvPatch):
    def gs(self) -> GlobalSnapshot:
        return GlobalSnapshot(now=NOW, stages=[session("/a", "alpha", [Worker("w", RUNNING, start=NOW - 9)],
                                                       state="active", name="one")])

    def test_a_short_or_narrow_screen_collapses_the_banner_to_one_line(self) -> None:
        for cols, rows in ((120, 40), (60, 40), (60, 50)):
            frame = rows_of(self.gs(), cols, rows)
            self.assertIn("◆ Station", frame[0], (cols, rows))
            self.assertNotIn(TITLE, "\n".join(frame), (cols, rows))

    def test_a_big_terminal_keeps_the_text_title(self) -> None:
        frame = rows_of(self.gs(), 120, 50)
        self.assertIn(TITLE, frame[0])
        self.assertNotIn("◆ Station", "\n".join(frame))


class IdleRows(EnvPatch):
    def test_idle_row_shows_no_zero_counts_or_flat_sparkline(self) -> None:
        quiet = session("/q", "quiet", [Worker("d", DONE, start=NOW - 90, end=NOW - 80)], state="idle",
                        name="quiet-one", last=NOW - 600)
        gs = GlobalSnapshot(now=NOW, stages=[quiet])
        frame = plain(render_global(gs, UIState(gopen_keys={"p:/q"}), 120, 38, NOW))
        line = next(ln for ln in frame if "quiet-one" in ln)
        self.assertNotIn("0 live", line)
        self.assertIn("◐", line)
        self.assertNotIn("▁▁▁▁", line)


class PhoneFrame(EnvPatch):
    def test_a_46_column_frame_keeps_attention_working_model_and_filter(self) -> None:
        attn = session("/a", "alpha", [Worker("bad", FAILED, start=NOW - 60, end=NOW - 1)], state="active",
                       name="one", lead_state="gone")
        busy = session("/b", "beta", [Worker("x", RUNNING, start=NOW - 9)], state="active", name="two")
        frame = "\n".join(rows_of(GlobalSnapshot(now=NOW, stages=[attn, busy]), 46, 30))
        self.assertIn("1 problem", frame)
        self.assertIn("working", frame)
        self.assertIn("Sonnet", frame)   # the model name stays in the row cell (cut short to fit)
        self.assertIn("filter", frame)


class Ordering(EnvPatch):
    """Four sessions in the active section: needs attention, then working, then most recently active."""

    def test_attention_then_working_then_recent_then_older(self) -> None:
        older = session("/older", "older", [], state="active", name="older", lead_state="gone", last=NOW - 300)
        recent = session("/recent", "recent", [], state="active", name="recent", lead_state="gone", last=NOW - 5)
        work = session("/work", "work", [Worker("w", RUNNING, start=NOW - 9)], state="active", name="work",
                       last=NOW - 900)
        attn = session("/attn", "attn", [Worker("bad", FAILED, start=NOW - 60, end=NOW - 1)], state="active",
                       name="attn", lead_state="gone", last=NOW - 500)
        gs = GlobalSnapshot(now=NOW, stages=[older, recent, work, attn])
        order = [r[0] for r in global_rows(gs, UIState()) if r[1] == "stage"]
        self.assertEqual(order, ["/attn", "/work", "/recent", "/older"])

    def test_the_same_order_shows_on_screen(self) -> None:
        recent = session("/recent", "recent", [], state="active", name="recent", lead_state="gone", last=NOW - 5)
        work = session("/work", "work", [Worker("w", RUNNING, start=NOW - 9)], state="active", name="work")
        frame = rows_of(GlobalSnapshot(now=NOW, stages=[recent, work]), 120, 38)
        at = [next(i for i, ln in enumerate(frame) if name in ln) for name in ("work", "recent")]
        self.assertEqual(at, sorted(at))


class LoadingMarker(EnvPatch):
    def test_loading_shows_on_the_quick_pass_and_goes_when_the_full_pass_lands(self) -> None:
        busy = session("/b", "busy", [Worker("x", RUNNING, start=NOW - 9)], state="active", name="busy-one")
        gs = GlobalSnapshot(now=NOW, stages=[busy])
        gs.partial = True   # what app.py sets on the quick first snapshot
        self.assertIn("loading…", "\n".join(rows_of(gs, 120, 38)))
        gs.partial = False  # the full pass has landed
        self.assertNotIn("loading…", "\n".join(rows_of(gs, 120, 38)))


if __name__ == "__main__":
    unittest.main()
