"""OpenClaw background jobs belong to their recorded requester session."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import collect

NOW = 1_800_000_000.0
MAIN = "agent:openclaw:main"
CHILD = "agent:openclaw:subagent:child"


class OpenClawJobs(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        agent_db = self.home / ".openclaw/agents/openclaw/agent/openclaw-agent.sqlite"
        agent_db.parent.mkdir(parents=True)
        self.agent = sqlite3.connect(agent_db)
        self.addCleanup(self.agent.close)
        self.agent.execute("CREATE TABLE session_nodes (session_key TEXT, display_name TEXT, label TEXT, "
                           "last_activity_at INTEGER, updated_at INTEGER, created_at INTEGER, entry_json TEXT)")
        state_db = self.home / ".openclaw/state/openclaw.sqlite"
        state_db.parent.mkdir(parents=True)
        self.state = sqlite3.connect(state_db)
        self.addCleanup(self.state.close)
        self.state.execute("CREATE TABLE task_runs (task_id TEXT, runtime TEXT, task_kind TEXT, "
                           "requester_session_key TEXT, child_session_key TEXT, parent_task_id TEXT, "
                           "agent_id TEXT, status TEXT, created_at INTEGER, started_at INTEGER, "
                           "ended_at INTEGER, last_event_at INTEGER)")

    def session(self, key: str, parent: str = "") -> None:
        stamp = int((NOW - 120) * 1000)
        entry = {"parentSessionKey": parent} if parent else {}
        self.agent.execute("INSERT INTO session_nodes VALUES (?,?,?,?,?,?,?)",
                           (key, "", key.rsplit(":", 1)[-1], stamp, stamp, stamp, json.dumps(entry)))
        self.agent.commit()

    def job(self, task_id: str, requester: str, status: str, age: int, *, parent_task: str = "",
            runtime: str = "cli", kind: str = "exec") -> None:
        stamp = int((NOW - age) * 1000)
        ended = stamp if status in ("succeeded", "failed") else None
        self.state.execute("INSERT INTO task_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (task_id, runtime, kind, requester, None, parent_task, "openclaw", status,
                            stamp - 10_000, stamp - 8_000, ended, stamp))
        self.state.commit()

    def rows(self) -> dict[str, collect.Stage]:
        stages = collect._co_openclaw_stages(NOW, False, str(self.home))
        collect._co_nest_workers(stages, {})
        return {s.session_id: s for s in stages}

    def test_linked_jobs_have_live_done_and_failed_states(self) -> None:
        self.session(MAIN)
        self.job("live", MAIN, "running", 5)
        self.job("ok", MAIN, "succeeded", 20)
        self.job("bad", MAIN, "failed", 30)
        row = self.rows()[MAIN]
        self.assertEqual({w.native_id: w.state for w in row.snap.workers},
                         {"live": "running", "ok": "done", "bad": "failed"})
        self.assertEqual((row.live, row.solo), (1, False))

    def test_job_requested_by_subagent_nests_under_that_worker(self) -> None:
        self.session(MAIN)
        self.session(CHILD, MAIN)
        grandchild = "agent:openclaw:subagent:grandchild"
        self.session(grandchild, CHILD)
        self.job("nested", grandchild, "succeeded", 20)
        row = self.rows()[MAIN]
        by_id = {w.native_id: w for w in row.snap.workers}
        self.assertEqual((by_id[grandchild].parent, by_id[grandchild].level), (by_id[CHILD].name, 1))
        self.assertEqual((by_id["nested"].parent, by_id["nested"].level), (by_id[grandchild].name, 2))

    def test_job_makes_its_recorded_one_off_parent_a_row(self) -> None:
        parent = "agent:openclaw:one-off-run"
        self.session(parent)
        self.job("run-job", parent, "succeeded", 20)
        self.assertEqual([w.native_id for w in self.rows()[parent].snap.workers], ["run-job"])

    def test_no_requester_link_does_not_attach_by_time(self) -> None:
        self.session(MAIN)
        self.job("unknown", "", "running", 5)
        self.job("elsewhere", "agent:openclaw:other", "running", 5)
        self.job("own-session", MAIN, "running", 5, kind="session")
        self.assertEqual(self.rows()[MAIN].snap.workers, [])
