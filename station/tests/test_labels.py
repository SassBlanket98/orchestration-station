"""Tests for labels: the readable model names shown in the UI and the web payloads."""
from __future__ import annotations

import unittest

from labels import display_model
from model import DONE, LogState, Snapshot, Worker
from render import render_plain

NOW = 1_760_050_000.0

TABLE = [
    ("deepseek-v4.1-flash", "DeepSeek V4.1 Flash"),
    ("deepseek-v4.1-flash-free", "DeepSeek V4.1 Flash"),
    ("opencode/deepseek-v4.1-flash-free", "DeepSeek V4.1 Flash"),
    ("claude-haiku-5-5", "Haiku 5.5"),
    ("anthropic/claude-sonnet-5-5", "Sonnet 5.5"),
    ("claude-opus-5-5", "Opus 5.5"),
    ("sonnet-5.5", "Sonnet 5.5"),
    ("opus-5.5", "Opus 5.5"),
    ("haiku-5.5", "Haiku 5.5"),
    ("composer-2.5", "Composer 2.5"),
    ("cursor-composer-2.5", "Composer 2.5"),
    ("gpt-6-sol", "GPT-6 Sol"),
    ("gpt-6.1-sol", "GPT-6.1 Sol"),
    ("gpt-6-luna", "GPT-6 Luna"),
    ("flint-40b-free", "Flint 40B"),
    ("opencode-go/glm-5.3-flash-free", "GLM-5.3 Flash"),
    ("grok-4.6-medium", "Grok 4.6"),
]


class DisplayModelTests(unittest.TestCase):
    def test_known_ids_map_to_readable_names(self) -> None:
        for raw, want in TABLE:
            with self.subTest(raw=raw):
                self.assertEqual(display_model(raw), want)

    def test_gpt_6_sol_is_not_given_a_version_it_does_not_have(self) -> None:
        self.assertEqual(display_model("gpt-6-sol"), "GPT-6 Sol")
        self.assertNotEqual(display_model("gpt-6-sol"), "GPT-6.1 Sol")
        self.assertEqual(display_model("gpt-6.1-sol"), "GPT-6.1 Sol")

    def test_lookup_ignores_case(self) -> None:
        self.assertEqual(display_model("Claude-Haiku-5-5"), "Haiku 5.5")
        self.assertEqual(display_model("OPENCODE/Flint-40B-Free"), "Flint 40B")

    def test_provider_prefix_is_stripped_before_lookup(self) -> None:
        self.assertEqual(display_model("opencode-go/grok-4.6-medium"), "Grok 4.6")

    def test_unknown_id_keeps_its_text_without_the_prefix(self) -> None:
        self.assertEqual(display_model("opencode/Some-New-Model"), "Some-New-Model")
        self.assertEqual(display_model("muse-1"), "muse-1")
        self.assertEqual(display_model("cursor-custom-thing"), "custom-thing")

    def test_empty_id_stays_empty(self) -> None:
        self.assertEqual(display_model(""), "")

    def test_claude_short_form_gets_its_version_from_the_id(self) -> None:
        self.assertEqual(display_model("sonnet-6.2"), "Sonnet 6.2")
        self.assertEqual(display_model("Opus-5.5"), "Opus 5.5")

    def test_display_is_idempotent(self) -> None:
        for raw, _ in TABLE:
            with self.subTest(raw=raw):
                once = display_model(raw)
                self.assertEqual(display_model(once), once)

    def test_worker_model_is_shown_as_its_display_name_in_the_plain_view(self) -> None:
        worker = Worker(name="w1", state=DONE, model="opencode/deepseek-v4.1-flash-free", family="opencode",
                        start=NOW - 600, end=NOW - 60, log=LogState())
        txt = render_plain(Snapshot(now=NOW, workers=[worker]), 120)
        self.assertIn("DeepSeek V4.1 Flash", txt)
        self.assertNotIn("opencode/deepseek-v4.1-flash-free", txt)


if __name__ == "__main__":
    unittest.main()
