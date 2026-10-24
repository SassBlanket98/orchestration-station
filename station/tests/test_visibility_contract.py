"""Visibility contract: one-shot JSON, execution detail, and brief-first defaults."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock

import app
import webdata
from model import RUNNING, Snapshot, Worker


def _worker(**overrides) -> Worker:
    fields = {
        "name": "alpha", "state": RUNNING, "goal": "Ship the goal",
        "execution_id": "exec-1", "account": "acct-a", "native_id": "native-1",
        "parent_id": "", "root_id": "root-1", "linkage": "stage", "outcome": "unknown",
        "brief_text": [f"line {i}" for i in range(400)], "result_text": ["done"],
        "purpose_source": "brief", "output_available": True,
    }
    fields.update(overrides)
    return Worker(**fields)


class ContractDocumentTests(unittest.TestCase):
    def test_single_payload_carries_identity_goal_and_contract_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Snapshot(title="t", now=1.0, workers=[_worker()])
            payload = webdata.single_payload(snap, stage_dir=tmp, label="x")
            doc = webdata.contract_document(payload, diagnostics=[])
        self.assertEqual(doc["schema"], "status.visibility")
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(doc["diagnostics"], [])
        self.assertEqual(doc["v"], 2)                       # old API v2 envelope preserved
        self.assertIn("stages", doc)
        row = doc["stages"][0]["workers"][0]
        self.assertEqual(row["execution_id"], "exec-1")
        self.assertEqual(row["account"], "acct-a")
        self.assertEqual(row["goal"], "Ship the goal")
        self.assertEqual(row["state"], RUNNING)
        self.assertEqual(row["linkage"], "stage")
        self.assertNotIn("brief_text", row)                 # full brief stays on the detail route

    def test_cli_json_is_one_shot_and_never_starts_a_listener(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(app, "run_serve") as serve:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = app.main(["--json", "--stage", tmp])
        serve.assert_not_called()
        self.assertEqual(code, 0)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["schema"], "status.visibility")
        self.assertIsInstance(doc["diagnostics"], list)

    def test_cli_execution_unknown_id_fails_with_json_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = app.main(["--json", "--stage", tmp, "--execution", "missing"])
        self.assertNotEqual(code, 0)
        self.assertIn("error", json.loads(out.getvalue()))


class ExecutionDetailTests(unittest.TestCase):
    def test_native_only_worker_detail_without_stage_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            detail = webdata.execution_detail([_worker()], "exec-1", stage_dir=tmp)
        self.assertEqual(detail["execution_id"], "exec-1")
        self.assertEqual(detail["state"], RUNNING)
        self.assertEqual(detail["goal"], "Ship the goal")
        self.assertEqual(detail["native_id"], "native-1")
        self.assertEqual(detail["result"], ["done"])

    def test_full_brief_is_not_cut_at_old_line_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            detail = webdata.execution_detail([_worker()], "exec-1", stage_dir=tmp)
        self.assertEqual(len(detail["brief"]), 400)
        self.assertEqual(detail["brief"][-1], "line 399")

    def test_unknown_or_traversal_execution_id_is_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(webdata.execution_detail([_worker()], "missing", stage_dir=tmp))
            self.assertIsNone(webdata.execution_detail([_worker()], "../exec-1", stage_dir=tmp))

    def test_metrics_are_not_fabricated_for_native_only_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            detail = webdata.execution_detail([_worker()], "exec-1", stage_dir=tmp)
        self.assertIsNone(detail["cost"])
        self.assertIsNone(detail["tokens"]["in"])
