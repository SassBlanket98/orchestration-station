"""OpenAI usage: quota openai --json, masthead chip, view [5], /api/state."""
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
    OPENAI_ACCOUNT,
    GlobalSnapshot,
    SpendDay,
    SpendModel,
    SpendSnapshot,
    UIState,
    Worker,
)
from render import (
    _rd_spend_chip,
    _rd_spend_masthead_chip,
    render_global,
)
from usageview import usage_lines

_FIXTURE = {
    "account": "openai",
    "generatedAt": 1_760_000_000_000,
    "currency": "usd",
    "spend": {
        "since": "2026-10-01",
        "monthToDateUsd": 12.34,
        "perDay": [{"date": "2026-10-22", "usd": 0.75, "requests": 4}],
        "perModel": [
            {
                "model": "gpt-5",
                "usd": 0.5,
                "requests": 3,
                "promptTokens": 100,
                "completionTokens": 20,
            },
        ],
    },
    "balance": {"usd": None, "at": None},
    "budget": {"limitUsd": 50.0, "usedPct": 24.7},
    "stale": False,
    "source": "admin-api",
}


class ParseTests(unittest.TestCase):
    def test_with_spend(self) -> None:
        c = collect.Collector("", quota=False)
        snap = c._co_parse_spend(OPENAI_ACCOUNT, _FIXTURE, NOW)
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap.month_to_date_usd, 12.34)
        self.assertIsNone(snap.balance)
        self.assertEqual(len(snap.per_day), 1)
        self.assertEqual(snap.per_model[0].model, "gpt-5")
        self.assertEqual(snap.budget_limit_usd, 50.0)

    def test_partial_rows_skipped(self) -> None:
        body = dict(_FIXTURE)
        body["spend"] = {
            "since": "2026-10-01",
            "monthToDateUsd": 1.0,
            "perDay": [{"date": "2026-10-22", "usd": "bad", "requests": 1}, {"date": "2026-10-23", "usd": 0.5, "requests": 2}],
            "perModel": [],
        }
        c = collect.Collector("", quota=False)
        snap = c._co_parse_spend(OPENAI_ACCOUNT, body, NOW)
        assert snap is not None
        self.assertEqual(len(snap.per_day), 1)
        self.assertEqual(snap.per_day[0].usd, 0.5)

    def test_malformed_top_level_returns_none(self) -> None:
        c = collect.Collector("", quota=False)
        self.assertIsNone(c._co_parse_spend(OPENAI_ACCOUNT, [], NOW))

    def test_stale_flag(self) -> None:
        body = json.loads(json.dumps(_FIXTURE))
        body["stale"] = True
        c = collect.Collector("", quota=False)
        snap = c._co_parse_spend(OPENAI_ACCOUNT, body, NOW)
        assert snap is not None
        self.assertTrue(snap.stale)

    def test_command_failure_leaves_block_absent(self) -> None:
        with mock.patch.dict(os.environ, {"STATUS_OPENAI_CMD": "/bin/false"}):
            c = collect.Collector("", quota=True)
            deadline = time.time() + 3
            while time.time() < deadline and not c._co_spend_ok(OPENAI_ACCOUNT):
                time.sleep(0.05)
            self.assertFalse(c._co_spend_ok(OPENAI_ACCOUNT))
            self.assertIsNone(c._co_spend_snapshot(OPENAI_ACCOUNT))
            c.close()


class ChipTests(unittest.TestCase):
    def test_chip_mtd_no_out(self) -> None:
        snap = SpendSnapshot(
            provider=OPENAI_ACCOUNT, month_to_date_usd=12.34, since="2026-10-01",
            per_day=[], per_model=[],
        )
        text, alert, _stale = _rd_spend_chip(snap)
        self.assertEqual(text, "openai $12.34 mtd")
        self.assertNotIn("out", text)
        self.assertFalse(alert)
        self.assertTrue(_rd_spend_masthead_chip(snap))

    def test_masthead_shows_openai(self) -> None:
        ai_snap = SpendSnapshot(
            provider=OPENAI_ACCOUNT, month_to_date_usd=12.34, since="2026-10-01",
            per_day=[], per_model=[],
        )
        gs = GlobalSnapshot(
            now=NOW,
            stages=[station("/a", "alpha", [Worker("w", DONE)], last=NOW)],
            quota_ok=True,
            spend={OPENAI_ACCOUNT: ai_snap},
            spend_ok={OPENAI_ACCOUNT: True},
        )
        frame = "\n".join(plain(render_global(gs, UIState(), 120, 30, NOW)))
        self.assertIn("openai $12.34 mtd", frame)

    def test_masthead_shows_spend_without_quota(self) -> None:
        ai_snap = SpendSnapshot(provider=OPENAI_ACCOUNT, month_to_date_usd=12.34, since="2026-10-01",
                                per_day=[], per_model=[])
        gs = GlobalSnapshot(now=NOW, stages=[station("/a", "alpha", [Worker("w", DONE)], last=NOW)],
                            quota_ok=False, spend={OPENAI_ACCOUNT: ai_snap}, spend_ok={OPENAI_ACCOUNT: True})
        frame = "\n".join(plain(render_global(gs, UIState(), 120, 30, NOW)))
        self.assertIn("openai $12.34 mtd", frame)
        self.assertNotIn("quota offline", frame)


