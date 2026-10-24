"""Usage screen (u / 5): account order, fit-to-screen steps, sections, history sampling, keys."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest
from typing import ClassVar
from unittest.mock import patch

import collect
from app import _ap_global_key
from model import (
    GlobalSnapshot,
    OpenRouterSnapshot,
    OpenRouterSpendDay,
    OpenRouterSpendModel,
    QuotaAccount,
    QuotaCredit,
    QuotaExtra,
    QuotaWindow,
    Snapshot,
    UIState,
)
from render import render_frame
from usageview import _uv_rank, usage_lines, usage_screen

NOW = 1_800_000_000.0
H, D = 3600, 86400
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(lines: list[str]) -> str:
    return _ANSI.sub("", "\n".join(lines))


def win(name: str, wid: str, used: float, pace: float | None, reset: float, **kw: object) -> QuotaWindow:
    return QuotaWindow(name, used, pace, window=wid, resets_at=NOW + reset, **kw)   # type: ignore[arg-type]


def accounts() -> list[QuotaAccount]:
    return [
        QuotaAccount("claude-work", [win("5h", "five_hour", 21, 82, H), win("Week", "seven_day", 40, 23, 5 * D)]),
        QuotaAccount("opencode-go", [win("5h", "five_hour", 3, 79, H), win("30 days", "thirty_day", 76, 66, 10 * D, runs_out_at=NOW + 8 * D)],
                     extra=QuotaExtra(7.54, "estimated", NOW - 16 * D)),
        QuotaAccount("cursor-models", [win("Auto", "auto", 100, 92, 2 * D)]),
        QuotaAccount("codex", [win("5h", "five_hour", 8, 77, H)], in_use=True),
        QuotaAccount("claude", [win("5h", "five_hour", 48, 59, 2 * H), win("Week", "seven_day", 36, 48, 3 * D)], in_use=True),
        QuotaAccount("grok-bot", [], stale=True),
        QuotaAccount("anthropic-api", [], stale=True),
        QuotaAccount("anthropic-api", credit=QuotaCredit(8.05, 0.9, NOW + 9 * D)),
    ]


def openrouter() -> OpenRouterSnapshot:
    return OpenRouterSnapshot(
        balance=14.2, floor_usd=5.0, burn_per_day=1.8, runs_out_at=NOW + 8 * D, balance_at=NOW, low=False, stale=False,
        since="2026-10-10",
        per_day=[OpenRouterSpendDay("2026-10-23", 1.81, 40), OpenRouterSpendDay("2026-10-24", 1.46, 59)],
        per_model=[OpenRouterSpendModel("deepseek/deepseek-v4", 2.0, 80, 1, 1), OpenRouterSpendModel("z-ai/glm-5.3", 1.0, 19, 1, 1)])


class OrderAndSections(unittest.TestCase):
    def test_rank_is_users_order(self) -> None:
        names = ["claude-work", "opencode-go", "cursor-models", "codex", "claude", "zzz"]
        self.assertEqual(sorted(names, key=_uv_rank), ["claude", "codex", "cursor-models", "opencode-go", "claude-work", "zzz"])

    def test_screen_lists_accounts_in_order_and_sections(self) -> None:
        text = plain(usage_lines(accounts(), True, openrouter(), True, 0, 100, NOW)[0])
        order = [text.index(n) for n in ("● claude ", "● codex", "● cursor-models", "● opencode-go", "● claude-work")]
        self.assertEqual(order, sorted(order))
        for head in ("SUBSCRIPTIONS", "API CREDITS", "OPENROUTER", "NO LIVE DATA"):
            self.assertIn(head, text)

    def test_duplicate_stale_twin_of_credit_account_is_merged(self) -> None:
        text = plain(usage_lines(accounts(), True, None, False, 0, 100, NOW)[0])
        self.assertEqual(text.count("anthropic-api"), 1)
        self.assertIn("$8.05 left", text)
        self.assertIn("grok-bot", text.split("NO LIVE DATA")[1])

    def test_verdicts_and_extra_spend(self) -> None:
        text = plain(usage_lines(accounts(), True, None, False, 0, 100, NOW)[0])
        self.assertIn("11 under pace", text)       # claude: 48 used, pace 59
        self.assertIn("17 over pace", text)        # claude-work week: 40 vs 23
        self.assertIn("capped", text)              # cursor at 100%
        self.assertIn("+$7.54 past cap", text)
        self.assertIn("out in 8d", text)

    def test_unavailable_quota(self) -> None:
        text = plain(usage_lines([], False, None, False, 0, 80, NOW)[0])
        self.assertIn("no usage data", text)
        self.assertIn("STATUS_QUOTA_CMD", text)

    def test_spend_still_shown_when_quota_is_down(self) -> None:
        # No quota command (the open-source default): the spend block must still render.
        text = plain(usage_lines(accounts(), False, openrouter(), True, 0, 100, NOW)[0])
        self.assertIn("today $1.46", text)
        self.assertIn("quota unavailable", text)
        self.assertNotIn("claude-work", text)          # stale quota accounts are not drawn
        self.assertNotIn("API CREDITS", text)

    def test_openrouter_today_vs_yesterday(self) -> None:
        text = plain(usage_lines(accounts(), True, openrouter(), True, 0, 100, NOW)[0])
        self.assertIn("today $1.46", text)
        self.assertIn("yesterday $1.81", text)
        self.assertIn("deepseek-v4 67%", text)


class FitToScreen(unittest.TestCase):
    hist: ClassVar[dict[str, list[float]]] = {"claude": [10.0, 20.0, 30.0, 40.0, 48.0]}

    def test_tall_screen_keeps_everything_with_sparklines(self) -> None:
        text = plain(usage_screen(accounts(), True, openrouter(), True, 0, 100, 60, NOW, self.hist))
        self.assertIn("last 24h", text)
        self.assertIn("low 10% · high 48%", text)

    def test_short_wide_screen_goes_two_columns(self) -> None:
        lines = usage_screen(accounts(), True, openrouter(), True, 0, 150, 30, NOW, self.hist)
        text = plain(lines)
        row = next(x for x in text.split("\n") if "● claude " in x)
        self.assertIn("● codex", row)                     # claude and codex share a row
        self.assertEqual(len(lines), 30)

    def test_short_narrow_screen_drops_sparklines_then_scrolls(self) -> None:
        text = plain(usage_screen(accounts(), True, openrouter(), True, 0, 100, 24, NOW, self.hist))
        self.assertNotIn("last 24h", text)
        scrolled = plain(usage_screen(accounts(), True, openrouter(), True, 6, 100, 24, NOW, self.hist))
        self.assertNotEqual(text, scrolled)

    def test_every_line_is_box_width(self) -> None:
        for cols, rows in ((80, 30), (100, 46), (150, 30), (200, 50)):
            for line in usage_screen(accounts(), True, openrouter(), True, 0, cols, rows, NOW, self.hist):
                self.assertEqual(len(_ANSI.sub("", line)), cols, (cols, rows))


class HistorySampling(unittest.TestCase):
    def collector(self, path: str) -> collect.Collector:
        os.environ["STATUS_USAGE_HISTORY"] = path
        self.addCleanup(os.environ.pop, "STATUS_USAGE_HISTORY", None)
        return collect.Collector(tempfile.mkdtemp(), quota=False)

    def test_samples_throttled_pruned_and_reloaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hist.jsonl")
            c = self.collector(path)
            now = time.time()
            c._co_usage_sample(accounts(), now - 30 * H)       # too old: dropped on the next write
            c._co_usage_sample(accounts(), now - 100)
            c._co_usage_sample(accounts(), now - 50)           # inside 5 minutes: skipped
            with open(path) as fh:
                rows = [json.loads(x) for x in fh]
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]["a"]["claude"], 48.0)
            self.assertNotIn("grok-bot", rows[0]["a"])         # stale / windowless accounts are not sampled
            self.assertEqual(rows[0]["a"]["opencode-go"], 3.0)  # the shortest window
            again = self.collector(path)
            self.assertEqual(again._co_usage_series(now)["claude"], [48.0])

    def test_series_buckets_last_value_per_half_hour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            c = self.collector(os.path.join(tmp, "h.jsonl"))
            now = time.time()
            c._co_usage = [(now - 3 * H, {"a": 10.0}), (now - 3 * H + 60, {"a": 12.0}), (now - 60, {"a": 30.0})]
            self.assertEqual(c._co_usage_series(now)["a"], [12.0, 30.0])

    def test_bad_lines_and_missing_file_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "h.jsonl")
            with open(path, "w") as fh:
                fh.write("not json\n" + json.dumps({"t": time.time() - 60, "a": {"x": 5}}) + "\n" + json.dumps({"t": "?"}) + "\n")
            c = self.collector(path)
            self.assertEqual(c._co_usage_series(time.time()), {"x": [5.0]})
            self.assertEqual(self.collector(os.path.join(tmp, "none.jsonl"))._co_usage_series(time.time()), {})


class NoColor(unittest.TestCase):
    def test_usage_screen_has_no_ansi_and_keeps_meaning(self) -> None:
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            lines = usage_screen(accounts(), True, openrouter(), True, 0, 100, 50, NOW)
        self.assertFalse(any("\x1b[" in line for line in lines))
        text = plain(lines)
        self.assertIn("100% cap", text)          # cursor-models capped window
        self.assertIn(" 76% warn", text)         # opencode-go thirty_day window
        self.assertIn("17 over pace", text)
        self.assertIn("* claude", text)          # in-use marker without colour

    def test_render_frame_strips_quota_header_too(self) -> None:
        snap = Snapshot(quota=accounts(), quota_ok=True, openrouter=openrouter(), openrouter_ok=True)
        ui = UIState(view="quota")
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            lines = render_frame(snap, ui, 100, 40, NOW)
        self.assertFalse(any("\x1b[" in line for line in lines))
        self.assertIn("SUBSCRIPTIONS", plain(lines))


class Keys(unittest.TestCase):
    def test_u_and_5_open_the_same_screen_and_scroll(self) -> None:
        gs = GlobalSnapshot()
        for key in ("u", "5"):
            ui = UIState()
            _ap_global_key(ui, key, gs, 40)
            self.assertEqual(ui.view, "quota")
            _ap_global_key(ui, "down", gs, 40)
            self.assertEqual(ui.scroll, 1)
            _ap_global_key(ui, "up", gs, 40)
            _ap_global_key(ui, "up", gs, 40)
            self.assertEqual(ui.scroll, 0)
            _ap_global_key(ui, key, gs, 40)
            self.assertEqual(ui.view, "overview")


if __name__ == "__main__":
    unittest.main()
