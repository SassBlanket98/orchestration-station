"""Harness metrics: OpenCode session_v2, Codex rollouts, Claude transcripts and the collector's fill rules."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from unittest import mock

import collect
import native
from model import LogState, Stage, Worker

NOW = 1_800_000_000.0


def iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=UTC).isoformat()


def write_jsonl(path: str, events: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(event) + "\n" for event in events))


def codex_meta(sid: str, cwd: str = "/tmp/task") -> dict:
    return {"type": "session_meta", "timestamp": iso(NOW - 3600), "payload": {"id": sid, "cwd": cwd, "source": "cli"}}


def codex_usage(total: tuple[int, int, int, int], last: tuple[int, int, int, int] | None = None) -> dict:
    def row(values: tuple[int, int, int, int]) -> dict:
        return {"input_tokens": values[0], "cached_input_tokens": values[1], "cache_write_input_tokens": values[2],
                "output_tokens": values[3], "reasoning_output_tokens": 0}
    return {"type": "token_usage_record", "timestamp": iso(NOW - 60),
            "payload": {"thread_token_usage": row(total), "usage": row(last or total)}}


class CodexMetrics(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.path = os.path.join(self.home, ".codex/sessions/2026/10/24",
                                 "rollout-2026-10-24T10-00-00-thread-1.jsonl")

    def test_rollout_usage_window_tools_and_pulses(self) -> None:
        write_jsonl(self.path, [
            codex_meta("thread-1"),
            {"type": "event_msg", "timestamp": iso(NOW - 120),
             "payload": {"type": "task_started", "model_context_window": 200_000}},
            codex_usage((1000, 200, 0, 50)),
            {"type": "event_msg", "timestamp": iso(NOW - 30),
             "payload": {"type": "token_count", "info": {
                 "total_token_usage": {"input_tokens": 1500, "cached_input_tokens": 300, "cache_write_input_tokens": 0,
                                       "output_tokens": 60, "reasoning_output_tokens": 5},
                 "last_token_usage": {"input_tokens": 750, "cached_input_tokens": 100, "cache_write_input_tokens": 0,
                                      "output_tokens": 10, "reasoning_output_tokens": 0},
                 "model_context_window": 200_000}}},
            {"type": "response_item", "timestamp": iso(NOW - 20), "payload": {"type": "custom_tool_call", "call_id": "a"}},
            {"type": "response_item", "timestamp": iso(NOW - 10), "payload": {"type": "function_call", "call_id": "b"}},
        ])
        self.assertEqual(native.read_codex_sessions(self.home).get("thread-1"), self.path)
        metrics = native.read_codex_metrics(self.path, NOW)
        self.assertEqual((metrics["tokens_in"], metrics["tokens_out"], metrics["tokens_cache"]), (1500, 65, 300))
        self.assertEqual(metrics["tokens_total"], 1565)
        self.assertEqual((metrics["ctx_window"], metrics["ctx_tokens"]), (200_000, 750))
        self.assertEqual(metrics["tools"], 2)
        self.assertEqual([pulse[0] for pulse in metrics["tool_pulses"]], [NOW - 20, NOW - 10])
        self.assertIsNone(metrics["cost"])

    def test_incremental_read_and_rewrite_reset(self) -> None:
        write_jsonl(self.path, [codex_meta("thread-1"), codex_usage((100, 0, 0, 10))])
        first = native.read_codex_metrics(self.path, NOW)
        self.assertEqual((first["tokens_in"], first["tools"]), (100, 0))
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(codex_usage((200, 0, 0, 20))) + "\n")
            fh.write(json.dumps({"type": "response_item", "timestamp": iso(NOW - 5),
                                 "payload": {"type": "custom_tool_call", "call_id": "c"}}) + "\n")
        second = native.read_codex_metrics(self.path, NOW)
        self.assertEqual((second["tokens_in"], second["tokens_out"], second["tools"]), (200, 20, 1))
        write_jsonl(self.path, [codex_meta("thread-1"), codex_usage((50, 0, 0, 5))])
        third = native.read_codex_metrics(self.path, NOW)
        self.assertEqual((third["tokens_in"], third["tools"]), (50, 0))

    def test_native_record_carries_usage_and_scan_window(self) -> None:
        write_jsonl(self.path, [codex_meta("thread-1"), codex_usage((1000, 200, 0, 50)),
                                {"type": "event_msg", "timestamp": iso(NOW - 30),
                                 "payload": {"type": "task_started", "model_context_window": 258_400}}])
        items = native.read_native_executions("codex", self.home, NOW)
        record = next(item.record for item in items if item.record.native_id == "thread-1")
        self.assertEqual((record.tokens_in, record.tokens_out, record.tokens_cache), (1000, 50, 200))
        self.assertEqual(record.context_tokens, 1000)
        self.assertIsNone(record.cost, "codex is a subscription route: no USD")
        self.assertEqual(native.native_metrics("codex", "thread-1")["ctx_window"], 258_400)


class OpenCodeMetrics(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.db = os.path.join(self.home, ".local/share/opencode/opencode.db")
        os.makedirs(os.path.dirname(self.db))
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT,"
                     " time_created INT, time_updated INT, time_idle INT, idle_outcome TEXT, time_archived INT,"
                     " cost REAL, tokens_input INT, tokens_output INT, tokens_reasoning INT, tokens_cache_read INT,"
                     " tokens_cache_write INT)")
        conn.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INT,"
                     " time_created INT, time_updated INT, data TEXT)")
        conn.execute("INSERT INTO session_v2 VALUES ('root',NULL,'/tmp/p','Lead work','{}',1000,2000,NULL,NULL,NULL,"
                     "0.25,1000,200,50,3000,10)")
        conn.execute("INSERT INTO session_v2 VALUES ('kid','root','/tmp/p','Child one','{}',1000,2000,2200,"
                     "'succeeded',NULL,0.05,100,20,5,200,0)")
        tool = [{"type": "tool", "id": "t1", "name": "read", "state": {"status": "completed"}}]
        conn.execute("INSERT INTO session_message VALUES ('m1','root','assistant',1,?,?,?)",
                     (int((NOW - 100) * 1000), int((NOW - 100) * 1000), json.dumps({"content": tool})))
        conn.execute("INSERT INTO session_message VALUES ('m2','root','assistant',2,?,?,?)",
                     (int((NOW - 50) * 1000), int((NOW - 50) * 1000), json.dumps({"content": tool + tool})))
        conn.commit()
        conn.close()

    def test_session_map_totals_cost_and_tools(self) -> None:
        rows = native.read_opencode_sessions(self.home)
        root = rows["root"]
        self.assertEqual((root["tokens_in"], root["tokens_out"], root["tokens_cache"]), (1000, 250, 3010))
        self.assertEqual((root["tokens_total"], root["cost"]), (1250, 0.25))
        self.assertEqual((root["parent_id"], root["title"]), ("", "Lead work"))
        tools = native.read_opencode_lead_tools(self.home, {"root": root}, NOW)
        self.assertEqual(tools["root"][0], 3)
        self.assertEqual([pulse[0] for pulse in tools["root"][1]], [NOW - 100, NOW - 50])

    def test_native_records_carry_opencode_tokens_and_cost(self) -> None:
        items = native.read_native_executions("opencode", self.home, NOW)
        records = {item.record.native_id: item.record for item in items}
        self.assertEqual((records["root"].tokens_in, records["root"].tokens_out,
                          records["root"].tokens_cache, records["root"].cost), (1000, 250, 3010, 0.25))
        self.assertEqual(records["kid"].parent_id, native.execution_key("opencode", "personal", "root"))

    def test_old_schema_without_token_columns_still_reads(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            db = os.path.join(home, ".local/share/opencode/opencode.db")
            os.makedirs(os.path.dirname(db))
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT,"
                         " time_created INT, time_updated INT, time_idle INT, idle_outcome TEXT, time_archived INT)")
            conn.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INT, data TEXT)")
            conn.execute("INSERT INTO session_v2 VALUES ('old',NULL,'/tmp','Old','{}',1,2,NULL,NULL,NULL)")
            conn.commit()
            conn.close()
            rows = native.read_opencode_sessions(home)
            self.assertEqual(rows["old"]["tokens_total"], None)
            native.read_native_executions("opencode", home, NOW)
            self.assertIsNone(native.native_metrics("opencode", "old")["tokens_in"])


class ClaudeMetrics(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.path = os.path.join(self.home, ".claude/projects/-tmp/p1.jsonl")

    def assistant(self, message_id: str, usage: dict, stamp: float, tools: bool = False) -> dict:
        content = [{"type": "text", "text": "working"}]
        if tools:
            content.append({"type": "tool_use", "id": "call-1", "name": "Read", "input": {"file_path": "/tmp/x"}})
        return {"type": "assistant", "timestamp": iso(stamp),
                "message": {"id": message_id, "usage": usage, "content": content, "model": "claude-haiku-5-5"}}

    def test_message_usage_is_deduped_by_id_and_window_comes_from_cost_state(self) -> None:
        write_jsonl(self.path, [
            self.assistant("m1", {"input_tokens": 100, "cache_creation_input_tokens": 10,
                                  "cache_read_input_tokens": 5, "output_tokens": 20}, NOW - 100, tools=True),
            self.assistant("m1", {"input_tokens": 100, "cache_creation_input_tokens": 10,
                                  "cache_read_input_tokens": 5, "output_tokens": 30}, NOW - 99, tools=True),
            self.assistant("m2", {"input_tokens": 50, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 100, "output_tokens": 10}, NOW - 50),
            {"type": "cost-state", "sessionId": "p1",
             "modelUsage": {"claude-haiku-5-5": {"inputTokens": 1, "contextWindow": 200_000, "costUSD": 1.5}}},
        ])
        metrics = native.read_claude_metrics(self.path, NOW)
        self.assertEqual((metrics["tokens_in"], metrics["tokens_out"], metrics["tokens_cache"]), (160, 40, 105))
        self.assertEqual((metrics["ctx_window"], metrics["ctx_tokens"]), (200_000, 150))
        self.assertEqual(metrics["tools"], 1, "the repeated m1 record re-sends the same tool call")
        self.assertEqual([pulse[0] for pulse in metrics["tool_pulses"]], [NOW - 100])
        self.assertIsNone(metrics["cost"], "a Claude subscription transcript reports no billing cost here")

    def test_result_event_overrides_totals(self) -> None:
        write_jsonl(self.path, [
            self.assistant("m1", {"input_tokens": 100, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0, "output_tokens": 20}, NOW - 100),
            {"type": "result", "total_cost_usd": 0.5,
             "usage": {"input_tokens": 700, "cache_creation_input_tokens": 0,
                       "cache_read_input_tokens": 300, "output_tokens": 90},
             "modelUsage": {"m": {"contextWindow": 128_000}}},
        ])
        metrics = native.read_claude_metrics(self.path, NOW)
        self.assertEqual((metrics["tokens_in"], metrics["tokens_out"], metrics["tokens_cache"]), (700, 90, 300))
        self.assertEqual(metrics["ctx_window"], 128_000)
        self.assertEqual(metrics["cost"], 0.5, "the reader reports the transcript's cost; billing decides whether it stays")


class WorkerFill(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        patch = mock.patch.dict(os.environ, {"HOME": self.home})
        patch.start()
        self.addCleanup(patch.stop)

    def test_native_index_fills_missing_and_replaces_a_log_estimate(self) -> None:
        native._nv_metrics[("codex", "tid")] = {
            "tokens_in": 10, "tokens_out": 4, "tokens_cache": 2, "ctx_tokens": 8, "ctx_window": 100, "cost": None}
        self.addCleanup(native._nv_metrics.pop, ("codex", "tid"), None)
        worker = Worker("w", "done", family="codex", native_id="tid")
        collect._co_metrics_workers([worker], NOW)
        self.assertEqual((worker.log.tokens_in, worker.log.tokens_out, worker.log.tokens_cache), (10, 4, 2))
        self.assertEqual((worker.log.ctx_tokens, worker.log.ctx_window), (8, 100))
        self.assertIsNone(worker.log.cost)

        logged = Worker("k", "done", family="codex", native_id="tid", log=LogState(tokens_in=99, cost=1.25))
        collect._co_metrics_workers([logged], NOW)
        self.assertEqual((logged.log.tokens_in, logged.log.cost), (10, 1.25),
                         "the harness's own store beats a stale log value; a missing one leaves the log's cost")

    def test_native_metrics_replace_a_stale_log_value_and_refresh_each_snapshot(self) -> None:
        """5. The harness's own numbers win over a log estimate, and a newer native read updates them."""
        def put(tokens_in: int, cost: float) -> None:
            native._nv_metrics[("codex", "tid")] = {"tokens_in": tokens_in, "tokens_out": None,
                                                    "tokens_cache": None, "ctx_tokens": None,
                                                    "ctx_window": None, "cost": cost}
        put(100, 0.50)
        self.addCleanup(native._nv_metrics.pop, ("codex", "tid"), None)
        worker = Worker("w", "done", family="codex", native_id="tid", log=LogState(tokens_in=10, cost=0.10))

        collect._co_metrics_workers([worker], NOW)
        self.assertEqual((worker.log.tokens_in, worker.log.cost), (100, 0.50))

        put(250, 0.75)
        collect._co_metrics_workers([worker], NOW)
        self.assertEqual((worker.log.tokens_in, worker.log.cost), (250, 0.75), "a new native read updates the row")

        collect._co_metrics_fill(worker.log, {"tokens_in": 7, "cost": 0.01}, "record")
        self.assertEqual((worker.log.tokens_in, worker.log.cost), (250, 0.75),
                         "a lower-priority record cannot overwrite the native values")

    def test_a_fold_takes_a_native_value_over_the_other_half_s_log_estimate(self) -> None:
        """5. A fold keeps the highest-priority source, not whichever half happens to be the base row."""
        base = Worker("w", "done", start=NOW - 900, end=NOW - 600, pid=4242,
                      log=LogState(tokens_in=10, cost=0.10))
        other = Worker("w", "done", start=NOW - 900, end=NOW - 600, pid=4242, native_id="ses_a")
        collect._co_metrics_fill(other.log, {"tokens_in": 100, "cost": 0.50}, "native")
        (folded,) = collect._co_fold_duplicate_workers([base, other])
        self.assertEqual((folded.log.tokens_in, folded.log.cost), (100, 0.50))

    def test_opencode_child_matches_on_unique_parent_and_short_title(self) -> None:
        rows = {"kid": {"parent_id": "ses_root", "title": "Child one", "tokens_total": 5, "cost": 0.1}}
        with mock.patch.object(collect, "read_opencode_sessions", return_value=rows):
            worker = Worker("Child one", "done", family="deepseek", session="ses_root")
            collect._co_metrics_workers([worker], NOW)
        ambiguous = Worker("Child one", "done", family="deepseek", session="ses_root")
        with mock.patch.object(collect, "read_opencode_sessions",
                               return_value={**rows, "kid2": {"parent_id": "ses_root", "title": "Child one"}}):
            collect._co_metrics_workers([ambiguous], NOW)
        self.assertEqual((worker.log.tokens_in, worker.log.tokens_out, worker.log.cost), (None, None, 0.1))
        self.assertEqual(ambiguous.log.cost, None, "two matching rows stay unknown")


