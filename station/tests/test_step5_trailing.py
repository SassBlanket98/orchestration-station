"""Claude subagent outcome after trailing non-work transcript records."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import native

NOW = 1_792_784_000.0


class ClaudeTrailingOutcome(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".claude/projects/project/parent/subagents/agent-child.jsonl"
        self.path.parent.mkdir(parents=True)

    def write(self, events: list[dict]) -> None:
        self.path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        os.utime(self.path, (NOW - 600, NOW - 600))

    def outcome(self, events: list[dict]) -> str:
        self.write(events)
        items = native.read_native_executions("claude", self.temp.name, NOW)
        self.assertEqual(len(items), 1)
        return items[0].record.outcome

    @staticmethod
    def turn(stop: str = "end_turn") -> dict:
        return {"type": "assistant", "sessionId": "parent", "agentId": "child",
                "message": {"role": "assistant", "content": [], "stop_reason": stop}}

    @staticmethod
    def trailing(kind: str, **fields) -> dict:
        return {"type": kind, "sessionId": "parent", "agentId": "child", **fields}

    def test_meta_system_attachment_and_hook_records_preserve_clean_end_turn(self) -> None:
        trailing = [
            self.trailing("user", isMeta=True, message={"role": "user", "content": "reminder"}),
            self.trailing("user", isMeta=True, message={"role": "user", "content": "hook output"}),
            self.trailing("system", subtype="hook"),
            self.trailing("attachment"),
        ]
        for event in trailing:
            with self.subTest(type=event["type"], subtype=event.get("subtype")):
                self.assertEqual(self.outcome([self.turn(), event]), "success")

    def test_real_user_message_after_end_turn_resets_outcome(self) -> None:
        user = self.trailing("user", message={"role": "user", "content": "new request"})
        self.assertEqual(self.outcome([self.turn(), user]), "unknown")

    def test_tool_result_after_end_turn_resets_outcome(self) -> None:
        result = self.trailing("user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tool-1", "content": "done"}]})
        self.assertEqual(self.outcome([self.turn(), result]), "unknown")

    def test_explicit_result_success_counts_as_finished(self) -> None:
        result = self.trailing("result", subtype="success")
        self.assertEqual(self.outcome([result]), "success")

    def test_result_success_with_error_flag_counts_as_failed(self) -> None:
        result = self.trailing("result", subtype="success", is_error=True)
        self.assertEqual(self.outcome([self.turn(), result]), "failed")

    def test_result_success_does_not_finish_with_unresolved_tool_use(self) -> None:
        tool = self.trailing("assistant", message={"role": "assistant", "content": [
            {"type": "tool_use", "id": "tool-1"}], "stop_reason": "tool_use"})
        result = self.trailing("result", subtype="success")
        self.assertEqual(self.outcome([tool, result]), "unknown")

    def test_truthy_non_boolean_is_meta_does_not_hide_a_user_turn(self) -> None:
        user = self.trailing("user", isMeta=1, message={"role": "user", "content": "new request"})
        self.assertEqual(self.outcome([self.turn(), user]), "unknown")


if __name__ == "__main__":
    unittest.main()
