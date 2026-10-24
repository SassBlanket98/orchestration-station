"""C7 Codex display names: real prompt first, a short fallback while the prompt is unavailable."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import codexread
import collect
from model import DONE, LogState, Snapshot, Stage, Worker
from render import render_plain

SID = "01a0c0de-0000-7000-8000-00000000c1de"


class CodexNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        codexread._crd_cache.clear()
        collect._co_cc_codex_cache.clear()

    def rollout(self, name: str, prompt: str | None = None, sid: str = SID) -> str:
        path = self.dir / name
        entries = [{"type": "session_meta", "payload": {"id": sid}}]
        if prompt is not None:
            entries.append({"type": "response_item", "payload": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": prompt}]}})
        path.write_text("".join(json.dumps(item) + "\n" for item in entries), encoding="utf-8")
        return str(path)

    def worker(self, path: str, name: str = SID) -> Worker:
        return Worker(name, DONE, log=LogState(path=path, harness="codex"))

    def test_uuid_named_worker_uses_the_real_prompt_name(self) -> None:
        worker = self.worker(self.rollout("prompt.jsonl", "Name: prompt task\nDo the work"))
        collect._co_cc_codex_worker(worker)
        self.assertEqual(worker.name, "prompt task")

    def test_injected_header_only_uses_the_short_id_fallback(self) -> None:
        path = self.rollout("header.jsonl", "<environment_context>\nnot a prompt")
        worker = self.worker(path, "<environment_context>")
        collect._co_cc_codex_worker(worker)
        self.assertEqual(worker.name, f"codex {SID[:8]}")

    def test_good_session_name_is_kept(self) -> None:
        path = self.rollout("named.jsonl", "Name: rollout prompt")
        stage = Stage(path="/x", label="project", session_id=SID, session_name="thread title")
        collect._co_cc_codex_stage(stage, path)
        self.assertEqual(stage.session_name, "thread title")

    def test_fallback_upgrades_when_the_prompt_arrives(self) -> None:
        path = Path(self.rollout("growing.jsonl"))
        first = codexread.read_codex_session(str(path))
        self.assertEqual(first.name, f"codex {SID[:8]}")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                                  "content": [{"type": "input_text", "text": "Name: arrived prompt"}]}}) + "\n")
        second = codexread.read_codex_session(str(path))
        self.assertEqual(second.name, "arrived prompt")

    def test_synthetic_fleet_plain_rows_never_show_full_uuid_names(self) -> None:
        fleet = [
            self.worker(self.rollout("one.jsonl", "Name: first task")),
            self.worker(self.rollout("two.jsonl")),
        ]
        collect._co_metrics_workers(fleet, 1_800_000_000.0)
        rendered = render_plain(Snapshot(title="fleet", now=1_800_000_000.0, workers=fleet))
        self.assertNotRegex(rendered, re.compile(SID, re.IGNORECASE))
        self.assertIn("first task", rendered)
        self.assertIn(f"codex {SID[:8]}", rendered)


if __name__ == "__main__":
    unittest.main()
