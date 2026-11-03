"""Native instructions and exact parent links survive large/rotated transcripts."""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import collect

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

    def test_native_session_titles_use_first_real_user_prompt(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-codex.jsonl", [
                self.meta("codex-12345678"),
                self.user("AGENTS.md instructions\n<environment_context>cwd=/tmp</environment_context>\n"
                          "# Repair the dashboard names\nmore detail"),
            ])
            codex_item = api.read_native_executions("codex", home, NOW)[0]
            self.assertEqual(codex_item.record.title, "Repair the dashboard names")

            index = Path(home) / ".codex/session_index.jsonl"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text(json.dumps({"thread_id": "codex-12345678", "thread_name": "Codex explicit name"}) + "\n")
            codex_item = api.read_native_executions("codex", home, NOW)[0]
            self.assertEqual(codex_item.record.title, "Codex explicit name")

            claude_dir = Path(home) / ".claude/projects/p"
            claude_dir.mkdir(parents=True)
            claude_path = claude_dir / "claude-12345678.jsonl"
            claude_path.write_text("\n".join(json.dumps(row) for row in [
                {"type": "system", "subtype": "system-reminder", "sessionId": "claude-12345678",
                 "message": {"role": "user", "content": "<system-reminder>rules</system-reminder>"}},
                {"type": "user", "sessionId": "claude-12345678", "cwd": "/tmp",
                 "message": {"role": "user", "content": "# Review the session row"}},
            ]) + "\n")
            claude_item = api.read_native_executions("claude", home, NOW)[0]
            self.assertEqual(claude_item.record.title, "Review the session row")

            cursor_dir = Path(home) / ".cursor/projects/p/agent-transcripts/cursor-12345678"
            cursor_dir.mkdir(parents=True)
            cursor_meta = Path(home) / ".cursor/chats/project/cursor-12345678"
            cursor_meta.mkdir(parents=True)
            (cursor_meta / "meta.json").write_text(json.dumps({"schemaVersion": 1, "cwd": "/tmp"}))
            (cursor_dir / "cursor-12345678.jsonl").write_text(json.dumps({
                "role": "user", "message": {"content": [{"type": "text",
                    "text": "<user_query>\n**Name the Cursor session**\nDo the work</user_query>"}]}
            }) + "\n")
            cursor_item = api.read_native_executions("cursor", home, NOW)[0]
            self.assertEqual(cursor_item.record.title, "Name the Cursor session")
            self.assertFalse(any(api._NV_ID_NAME.fullmatch(item.record.title)
                                 for item in (codex_item, claude_item, cursor_item)))

    def test_prompt_name_strips_markdown_and_truncates_without_returning_ids(self):
        api = self.api()
        self.assertEqual(api._nv_prompt_name("# **A short name**\nmore"), "A short name")
        name = api._nv_prompt_name("`" + "long name " * 8 + "`")
        self.assertEqual((len(name), name[-1]), (60, "…"))
        self.assertEqual(api._nv_prompt_name("ses_0123456789abcdef"), "")

    def test_codex_rollout_name_skips_codex_bootstrap_messages(self):
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-real.jsonl", [
                self.meta("real-thread"),
                {"type": "response_item", "payload": {"type": "message", "role": "developer",
                 "content": [{"type": "input_text", "text": "runtime developer message"}]}},
                self.user("# AGENTS.md instructions for /project\n<INSTRUCTIONS>rules</INSTRUCTIONS>"),
                self.user("<environment_context>cwd=/project</environment_context>"),
                self.user("<skills_instructions>skills</skills_instructions>"),
                self.user("<permissions instructions>permissions</permissions instructions>"),
                self.user("Please make the worker names readable"),
            ])
            item = self.api().read_native_executions("codex", home, NOW)[0]
            self.assertEqual(item.record.title, "Please make the worker names readable")
            self.assertEqual(item.brief, "# AGENTS.md instructions for /project\n<INSTRUCTIONS>rules</INSTRUCTIONS>")

    def test_codex_thread_name_wins_over_rollout_prompt(self):
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-indexed.jsonl", [
                self.meta("indexed-thread"), self.user("Do the real task"),
            ])
            index = Path(home) / ".codex/session_index.jsonl"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text(json.dumps({"id": "indexed-thread", "thread_name": "Indexed real title"}) + "\n")
            item = self.api().read_native_executions("codex", home, NOW)[0]
            self.assertEqual(item.record.title, "Indexed real title")

    def test_launcher_boilerplate_prompts_are_not_names(self):
        api = self.api()
        prompts = (
            "Do the task in your task file. Follow it exactly.",
            "Do the task in your system prompt. Write /home/user/output.md",
            "Do the review in your task file. Follow it exactly.",
            "Read BRIEF.md in the current directory and do exactly what it says.",
            '"Read BRIEF.md in the current directory and do exactly what it says."',
            "Follow it exactly",
            "Read NOTES.md and follow it exactly",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(api._nv_prompt_name(prompt), "")

    def test_stage_report_path_names_launcher_prompt(self):
        api = self.api()
        self.assertEqual(
            api._nv_prompt_name("Do the review in your task file: .stage/review/briefs/cache-fix.md"),
            "cache-fix (review)",
        )
        self.assertEqual(
            api._nv_prompt_name("Do the task in your system prompt: .stage/run-a/report-task/REPORT.md"),
            "report-task (run-a)",
        )

    def test_uninformative_prompt_falls_back_to_model_cwd_and_local_time(self):
        api = self.api()
        from model import ExecutionRecord
        record = ExecutionRecord("id", harness="claude", native_id="native-id", model="gpt-6-luna",
                                 cwd="/tmp/project-name", started=NOW)
        expected = f"GPT-6 Luna · project-name · {datetime.fromtimestamp(NOW).strftime('%H:%M')}"
        self.assertEqual(api._nv_session_name(record, "Read BRIEF.md and follow it exactly"), expected)
        self.assertEqual(api._nv_session_name(record, "Read BRIEF.md and follow it exactly",
                                             "Read BRIEF.md and follow it exactly"), expected)

    def test_fallback_name_from_an_earlier_scan_gives_way_to_a_later_prompt(self):
        api = self.api()
        from model import ExecutionRecord
        record = ExecutionRecord("id", harness="claude", native_id="native-id", model="gpt-6-luna",
                                 cwd="/tmp/project-name", started=NOW)
        record.title = api._nv_session_name(record, "")
        self.assertTrue(record.title.startswith("GPT-6 Luna · project-name · "))
        self.assertEqual(api._nv_session_name(record, "Fix the login redirect"), "Fix the login redirect")
        record.cwd, record.model = "/tmp/other-dir", "claude-opus-5-5"   # later lines fill in cwd and model
        self.assertEqual(api._nv_session_name(record, "Fix the login redirect"), "Fix the login redirect")
        record.title = "My custom title"
        self.assertEqual(api._nv_session_name(record, "Fix the login redirect"), "My custom title")

    def test_codex_unchanged_sessions_do_not_reparse_for_names(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as home:
            for i in range(70):
                sid = f"thread-{i}"
                self.codex_file(home, f"rollout-{i}.jsonl", [self.meta(sid), self.user(f"Task {i}")])
            from codexread import read_codex_prompt_prefix
            with mock.patch("codexread.read_codex_prompt_prefix", wraps=read_codex_prompt_prefix) as reader:
                api.read_native_executions("codex", home, NOW)
                first_count = reader.call_count
                api.read_native_executions("codex", home, NOW)
                self.assertEqual(first_count, 70)
                self.assertEqual(reader.call_count, first_count)

    def test_codex_invalid_utf8_index_does_not_abort_scan(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-good.jsonl", [self.meta("good"), self.user("A readable task")])
            index = Path(home) / ".codex/session_index.jsonl"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_bytes(b"\xff\n" + json.dumps({"id": "good", "thread_name": "Still works"}).encode() + b"\n")
            self.assertEqual(api.read_native_executions("codex", home, NOW)[0].record.title, "Still works")

    def test_codex_title_index_is_cached_and_oversized_lines_are_skipped(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as home:
            index = Path(home) / ".codex/session_index.jsonl"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_bytes(b"{" + b"x" * (api._NV_CODEX_INDEX_LINE_MAX + 1) + b"}\n"
                             + json.dumps({"id": "ok", "thread_name": "Cached title"}).encode() + b"\n")
            original_open = Path.open
            api._nv_codex_index_cache.pop(str(index), None)
            with mock.patch.object(Path, "open", autospec=True, side_effect=original_open) as open_file:
                first = api._nv_codex_thread_names(home)
                second = api._nv_codex_thread_names(home)
                self.assertEqual(open_file.call_count, 1)
            self.assertEqual(first, {"ok": "Cached title"})
            self.assertIs(first, second)

    def test_codex_launcher_title_uses_filtered_full_prompt_and_rejects_id_fallback(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-long.jsonl", [
                self.meta("launcher"), self.user("# AGENTS.md instructions for /project\n<INSTRUCTIONS>rules</INSTRUCTIONS>"),
                self.user("Do the review in your task file: .stage/review/briefs/cache-fix.md")])
            (Path(home) / ".codex/session_index.jsonl").write_text("", encoding="utf-8")
            item = api.read_native_executions("codex", home, NOW)[0]
            self.assertEqual(item.record.title, "cache-fix (review)")
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-id.jsonl", [
                self.meta("01234567-89ab-cdef-0123-456789abcdef"), self.user("01234567-89ab-cdef-0123-456789abcdef")])
            item = api.read_native_executions("codex", home, NOW)[0]
            self.assertNotEqual(item.record.title, "codex 01234567")
            self.assertNotRegex(item.record.title, r"(?i)^codex [0-9a-f]{8}$")

    def test_cursor_unlinked_marker_survives_long_prompt_name(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as home:
            transcript = Path(home) / ".cursor/projects/p/agent-transcripts/sid/sid.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text(json.dumps({"role": "user", "message": {"content": [
                {"type": "text", "text": "A" * 90}]}}) + "\n", encoding="utf-8")
            item = api.read_native_executions("cursor", home, NOW)[0]
            self.assertTrue(item.record.title.endswith(" (unlinked)"))
            self.assertLessEqual(len(item.record.title), 60)

        with tempfile.TemporaryDirectory() as home:
            transcript = Path(home) / ".cursor/projects/p/agent-transcripts/sid/sid.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text(json.dumps({"role": "user", "message": {"content": [
                {"type": "text", "text": "<user_query>Short task</user_query>"}]}}) + "\n", encoding="utf-8")
            item = api.read_native_executions("cursor", home, NOW)[0]
            self.assertEqual(item.record.title, "Short task (unlinked)")

        with tempfile.TemporaryDirectory() as home:
            for sid in ("one", "two", "three"):
                transcript = Path(home) / f".cursor/projects/p/agent-transcripts/{sid}/{sid}.jsonl"
                transcript.parent.mkdir(parents=True)
                query = "<user_query>" + "A" * 90 + "</user_query>"
                events = [
                    {"role": "user", "message": {"content": [{"type": "text", "text": query}]}},
                    {"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Task",
                                                                          "input": {"prompt": "A" * 90}}]}},
                ]
                transcript.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            items = api.read_native_executions("cursor", home, NOW)
            self.assertEqual(len(items), 3)
            self.assertTrue(all(item.record.title.endswith(" (ambiguous, unlinked)") for item in items))

    def test_unknown_start_fallback_uses_source_mtime_before_native_id_suffix(self):
        from model import ExecutionRecord
        api = self.api()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "transcript.jsonl"
            source.write_text("{}\n")
            stamp = NOW - 600
            os.utime(source, (stamp, stamp))
            first = ExecutionRecord("one", harness="claude", native_id="abcdef123456", cwd="",
                                    source=str(source), started=0)
            expected = datetime.fromtimestamp(stamp).strftime("%H:%M")
            self.assertTrue(api._nv_session_name(first, "").endswith(expected))
            source.unlink()
            second = ExecutionRecord("two", harness="claude", native_id="987654321abc", cwd="", started=0)
            self.assertTrue(api._nv_session_name(second, "").endswith("987654"))

    def test_codex_name_fallback_does_not_reuse_stale_derived_title(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as home:
            self.codex_file(home, "rollout-stale.jsonl", [self.meta("stale"),
                self.user("# AGENTS.md instructions\n<INSTRUCTIONS>rules</INSTRUCTIONS>"),
                self.user("Read BRIEF.md and follow it exactly")])
            index = Path(home) / ".codex/session_index.jsonl"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text(json.dumps({"id": "stale", "thread_name": "Explicit name"}) + "\n")
            first = api.read_native_executions("codex", home, NOW)[0]
            self.assertEqual(first.record.title, "Explicit name")
            index.write_text("")
            second = api.read_native_executions("codex", home, NOW)[0]
            self.assertNotEqual(second.record.title, "Explicit name")
            self.assertNotIn("rules", second.record.title)

    def test_codex_name_reads_only_a_bounded_prefix(self):
        from codexread import read_codex_prompt_prefix
        with tempfile.TemporaryDirectory() as home:
            path = self.codex_file(home, "rollout-prefix.jsonl", [
                self.meta("prefix"), self.user("# AGENTS.md instructions"), self.user("# The real title"),
            ])
            with path.open("ab") as stream:
                event = json.dumps({"type": "event_msg", "payload": {"type": "noise", "text": "x" * 1000}}) + "\n"
                stream.write((event * 1000).encode())
            self.assertEqual(read_codex_prompt_prefix(str(path), max_bytes=1024), ["# The real title"])

    def test_codex_prefix_reader_stops_at_byte_limit(self):
        from codexread import read_codex_prompt_prefix
        with tempfile.TemporaryDirectory() as home:
            path = self.codex_file(home, "rollout-limited.jsonl", [self.meta("limited")])
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"type": "event_msg", "payload": {"type": "noise", "text": "x" * 2000}}) + "\n")
                stream.write(json.dumps(self.user("# Too far")) + "\n")
            self.assertEqual(read_codex_prompt_prefix(str(path), max_bytes=1024), [])

    def test_codex_prefix_finds_prompt_after_large_realistic_preamble(self):
        from codexread import read_codex_prompt_prefix
        with tempfile.TemporaryDirectory() as home:
            events = [self.meta("large-preamble")]
            # Several large developer messages model the injected system and repo instructions.
            for _ in range(3):
                events.append({"type": "response_item", "payload": {"type": "message", "role": "developer",
                    "content": [{"type": "input_text", "text": "developer instructions " + "x" * 99000}]}})
            events.extend([
                self.user("# AGENTS.md instructions\n" + "rules " * 3000),
                self.user("<environment_context>\n" + "cwd=/tmp/task " * 3000 + "</environment_context>"),
                self.user("# Real rollout name"),
            ])
            path = self.codex_file(home, "rollout-large-prefix.jsonl", events)
            self.assertGreater(path.stat().st_size, 300_000)
            self.assertEqual(read_codex_prompt_prefix(str(path)), ["# Real rollout name"])

    def test_codex_prefix_stops_at_two_mib_without_a_real_prompt(self):
        import codexread
        from codexread import read_codex_prompt_prefix
        with tempfile.TemporaryDirectory() as home:
            events = [self.meta("no-prompt")]
            for _ in range(170):
                events.append({"type": "response_item", "payload": {"type": "message", "role": "developer",
                    "content": [{"type": "input_text", "text": "preamble " + "x" * 18000}]}})
            path = self.codex_file(home, "rollout-no-prompt.jsonl", events)
            parsed = 0
            original = codexread._crd_item

            def count_items(state, obj):
                nonlocal parsed
                parsed += 1
                return original(state, obj)

            with mock.patch("codexread._crd_item", side_effect=count_items):
                self.assertEqual(read_codex_prompt_prefix(str(path)), [])
            self.assertGreater(parsed, 80)
            self.assertLess(parsed, len(events))

    def test_prompt_name_is_bounded_redacted_and_skips_assignment_headers(self):
        api = self.api()
        self.assertEqual(api._nv_prompt_name("Job: review\nModel: luna\nEffort: high\n# Actual task"), "Actual task")
        self.assertEqual(api._nv_prompt_name("Builder: luna\nMode: build\n# Actual task"), "Actual task")
        self.assertIn("…", api._nv_prompt_name("Use sk-" + "a" * 32 + " for this task"))
        self.assertNotIn("sk-", api._nv_prompt_name("Use sk-" + "a" * 32 + " for this task"))
        for token in ("ghp_" + "a" * 30, "AKIA" + "A" * 16, "Bearer secret-value", "f" * 30, "Q" * 30):
            self.assertNotIn(token, api._nv_prompt_name(f"Use {token} now"))
        self.assertLessEqual(len(api._nv_prompt_name("<command-x>" * 30000 + "# bounded")), 60)

    def test_secret_masking_happens_before_name_truncation(self):
        api = self.api()
        for secret in ("sk-" + "a" * 32, "ghp_" + "a" * 30, "AKIA" + "A" * 16):
            with self.subTest(secret=secret[:4]):
                name = api._nv_prompt_name("x" * 55 + secret)
                self.assertNotIn(secret[:8], name)

    def test_claude_custom_title_overrides_ai_title(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".claude/projects/p"
            folder.mkdir(parents=True)
            path = folder / "named.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in [
                {"type": "ai-title", "sessionId": "claude-title-1234", "aiTitle": "AI title"},
                {"type": "custom-title", "sessionId": "claude-title-1234", "customTitle": "My title"},
                {"type": "user", "sessionId": "claude-title-1234", "cwd": "/tmp",
                 "message": {"role": "user", "content": "Do the thing"}},
            ]) + "\n")
            item = self.api().read_native_executions("claude", home, NOW)[0]
            self.assertEqual(item.record.title, "My title")

    def test_opencode_blank_and_generated_titles_use_prompt_and_keep_identity(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / ".local/share/opencode/opencode.db"
            path.parent.mkdir(parents=True)
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT, time_created INT, time_updated INT, time_idle INT, idle_outcome TEXT, time_archived INT)")
            conn.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INT, data TEXT)")
            rows = (("ses_root", None, "", "Goal: Session root"),
                    ("ses_blank", "ses_root", "", "Goal: Name the OpenCode run\nFurther details"),
                    ("ses_generated", "ses_root", "Shell command request title generation",
                     "Goal: Name the OpenCode run\nFurther details"),
                    ("ses_orphan", "ses_missing", "", "Goal: Orphan session"))
            for sid, parent, title, prompt in rows:
                conn.execute("INSERT INTO session_v2 VALUES (?,?, '/tmp',?,'{}',?,?,NULL,NULL,NULL)",
                             (sid, parent, title, NOW * 1000, NOW * 1000))
                conn.execute("INSERT INTO session_message VALUES (?, 'user', 1, ?)",
                             (sid, json.dumps({"text": prompt})))
            conn.commit()
            conn.close()
            items = api.read_native_executions("opencode", home, NOW)
            self.assertEqual({item.record.title for item in items},
                             {"Name the OpenCode run", "Session root", "Orphan session"})
            workers = [collect._co_execution_worker(item, NOW, False) for item in items
                       if item.record.native_id in ("ses_blank", "ses_generated")]
            self.assertEqual({worker.name for worker in workers}, {"Name the OpenCode run"})
            self.assertEqual({worker.native_id for worker in workers}, {"ses_blank", "ses_generated"})
            self.assertEqual(len({worker.key for worker in workers}), 2)
            self.assertFalse(any(api._NV_ID_NAME.fullmatch(worker.name) for worker in workers))
            collector = collect.GlobalCollector([], quota=False)
            stages = collector._co_execution_stages(items, NOW, True)
            root = next(stage for stage in stages if stage.native_id == "ses_root")
            self.assertEqual(root.session_name, "Session root")
            self.assertEqual(len(root.snap.workers), 2)
            orphan = next(stage for stage in stages if stage.session_name == "Orphan session")
            self.assertEqual(orphan.session_name, "Orphan session")

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
