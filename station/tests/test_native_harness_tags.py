"""Native harness identity, Cursor model parsing and OpenCode helper filtering."""
from __future__ import annotations

import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import collect
import opsgrid
import webdata
from model import ExecutionEvidence, ExecutionRecord, LogState, Snapshot, Stage, Worker
from registry import execution_key

NOW = 1791749000


class NativeHarnessTags(unittest.TestCase):
    def test_legacy_tags_still_fill_stage_harness(self):
        stages = [Stage(path=f"/tmp/{tag}", label=tag, tag=tag) for tag in ("CDX", "CUR", "OCode", "OClaw")]

        collect._co_cc_fill_harness(stages)

        self.assertEqual([stage.harness for stage in stages], ["codex", "cursor", "opencode", "openclaw"])

    def test_bare_four_harnesses_first_poll(self):
        collector = collect.GlobalCollector([], quota=False)
        collector._co_proc_table = dict
        records = []
        cases = (("claude", "personal"), ("claude", "work"),
                 ("codex", "personal"), ("opencode", "personal"), ("cursor", "personal"))
        for harness, account in cases:
            native_id = f"{harness}-{account}"
            record = ExecutionRecord(execution_key(harness, account, native_id), harness=harness,
                                     account=account, native_id=native_id, role="lead",
                                     cwd="/tmp/project", started=NOW, activity=NOW)
            records.append(ExecutionEvidence(record))
        rows = collector._co_execution_stages(records, NOW, True)
        rows = collect._co_attach_executions(rows)
        tags = {(row.lead_kind, row.account): row.tag for row in rows}
        self.assertEqual(tags, {("claude", "personal"): "CC·P", ("claude", "work"): "CC·W",
                                ("codex", "personal"): "Codex", ("opencode", "personal"): "OCode",
                                ("cursor", "personal"): "Cursr"})
        self.assertEqual({row.lead_kind for row in rows}, {"claude", "codex", "opencode", "cursor"})

    def test_claude_work_label(self):
        record = ExecutionRecord(execution_key("claude", "work", "agent"), harness="claude",
                                 account="work", native_id="agent", role="worker", cwd="/tmp/project")
        worker = collect._co_execution_worker(ExecutionEvidence(record), NOW, True)
        self.assertEqual(worker.harness, "claude")
        self.assertEqual(worker.log.harness, "claude")
        self.assertEqual(opsgrid._og_worker_harness(worker), "CC·W")

    def test_unknown_claude_account_has_neutral_worker_and_lead_labels(self):
        worker = Worker(name="claude", state="running", family="claude", harness="claude",
                        log=LogState(harness="claude"))
        self.assertEqual(opsgrid._og_worker_harness(worker), "CC")
        worker.account = "personal"
        self.assertEqual(opsgrid._og_worker_harness(worker), "CC·P")
        worker.account = "work"
        self.assertEqual(opsgrid._og_worker_harness(worker), "CC·W")
        stage = Stage(path="/tmp/project", label="project", lead_kind="claude")
        self.assertEqual(opsgrid._og_stage_harness(stage), "CC")
        stage.account = "personal"
        self.assertEqual(opsgrid._og_stage_harness(stage), "CC·P")
        stage.account = "work"
        self.assertEqual(opsgrid._og_stage_harness(stage), "CC·W")

    def test_native_worker_bins_unknown_but_parsed_log_bins_known(self):
        record = ExecutionRecord(execution_key("claude", "personal", "worker"), harness="claude",
                                 account="personal", native_id="worker", role="worker", cwd="/tmp/project")
        native_worker = collect._co_execution_worker(ExecutionEvidence(record), NOW, True)
        native_row = webdata.global_payload(webdata.GlobalSnapshot(
            now=NOW, stages=[Stage(path="/tmp/project", label="project", snap=Snapshot(workers=[native_worker]))]
        ))["stages"][0]["workers"][0]
        self.assertIsNone(native_row["tool_bins"])

        parsed = Worker(name="parsed", state="running", family="claude", harness="claude",
                        log=LogState(path="/tmp/parsed.jsonl", harness="claude",
                                     tool_pulses=[(NOW, 2)]))
        parsed_row = webdata.global_payload(webdata.GlobalSnapshot(
            now=NOW, stages=[Stage(path="/tmp/project", label="project", snap=Snapshot(workers=[parsed]))]
        ))["stages"][0]["workers"][0]
        self.assertEqual(parsed_row["tool_bins"][-1], 2)

    def test_cursor_verbose_model(self):
        with tempfile.TemporaryDirectory() as home:
            transcript = Path(home) / ".cursor/projects/project/agent-transcripts/chat/chat.jsonl"
            transcript.parent.mkdir(parents=True)
            tool_event = {"role": "assistant", "message": {"content": [{
                "type": "tool_use", "input": {"model": "claude-opus-5-5"}}]}}
            chat_event = {"role": "assistant", "message": {"content": [{
                "providerOptions": {"cursor": {"modelName": "Claude Haiku 5.5 300K Medium No Thinking"}},
                "type": "text", "text": "trimmed fixture"}]}}
            transcript.write_text(json.dumps(tool_event) + "\n" + json.dumps(chat_event) + "\n")
            native = importlib.import_module("native")
            items = native.read_native_executions("cursor", home, NOW)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].record.model, "claude-haiku-5-5")
            self.assertEqual(collect.family_of(items[0].record.model), "claude")
            self.assertEqual(native._nv_cursor_model_value("Unlisted Model 42"), "Unlisted Model 42")

    def test_opencode_exact_helper_hidden_parent_preserved(self):
        items = self._opencode_rows([
            ("parent", None, "User task"),
            ("helper", None, "  Shell command request title generation "),
        ])
        self.assertEqual({item.record.native_id for item in items}, {"parent"})

    def test_similar_user_title_preserved(self):
        items = self._opencode_rows([
            ("similar", None, "Shell command request title generation for my task"),
        ])
        self.assertEqual([item.record.native_id for item in items], ["similar"])

    def _opencode_rows(self, rows):
        with tempfile.TemporaryDirectory() as home:
            db = Path(home) / ".local/share/opencode/opencode.db"
            db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT, time_created INT, time_updated INT, time_idle INT, idle_outcome TEXT, time_archived INT, agent TEXT, metadata TEXT)")
                conn.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INT, data TEXT)")
                for sid, parent, title in rows:
                    conn.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (sid, parent, "/tmp/work", title, json.dumps({"id": "m1"}),
                                  1000, 2000, None, None, None, None, None))
            return importlib.import_module("native").read_native_executions("opencode", home, NOW)


class ToolBinsKnownTests(unittest.TestCase):
    """Native rows keep unknown (null) tool bins even though they now carry a harness; read logs keep real bins."""

    NOW = 1_000_000.0

    def bins(self, **worker) -> object:
        return webdata._worker_dict(Worker(name="w", state="done", **worker), self.NOW)["tool_bins"]

    def test_native_row_with_harness_but_no_log_is_unknown(self) -> None:
        self.assertIsNone(self.bins(execution_id="x1", log=LogState(harness="codex")))

    def test_path_only_unparsed_log_is_unknown(self) -> None:
        self.assertIsNone(self.bins(log=LogState(path="/x/w.log")))

    def test_parsed_log_with_harness_and_no_calls_is_zero_bins(self) -> None:
        self.assertEqual(self.bins(log=LogState(path="/x/w.log", harness="claude")), [0] * webdata.TOOL_BINS)


if __name__ == "__main__":
    unittest.main()
