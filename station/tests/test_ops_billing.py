"""Billing: a USD cost is kept only on a metered route, whatever source reported it."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import collect
import native
from logparse import LogState
from model import DONE, Worker

NOW = 1_800_000_000.0


class BillingRoute(unittest.TestCase):
    """7. Route evidence decides whether a cost is real; the gate runs once, before any total."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        models = os.path.join(tmp.name, "models.json")
        with open(models, "w", encoding="utf-8") as fh:
            json.dump({"models": [
                {"id": "haiku", "cli": "claude", "cliModel": "claude-haiku-5-5", "subscription": "claude"},
                {"id": "haiku-api", "cli": "claude", "cliModel": "claude-haiku-5-5", "subscription": "anthropic-api"},
                {"id": "luna", "cli": "codex", "cliModel": "gpt-6-luna", "subscription": "codex"},
                {"id": "deepseek-or", "cli": "opencode",
                 "cliModel": "openrouter/deepseek/deepseek-v4.1-flash-1017", "subscription": "openrouter"},
                {"id": "deepseek", "cli": "opencode",
                 "cliModel": "opencode-go/deepseek-v4.1-flash", "subscription": "opencode-go"},
                {"id": "deepseek-api", "cli": "opencode",
                 "cliModel": "opencode/deepseek-v4.1-flash", "subscription": "opencode-api"},
                {"id": "ornith", "cli": "local", "cliModel": "ornith-1.5-9b", "subscription": "local"},
            ]}, fh)
        patch = mock.patch.object(collect, "_co_models_file", models)
        patch.start()
        self.addCleanup(patch.stop)
        collect._co_route_cache.clear()
        self.addCleanup(collect._co_route_cache.clear)

    def cost_after(self, worker: Worker) -> float | None:
        collect._co_gate_costs([worker])
        return worker.log.cost

    def test_a_subscription_claude_result_with_total_cost_keeps_no_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sid.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "result", "total_cost_usd": 1.25,
                                     "usage": {"input_tokens": 5, "output_tokens": 5}}) + "\n")
            metrics = native.read_claude_metrics(path, NOW)
        self.assertEqual(metrics["cost"], 1.25, "the reader reports what the transcript says")
        worker = Worker("w", DONE, family="claude", model="haiku high",
                        log=LogState(harness="claude", cost=1.25))
        self.assertIsNone(self.cost_after(worker), "a subscription route is not billed per token")

    def test_a_metered_api_route_keeps_the_same_cost(self) -> None:
        worker = Worker("w", DONE, family="claude", model="haiku-api high",
                        log=LogState(harness="claude", cost=1.25))
        self.assertEqual(self.cost_after(worker), 1.25)

    def test_an_api_config_transcript_is_metered_without_a_picker_id(self) -> None:
        worker = Worker("w", DONE, family="claude", model="claude-haiku-5-5 (high)",
                        log=LogState(harness="claude", cost=1.25,
                                     path="/home/x/.claude-api/projects/-p/sid.jsonl"))
        self.assertEqual(self.cost_after(worker), 1.25)

    def test_the_flat_opencode_go_subscription_keeps_no_cost(self) -> None:
        """`opencode-go/` is the Go plan with usage windows, not Zen credits."""
        for model in ("deepseek high", "opencode-go/deepseek-v4.1-flash"):
            with self.subTest(model=model):
                worker = Worker("w", DONE, family="deepseek", model=model,
                                log=LogState(harness="opencode", cost=0.25))
                self.assertIsNone(self.cost_after(worker))

    def test_zen_credits_and_openrouter_keep_their_cost(self) -> None:
        for model in ("deepseek-api high", "opencode/deepseek-v4.1-flash", "deepseek-or high"):
            with self.subTest(model=model):
                worker = Worker("w", DONE, family="deepseek", model=model,
                                log=LogState(harness="opencode", cost=0.25))
                self.assertEqual(self.cost_after(worker), 0.25)
    def test_a_subscription_transcript_and_a_local_model_keep_no_cost(self) -> None:
        for model, path in (("claude-haiku-5-5 (high)", "/home/x/.claude-team-work/projects/-p/sid.jsonl"),
                            ("ornith high", ""), ("luna high", "")):
            with self.subTest(model=model):
                worker = Worker("w", DONE, model=model, log=LogState(cost=0.75, path=path))
                self.assertIsNone(self.cost_after(worker))


if __name__ == "__main__":
    unittest.main()
