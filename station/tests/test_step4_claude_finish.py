"""Claude subagent endings: a completed turn is distinct from an interrupted transcript."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import collect
import native
from model import DONE, NOREPORT

NOW = 1_792_784_000.0


class ClaudeSubagentFinish(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".claude/projects/project/parent/subagents/agent-child.jsonl"
        self.path.parent.mkdir(parents=True)

    def write(self, events: list[dict], age: float = 600) -> None:
        self.path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        os.utime(self.path, (NOW - age, NOW - age))

    def read(self, now: float = NOW):
        items = native.read_native_executions("claude", self.temp.name, now)
        self.assertEqual(len(items), 1)
        return items[0]

    @staticmethod
    def user(content: list[dict] | str) -> dict:
        return {"type": "user", "sessionId": "parent", "agentId": "child", "cwd": "/tmp/project",
                "timestamp": "2026-10-23T15:00:00Z", "message": {"role": "user", "content": content}}

    @staticmethod
    def assistant(content: list[dict], stop: str | None) -> dict:
        return {"type": "assistant", "sessionId": "parent", "agentId": "child",
                "timestamp": "2026-10-23T15:00:10Z",
                "message": {"role": "assistant", "content": content, "stop_reason": stop}}

    def test_idle_final_end_turn_is_done_even_with_trailing_attachment(self) -> None:
        self.write([self.user("Goal: inspect files"),
                    self.assistant([{"type": "tool_use", "id": "tool-1", "name": "Read"}], "tool_use"),
                    self.user([{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"}]),
                    self.assistant([{"type": "text", "text": "Finished."}], "end_turn"),
                    {"type": "attachment", "sessionId": "parent", "agentId": "child"}])
        item = self.read()
        self.assertEqual(item.record.outcome, "success")
        self.assertEqual(item.record.state, "finished")
        self.assertEqual(collect._co_execution_worker(item, NOW, False).state, DONE)

    def test_fresh_final_turn_becomes_done_after_file_is_idle(self) -> None:
        self.write([self.user("Goal: inspect files"),
                    self.assistant([{"type": "text", "text": "Finished."}], "end_turn")], age=10)
        self.assertEqual(self.read().record.outcome, "unknown")
        self.assertEqual(self.read(NOW + 120).record.outcome, "success")

    def test_unanswered_tool_use_stays_without_report(self) -> None:
        self.write([self.user("Goal: inspect files"),
                    self.assistant([{"type": "text", "text": "Started."}], "end_turn"),
                    self.assistant([{"type": "tool_use", "id": "tool-2", "name": "Read"}], "tool_use")])
        item = self.read()
        self.assertEqual(item.record.outcome, "unknown")
        self.assertEqual(collect._co_execution_worker(item, NOW, False).state, NOREPORT)

    def test_pending_tool_result_does_not_count_as_clean_end(self) -> None:
        self.write([self.user("Goal: inspect files"),
                    self.assistant([{"type": "tool_use", "id": "tool-3", "name": "Read"}], "tool_use"),
                    self.assistant([{"type": "text", "text": "Finished."}], "end_turn")])
        self.assertEqual(self.read().record.outcome, "unknown")

    def test_explicit_error_after_end_turn_stays_failed(self) -> None:
        self.write([self.user("Goal: inspect files"),
                    self.assistant([{"type": "text", "text": "Finished."}], "end_turn"),
                    {"type": "result", "subtype": "error", "is_error": True,
                     "sessionId": "parent", "agentId": "child"}])
        item = self.read()
        self.assertEqual(item.record.outcome, "failed")

    def test_stopped_marker_after_end_turn_is_not_done(self) -> None:
        self.write([self.user("Goal: inspect files"),
                    self.assistant([{"type": "text", "text": "Finished."}], "end_turn"),
                    {"type": "system", "subtype": "stopped", "sessionId": "parent", "agentId": "child"}])
        item = self.read()
        self.assertEqual(item.record.outcome, "cancelled")
        self.assertEqual(collect._co_execution_worker(item, NOW, False).state, NOREPORT)


if __name__ == "__main__":
    unittest.main()
