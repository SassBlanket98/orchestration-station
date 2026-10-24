from __future__ import annotations

import json
import os
import tempfile
import unittest

import collect
from model import ExecutionEvidence, ExecutionRecord, Snapshot, Stage, Worker
from registry import execution_key


def _worker(name: str, *, state: str = "running", start: float = 10.0) -> Worker:
    return Worker(name=name, state=state, start=start)


def _lead(session_id: str, lead_kind: str = "claude") -> Stage:
    return Stage(path="stage", label="project", snap=Snapshot(), lead_state="live",
                 lead_kind=lead_kind, session_id=session_id, solo=True)


def _execution(harness: str, account: str, native_id: str, *, root_id: str = "",
               parent_id: str = "", workers: list[Worker] | None = None,
               live: bool = False, goal: str = "", brief: list[str] | None = None) -> Stage:
    record = ExecutionRecord(execution_key(harness, account, native_id), harness=harness,
                             account=account, native_id=native_id, root_id=root_id, parent_id=parent_id,
                             goal=goal, role="lead" if not parent_id else "worker")
    evidence = ExecutionEvidence(record, brief="\n".join(brief or []))
    return Stage(path=f"execution:{evidence.record.execution_id}", label="execution",
                 snap=Snapshot(workers=workers or []), execution_id=evidence.record.execution_id,
                 root_id=evidence.record.root_id, parent_id=evidence.record.parent_id,
                 account=evidence.record.account, lead_kind=evidence.record.harness,
                 lead_state="live" if live else "gone", live=int(live), goal=evidence.record.goal,
                 brief_text=evidence.brief.splitlines(), native_id=evidence.record.native_id)


class TestAttachExecutions(unittest.TestCase):
    def test_station_execution_keys_use_native_harness_and_account(self) -> None:
        cases = (("claude", "claude", "personal"),
                 ("claude work", "claude", "work"),
                 ("codex", "codex", "personal"),
                 ("t3 code", "codex", "personal"),
                 ("opencode", "opencode", "personal"),
                 ("cursor", "cursor", "personal"),
                 ("openclaw", "openclaw", "openclaw"))
        for kind, harness, account in cases:
            with self.subTest(kind=kind):
                self.assertEqual(collect._co_station_exec_key(_lead("S", kind)),
                                 execution_key(harness, account, "S"))
        self.assertEqual(collect._co_station_exec_key(_lead("", "claude")), "")
        self.assertEqual(collect._co_station_exec_key(_lead("S", "claude bg")), "")

    def test_attaches_personal_claude_subagents_to_live_lead(self) -> None:
        lead = _lead("S")
        root = execution_key("claude", "personal", "S")
        child = _execution("claude", "personal", "agent-1", root_id=root,
                           workers=[_worker("a"), _worker("b", start=20)])

        stages = collect._co_attach_executions([lead, child])

        self.assertEqual(stages, [lead])
        self.assertEqual([w.name for w in lead.snap.workers], ["a", "b"])

    def test_work_claude_attaches_only_work_account_executions(self) -> None:
        lead = _lead("S", "claude work")
        work = _execution("claude", "work", "work-agent",
                          root_id=execution_key("claude", "work", "S"), workers=[_worker("work")])
        personal = _execution("claude", "personal", "personal-agent",
                              root_id=execution_key("claude", "personal", "S"), workers=[_worker("personal")])

        stages = collect._co_attach_executions([lead, work, personal])

        self.assertIn("work", [w.name for w in lead.snap.workers])
        self.assertNotIn("personal", [w.name for w in lead.snap.workers])
        self.assertIn(personal, stages)
        self.assertEqual(personal.tag, "CC·P")

    def test_duplicate_lead_execution_fills_missing_goal_and_is_dropped(self) -> None:
        key = execution_key("claude", "personal", "S")
        lead = _lead("S")
        duplicate = _execution("claude", "personal", "S", root_id=key,
                               workers=[_worker("subagent")], goal="Build feature",
                               brief=["Implement feature"])

        stages = collect._co_attach_executions([lead, duplicate])

        self.assertEqual(stages, [lead])
        self.assertEqual(lead.goal, "Build feature")
        self.assertEqual(lead.brief_text, ["Implement feature"])
        self.assertEqual([w.name for w in lead.snap.workers], ["subagent"])

    def test_remaining_execution_gets_harness_tag(self) -> None:
        row = _execution("opencode", "personal", "unknown-root", root_id="unknown")

        stages = collect._co_attach_executions([row])

        self.assertEqual(stages, [row])
        self.assertEqual(row.tag, "OCode")
        self.assertEqual(row.lead_kind, "opencode")

    def test_missing_harness_is_recovered_from_native_execution_key(self) -> None:
        row = _execution("codex", "personal", "unknown-root", root_id="unknown")
        row.lead_kind = ""

        collect._co_attach_executions([row])

        self.assertEqual(row.tag, "CDX")
        self.assertEqual(row.lead_kind, "codex")

    def test_synthetic_root_inherits_member_identity_for_leftover_tag(self) -> None:
        root_key = execution_key("claude", "personal", "lead")
        record = ExecutionRecord(execution_key("claude", "personal", "agent"), harness="claude",
                                 account="personal", native_id="agent", role="worker",
                                 parent_id=root_key, cwd="/project")
        collector = collect.GlobalCollector([], quota=False)
        collector._co_proc_table = dict

        rows = collector._co_execution_stages([ExecutionEvidence(record)], 100.0, True)
        rows = collect._co_attach_executions(rows)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].execution_id, root_key)
        self.assertEqual(rows[0].tag, "CC·P")
        self.assertEqual(rows[0].lead_kind, "claude")
        self.assertEqual(rows[0].account, "personal")

    def test_empty_session_id_never_attaches(self) -> None:
        lead = _lead("")
        row = _execution("claude", "personal", "agent",
                         root_id=execution_key("claude", "personal", ""), workers=[_worker("child")])

        stages = collect._co_attach_executions([lead, row])

        self.assertIn(row, stages)
        self.assertEqual(lead.snap.workers, [])

    def test_attachment_recomputes_live_count_solo_activity_and_groups(self) -> None:
        lead = _lead("S")
        row = _execution("claude", "personal", "agent",
                         root_id=execution_key("claude", "personal", "S"),
                         workers=[_worker("running", start=15), _worker("done", state="done", start=30)])

        stages = collect._co_attach_executions([lead, row])

        self.assertEqual(stages, [lead])
        self.assertEqual(lead.live, 1)
        self.assertFalse(lead.solo)
        self.assertEqual(lead.activity, 30)
        self.assertEqual(lead.last_activity, 30)
        self.assertEqual(lead.snap.groups, collect._co_groups(lead.snap.workers, []))


