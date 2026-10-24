"""C7 collect wiring: the codex rollout reader hook, harness/resume fields, and the unlinked worker cap."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import collect
from codexread import CodexSession
from model import DONE, CCCall, LogState, Stage, Worker

NOW = 1_800_000_000.0
TTY = 34816
DEAD_PID = 4194304  # beyond a normal pid_max, so /proc never has it


def write(path: str, text: str = "", mtime: float | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def write_rollout(path: str, session_id: str = "abc", cwd: str = "/x/proj") -> None:
    write(path, json.dumps({"type": "session_meta", "payload": {"id": session_id, "cwd": cwd}}) + "\n")


def codex_session(**kw: object) -> CodexSession:
    return CodexSession(
        session_id=str(kw.get("session_id", "abc")),
        path=str(kw.get("path", "")),
        name=str(kw.get("name", "codexread wiring")),
        prompt=list(kw.get("prompt", ["Wire codexread into collect", "and cap the unlinked group"])),
        system_prompt=list(kw.get("system_prompt", ["You are Codex."])),
        calls=list(kw.get("calls", [CCCall(ts=NOW - 5, tool="apply_patch", arg="src/collect.py")])),
        resume_cmd=str(kw.get("resume_cmd", "codex resume abc")),
    )


class CodexStageTest(unittest.TestCase):
    """A Codex session row gets its fields from the rollout, through the metrics helper that runs the reader."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.rollout = os.path.join(self.home, ".codex", "sessions", "rollout-abc.jsonl")
        write_rollout(self.rollout)
        collect._co_cc_codex_cache.clear()
        self.addCleanup(collect._co_cc_codex_cache.clear)

    def stage(self, **kw: object) -> Stage:
        return Stage(path="/x/.stage", label="proj", tag="CDX", session_id="abc", lead_kind="codex",
                     session_name=str(kw.get("session_name", "")), goal=str(kw.get("goal", "")))

    def wire(self, stage: Stage, session: CodexSession) -> mock.Mock:
        with mock.patch.object(collect, "read_codex_sessions", return_value={"abc": self.rollout}), \
                mock.patch.object(collect, "read_codex_session", return_value=session) as reader:
            collect._co_metrics_lead(stage, NOW, home=self.home)
        return reader

    def test_fields_prompt_system_calls_resume_and_transcript(self) -> None:
        stage, session = self.stage(), codex_session(path=self.rollout)
        self.wire(stage, session)
        self.assertEqual(stage.harness, "codex")
        self.assertEqual(stage.prompt, session.prompt)
        self.assertEqual(stage.system_prompt, session.system_prompt)
        self.assertEqual(stage.calls, session.calls)
        self.assertEqual(stage.transcript_path, self.rollout)
        self.assertEqual(stage.resume_cmd, "codex resume abc")

    def test_an_empty_name_uses_the_session_name(self) -> None:
        stage = self.stage(session_name="")
        self.wire(stage, codex_session(name="real title"))
        self.assertEqual(stage.session_name, "real title")

    def test_a_uuid_name_is_replaced(self) -> None:
        stage = self.stage(session_name="0f8b3c2a-1111-2222-3333-444455556666")
        self.wire(stage, codex_session(name="real title"))
        self.assertEqual(stage.session_name, "real title")

    def test_an_injected_header_name_and_goal_are_replaced(self) -> None:
        stage = self.stage(session_name="# AGENTS.md instructions for /x", goal="# AGENTS.md instructions for /x")
        self.wire(stage, codex_session(name="real title", prompt=["the real first prompt", "second line"]))
        self.assertEqual(stage.session_name, "real title")
        self.assertEqual(stage.goal, "the real first prompt")

    def test_a_real_name_and_goal_are_kept(self) -> None:
        stage = self.stage(session_name="keep me", goal="keep this goal")
        self.wire(stage, codex_session(name="ignored", prompt=["ignored prompt"]))
        self.assertEqual(stage.session_name, "keep me")
        self.assertEqual(stage.goal, "keep this goal")

    def test_resume_falls_back_to_the_thread_id(self) -> None:
        stage = self.stage()
        stage.native_id = "abc"
        self.wire(stage, codex_session(resume_cmd=""))
        self.assertEqual(stage.resume_cmd, "codex resume abc")

    def test_the_reader_is_not_rung_again_while_the_rollout_is_unchanged(self) -> None:
        stage = self.stage()
        with mock.patch.object(collect, "read_codex_sessions", return_value={"abc": self.rollout}), \
                mock.patch.object(collect, "read_codex_session", return_value=codex_session()) as reader:
            collect._co_metrics_lead(stage, NOW, home=self.home)
            collect._co_metrics_lead(stage, NOW, home=self.home)
        self.assertEqual(reader.call_count, 1)