class ViewTests(unittest.TestCase):
    def test_openai_section(self) -> None:
        snap = SpendSnapshot(
            provider=OPENAI_ACCOUNT, month_to_date_usd=12.34, since="2026-10-01",
            per_day=[SpendDay("2026-10-22", 0.75, 4)],
            per_model=[SpendModel("gpt-5", 0.5, 3, 100, 20)],
        )
        lines = usage_lines([], True, None, False, 0, 100, NOW, spend={OPENAI_ACCOUNT: snap},
                            spend_ok={OPENAI_ACCOUNT: True})[0]
        text = plain(lines)
        self.assertTrue(any("OPENAI" in ln for ln in text))
        self.assertTrue(any("$12.34 mtd" in ln for ln in text))
        self.assertTrue(any("today $0.75" in ln for ln in text))


class WebdataTests(unittest.TestCase):
    def test_payload_openai_in_spend(self) -> None:
        snap = SpendSnapshot(
            provider=OPENAI_ACCOUNT, month_to_date_usd=12.34, stale=False, since="2026-10-01",
            per_day=[SpendDay("2026-10-22", 0.1, 1)],
            per_model=[],
        )
        gs = GlobalSnapshot(now=NOW, spend={OPENAI_ACCOUNT: snap}, spend_ok={OPENAI_ACCOUNT: True})
        payload = webdata.global_payload(gs)
        block = payload["spend"]["openai"]
        self.assertEqual(block["monthToDateUsd"], 12.34)
        self.assertEqual(block["perDay"][0]["date"], "2026-10-22")


class SpendOnlyPollTest(unittest.TestCase):
    """Serve mode polls every spend provider, not only OpenRouter."""

    def test_openrouter_only_starts_a_thread_without_quota(self) -> None:
        col = collect.Collector("", quota=False, openrouter_only=True)
        try:
            self.assertIsNotNone(col._co_thread)
            self.assertFalse(col._co_quota_on)
            self.assertIn(OPENAI_ACCOUNT, col._co_spend_cmds)
        finally:
            col.close()


class ThirdProviderTableTest(unittest.TestCase):
    def test_extra_provider_end_to_end(self) -> None:
        import stat
        import tempfile

        fixture = {"account": "fakeco", "spend": {"since": "2026-10-01", "monthToDateUsd": 3.0, "perDay": [], "perModel": []}}
        table = dict(collect._CO_SPEND_PROVIDERS)
        table["fakeco"] = ("STATUS_FAKECO_CMD", "/bin/false")
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write("#!/bin/sh\n")
            fh.write("printf '%s' '" + json.dumps(fixture).replace("'", "'\\''") + "'\n")
            script = fh.name
        os.chmod(script, stat.S_IRWXU)
        with mock.patch.object(collect, "_CO_SPEND_PROVIDERS", table), mock.patch.dict(
            os.environ, {"STATUS_FAKECO_CMD": script}
        ):
            c = collect.Collector("", quota=False)
            c._co_spend_cmds["fakeco"] = __import__("shlex").split(script)
            snap = c._co_run_spend("fakeco")
        os.unlink(script)
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap.month_to_date_usd, 3.0)


class OpenAiOverflowTest(unittest.TestCase):
    """Review finding (Luna): a JSON integer too big for a float must read as malformed, not raise."""

    def test_huge_integers_are_treated_as_missing(self) -> None:
        collector = collect.Collector("", quota=False)
        huge = 10 ** 10000
        data = {"floorUsd": huge, "credit": {"balanceUsd": huge},
                "spend": {"since": "2026-10-01", "monthToDateUsd": 5.0,
                          "perDay": [{"date": "2026-10-01", "usd": huge}, {"date": "2026-10-02", "usd": 1.5}],
                          "perModel": [{"model": "m", "usd": huge}]},
                "budget": {"limitUsd": huge, "usedPct": huge}}
        snap = collector._co_parse_spend("openai", data, 1000.0)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.month_to_date_usd, 5.0)
        self.assertIsNone(snap.balance)
        self.assertIsNone(snap.floor_usd)
        self.assertEqual([d.date for d in snap.per_day], ["2026-10-02"])
        self.assertEqual(snap.per_model, [])
        self.assertIsNone(snap.budget_limit_usd)
        self.assertIsNone(collector._co_parse_spend("openai", {"credit": {"balanceUsd": huge}}, 1000.0))


if __name__ == "__main__":
    unittest.main()