if __name__ == "__main__":
    unittest.main()


class TestRunRecordMerge(unittest.TestCase):
    def test_native_row_named_by_a_run_record_is_the_stage_worker_not_a_second_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "review.run.json"), "w", encoding="utf-8") as fh:
                json.dump({"schema": 1, "name": "review", "harness": "opencode", "native_id": "ses_known"}, fh)
            station = Stage(path=tmp, label="proj", snap=Snapshot(workers=[_worker("review", state="done")]), dir=tmp)
            native = _worker("Reviewing the diff", state="noreport")
            native.native_id = "ses_known"
            other = _worker("stranger", state="noreport")
            other.native_id = "ses_other"
            execution = _execution("opencode", "personal", "ses_known", workers=[native, other])
            alone = _execution("opencode", "personal", "ses_solo", workers=[])

            stages = collect._co_attach_executions([station, execution, alone])

            self.assertEqual([w.name for w in station.snap.workers], ["review"])
            self.assertEqual(station.snap.workers[0].native_id, "ses_known")
            self.assertIn(execution, stages)                       # the unrelated native row is still shown
            self.assertEqual([w.name for w in execution.snap.workers], ["stranger"])


class TestOwnNativeRow(unittest.TestCase):
    def _stage(self, native_start: float, native_family: str = "claude") -> Stage:
        parent = Worker(name="probe", state="running", start=100.0, pid=500, family="claude")
        child = Worker(name="claude-503", state="running", start=native_start, pid=503, family=native_family,
                       execution_id="exec1", native_id="nat1", ancestors=(500,), launched=100.0)
        return Stage(path="s", label="s", snap=Snapshot(workers=[parent, child]), dir="/x")

    def test_running_worker_and_its_own_native_session_are_one_row(self) -> None:
        stage = self._stage(104.0)
        collect._co_nest_workers([stage], {})
        self.assertEqual([w.name for w in stage.snap.workers], ["probe"])
        self.assertEqual(stage.snap.workers[0].native_id, "nat1")

    def test_a_later_subagent_stays_a_separate_child(self) -> None:
        stage = self._stage(160.0)
        collect._co_nest_workers([stage], {})
        self.assertEqual(sorted(w.name for w in stage.snap.workers), ["claude-503", "probe"])

    def test_process_row_of_a_running_worker_merges_too(self) -> None:
        parent = Worker(name="probe", state="running", start=100.0, pid=500, family="codex")
        child = Worker(name="codex-503", state="running", start=102.0, pid=503, family="codex", model="GPT-6 Luna")
        stage = Stage(path="s", label="s", snap=Snapshot(workers=[parent, child]), dir="/x")
        stage.snap.workers[1].ancestors = (500,)
        collect._co_nest_workers([stage], {})
        self.assertEqual([w.name for w in stage.snap.workers], ["probe"])
        self.assertEqual(stage.snap.workers[0].model, "GPT-6 Luna")


class TestNestedStagePids(unittest.TestCase):
    def test_process_below_a_nested_stage_worker_is_already_listed(self) -> None:
        procs = {500: (1, 0, 0, 0, 0), 503: (500, 0, 0, 0, 0), 900: (1, 0, 0, 0, 0)}
        self.assertTrue(collect._co_ancestor_in(503, {500}, procs))
        self.assertFalse(collect._co_ancestor_in(900, {500}, procs))
        self.assertFalse(collect._co_ancestor_in(503, set(), procs))

    def test_pids_come_from_pid_files_one_level_down(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "review"))
            with open(os.path.join(tmp, "review", "w1.pid"), "w", encoding="utf-8") as fh:
                fh.write("4242\n")
            with open(os.path.join(tmp, "own.pid"), "w", encoding="utf-8") as fh:
                fh.write("1\n")
            self.assertEqual(collect._co_nested_stage_pids(tmp), {4242})


class TestSubagentDescription(unittest.TestCase):
    def test_title_comes_from_the_meta_json_beside_the_transcript(self) -> None:
        from pathlib import Path

        import native
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "agent-abc.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            self.assertEqual(native._nv_subagent_description(transcript), "")
            (Path(tmp) / "agent-abc.meta.json").write_text(json.dumps({"description": "  Read subagent one files "}), encoding="utf-8")
            self.assertEqual(native._nv_subagent_description(transcript), "Read subagent one files")
