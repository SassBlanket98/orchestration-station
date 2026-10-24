"""Tests for the session fields webdata adds to every stage element: name, model, effort, section, id and cwd."""
from __future__ import annotations

import unittest

import webdata
from model import GlobalSnapshot, Snapshot, Stage

SESSION_KEYS = ("session_name", "lead_model", "effort", "section", "session_id", "cwd")


def linked_stage() -> Stage:
    return Stage(path="/p/.stage", label="p", snap=Snapshot(), live=1, lead_state="live", lead_pid=7,
                 session_id="sess-1", session_name="Board sync", lead_model="sonnet-5.5",
                 effort="high", state="active", cwd="/p")


def unlinked_stage() -> Stage:
    return Stage(path="/q", label="q", snap=Snapshot(), session_name="Old name", lead_model="opus-5.5",
                 effort="low", state="closed", unlinked=True, cwd="/q")


class SessionFieldTests(unittest.TestCase):
    def test_summary_element_has_all_keys_with_types(self) -> None:
        element = webdata.global_payload(GlobalSnapshot(stages=[linked_stage()]))["stages"][0]
        for key in SESSION_KEYS:
            self.assertIn(key, element)
        self.assertIsInstance(element["session_name"], str)
        self.assertIsInstance(element["lead_model"], str)
        self.assertIsInstance(element["effort"], str)
        self.assertIsInstance(element["section"], str)
        self.assertIsInstance(element["session_id"], str)
        self.assertIsInstance(element["cwd"], str)

    def test_linked_live_stage_carries_its_values(self) -> None:
        element = webdata.global_payload(GlobalSnapshot(stages=[linked_stage()]))["stages"][0]
        self.assertEqual(element["session_name"], "Board sync")
        self.assertEqual(element["lead_model"], "Sonnet 5.5")
        self.assertEqual(element["effort"], "high")
        self.assertEqual(element["section"], "active")
        self.assertEqual(element["session_id"], "sess-1")
        self.assertEqual(element["cwd"], "/p")

    def test_unlinked_stage_blanks_name_model_and_effort(self) -> None:
        element = webdata.global_payload(GlobalSnapshot(stages=[unlinked_stage()]))["stages"][0]
        self.assertEqual(element["session_name"], "")
        self.assertEqual(element["lead_model"], "")
        self.assertEqual(element["effort"], "")
        self.assertEqual(element["section"], "closed")
        self.assertEqual(element["cwd"], "/q")

    def test_stage_payload_carries_the_same_fields(self) -> None:
        item = webdata.stage_payload(GlobalSnapshot(stages=[linked_stage()]), "/p/.stage")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["session_name"], "Board sync")
        self.assertEqual(item["section"], "active")
        self.assertEqual(item["session_id"], "sess-1")


if __name__ == "__main__":
    unittest.main()
