"""Ops retention: the 24 h age decision runs after work state, so a dirty old execution root stays listed."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

import collect
from model import ExecutionEvidence, ExecutionRecord, Stage

NOW = 1_800_000_000.0
DAY = 86400


def git(repo: str, *args: str) -> None:
    subprocess.run(["git", "-C", repo, "-c", "user.email=a@b.c", "-c", "user.name=test", *args],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)


def repo(root: str, *, dirty: bool) -> str:
    """A real worktree: one committed file, then (optionally) an uncommitted change on top of it."""
    path = os.path.join(root, "dirty" if dirty else "clean")
    os.makedirs(path)
    git(path, "init", "-q")
    with open(os.path.join(path, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("one\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-q", "-m", "one")
    if dirty:
        with open(os.path.join(path, "a.txt"), "a", encoding="utf-8") as fh:
            fh.write("two\n")
    return path


class DirtyWorkRetention(unittest.TestCase):
    """2. A three-day-old root is discovered, then kept or dropped by the ops window from its work state."""

    def stage_for(self, cwd: str) -> Stage:
        g = collect.GlobalCollector([], quota=False)
        self.addCleanup(g.close)
        g._co_proc_table = dict
        root = ExecutionRecord("root", harness="claude", account="personal", role="lead", cwd=cwd,
                               started=NOW - 3 * DAY, activity=NOW - 3 * DAY, state="finished")
        with mock.patch.object(collect, "_co_read_text", return_value="boot\n"):
            stages = g._co_execution_stages([ExecutionEvidence(root)], NOW, False)
        self.assertEqual([stage.execution_id for stage in stages], ["root"], "the early filter only cost-bounds")
        return stages[0]

    def retained(self, stage: Stage) -> list[Stage]:
        stages = [stage]
        collect.attach_work(stages, NOW)
        collect._co_ops_windows(stages, NOW)
        return stages

    def test_a_three_day_old_execution_root_with_dirty_work_is_still_listed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stage = self.stage_for(repo(root, dirty=True))
            self.assertEqual(self.retained(stage), [stage])
            self.assertEqual(stage.work.badge() if stage.work is not None else "", "uncommitted")

    def test_a_clean_three_day_old_execution_root_is_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stage = self.stage_for(repo(root, dirty=False))
            self.assertEqual(self.retained(stage), [])

    def test_the_public_snapshot_lists_dirty_work_and_hides_the_clean_root(self) -> None:
        """The whole path: discovery, attach_work, then the ops window, with the real GlobalCollector."""
        with tempfile.TemporaryDirectory() as root:
            dirty, clean = repo(root, dirty=True), repo(root, dirty=False)
            items = [ExecutionEvidence(ExecutionRecord(
                "dirty", harness="claude", account="personal", role="lead", cwd=dirty,
                started=NOW - 3 * DAY, activity=NOW - 3 * DAY, state="finished")),
                     ExecutionEvidence(ExecutionRecord(
                         "clean", harness="claude", account="personal", role="lead", cwd=clean,
                         started=NOW - 3 * DAY, activity=NOW - 3 * DAY, state="finished"))]
            g = collect.GlobalCollector([root], quota=False)
            self.addCleanup(g.close)
            g._co_proc_table = dict
            with mock.patch.object(g, "_co_executions", return_value=items), \
                    mock.patch.object(collect, "_co_orphan_leads", return_value={}):
                snap = g.snapshot(now=NOW)
        self.assertEqual([stage.execution_id for stage in snap.stages if stage.execution_id], ["dirty"])


if __name__ == "__main__":
    unittest.main()
