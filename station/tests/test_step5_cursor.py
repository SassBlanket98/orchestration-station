"""Cursor transcript and Task lineage fixtures based on the on-disk JSONL shape."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import collect
import native
from model import ExecutionRecord, RegistrySnapshot

NOW = 1_800_000_000.0


class CursorNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def chat(self, chat_id, lines, *, project="home-the user-Projects-project", meta=None):
        path = self.home / ".cursor/projects" / project / "agent-transcripts" / chat_id / f"{chat_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        if meta is not None:
            info = self.home / ".cursor/chats/hash" / chat_id / "meta.json"
            info.parent.mkdir(parents=True, exist_ok=True)
            info.write_text(json.dumps({"schemaVersion": 1, "createdAtMs": 1_799_999_000_000,
                                        "updatedAtMs": 1_799_999_900_000, "cwd": "/home/user/Projects/project",
                                        "title": "ignored", **meta}))
        return path

    @staticmethod
    def user(prompt):
        return {"role": "user", "message": {"content": [{"type": "text", "text":
                "Context supplied by Cursor\n<user_query>\n" + prompt + "\n</user_query>"}]}}

    @staticmethod
    def task(prompt, *, dynamic=True):
        inp = {"description": "do work", "prompt": prompt, "subagent_type": "generalPurpose"}
        if dynamic:
            inp = {"namespace": "cursor", "toolName": "Task", "arguments": inp}
        return {"role": "assistant", "message": {"content": [{"type": "tool_use",
                "name": "CallDynamicTool" if dynamic else "Task", "input": inp}]}}

    def records(self):
        return {e.record.native_id: e for e in native.read_native_executions("cursor", str(self.home), NOW)}

    def test_lead_uses_meta_query_and_turn_status(self):
        self.chat("lead", [self.user("Fix the parser"), {"role": "assistant", "message":
                  {"content": [{"type": "text", "text": "Done"}]}} ,
                  {"type": "turn_ended", "status": "success"}], meta={})
        item = self.records()["lead"]
        self.assertEqual((item.record.role, item.record.cwd), ("lead", "/home/user/Projects/project"))
        self.assertEqual((item.record.started, item.record.activity), (1_799_999_000.0, 1_799_999_900.0))
        self.assertEqual((item.record.state, item.record.outcome), ("idle", "success"))
        self.assertEqual((item.brief, item.record.goal, item.result), ("Fix the parser", "Fix the parser", "Done"))

    def test_exact_task_prompt_links_child_across_project_slugs(self):
        self.chat("parent", [self.user("Lead request"), self.task("  inspect files\r\nthen report  "),
                             {"type": "turn_ended", "status": "success"}],
                  project="home-the user-Projects-project-stage-run", meta={"cwd": "/home/user/Projects/project/.stage/run"})
        self.chat("child", [self.user("inspect files\nthen report"), {"type": "turn_ended", "status": "success"}])
        records = self.records()
        child = records["child"].record
        self.assertEqual((child.role, child.parent_id, child.root_id, child.linkage),
                         ("worker", native.execution_key("cursor", "personal", "parent"),
                          native.execution_key("cursor", "personal", "parent"), "native"))
        self.assertEqual(child.cwd, "/home/user/Projects/project")
        self.assertEqual((child.state, child.outcome), ("finished", "success"))

    def test_ambiguous_and_unmatched_children_stay_visible_without_parent(self):
        for sid in ("one", "two"):
            self.chat(sid, [self.user("lead"), self.task("shared", dynamic=False)], meta={})
        self.chat("ambiguous", [self.user("shared")])
        self.chat("unmatched", [self.user("different")])
        records = self.records()
        for sid in ("ambiguous", "unmatched"):
            rec = records[sid].record
            self.assertEqual((rec.role, rec.parent_id, rec.linkage), ("worker", "", "unknown"))
            self.assertIn("unlinked", rec.title.lower())

    def test_recent_no_end_is_running_old_no_end_is_unknown_finished(self):
        recent = self.chat("recent", [self.user("wait")], meta={"updatedAtMs": int((NOW - 10) * 1000)})
        old = self.chat("old", [self.user("wait")], meta={"updatedAtMs": int((NOW - 1000) * 1000)})
        records = self.records()
        self.assertEqual(records["recent"].record.state, "running")
        self.assertEqual((records["old"].record.state, records["old"].record.outcome), ("idle", "unknown"))
        with recent.open("a") as stream:
            stream.write(json.dumps({"type": "turn_ended", "status": "error"}) + "\n")
        self.assertEqual((self.records()["recent"].record.state, self.records()["recent"].record.outcome),
                         ("idle", "failed"))
        self.assertTrue(old.exists())

    def test_new_turn_after_prior_success_is_not_still_finished(self):
        path = self.chat("followup", [self.user("First"), {"role": "assistant", "message": {
            "content": [{"type": "text", "text": "Old answer"}]}},
            {"type": "turn_ended", "status": "success"}],
                         meta={"updatedAtMs": int((NOW - 10) * 1000)})
        self.assertEqual(self.records()["followup"].record.outcome, "success")
        with path.open("a") as stream:
            stream.write(json.dumps(self.user("Second")) + "\n")
        item = self.records()["followup"]
        record = item.record
        self.assertEqual((record.state, record.outcome), ("running", "unknown"))
        self.assertFalse(record.output_available)
        self.assertEqual(item.result, "")

    def test_collector_scans_cursor_only_after_quick_pass(self):
        collector = collect.GlobalCollector([str(self.home)], ledger=str(self.home / "runs.jsonl"), quota=False)
        self.addCleanup(collector.close)
        with mock.patch.object(collect, "read_native_executions", return_value=[]) as scan, \
                mock.patch.dict(collect._co_native_cache, clear=True):
            collector._co_quick = True
            collector._co_executions(NOW)
            scan.assert_not_called()
            collector._co_quick = False
            collector._co_executions(NOW)
            self.assertIn("cursor", [call.args[0] for call in scan.call_args_list])

    def test_explicit_parent_record_overrides_prompt_match(self):
        self.chat("prompt-parent", [self.user("Lead"), self.task("Do the task")], meta={})
        self.chat("named-parent", [self.user("Other lead")], meta={})
        self.chat("child", [self.user("Do the task")])
        inferred = self.records()["child"].record
        self.assertEqual(inferred.parent_id, native.execution_key("cursor", "personal", "prompt-parent"))
        exact_parent = native.execution_key("cursor", "personal", "named-parent")
        registered = ExecutionRecord(inferred.execution_id, harness="cursor", account="personal",
                                     native_id="child", role="worker", parent_id=exact_parent,
                                     linkage="launch", source="registry")
        merged, _ = collect.reconcile_executions(RegistrySnapshot(records=[registered]), list(self.records().values()), {}, NOW)
        child = next(item.record for item in merged if item.record.native_id == "child")
        self.assertEqual((child.parent_id, child.linkage), (exact_parent, "launch"))
        self.assertEqual(child.root_id, exact_parent)

    def test_non_meta_start_uses_first_parseable_timestamp_and_meta_zero_falls_back(self):
        self.chat("timestamped", [{"role": "user", "timestamp": NOW - 80,
                                    "message": {"content": [{"type": "text", "text": "hello"}]}}])
        item = self.records()["timestamped"]
        self.assertEqual(item.record.started, NOW - 80)
        path = self.chat("meta-fallback", [self.user("hello")], meta={"createdAtMs": 0,
                                                                         "updatedAtMs": int((NOW + 50) * 1000)})
        expected_mtime = path.stat().st_mtime
        record = self.records()["meta-fallback"].record
        self.assertEqual(record.started, expected_mtime)
        self.assertEqual(record.activity, NOW + 50)

    def test_activity_is_at_least_transcript_mtime(self):
        path = self.chat("activity", [self.user("hello")], meta={"updatedAtMs": int((NOW - 10) * 1000)})
        # Metadata is deliberately older than the transcript's filesystem timestamp.
        os.utime(path, (NOW + 20, NOW + 20))
        record = self.records()["activity"].record
        self.assertEqual(record.activity, NOW + 20)

    def test_meta_outside_chat_root_is_ignored(self):
        outside = self.home / "outside.json"
        outside.write_text(json.dumps({"schemaVersion": 1, "cwd": "/wrong", "createdAtMs": 1}))
        link = self.home / ".cursor/chats/hash/escaped/meta.json"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        self.chat("escaped", [self.user("hi")])
        self.assertEqual(self.records()["escaped"].record.role, "worker")


if __name__ == "__main__":
    unittest.main()