class CodexWorkerTest(unittest.TestCase):
    """A Codex worker whose log is a rollout gets the same fields."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.rollout = os.path.join(self.home, "rollout-abc.jsonl")
        write_rollout(self.rollout)
        collect._co_cc_codex_cache.clear()
        self.addCleanup(collect._co_cc_codex_cache.clear)

    def wire(self, worker: Worker, session: CodexSession) -> None:
        with mock.patch.object(collect, "read_codex_session", return_value=session):
            collect._co_metrics_workers([worker], NOW)

    def worker(self, path: str, **kw: object) -> Worker:
        return Worker("w", DONE, title=str(kw.get("title", "")), goal=str(kw.get("goal", "")),
                      log=LogState(path=path, harness="codex"))

    def test_a_rollout_log_fills_the_worker_fields(self) -> None:
        session = codex_session(name="real title", prompt=["do the wiring"])
        worker = self.worker(self.rollout, title="0f8b3c2a-1111-2222-3333-444455556666",
                             goal="# AGENTS.md instructions for /x")
        self.wire(worker, session)
        self.assertEqual(worker.harness, "codex")
        self.assertEqual(worker.prompt, ["do the wiring"])
        self.assertEqual(worker.system_prompt, session.system_prompt)
        self.assertEqual(worker.calls, session.calls)
        self.assertEqual(worker.resume_cmd, "codex resume abc")
        self.assertEqual(worker.title, "real title")
        self.assertEqual(worker.goal, "do the wiring")

    def test_a_log_symlinked_to_a_rollout_is_wired(self) -> None:
        link = os.path.join(self.home, "w.log")
        os.symlink(self.rollout, link)
        worker = self.worker(link)
        self.wire(worker, codex_session(name="via link"))
        self.assertEqual(worker.title, "via link")
        self.assertEqual(worker.prompt, ["Wire codexread into collect", "and cap the unlinked group"])

    def test_a_jsonl_that_is_not_a_rollout_is_left_alone(self) -> None:
        plain = os.path.join(self.home, "plain.jsonl")
        write(plain, '{"type": "response_item"}\n')
        worker = self.worker(plain, title="keep me")
        self.wire(worker, codex_session())
        self.assertEqual(worker.prompt, [])
        self.assertEqual(worker.title, "keep me")


class HarnessTest(unittest.TestCase):
    """Every row's harness comes from its tag; Claude and Codex rows get a resume command."""

    def test_tag_to_harness_and_resume(self) -> None:
        stages = [
            Stage(path="/a", label="a", tag="CC·W", session_id="s1", native_id="s1"),
            Stage(path="/b", label="b", tag="CC·P", native_id="s2"),
            Stage(path="/c", label="c", tag="CDX", native_id="abc"),
            Stage(path="/d", label="d", tag="OCode", session_id="ses_1"),
            Stage(path="/e", label="e", tag="OClaw"),
            Stage(path="/f", label="f", tag="CUR"),
            Stage(path="/g", label="g", tag="T3"),
        ]
        collect._co_cc_fill_harness(stages)
        self.assertEqual([s.harness for s in stages],
                         ["claude", "claude", "codex", "opencode", "openclaw", "cursor", ""])
        self.assertEqual(stages[0].resume_cmd, "claude --resume s1")
        self.assertEqual(stages[1].resume_cmd, "claude --resume s2")
        self.assertEqual(stages[2].resume_cmd, "codex resume abc")
        self.assertEqual(stages[3].resume_cmd, "")

    def test_an_existing_harness_is_not_overwritten(self) -> None:
        stage = Stage(path="/a", label="a", tag="CDX", harness="codex", resume_cmd="codex resume keep")
        collect._co_cc_fill_harness([stage])
        self.assertEqual((stage.harness, stage.resume_cmd), ("codex", "codex resume keep"))


