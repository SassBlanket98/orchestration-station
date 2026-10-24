from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import collect
from model import DONE, Milestone, Snapshot, Worker

NOW = 1_800_000_000.0


class SessionData(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stage = os.path.join(self.tmp.name, "project", ".stage")
        os.makedirs(self.stage)

    def test_session_sidecar_parses_id_and_missing_or_bad_files_are_empty(self) -> None:
        with open(os.path.join(self.stage, "worker.session"), "w", encoding="utf-8") as fh:
            json.dump({"lead_session": "sid-1", "lead_harness": "claude"}, fh)
        self.assertEqual(collect._co_worker_session(self.stage, "worker"), "sid-1")
        self.assertEqual(collect._co_worker_session(self.stage, "absent"), "")
        with open(os.path.join(self.stage, "worker.session"), "w", encoding="utf-8") as fh:
            fh.write("not json")
        self.assertEqual(collect._co_worker_session(self.stage, "worker"), "")

    def test_effort_parser_normalizes_supported_words(self) -> None:
        self.assertEqual(collect._co_effort("reasoning effort: med"), "medium")
        self.assertEqual(collect._co_effort("sonnet-work HIGH"), "high")
        self.assertEqual(collect._co_effort("small"), "")

    def test_worker_effort_prefers_log_header_then_model_text(self) -> None:
        os.makedirs(os.path.join(self.stage, "worker"))
        with open(os.path.join(self.stage, "worker.pid"), "w", encoding="utf-8") as fh:
            fh.write("99999999")
        with open(os.path.join(self.stage, "worker.log"), "w", encoding="utf-8") as fh:
            fh.write("reasoning effort: xhigh\n")
        with open(os.path.join(self.stage, "worker.model"), "w", encoding="utf-8") as fh:
            fh.write("luna medium\n")
        collector = collect.Collector(self.stage, quota=False)
        self.addCleanup(collector.close)
        worker = collector._co_one_worker(self.stage, "worker", os.path.join(self.stage, "worker.pid"),
                                          99999999, NOW, {}, {})
        self.assertEqual(worker.effort, "xhigh")
        os.unlink(os.path.join(self.stage, "worker.log"))
        worker = collector._co_one_worker(self.stage, "worker", os.path.join(self.stage, "worker.pid"),
                                          99999999, NOW, {}, {})
        self.assertEqual(worker.effort, "medium")

    def test_claude_title_precedence_and_three_word_cut(self) -> None:
        config = os.path.join(self.tmp.name, ".claude-team-work")
        os.makedirs(os.path.join(config, "sessions"))
        cwd = os.path.join(self.tmp.name, "project")
        sid = "claude-session"
        with open(os.path.join(config, "sessions", "42.json"), "w", encoding="utf-8") as fh:
            json.dump({"sessionId": sid, "cwd": cwd}, fh)
        transcript_dir = os.path.join(config, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))   # the folder rule Claude itself uses
        os.makedirs(transcript_dir)
        transcript = os.path.join(transcript_dir, sid + ".jsonl")
        rows = [
            {"type": "ai-title", "aiTitle": "ai name ignored"},
            {"type": "agent-name", "agentName": "agent name ignored"},
            {"type": "custom-title", "customTitle": "one two three four five"},
            {"type": "assistant", "message": {"model": "claude-sonnet-5-5"}, "effort": "medium"},
        ]
        with open(transcript, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(row) + "\n" for row in rows)
        lead = collect._co_Lead(42, "claude", "live", cwd, NOW - 10)
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": config}):
            info = collect._co_lead_session(self.stage, lead)
        self.assertEqual((info["session_id"], info["session_name"], info["tag"]),
                         (sid, "one two three four five", "CC·W"))
        self.assertEqual((info["lead_model"], info["effort"]), ("sonnet-5.5", "medium"))
        with open(os.path.join(self.stage, "lead.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "explicit lead name four"}, fh)
        os.utime(os.path.join(self.stage, "lead.json"), (NOW, NOW))   # written after the lead started
        info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["session_name"], "explicit lead name four")

    def test_t3_code_is_its_own_harness_tag_and_section(self) -> None:
        from model import GlobalSnapshot, LeadInfo, Snapshot, Stage, UIState
        from render import render_global
        from widgets import _wd_SGR_RE

        lead = collect._co_Lead(5, "t3 code", "background", os.path.join(self.tmp.name, "project"), NOW - 10)
        info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["tag"], "T3")
        stage = Stage(os.path.join(self.tmp.name, "project"), "t3proj", Snapshot(now=NOW), 0, NOW - 5, LeadInfo("opus"))
        stage.state, stage.tag = "active", "T3"
        text = "\n".join(_wd_SGR_RE.sub("", ln) for ln in render_global(GlobalSnapshot(stages=[stage]), UIState(), 100, 30, NOW))
        self.assertRegex(text, r"t3proj.*T3")

    def test_claude_activity_is_last_message_not_file_mtime(self) -> None:
        config = os.path.join(self.tmp.name, ".claude")
        os.makedirs(os.path.join(config, "sessions"))
        cwd = os.path.join(self.tmp.name, "project")
        sid = "claude-session"
        with open(os.path.join(config, "sessions", "43.json"), "w", encoding="utf-8") as fh:
            json.dump({"sessionId": sid, "cwd": cwd}, fh)
        transcript_dir = os.path.join(config, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
        os.makedirs(transcript_dir)
        transcript = os.path.join(transcript_dir, sid + ".jsonl")
        rows = [
            {"type": "user", "timestamp": "2026-10-11T03:39:00.000Z", "message": {}},
            {"type": "assistant", "timestamp": "2026-10-11T03:40:00.000Z", "message": {"model": "claude-opus-5-5"}},
            {"type": "attachment", "timestamp": "2026-10-11T07:55:00.000Z"},
        ]
        with open(transcript, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(row) + "\n" for row in rows)
        lead = collect._co_Lead(43, "claude", "live", cwd, NOW - 10)
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": config}):
            info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["activity"], datetime(2026, 10, 11, 3, 40, tzinfo=timezone.utc).timestamp())
        self.assertGreater(os.stat(transcript).st_mtime, info["activity"])
        with open(transcript, "w", encoding="utf-8") as fh:   # no user/assistant entry: mtime fallback
            fh.write(json.dumps({"type": "custom-title", "customTitle": "x"}) + "\n")
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": config}):
            info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["activity"], os.stat(transcript).st_mtime)

    def test_claude_sidecar_updated_at_is_activity_without_transcript(self) -> None:
        config = os.path.join(self.tmp.name, ".claude")
        os.makedirs(os.path.join(config, "sessions"))
        cwd = os.path.join(self.tmp.name, "project")
        with open(os.path.join(config, "sessions", "44.json"), "w", encoding="utf-8") as fh:
            json.dump({"sessionId": "no-transcript", "cwd": cwd, "updatedAt": 1_800_000_123_456}, fh)
        lead = collect._co_Lead(44, "claude", "live", cwd, NOW - 10)
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": config}), \
                mock.patch.object(collect, "_co_argv", return_value=[]):
            info = collect._co_lead_session(self.stage, lead)
        self.assertAlmostEqual(info["activity"], 1_800_000_123.456)
        self.assertEqual(info["session_id"], "no-transcript")

    def test_claude_name_comes_from_argv_then_sidecar(self) -> None:
        config = os.path.join(self.tmp.name, ".claude")
        os.makedirs(os.path.join(config, "sessions"))
        cwd = os.path.join(self.tmp.name, "project")
        with open(os.path.join(config, "sessions", "45.json"), "w", encoding="utf-8") as fh:
            json.dump({"sessionId": "named", "cwd": cwd, "name": "monitor-orchestrator"}, fh)
        lead = collect._co_Lead(45, "claude", "live", cwd, NOW - 10)
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": config}), \
                mock.patch.object(collect, "_co_argv", return_value=[]):
            info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["session_name"], "monitor-orchestrator")
        for argv, name in ((["claude", "-n", "argv name"], "argv name"), (["claude", "--name=eq name"], "eq name")):
            with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": config}), \
                    mock.patch.object(collect, "_co_argv", return_value=argv):
                info = collect._co_lead_session(self.stage, lead)
            self.assertEqual(info["session_name"], name)

    def test_derived_sidecar_name_is_ignored_and_argv_beats_stale_lead_json(self) -> None:
        config = os.path.join(self.tmp.name, ".claude")
        os.makedirs(os.path.join(config, "sessions"))
        cwd = os.path.join(self.tmp.name, "project")
        lead = collect._co_Lead(46, "claude", "live", cwd, NOW - 10)
        environ = {"CLAUDE_CONFIG_DIR": config}
        sidecar = os.path.join(config, "sessions", "46.json")
        with open(sidecar, "w", encoding="utf-8") as fh:
            json.dump({"sessionId": "derived", "cwd": cwd, "name": "pc-upgrade-3d", "nameSource": "derived"}, fh)
        with mock.patch.object(collect, "_co_environ", return_value=environ), \
                mock.patch.object(collect, "_co_argv", return_value=[]):
            info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["session_name"], "")   # derived name does not count; no transcript title either
        with open(sidecar, "w", encoding="utf-8") as fh:
            json.dump({"sessionId": "derived", "cwd": cwd, "name": "user picked", "nameSource": "user"}, fh)
        with mock.patch.object(collect, "_co_environ", return_value=environ), \
                mock.patch.object(collect, "_co_argv", return_value=[]):
            info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["session_name"], "user picked")
        lead_json = os.path.join(self.stage, "lead.json")
        with open(lead_json, "w", encoding="utf-8") as fh:
            json.dump({"name": "old lead name"}, fh)
        os.utime(lead_json, (NOW - 100, NOW - 100))   # written before the lead started: stale
        with mock.patch.object(collect, "_co_environ", return_value=environ), \
                mock.patch.object(collect, "_co_argv", return_value=["claude", "-n", "argv name"]):
            info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["session_name"], "argv name")
        os.utime(lead_json, (NOW, NOW))   # written after the lead started: a rename it made itself, so it wins
        with mock.patch.object(collect, "_co_environ", return_value=environ), \
                mock.patch.object(collect, "_co_argv", return_value=["claude", "-n", "argv name"]):
            info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["session_name"], "old lead name")

    def test_headless_child_sidecar_fills_name_and_activity_when_lead_has_none(self) -> None:
        config = os.path.join(self.tmp.name, ".claude")
        os.makedirs(os.path.join(config, "sessions"))
        cwd = os.path.join(self.tmp.name, "project")
        with open(os.path.join(config, "sessions", "99.json"), "w", encoding="utf-8") as fh:
            json.dump({"sessionId": "child-sid", "cwd": cwd, "name": "child run", "updatedAt": 1_800_000_500_000}, fh)
        lead = collect._co_Lead(42, "claude", "live", cwd, NOW - 10)
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": config}), \
                mock.patch.object(collect, "_co_argv", return_value=[]):
            info = collect._co_lead_session(self.stage, lead, "child-sid")
        self.assertEqual((info["session_id"], info["session_name"]), ("child-sid", "child run"))
        self.assertAlmostEqual(info["activity"], 1_800_000_500.0)

    def test_one_real_stage_splits_live_closed_and_unlinked_workers(self) -> None:
        for name in ("live-worker", "closed-worker", "legacy-worker"):
            os.makedirs(os.path.join(self.stage, name), exist_ok=True)
            with open(os.path.join(self.stage, name + ".pid"), "w", encoding="utf-8") as fh:
                fh.write("99999999")
        gc = collect.GlobalCollector([self.tmp.name], ledger=os.path.join(self.tmp.name, "ledger"), quota=False)
        self.addCleanup(gc.close)
        gc._co_lead_map = lambda: {self.stage: collect._co_Lead(42, "cursor", "live", os.path.dirname(self.stage), NOW - 100)}
        workers = [Worker("live-worker", DONE, session="live"),
                   Worker("closed-worker", DONE, session="closed"),
                   Worker("legacy-worker", DONE)]
        with mock.patch.object(collect, "_co_lead_session", return_value={
            "session_id": "live", "session_name": "", "tag": "CUR", "lead_model": "", "effort": "",
            "parent": "launcher", "root_session": "root", "launch_depth": 2,
            "app": True, "app_id": "bridge", "tmux": "heading",
        }), mock.patch.object(collect.Collector, "snapshot", return_value=Snapshot(
            title="project", now=NOW, workers=workers, milestones=[Milestone(None, "milestone")])):
            snap = gc.snapshot(now=NOW, all_history=True)
        rows = {row.session_id or ("unlinked" if row.unlinked else "none"): row for row in snap.stages}
        self.assertEqual(set(rows), {"live", "closed", "unlinked"})
        self.assertEqual([w.name for w in rows["live"].snap.workers], ["live-worker"])
        self.assertEqual([w.name for w in rows["closed"].snap.workers], ["closed-worker"])
        self.assertEqual([w.name for w in rows["unlinked"].snap.workers], ["legacy-worker"])
        self.assertEqual(len({row.path for row in snap.stages}), 3)
        self.assertEqual(rows["live"].path, self.stage)
        self.assertTrue(all(row.dir == self.stage for row in snap.stages))
        self.assertTrue(all(row.lead_state == "gone" for row in (rows["closed"], rows["unlinked"])))
        self.assertEqual(rows["live"].snap.milestones[0].text, "milestone")
        self.assertEqual((rows["live"].parent, rows["live"].root_session, rows["live"].launch_depth),
                         ("launcher", "root", 2))
        self.assertEqual((rows["live"].app, rows["live"].app_id, rows["live"].tmux), (True, "bridge", "heading"))
        for row in (rows["closed"], rows["unlinked"]):
            self.assertEqual((row.parent, row.root_session, row.launch_depth, row.app, row.app_id, row.tmux),
                             ("", "", 0, False, "", ""))
        self.assertEqual((rows["live"].tag, rows["closed"].tag, rows["unlinked"].tag), ("CUR", "", ""))


    def test_a_lead_json_naming_another_pid_is_ignored(self) -> None:
        with open(os.path.join(self.stage, "lead.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "merge subagents", "pid": 19916, "harness": "opencode"}, fh)
        os.utime(os.path.join(self.stage, "lead.json"), (NOW, NOW))
        lead = collect._co_Lead(42, "claude", "live", os.path.join(self.tmp.name, "project"), NOW - 10)
        info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["session_name"], "")

    def test_launcher_slot_names_are_not_task_names(self) -> None:
        self.assertTrue(collect._co_is_launcher_name("sample-project-a6", "/home/x/Projects/sample-project"))
        self.assertTrue(collect._co_is_launcher_name("sample-project", "/home/x/Projects/sample-project"))
        self.assertFalse(collect._co_is_launcher_name("monitor full page", "/home/x/Projects/sample-project"))
        self.assertFalse(collect._co_is_launcher_name("sample-project redesign", "/home/x/Projects/sample-project"))

    def test_a_session_with_no_title_is_named_from_its_first_prompt(self) -> None:
        config = os.path.join(self.tmp.name, ".claude")
        os.makedirs(os.path.join(config, "sessions"))
        cwd = os.path.join(self.tmp.name, "project")
        with open(os.path.join(config, "sessions", "77.json"), "w", encoding="utf-8") as fh:
            json.dump({"sessionId": "untitled", "cwd": cwd}, fh)
        folder = os.path.join(config, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
        os.makedirs(folder)
        rows = [{"type": "user", "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
                {"type": "user", "message": {"role": "user", "content": "Fix the login redirect loop on mobile"}}]
        with open(os.path.join(folder, "untitled.jsonl"), "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in rows)
        lead = collect._co_Lead(77, "claude", "live", cwd, NOW - 10)
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": config}), \
                mock.patch.object(collect, "_co_argv", return_value=[]):
            info = collect._co_lead_session(self.stage, lead)
        self.assertEqual(info["session_name"], "Fix the login redirect loop")

    def test_first_prompt_title_survives_non_object_json_lines(self) -> None:
        path = os.path.join(self.tmp.name, "odd.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("null\n[1,2]\n42\n" + json.dumps({"type": "user", "message": {"content": "Do the thing"}}) + "\n")
        self.assertEqual(collect._co_first_prompt_title(path), "Do the thing")
