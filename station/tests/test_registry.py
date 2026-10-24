"""Registry behavior: invalid evidence cannot invent ownership or read other artifacts."""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path


class RegistryContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("registry"), "read-only execution registry is missing")
        return importlib.import_module("registry")

    def record(self, **changes):
        value = {"schema": 1, "execution_id": "a" * 64, "harness": "codex", "account": "personal",
                 "native_id": "session-a", "role": "worker", "goal": "read the assigned module", "started": 10.0}
        value.update(changes)
        folder = self.root / "records"
        folder.mkdir(exist_ok=True)
        (folder / (value["execution_id"] + ".json")).write_text(json.dumps(value))

    def test_missing_registry_is_read_only(self):
        api = self.api()
        missing = self.root / "missing"
        self.assertEqual(api.read_registry(str(missing)).records, [])
        self.assertFalse(missing.exists())

    def test_absent_usage_stays_unknown(self):
        self.record()
        record = self.api().read_registry(str(self.root)).records[0]
        self.assertEqual(record.goal, "read the assigned module")
        self.assertIsNone(record.context_tokens)
        self.assertIsNone(record.cost)

    def test_invalid_record_is_diagnostic_not_a_crash(self):
        for bad in ({"schema": 2}, {"pid": True}, {"started": float("nan")}, {"role": "service"},
                    {"activity": None}, {"registered": None}, {"revision": None}):
            with self.subTest(bad=bad):
                self.record(**bad)
                snap = self.api().read_registry(str(self.root))
                self.assertEqual(snap.records, [])
                self.assertTrue(snap.diagnostics)

    def test_account_namespaces_prevent_native_id_collision(self):
        api = self.api()
        self.assertNotEqual(api.execution_key("claude", "personal", "same"),
                            api.execution_key("claude", "work", "same"))

    def test_complete_brief_is_read_without_line_cutoff(self):
        folder = self.root / "artifacts" / ("a" * 64)
        folder.mkdir(parents=True)
        (folder / "brief.txt").write_text("\n".join(f"line {i}" for i in range(4000)))
        lines = self.api().read_execution_artifact(str(self.root), "a" * 64, "brief")
        self.assertEqual(len(lines), 4000)
        self.assertEqual(lines[-1], "line 3999")

    def test_symlinked_artifact_cannot_escape_registry(self):
        folder = self.root / "artifacts" / ("a" * 64)
        folder.mkdir(parents=True)
        outside = self.root / "unrelated.txt"
        outside.write_text("unrelated content")
        os.symlink(outside, folder / "brief.txt")
        self.assertEqual(self.api().read_execution_artifact(str(self.root), "a" * 64, "brief"), [])

    def test_cross_execution_artifact_reference_is_rejected(self):
        self.record(brief_ref="artifacts/" + "b" * 64 + "/brief.txt")
        snap = self.api().read_registry(str(self.root))
        self.assertEqual(snap.records, [])
        self.assertTrue(snap.diagnostics)

    def test_recursive_json_is_diagnostic(self):
        self.record()
        (self.root / "records" / ("a" * 64 + ".json")).write_text("[" * 2000 + "]" * 2000)
        snap = self.api().read_registry(str(self.root))
        self.assertEqual(snap.records, [])
        self.assertTrue(snap.diagnostics)


if __name__ == "__main__":
    unittest.main()