class CollectorRowsTest(unittest.TestCase):
    """Rows built by a GlobalCollector over a temp tree pick up harness, resume and transcript."""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="cc-collect-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.root = os.path.join(self.dir, "Projects")
        self.procs: dict[int, tuple[int, int, int, int, int]] = {}
        self.env: dict[int, dict[str, str]] = {}
        self.argv: dict[int, list[str]] = {}
        self.cwd: dict[int, str] = {}
        self.patches = [
            mock.patch.object(collect, "_co_scan_procs", side_effect=lambda: dict(self.procs)),
            mock.patch.object(collect, "_co_argv", side_effect=lambda pid: list(self.argv.get(pid, []))),
            mock.patch.object(collect, "_co_cwd", side_effect=lambda pid: self.cwd.get(pid, "")),
            mock.patch.object(collect, "_co_environ", side_effect=lambda pid: dict(self.env.get(pid, {}))),
            mock.patch.object(collect, "_co_pid_live", return_value=False),
            mock.patch.object(collect, "_co_boot_time", return_value=NOW - 3600),
            mock.patch.object(collect, "_co_clk_tck", return_value=100),
            mock.patch.object(collect, "_co_orphan_leads", return_value={}),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.gc = collect.GlobalCollector([self.root], ledger=os.path.join(self.dir, "none.jsonl"), quota=False)
        self.addCleanup(self.gc.close)
        self.project = os.path.join(self.root, "proj")
        self.stage = os.path.join(self.project, ".stage")
        write(os.path.join(self.stage, "PROGRESS.md"), "09:00 started\n", NOW - 60)

    def add_lead(self, pid: int, argv: list[str], cwd: str, start: float = NOW - 3600) -> None:
        self.procs[pid] = (1, 0, 0, int(start - (NOW - 3600)), TTY)
        self.argv[pid] = argv
        self.cwd[pid] = cwd

    def row(self) -> Stage:
        return next(s for s in self.gc.snapshot(NOW, all_history=True).stages if s.path == self.stage)

    def test_a_claude_lead_row_gets_harness_resume_and_transcript(self) -> None:
        config = os.path.join(self.dir, "claude")
        session_id = "11111111-2222-3333-4444-555555555555"
        write(os.path.join(config, "sessions", "10.json"),
              json.dumps({"sessionId": session_id, "cwd": self.project}))
        transcript = os.path.join(config, "projects", re.sub(r"[^A-Za-z0-9]", "-", self.project),
                                  f"{session_id}.jsonl")
        write(transcript, '{"type": "user", "message": {"content": "hi"}}\n')
        self.env[10] = {"CLAUDE_CONFIG_DIR": config}
        self.add_lead(10, ["claude"], self.project)
        stage = self.row()
        self.assertEqual(stage.harness, "claude")
        self.assertEqual(stage.resume_cmd, f"claude --resume {session_id}")
        self.assertEqual(stage.transcript_path, transcript)

    def test_a_codex_lead_row_gets_its_prompt_and_resume(self) -> None:
        codex_home = os.path.join(self.dir, "codex")
        rollout = os.path.join(codex_home, "sessions", "2026", "10", "24", "rollout-2026-10-24T09-00-00-abc.jsonl")
        write_rollout(rollout, cwd=self.project)
        write(os.path.join(codex_home, "session_index.jsonl"),
              json.dumps({"thread_id": "abc", "thread_name": "buf"}) + "\n")
        self.add_lead(11, ["codex"], self.project)
        session = codex_session(path=rollout, name="collect wiring", prompt=["do the wiring"])
        with mock.patch.dict(os.environ, {"CODEX_HOME": codex_home}), \
                mock.patch.object(collect, "read_codex_sessions", return_value={"abc": rollout}), \
                mock.patch.object(collect, "read_codex_session", return_value=session):
            stage = self.row()
        self.assertEqual(stage.harness, "codex")
        self.assertEqual(stage.resume_cmd, "codex resume abc")
        self.assertEqual(stage.transcript_path, rollout)
        self.assertEqual(stage.prompt, ["do the wiring"])

    def test_an_opencode_lead_row_gets_its_harness(self) -> None:
        self.add_lead(12, ["opencode"], self.project)
        with mock.patch.dict(os.environ, {"HOME": self.dir}):
            stage = self.row()
        self.assertEqual(stage.harness, "opencode")
        self.assertEqual(stage.resume_cmd, "")


class UnlinkedGroupTest(unittest.TestCase):
    """The session-unknown group keeps every worker; only the tree caps what it lists."""

    def test_three_hundred_workers_are_all_kept_with_their_exact_live_count(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = tmp.name
        stage = os.path.join(root, "proj", ".stage")
        os.makedirs(stage, exist_ok=True)
        live_pids = {1000 + i for i in range(10)}   # the ten oldest are live
        for i in range(300):
            os.makedirs(os.path.join(stage, f"w{i:03d}"), exist_ok=True)
            write(os.path.join(stage, f"w{i:03d}.pid"), str(1000 + i), NOW - 30 - i)
            write(os.path.join(stage, f"w{i:03d}.log"), "", NOW - 30 - i)
        with mock.patch.object(collect, "_co_orphan_leads", return_value={}), \
                mock.patch.object(collect, "_co_pid_live",
                                  side_effect=lambda pid, *rest: pid in live_pids):
            g = collect.GlobalCollector([root], ledger=os.path.join(root, "none.jsonl"), quota=False)
            self.addCleanup(g.close)
            snap = g.snapshot(now=NOW, all_history=True)
        row = next(s for s in snap.stages if s.unlinked)
        self.assertEqual(len(row.snap.workers), 300)
        self.assertEqual({w.name for w in row.snap.workers}, {f"w{i:03d}" for i in range(300)})
        self.assertEqual(row.doing_note, "")
        self.assertEqual(row.live, 10)   # exact: the ten oldest live workers are kept too
        self.assertEqual(sum(group.total for group in row.snap.groups), 300)

    def test_a_small_group_is_not_capped_and_carries_no_note(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = tmp.name
        stage = os.path.join(root, "proj", ".stage")
        os.makedirs(stage, exist_ok=True)
        for i in range(3):
            os.makedirs(os.path.join(stage, f"w{i}"), exist_ok=True)
            write(os.path.join(stage, f"w{i}.pid"), str(DEAD_PID), NOW - 30 - i)
            write(os.path.join(stage, f"w{i}.log"), "", NOW - 30 - i)
        with mock.patch.object(collect, "_co_orphan_leads", return_value={}):
            g = collect.GlobalCollector([root], ledger=os.path.join(root, "none.jsonl"), quota=False)
            self.addCleanup(g.close)
            snap = g.snapshot(now=NOW, all_history=True)
        row = next(s for s in snap.stages if s.unlinked)
        self.assertEqual(len(row.snap.workers), 3)
        self.assertEqual(row.doing_note, "")


if __name__ == "__main__":
    unittest.main()