class LeadFill(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        patch = mock.patch.dict(os.environ, {"HOME": self.home})
        patch.start()
        self.addCleanup(patch.stop)

    def test_codex_lead_gets_tokens_window_and_tools_but_no_cost(self) -> None:
        path = os.path.join(self.home, ".codex/sessions/day", "rollout-2026-10-24T10-00-00-thread-9.jsonl")
        write_jsonl(path, [
            codex_meta("thread-9"),
            {"type": "event_msg", "timestamp": iso(NOW - 120),
             "payload": {"type": "task_started", "model_context_window": 250_000}},
            codex_usage((600, 0, 0, 40)),
            {"type": "response_item", "timestamp": iso(NOW - 7),
             "payload": {"type": "custom_tool_call", "call_id": "x"}},
        ])
        stage = Stage(path="/r/p/.stage", label="p", lead_kind="codex", session_id="thread-9")
        collect._co_metrics_lead(stage, NOW)
        self.assertEqual(stage.lead_tokens, 640)
        self.assertEqual(stage.lead_ctx_window, 250_000)
        self.assertEqual(stage.lead_ctx, 600)
        self.assertEqual(stage.lead_tools, 1)
        self.assertEqual([pulse[0] for pulse in stage.lead_tool_pulses], [NOW - 7])
        self.assertIsNone(stage.lead_cost)

    def test_claude_lead_reads_its_own_transcript(self) -> None:
        base = os.path.join(self.home, ".claude")
        os.makedirs(os.path.join(base, "sessions"))
        with open(os.path.join(base, "sessions", "4321.json"), "w", encoding="utf-8") as fh:
            json.dump({"sessionId": "sid-1", "cwd": "/r/p"}, fh)
        write_jsonl(os.path.join(base, "projects", "-r-p", "sid-1.jsonl"), [
            ClaudeMetrics.assistant(self, "m1", {"input_tokens": 12, "cache_creation_input_tokens": 0,
                                                 "cache_read_input_tokens": 30, "output_tokens": 4}, NOW - 3, True),
        ])
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": base}):
            stage = Stage(path="/r/p/.stage", label="p", lead_kind="claude", lead_pid=4321)
            collect._co_metrics_lead(stage, NOW)
        self.assertEqual((stage.lead_tokens, stage.lead_tools), (16, 1))
        self.assertEqual(stage.lead_ctx, 42)
        self.assertIsNone(stage.lead_cost, "a Claude subscription leaves cost unknown")
        self.assertIsNone(stage.lead_ctx_window, "an interactive transcript carries no window")

    def test_opencode_lead_gets_real_cost_and_tools(self) -> None:
        row = {"tokens_total": 120, "cost": 0.03, "parent_id": "", "title": "Lead", "updated": NOW}
        tools = {"ses_root": (4, [(NOW - 9, 4)])}
        with mock.patch.object(collect, "read_opencode_sessions", return_value={"ses_root": row}), \
                mock.patch.object(collect, "read_opencode_lead_tools", return_value=tools):
            stage = Stage(path="/r/p/.stage", label="p", lead_kind="opencode", session_id="ses_root",
                          lead_model="deepseek-or")   # an OpenRouter route: metered, so its cost stays
            collect._co_metrics_lead(stage, NOW)
        self.assertEqual((stage.lead_tokens, stage.lead_cost, stage.lead_tools), (120, 0.03, 4))
        self.assertEqual(stage.lead_tool_pulses, [(NOW - 9, 4)])

    def test_known_lead_values_are_not_replaced(self) -> None:
        stage = Stage(path="/r/p/.stage", label="p", lead_kind="codex", session_id="missing",
                      lead_tokens=77, lead_cost=2.0)
        collect._co_metrics_lead(stage, NOW)
        self.assertEqual((stage.lead_tokens, stage.lead_cost), (77, 2.0))

    def test_openclaw_rows_are_left_to_their_own_collector(self) -> None:
        stage = Stage(path="/r/openclaw/.stage", label="openclaw", lead_kind="openclaw", session_id="key", lead_tokens=5)
        with mock.patch.object(collect, "read_opencode_sessions", side_effect=AssertionError("must not read")):
            collect._co_metrics_lead(stage, NOW)
        self.assertEqual(stage.lead_tokens, 5)

    def test_batch_reads_the_opencode_db_once_for_every_lead(self) -> None:
        rows = {"ses_a": {"tokens_total": 1, "cost": 0.1, "updated": NOW},
                "ses_b": {"tokens_total": 2, "cost": 0.2, "updated": NOW}}
        stages = [Stage(path="/r/a/.stage", label="a", lead_kind="opencode", session_id="ses_a"),
                  Stage(path="/r/b/.stage", label="b", lead_kind="opencode", session_id="ses_b")]
        with mock.patch.object(collect, "read_opencode_sessions", return_value=rows) as sessions, \
                mock.patch.object(collect, "read_opencode_lead_tools", return_value={}) as tools:
            collect._co_metrics_leads(stages, NOW)
        self.assertEqual(sessions.call_count, 1)
        self.assertEqual(tools.call_count, 1)
        self.assertEqual([stage.lead_tokens for stage in stages], [1, 2])


if __name__ == "__main__":
    unittest.main()
