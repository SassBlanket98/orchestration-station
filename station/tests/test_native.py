"""Native instructions and exact parent links survive large/rotated transcripts."""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

NOW = 1791749000


class NativeEvidence(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("native"), "native execution adapters are missing")
        return importlib.import_module("native")

    def codex_file(self, home, name, events, folder="2026/10/11"):
        path = Path(home) / ".codex/sessions" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(x) for x in events) + "\n")
        return path

    def meta(self, sid, parent=None, originator=None, cwd="/tmp/task"):
        payload = {"id": sid, "cwd": cwd, "source": "cli"}
        if parent:
            payload["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
        if originator:
            payload["originator"] = originator
        return {"type": "session_meta", "payload": payload}

    def user(self, text):
        return {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}

    def done(self, text="verified result"):
        return {"type": "event_msg", "timestamp": "2026-10-11T20:00:00Z", "payload": {"type": "task_complete", "last_agent_message": text}}

    def by_id(self, items):
        return {x.record.native_id: x for x in items}

    def test_codex_initial_identity_and_brief_survive_large_tail(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".codex/sessions/2026/10/11"
            folder.mkdir(parents=True)
            events = [
                {"type": "session_meta", "payload": {"id": "child", "cwd": "/tmp/task", "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}}}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Goal: inspect adapter\nFull assigned instructions"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "x" * 70000}]}},
                {"type": "event_msg", "timestamp": "2026-10-11T20:00:00Z", "payload": {"type": "task_complete", "last_agent_message": "verified result"}},
            ]
            (folder / "rollout-child.jsonl").write_text("\n".join(json.dumps(x) for x in events))
            records = self.api().read_native_executions("codex", home, NOW)
            self.assertEqual(len(records), 1)
            item = records[0]
            self.assertEqual(item.record.native_id, "child")
            self.assertEqual(item.record.role, "worker")
            self.assertEqual(item.brief, "Goal: inspect adapter\nFull assigned instructions")
            self.assertEqual(item.record.goal, "inspect adapter")
            self.assertEqual(item.record.parent_id, self.api().execution_key("codex", "personal", "parent"))
            self.assertEqual(item.record.outcome, "success")

    def test_codex_completion_without_timestamp_is_still_read(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".codex/sessions/2026/10/11"
            folder.mkdir(parents=True)
            events = [self.meta("solo"), self.user("Goal: x"),
                      {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "ok"}}]
            (folder / "rollout-solo.jsonl").write_text("\n".join(json.dumps(x) for x in events))
            records = self.api().read_native_executions("codex", home, NOW)
            self.assertEqual([x.record.outcome for x in records], ["success"])

    def test_claude_account_and_subagent_ancestry(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".claude-team-work/projects/project/parent/subagents"
            folder.mkdir(parents=True)
            events = [{"type": "user", "sessionId": "parent", "agentId": "agent-child", "cwd": "/tmp/task",
                       "timestamp": "2026-10-11T20:00:00Z", "message": {"role": "user", "content": "inspect assigned file"}},
                      {"type": "assistant", "message": {"content": [{"type": "text", "text": "finished"}], "model": "test-model"}}]
            (folder / "agent-agent-child.jsonl").write_text("\n".join(json.dumps(x) for x in events))
            with mock.patch.dict(os.environ, {"STATION_CLAUDE_CONFIG_DIRS": ".claude-team-work"}):
                items = self.api().read_native_executions("claude", home, NOW)
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item.record.account, "work")
            self.assertEqual(item.record.parent_id, self.api().execution_key("claude", "work", "parent"))
            self.assertEqual(item.brief, "inspect assigned file")
            self.assertEqual(item.result, "finished")
            self.assertEqual(item.record.outcome, "unknown", "assistant text alone does not prove task completion")

    def test_claude_cwd_is_backfilled_from_first_later_line(self):
        # SDK/RC transcripts can open with a line that has no cwd; the cwd comes from a later line.
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".claude/projects/p"
            folder.mkdir(parents=True)
            events = [{"type": "mode", "sessionId": "sdk-run"},
                      {"type": "user", "sessionId": "sdk-run", "cwd": "/home/user", "entrypoint": "sdk-cli",
                       "message": {"role": "user", "content": "hi"}}]
            (folder / "sdk-run.jsonl").write_text("\n".join(json.dumps(x) for x in events) + "\n")
            items = self.api().read_native_executions("claude", home, NOW)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].record.cwd, "/home/user")

    def test_same_cwd_does_not_merge_native_sessions(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".codex/sessions/day"
            folder.mkdir(parents=True)
            for identity in ("one", "two"):
                (folder / (identity + ".jsonl")).write_text(json.dumps({"type": "session_meta", "payload": {"id": identity, "cwd": "/tmp/shared", "source": "cli"}}))
            records = self.api().read_native_executions("codex", home, NOW)
            self.assertEqual({x.record.native_id for x in records}, {"one", "two"})

    def test_same_native_id_in_two_accounts_gets_distinct_identity(self):
        with tempfile.TemporaryDirectory() as home:
            for root in (".claude/projects/p", ".claude-team-work/projects/p"):
                folder = Path(home) / root
                folder.mkdir(parents=True)
                (folder / "same.jsonl").write_text(json.dumps({"type": "user", "sessionId": "same", "cwd": "/tmp", "message": {"role": "user", "content": "hi"}}) + "\n")
            with mock.patch.dict(os.environ, {"STATION_CLAUDE_CONFIG_DIRS": ".claude:.claude-team-work"}):
                items = self.api().read_native_executions("claude", home, NOW)
            self.assertEqual(len({x.record.execution_id for x in items}), 2)
            self.assertEqual({x.record.account for x in items}, {"personal", "work"})

    def test_blank_and_malformed_complete_lines_do_not_stall_parsing(self):
        # Regression: a malformed complete line used to stop parsing, hiding the later completion event.
        with tempfile.TemporaryDirectory() as home:
            path = self.codex_file(home, "rollout-a.jsonl", [self.meta("a"), self.user("Goal: finish\nbody")])
            with path.open("a") as f:
                f.write("{not json}\n\n   \n")
                f.write(json.dumps(self.done("ok")) + "\n")
            item = self.by_id(self.api().read_native_executions("codex", home, NOW))["a"]
            self.assertEqual(item.record.outcome, "success")
            self.assertEqual(item.brief, "Goal: finish\nbody")

    def test_incomplete_append_is_retried_not_dropped(self):
        with tempfile.TemporaryDirectory() as home:
            path = self.codex_file(home, "rollout-b.jsonl", [self.meta("b"), self.user("Goal: wait")])
            tail = json.dumps(self.done("late"))
            with path.open("a") as f:
                f.write(tail[:20])
            api = self.api()
            first = self.by_id(api.read_native_executions("codex", home, NOW))["b"]
            self.assertEqual(first.record.outcome, "unknown")
            with path.open("a") as f:
                f.write(tail[20:] + "\n")
            second = self.by_id(api.read_native_executions("codex", home, NOW))["b"]
            self.assertEqual(second.record.outcome, "success")
            self.assertEqual(second.result, "late")

    def test_same_size_rewrite_is_reparsed_by_mtime(self):
        # Regression: an identical-size rewrite on the same inode kept the stale brief.
        with tempfile.TemporaryDirectory() as home:
            path = self.codex_file(home, "rollout-c.jsonl", [self.meta("c"), self.user("Goal: alpha task")])
            api = self.api()
            self.assertEqual(self.by_id(api.read_native_executions("codex", home, NOW))["c"].brief, "Goal: alpha task")
            stamp = os.stat(path).st_mtime_ns
            path.write_text("\n".join(json.dumps(x) for x in [self.meta("c"), self.user("Goal: bravo task")]) + "\n")
            self.assertEqual(len(path.read_bytes()), len(("\n".join(json.dumps(x) for x in [self.meta("c"), self.user("Goal: alpha task")]) + "\n").encode()))
            os.utime(path, ns=(stamp + 5_000_000_000, stamp + 5_000_000_000))
            self.assertEqual(self.by_id(api.read_native_executions("codex", home, NOW))["c"].brief, "Goal: bravo task")

    def test_truncation_and_rotation_reset_cached_state(self):
        with tempfile.TemporaryDirectory() as home:
            path = self.codex_file(home, "rollout-d.jsonl", [self.meta("d"), self.user("Goal: old"), self.done("old result")])
            api = self.api()
            self.assertEqual(self.by_id(api.read_native_executions("codex", home, NOW))["d"].brief, "Goal: old")
            path.write_text(json.dumps(self.meta("d")) + "\n" + json.dumps(self.user("Goal: fresh")) + "\n")
            self.assertEqual(self.by_id(api.read_native_executions("codex", home, NOW))["d"].brief, "Goal: fresh")
            self.assertEqual(self.by_id(api.read_native_executions("codex", home, NOW))["d"].record.outcome, "unknown")
            replacement = path.with_name("rotated.tmp")
            replacement.write_text(json.dumps(self.meta("d")) + "\n" + json.dumps(self.user("Goal: rotated")) + "\n")
            os.replace(replacement, path)
            self.assertEqual(self.by_id(api.read_native_executions("codex", home, NOW))["d"].brief, "Goal: rotated")

    def test_brief_survives_growth_beyond_64kib_tail(self):
        with tempfile.TemporaryDirectory() as home:
            path = self.codex_file(home, "rollout-e.jsonl", [self.meta("e"), self.user("Goal: keep original")])
            api = self.api()
            api.read_native_executions("codex", home, NOW)
            with path.open("a") as f:
                for _ in range(40):
                    f.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "y" * 4000}]}}) + "\n")
            item = self.by_id(api.read_native_executions("codex", home, NOW))["e"]
            self.assertEqual(item.brief, "Goal: keep original")
            self.assertEqual(item.record.goal, "keep original")

    def test_t3_originator_is_independent_of_codex(self):
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-t3.jsonl", [self.meta("t", originator="T3 Code desktop"), self.user("Goal: t3 work")])
            api = self.api()
            self.assertEqual(api.read_native_executions("codex", home, NOW), [])
            t3 = api.read_native_executions("t3", home, NOW)
            self.assertEqual([x.record.harness for x in t3], ["t3"])
            self.assertEqual(t3[0].record.execution_id, api.execution_key("t3", "personal", "t"))

    def test_root_link_follows_exact_parent_chain_only(self):
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-lead.jsonl", [self.meta("lead")])
            self.codex_file(home, "rollout-mid.jsonl", [self.meta("mid", parent="lead")])
            self.codex_file(home, "rollout-leaf.jsonl", [self.meta("leaf", parent="mid")])
            self.codex_file(home, "rollout-orphan.jsonl", [self.meta("orphan", parent="missing")])
            api = self.api()
            items = self.by_id(api.read_native_executions("codex", home, NOW))
            lead = api.execution_key("codex", "personal", "lead")
            self.assertEqual(items["leaf"].record.parent_id, api.execution_key("codex", "personal", "mid"))
            self.assertEqual(items["leaf"].record.root_id, lead)
            self.assertEqual(items["mid"].record.root_id, lead)
            self.assertEqual(items["lead"].record.root_id, lead)
            self.assertEqual(items["orphan"].record.root_id, "", "unresolved ancestry stays unknown, not guessed")

    def test_oversized_event_is_diagnosed_not_truncated(self):
        with tempfile.TemporaryDirectory() as home:
            big = {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "z" * (17 * 1024 * 1024)}]}}
            path = self.codex_file(home, "rollout-big.jsonl", [self.meta("big"), self.user("Goal: big input")])
            with path.open("a") as f:
                f.write(json.dumps(big) + "\n")
                f.write(json.dumps(self.done("after")) + "\n")
            item = self.by_id(self.api().read_native_executions("codex", home, NOW))["big"]
            self.assertNotIn("zzz", item.result, "partial oversized text must not be presented as the result")
            self.assertEqual(item.result, "after")
            self.assertIn("16 MiB", item.record.goal)
            self.assertEqual(item.record.outcome, "success", "parsing continues after the oversized event")

    def test_opencode_user_text_and_idle_outcomes(self):
        with tempfile.TemporaryDirectory() as home:
            db = Path(home) / ".local/share/opencode/opencode.db"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT, time_created INT, time_updated INT, time_idle INT, idle_outcome TEXT, time_archived INT)")
            conn.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INT, data TEXT)")
            rows = [("lead", None, "succeeded"), ("ok", "lead", "succeeded"), ("cut", "lead", "interrupted")]
            for sid, parent, outcome in rows:
                conn.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                             (sid, parent, "/tmp/work", "t", json.dumps({"id": "m1"}), 1000, 2000, 2500, outcome if parent else None))
            conn.execute("INSERT INTO session_message VALUES ('u1','ok','user',1,?)", (json.dumps({"text": "Goal: opencode task", "time": 1}),))
            conn.execute("INSERT INTO session_message VALUES ('a1','ok','assistant',2,?)", (json.dumps({"content": [{"type": "reasoning", "text": "hidden"}, {"type": "text", "text": "answer"}]}),))
            conn.execute("INSERT INTO session_message VALUES ('u2','cut','user',1,?)", (json.dumps({"text": "Goal: cut short", "time": 1}),))
            conn.commit()
            conn.close()
            api = self.api()
            items = self.by_id(api.read_native_executions("opencode", home, NOW))
            self.assertEqual(items["ok"].brief, "Goal: opencode task")
            self.assertEqual(items["ok"].result, "answer")
            self.assertEqual(items["ok"].record.goal, "opencode task")
            self.assertEqual(items["ok"].record.outcome, "success")
            self.assertEqual(items["ok"].record.parent_id, api.execution_key("opencode", "personal", "lead"))
            self.assertEqual(items["cut"].record.outcome, "cancelled")
            self.assertEqual(items["lead"].record.state, "idle")

    def test_opencode_metrics_see_a_write_that_only_reached_the_wal(self):
        """3. The metrics cache watches the -wal file too, so a WAL-only update is seen at once."""
        with tempfile.TemporaryDirectory() as home:
            db = Path(home) / ".local/share/opencode/opencode.db"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT,"
                         " time_archived INT, tokens_input INT, cost REAL)")
            conn.execute("INSERT INTO session_v2 VALUES ('ses_one', NULL, '/tmp/p', 'Lead', NULL, 100, 0.1)")
            conn.commit()
            api = self.api()
            api._metric_opencode_db.clear()
            self.addCleanup(api._metric_opencode_db.clear)
            self.assertEqual((api.read_opencode_sessions(home)["ses_one"]["tokens_in"],
                              api.read_opencode_sessions(home)["ses_one"]["cost"]), (100, 0.1))
            self.assertIsNone(api.read_opencode_sessions(home)["ses_one"]["ctx_window"])
            conn.execute("UPDATE session_v2 SET tokens_input = 500, cost = 0.5 WHERE id = 'ses_one'")
            conn.commit()   # the write stays in the -wal file until a checkpoint
            rows = api.read_opencode_sessions(home)
            conn.close()
            self.assertEqual((rows["ses_one"]["tokens_in"], rows["ses_one"]["cost"]), (500, 0.5))

    def test_opencode_counter_zeros_are_measured_values_not_unknown(self):
        """8. A measured 0 output/cache counter stays 0; only an absent column or NULL is unknown."""
        with tempfile.TemporaryDirectory() as home:
            db = Path(home) / ".local/share/opencode/opencode.db"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT,"
                         " model TEXT, time_created INT, time_updated INT, time_idle INT, idle_outcome TEXT,"
                         " time_archived INT, tokens_input INT, tokens_output INT, tokens_reasoning INT,"
                         " tokens_cache_read INT, tokens_cache_write INT, cost REAL)")
            conn.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INT, data TEXT)")
            conn.execute("INSERT INTO session_v2 VALUES ('ses_zero', NULL, '/tmp/p', 'Lead',"
                         " '{}', 1000, 2000, 2000, NULL, NULL, 7, 0, 0, 0, NULL, 0.0)")
            conn.execute("INSERT INTO session_v2 VALUES ('ses_null', NULL, '/tmp/q', 'Lead',"
                         " '{}', 1000, 2000, 2000, NULL, NULL, 7, NULL, NULL, NULL, NULL, 0.0)")
            conn.commit()
            api = self.api()
            api._metric_opencode_db.clear()
            self.addCleanup(api._metric_opencode_db.clear)
            rows = api.read_opencode_sessions(home)
            self.assertEqual((rows["ses_zero"]["tokens_out"], rows["ses_zero"]["tokens_cache"]), (0, 0))
            self.assertIsNone(rows["ses_null"]["tokens_out"], "SQL NULL is still unknown")
            item = self.by_id(api.read_native_executions("opencode", home, NOW))["ses_zero"]
            self.assertEqual((item.record.tokens_out, item.record.tokens_cache), (0, 0))
            self.assertEqual(item.record.cost, 0.0, "a measured zero cost stays 0")
            conn.close()

    def test_openclaw_openclaw_session_nodes_link_parent_and_status(self):
        with tempfile.TemporaryDirectory() as home:
            db = Path(home) / ".openclaw/agents/openclaw/agent/openclaw-agent.sqlite"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE session_nodes (session_key TEXT, entry_json TEXT, status TEXT)")
            child = {"parentSessionKey": "agent:openclaw:root", "sessionId": "s-child", "spawnedWorkspaceDir": "/tmp/ws",
                     "status": "done", "startedAt": 1791748000000, "endedAt": 1791748100000, "model": "gpt-test",
                     "inputTokens": 10, "outputTokens": 5, "estimatedCostUsd": 0.25}
            conn.execute("INSERT INTO session_nodes VALUES (?,?,?)", ("agent:openclaw:child", json.dumps(child), "done"))
            conn.execute("INSERT INTO session_nodes VALUES (?,?,?)", ("agent:openclaw:root", json.dumps({"sessionId": "s-root", "status": "running", "startedAt": 1791747000000}), "running"))
            conn.commit()
            conn.close()
            api = self.api()
            items = self.by_id(api.read_native_executions("openclaw", home, NOW))
            c = items["agent:openclaw:child"].record
            self.assertEqual(c.harness, "openclaw")
            self.assertEqual(c.account, "openclaw")
            self.assertEqual(c.parent_id, api.execution_key("openclaw", "openclaw", "agent:openclaw:root"))
            self.assertEqual(c.root_id, api.execution_key("openclaw", "openclaw", "agent:openclaw:root"))
            self.assertEqual((c.outcome, c.state, c.model, c.cwd, c.tokens_in, c.tokens_out), ("success", "finished", "gpt-test", "/tmp/ws", 10, 5))
            self.assertEqual(c.cost, 0.25)
            self.assertEqual(items["agent:openclaw:root"].record.state, "running")
            self.assertEqual(items["agent:openclaw:root"].record.role, "lead")

    def test_stored_brief_and_result_are_capped_at_1_mib(self):
        # Regression: brief and result strings were stored without any bound.
        cap = 1024 * 1024
        big = "a" * (3 * cap // 2)
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-cap.jsonl", [
                self.meta("cap"),
                self.user("Goal: big\n" + big),
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "b" * (3 * cap // 2)}]}},
            ])
            item = self.by_id(self.api().read_native_executions("codex", home, NOW))["cap"]
            for text in (item.brief, item.result):
                self.assertLessEqual(len(text.encode("utf-8")), cap)
                self.assertTrue(text.endswith("\n[truncated]"))
            self.assertTrue(item.brief.startswith("Goal: big\naaa"))
            self.assertEqual(item.record.goal, "big")
            self.assertTrue(item.record.output_available)

        with tempfile.TemporaryDirectory() as home:
            db = Path(home) / ".local/share/opencode/opencode.db"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT, time_created INT, time_updated INT, time_idle INT, idle_outcome TEXT, time_archived INT)")
            conn.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INT, data TEXT)")
            conn.execute("INSERT INTO session_v2 VALUES ('oc',NULL,'/tmp','t','{}',1000,2000,NULL,NULL,NULL)")
            conn.execute("INSERT INTO session_message VALUES ('u','oc','user',1,?)", (json.dumps({"text": "Goal: oc " + "c" * (3 * cap // 2)}),))
            conn.commit()
            conn.close()
            item = self.by_id(self.api().read_native_executions("opencode", home, NOW))["oc"]
            self.assertLessEqual(len(item.brief.encode("utf-8")), cap)
            self.assertTrue(item.brief.endswith("\n[truncated]"))
            self.assertTrue(item.record.goal.startswith("oc ccc"))

    def test_deleted_transcript_is_evicted_from_cache(self):
        # Regression: _nv_cache kept entries for transcripts that had been deleted.
        with tempfile.TemporaryDirectory() as home:
            api = self.api()
            gone = self.codex_file(home, "rollout-gone.jsonl", [self.meta("gone")])
            live = self.codex_file(home, "rollout-live.jsonl", [self.meta("live")])
            api.read_native_executions("codex", home, NOW)
            self.assertIn(str(gone), api._nv_cache)
            gone.unlink()
            api.read_native_executions("codex", home, NOW)
            self.assertNotIn(str(gone), api._nv_cache)
            self.assertIn(str(live), api._nv_cache)

    def test_symlinked_transcripts_outside_store_are_skipped(self):
        # Regression: rglob followed symlinked files and directories out of the harness store.
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            api = self.api()
            leak = Path(outside) / "secret.jsonl"
            leak.write_text(json.dumps(self.meta("leak")) + "\n" + json.dumps(self.user("Goal: leak")) + "\n")
            real = self.codex_file(home, "rollout-real.jsonl", [self.meta("real"), self.user("Goal: real")])
            folder = real.parent
            os.symlink(leak, folder / "rollout-link.jsonl")
            os.symlink(real, folder / "rollout-alias.jsonl")
            os.symlink(outside, folder / "linkdir")
            items = api.read_native_executions("codex", home, NOW)
            self.assertEqual([x.record.native_id for x in items], ["real"])

    def test_t3_detection_requires_exact_token(self):
        # Regression: any originator containing the substring 't3' was classed as T3.
        cases = {"T3 Code desktop": "t3", "t3-code": "t3", "t3chat": "codex", "not-t3x": "codex", "Codex Desktop": "codex"}
        api = self.api()
        for originator, expected in cases.items():
            with self.subTest(originator=originator), tempfile.TemporaryDirectory() as home:
                self.codex_file(home, "rollout-o.jsonl", [self.meta("o", originator=originator)])
                self.assertEqual(len(api.read_native_executions("t3", home, NOW)), 1 if expected == "t3" else 0)
                self.assertEqual(len(api.read_native_executions("codex", home, NOW)), 0 if expected == "t3" else 1)

    def test_claude_subagent_detected_at_any_ancestor(self):
        # Regression: only a direct parent named 'subagents' marked a child; nested layouts were lead records.
        with tempfile.TemporaryDirectory() as home:
            nested = Path(home) / ".claude/projects/p/parent/subagents/nested"
            nested.mkdir(parents=True)
            (nested / "agent-deep.jsonl").write_text(json.dumps({"type": "user", "sessionId": "parent", "agentId": "agent-deep", "cwd": "/tmp", "message": {"role": "user", "content": "hi"}}) + "\n")
            plain = Path(home) / ".claude/projects/p/parent"
            (plain / "main.jsonl").write_text(json.dumps({"type": "user", "sessionId": "lead", "cwd": "/tmp", "message": {"role": "user", "content": "hi"}}) + "\n")
            items = self.by_id(self.api().read_native_executions("claude", home, NOW))
            self.assertEqual(items["agent-deep"].record.role, "worker")
            self.assertEqual(items["agent-deep"].record.parent_id, self.api().execution_key("claude", "personal", "parent"))
            self.assertEqual(items["lead"].record.role, "lead")

    def test_cursor_and_missing_stores_return_empty_not_error(self):
        with tempfile.TemporaryDirectory() as home:
            api = self.api()
            self.assertEqual(api.read_native_executions("cursor", home, NOW), [])
            self.assertEqual(api.read_native_executions("opencode", home, NOW), [])
            self.assertEqual(api.read_native_executions("openclaw", home, NOW), [])


if __name__ == "__main__":
    unittest.main()
