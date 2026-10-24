from __future__ import annotations

import collections
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import collect
import logparse
import native
from model import ExecutionEvidence, ExecutionRecord

DEAD_PID = 4194304  # beyond a normal pid_max, so /proc never has it
LIVE_PID = 4000001  # reported alive only by the mocked liveness check
FAKE_PID = 4000002  # a lead whose /proc entry does not exist: only its session file says anything
NOW = 1_800_000_000.0
RECENT = NOW - 60


def write(path: str, text: str = "", mtime: float | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def add_worker(stage: str, mtime: float, name: str = "w", pid: int = DEAD_PID) -> None:
    os.makedirs(os.path.join(stage, name), exist_ok=True)
    write(os.path.join(stage, name + ".pid"), str(pid), mtime)
    write(os.path.join(stage, name + ".log"), "", mtime)


def only_live(pid: int, *_rest: object) -> bool:
    return pid == LIVE_PID


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.ledger = os.path.join(home.name, "runs.jsonl")  # never the real ledger
        patch = mock.patch.object(collect, "_co_orphan_leads", return_value={})
        patch.start()
        self.addCleanup(patch.stop)
        cache = mock.patch.dict(collect._co_native_cache, clear=True)
        cache.start()
        self.addCleanup(cache.stop)

    def make(self) -> collect.GlobalCollector:
        g = collect.GlobalCollector([self.root], ledger=self.ledger, quota=False)
        self.addCleanup(g.close)
        return g


class UnattachedTest(Base):
    def test_unattached_stage_is_flagged_and_counted(self) -> None:
        stage = os.path.join(self.root, "proj", ".stage")
        add_worker(stage, NOW - 100, name="attached")
        orphan = ExecutionEvidence(ExecutionRecord("orphan-1", harness="claude", started=RECENT, activity=RECENT,
                                                   root_id="unattached:global"))
        with mock.patch.object(collect.GlobalCollector, "_co_executions", return_value=[orphan]):
            snap = self.make().snapshot(now=NOW)
        flagged = [s for s in snap.stages if s.unattached]
        self.assertEqual([s.label for s in flagged], ["unattached"])
        self.assertEqual(len(flagged[0].snap.workers), 1)
        self.assertEqual(snap.unattached, 1)
        self.assertFalse(any(s.unattached for s in snap.stages if s.label != "unattached"))


class SessionNameTest(Base):
    def snapshot(self, g: collect.GlobalCollector) -> dict[str, object]:
        with mock.patch.object(collect, "_co_pid_live", side_effect=only_live):
            return {s.session_id: s for s in g.snapshot(now=NOW).stages}

    def test_non_lead_row_name_is_stable_across_sibling_changes(self) -> None:
        stage = os.path.join(self.root, "proj", ".stage")
        add_worker(stage, NOW - 900, name="old-a")
        add_worker(stage, NOW - 100, name="new-b")
        write(os.path.join(stage, "old-a.session"), json.dumps({"lead_session": "sess-old-0001"}))
        write(os.path.join(stage, "new-b.session"), json.dumps({"lead_session": "sess-new-0002"}))
        g = self.make()
        first = self.snapshot(g)
        add_worker(stage, NOW - 50, name="newer-c")
        write(os.path.join(stage, "newer-c.session"), json.dumps({"lead_session": "sess-zzz-0003"}))
        second = self.snapshot(g)
        name = first["sess-new-0002"].session_name
        self.assertTrue(name)
        self.assertEqual(name, "sess-new-0002"[-8:])
        self.assertEqual(second["sess-new-0002"].session_name, name)


class LeadFixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = tmp.name
        self.stage = os.path.join(tmp.name, "stage")
        environ = mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": self.config})
        environ.start()
        self.addCleanup(environ.stop)
        self.cwd = "/work/proj"
        self.transcript = os.path.join(self.config, "projects", "-work-proj", "sess-abc123.jsonl")

    def session_file(self) -> None:
        write(os.path.join(self.config, "sessions", f"{FAKE_PID}.json"),
              json.dumps({"sessionId": "sess-abc123", "cwd": self.cwd}))


class LeadDoingTest(LeadFixture):
    def test_last_user_prompt_stands_in_when_no_tool_or_reply(self) -> None:
        self.session_file()
        line = {"type": "user", "timestamp": "2026-10-24T10:00:00+00:00",
                "message": {"role": "user", "content": "fix the parser\nthanks"}}
        write(self.transcript, json.dumps(line) + "\n")
        events, note = collect._co_lead_doing_note(FAKE_PID, "claude")
        self.assertEqual([text for _, text in events], ["asks: fix the parser"])
        self.assertEqual(note, "")

    def test_no_session_file_is_the_note(self) -> None:
        self.assertEqual(collect._co_lead_doing_note(FAKE_PID, "claude"), ([], "no session file"))

    def test_session_without_transcript_is_the_note(self) -> None:
        self.session_file()
        self.assertEqual(collect._co_lead_doing_note(FAKE_PID, "claude"), ([], "no transcript"))


class SyntheticModelTest(LeadFixture):
    def test_lead_meta_keeps_last_real_model_over_placeholder(self) -> None:
        self.session_file()
        real = {"type": "assistant", "timestamp": "2026-10-24T10:00:00+00:00",
                "message": {"role": "assistant", "model": "claude-sonnet-x", "content": []}}
        placeholder = {"type": "assistant", "timestamp": "2026-10-24T10:01:00+00:00",
                       "message": {"role": "assistant", "model": "<synthetic>", "content": []}}
        write(self.transcript, "\n".join(json.dumps(line) for line in (real, placeholder)) + "\n")
        lead = collect._co_Lead(FAKE_PID, "claude", "live", self.cwd, NOW - 3600)
        with mock.patch.object(collect, "_co_argv_name", return_value=""):
            got = collect._co_lead_session(self.stage, lead)
        self.assertEqual(got["lead_model"], "claude-sonnet-x")
        self.assertNotIn("<synthetic>", got.values())

    def test_native_record_keeps_model_over_placeholder(self) -> None:
        st = collections.defaultdict(str, record=ExecutionRecord("e1", harness="claude", model="claude-sonnet-x"))
        obj = {"type": "assistant", "timestamp": "2026-10-24T10:00:00Z",
               "message": {"role": "assistant", "model": "<synthetic>", "content": "API Error"}}
        native._nv_apply(st, obj, "claude", "", Path(self.transcript))
        self.assertEqual(st["record"].model, "claude-sonnet-x")

    def test_logparse_never_takes_placeholder_as_model(self) -> None:
        state = types.SimpleNamespace(model="")
        logparse._lp_set_model(state, "<synthetic>")
        self.assertEqual(state.model, "")
        logparse._lp_set_model(state, "gpt-x")
        self.assertEqual(state.model, "gpt-x")


if __name__ == "__main__":
    unittest.main()
