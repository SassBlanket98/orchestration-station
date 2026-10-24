"""OpenRouter usage: quota openrouter --json, masthead chip, view [5], /api/state."""
from __future__ import annotations

import json
import os
import time
import unittest
from unittest import mock

from test_render_global import NOW, plain, station

import collect
import webdata
from model import (
    DONE,
    OPENROUTER_ACCOUNT,
    GlobalSnapshot,
    LogState,
    OpenRouterSnapshot,
    OpenRouterSpendDay,
    OpenRouterSpendModel,
    QuotaAccount,
    QuotaCredit,
    UIState,
    Worker,
)
from render import (
    _rd_g_openrouter_lines,
    _rd_openrouter_chip,
    _rd_openrouter_worker,
    render_global,
)

_FIXTURE = {
    "generatedAt": 1_760_000_000_000,
    "floorUsd": 2.0,
    "credit": {
        "account": "openrouter",
        "balanceUsd": 9.99,
        "balanceAt": 1_760_000_000_000,
        "burnPerDayUsd": 1.5,
        "runsOutAt": 1_761_000_000_000,
        "low": False,
        "reason": None,
    },
    "spend": {
        "since": "2026-10-10",
        "perDay": [{"date": "2026-10-22", "usd": 0.75, "requests": 4}],
        "perModel": [
            {
                "model": "deepseek/x",
                "usd": 0.5,
                "requests": 3,
                "promptTokens": 100,
                "completionTokens": 20,
            },
            {"model": "other/y", "usd": 0.25, "requests": 1, "promptTokens": 0, "completionTokens": 0},
        ],
    },
}


class ParseTests(unittest.TestCase):
    def test_with_spend(self) -> None:
        c = collect.Collector("", quota=False)
        snap = c._co_parse_openrouter(_FIXTURE, NOW)
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap.balance, 9.99)
        self.assertEqual(snap.floor_usd, 2.0)
        self.assertEqual(len(snap.per_day), 1)
        self.assertEqual(snap.per_model[0].model, "deepseek/x")

    def test_empty_spend(self) -> None:
        body = dict(_FIXTURE)
        body["spend"] = {"since": "2026-10-10", "perDay": [], "perModel": []}
        c = collect.Collector("", quota=False)
        snap = c._co_parse_openrouter(body, NOW)
        assert snap is not None
        self.assertEqual(snap.per_day, [])

    def test_stale_balance(self) -> None:
        body = json.loads(json.dumps(_FIXTURE))
        body["credit"]["balanceAt"] = int((NOW - 31 * 60) * 1000)
        c = collect.Collector("", quota=False)
        snap = c._co_parse_openrouter(body, NOW)
        assert snap is not None
        self.assertTrue(snap.stale)

    def test_under_floor_marks_low(self) -> None:
        body = json.loads(json.dumps(_FIXTURE))
        body["credit"]["balanceUsd"] = 1.0
        body["credit"]["low"] = False
        c = collect.Collector("", quota=False)
        snap = c._co_parse_openrouter(body, NOW)
        assert snap is not None
        self.assertTrue(snap.low)

    def test_command_failure_leaves_block_absent(self) -> None:
        with mock.patch.dict(os.environ, {"STATUS_OPENROUTER_CMD": "/bin/false"}):
            c = collect.Collector("", quota=True)
            deadline = time.time() + 3
            while time.time() < deadline and not c._co_openrouter_ok():
                time.sleep(0.05)
            self.assertFalse(c._co_openrouter_ok())
            self.assertIsNone(c._co_openrouter_snapshot())
            c.close()


class ChipTests(unittest.TestCase):
    def test_chip_text_and_alert(self) -> None:
        snap = OpenRouterSnapshot(
            balance=9.99, floor_usd=2.0, burn_per_day=None, runs_out_at=NOW + 86400,
            balance_at=NOW, low=False, stale=False, since="", per_day=[], per_model=[],
        )
        text, alert, _stale = _rd_openrouter_chip(snap)
        self.assertIn("openrouter $9.99", text)
        self.assertIn("out ", text)
        self.assertFalse(alert)
        snap.low = True
        _, alert, _ = _rd_openrouter_chip(snap)
        self.assertTrue(alert)

    def test_masthead_replaces_duplicate_credit(self) -> None:
        or_snap = OpenRouterSnapshot(
            balance=9.99, floor_usd=2.0, burn_per_day=None, runs_out_at=None,
            balance_at=NOW, low=False, stale=False, since="", per_day=[], per_model=[],
        )
        credit = QuotaCredit(balance=9.99, burn_per_day=None, runs_out_at=None, low=False)
        gs = GlobalSnapshot(
            now=NOW,
            stages=[station("/a", "alpha", [Worker("w", DONE)], last=NOW)],
            quota=[QuotaAccount(OPENROUTER_ACCOUNT, credit=credit)],
            quota_ok=True,
            openrouter=or_snap,
            openrouter_ok=True,
        )
        frame = "\n".join(plain(render_global(gs, UIState(), 120, 30, NOW)))
        self.assertEqual(frame.count("9.99"), 1)
        self.assertIn("openrouter $9.99", frame)


class ViewTests(unittest.TestCase):
    def test_openrouter_view_empty_spend(self) -> None:
        or_snap = OpenRouterSnapshot(
            balance=10.0, floor_usd=2.0, burn_per_day=0.5, runs_out_at=None,
            balance_at=NOW, low=False, stale=False, since="2026-10-10", per_day=[], per_model=[],
        )
        gs = GlobalSnapshot(now=NOW, openrouter=or_snap, openrouter_ok=True)
        lines = _rd_g_openrouter_lines(gs, 80)
        self.assertTrue(any("no OpenRouter spend yet" in ln for ln in lines))

    def test_view_key_renders(self) -> None:
        or_snap = OpenRouterSnapshot(
            balance=9.99, floor_usd=2.0, burn_per_day=1.0, runs_out_at=NOW + 86400,
            balance_at=NOW, low=False, stale=False, since="2026-10-10",
            per_day=[OpenRouterSpendDay("2026-10-22", 0.75, 4)],
            per_model=[OpenRouterSpendModel("m", 0.75, 4, 1, 2)],
        )
        gs = GlobalSnapshot(now=NOW, openrouter=or_snap, openrouter_ok=True)
        ui = UIState(view="openrouter")
        frame = "\n".join(plain(render_global(gs, ui, 120, 40, NOW)))
        self.assertIn("openrouter", frame.lower())
        self.assertIn("2026-10-22", frame)


class WorkerMarkerTests(unittest.TestCase):
    def test_openrouter_log_model(self) -> None:
        wk = Worker("w", DONE, model="deepseek-or", log=LogState(model="openrouter/deepseek/x"))
        self.assertTrue(_rd_openrouter_worker(wk))

    def test_local_deepseek_not_marked(self) -> None:
        wk = Worker("w", DONE, model="deepseek-v4.1-flash", log=LogState(model="deepseek-v4.1-flash"))
        self.assertFalse(_rd_openrouter_worker(wk))


class WebdataTests(unittest.TestCase):
    def test_payload_openrouter_null_when_offline(self) -> None:
        gs = GlobalSnapshot(now=NOW, openrouter_ok=False)
        payload = webdata.global_payload(gs)
        self.assertIsNone(payload["openrouter"])

    def test_payload_with_data(self) -> None:
        or_snap = OpenRouterSnapshot(
            balance=1.0, floor_usd=2.0, burn_per_day=None, runs_out_at=None,
            balance_at=NOW, low=True, stale=True, since="2026-10-10",
            per_day=[OpenRouterSpendDay("2026-10-22", 0.1, 1)],
            per_model=[OpenRouterSpendModel("m", 0.1, 1, 0, 0)],
        )
        gs = GlobalSnapshot(now=NOW, openrouter=or_snap, openrouter_ok=True)
        block = webdata.global_payload(gs)["openrouter"]
        self.assertEqual(block["balance"], 1.0)
        self.assertTrue(block["stale"])
        self.assertEqual(block["perDay"][0]["date"], "2026-10-22")


if __name__ == "__main__":
    unittest.main()


class OpenRouterOnlyPollTest(unittest.TestCase):
    """The phone monitor runs with quota=False but still has to fetch OpenRouter for /api/state."""

    def test_openrouter_only_starts_a_thread_without_quota(self) -> None:
        import collect
        col = collect.Collector("", quota=False, openrouter_only=True)
        try:
            self.assertIsNotNone(col._co_thread)
            self.assertFalse(col._co_quota_on)
        finally:
            col.close()

    def test_quota_false_alone_starts_no_thread(self) -> None:
        import collect
        col = collect.Collector("", quota=False)
        try:
            self.assertIsNone(col._co_thread)
        finally:
            col.close()
