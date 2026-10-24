from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

import gitstate
from model import Stage

NOW = 1_760_050_000.0


def _git(cwd: str, *args: str) -> None:
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


class WorkStateTest(unittest.TestCase):
    def setUp(self) -> None:
        gitstate._gs_cache.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.repo = os.path.join(self.root, "proj")
        os.mkdir(self.repo)
        _git(self.repo, "-c", "init.defaultBranch=main", "init", "-q")
        self.write("a.txt", "one\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "first")
        self.clock = NOW

    def write(self, name: str, text: str) -> None:
        full = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as handle:
            handle.write(text)

    def state(self):
        """Each call is a minute later than the last, so the 30 s cache never answers."""
        self.clock += 60
        return gitstate.work_state(self.repo, self.clock)

    def test_clean_repo_has_no_badge(self) -> None:
        state = self.state()
        self.assertEqual(state.branch, "main")
        self.assertIs(state.dirty, False)
        self.assertIsNone(state.ahead)
        self.assertIsNone(state.unmerged)
        self.assertEqual(state.badge(), "")

    def test_dirty_tracked_file_is_uncommitted(self) -> None:
        self.write("a.txt", "two\n")
        state = self.state()
        self.assertIs(state.dirty, True)
        self.assertEqual(state.badge(), "uncommitted")

    def test_untracked_stage_and_session_files_are_ignored(self) -> None:
        self.write(".stage/notes.md", "x\n")
        self.write(".sessions/2026/state.md", "x\n")
        state = self.state()
        self.assertIs(state.dirty, False)
        self.assertEqual(state.badge(), "")

    def test_an_untracked_source_file_is_uncommitted(self) -> None:
        self.write("new.py", "print('hi')\n")
        state = self.state()
        self.assertIs(state.dirty, True)
        self.assertEqual(state.badge(), "uncommitted")

    def test_untracked_stage_files_below_a_tracked_dir_are_ignored(self) -> None:
        self.write("pkg/keep.txt", "tracked\n")
        _git(self.repo, "add", "pkg/keep.txt")
        _git(self.repo, "commit", "-q", "-m", "add pkg")
        self.write("pkg/.stage/notes.md", "x\n")
        self.write("pkg/.sessions/s/state.md", "x\n")
        self.assertIs(self.state().dirty, False)

    def test_feature_branch_ahead_of_main_without_upstream_is_unmerged(self) -> None:
        _git(self.repo, "checkout", "-q", "-b", "feature")
        self.write("b.txt", "feature\n")
        _git(self.repo, "add", "b.txt")
        _git(self.repo, "commit", "-q", "-m", "feature work")
        state = self.state()
        self.assertEqual(state.branch, "feature")
        self.assertIsNone(state.ahead)
        self.assertIs(state.unmerged, True)
        self.assertEqual(state.badge(), "unmerged")

    def test_merged_branch_is_merged(self) -> None:
        _git(self.repo, "checkout", "-q", "-b", "done")
        state = self.state()
        self.assertIs(state.unmerged, False)
        self.assertEqual(state.badge(), "merged")

    def test_commits_not_on_upstream_are_unpushed(self) -> None:
        remote = os.path.join(self.root, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True, capture_output=True)
        _git(self.repo, "remote", "add", "origin", remote)
        _git(self.repo, "push", "-q", "-u", "origin", "main")
        self.write("c.txt", "new\n")
        _git(self.repo, "add", "c.txt")
        _git(self.repo, "commit", "-q", "-m", "local only")
        state = self.state()
        self.assertEqual(state.ahead, 1)
        self.assertIs(state.unmerged, None)
        self.assertEqual(state.badge(), "1 unpushed")

    def test_path_outside_a_repo_is_none(self) -> None:
        plain = os.path.join(self.root, "plain")
        os.mkdir(plain)
        self.assertIsNone(gitstate.work_state(plain, NOW))
        self.assertIsNone(gitstate.work_state("", NOW))

    def test_cache_answers_within_ttl_without_git(self) -> None:
        with mock.patch.object(gitstate.subprocess, "run", wraps=subprocess.run) as run:
            first = gitstate.work_state(self.repo, NOW)
            self.assertGreater(run.call_count, 0)
            run.reset_mock()
            second = gitstate.work_state(self.repo, NOW + 10)
            self.assertEqual(run.call_count, 0)
        self.assertIs(first, second)

    def test_cache_expires_after_30_seconds(self) -> None:
        gitstate.work_state(self.repo, NOW)
        with mock.patch.object(gitstate.subprocess, "run", wraps=subprocess.run) as run:
            gitstate.work_state(self.repo, NOW + 31)
            self.assertGreater(run.call_count, 0)

    def test_moved_branch_ref_recomputes_within_ttl(self) -> None:
        self.assertEqual(gitstate.work_state(self.repo, NOW).branch, "main")
        _git(self.repo, "checkout", "-q", "-b", "feature")
        self.write("b.txt", "feature\n")
        _git(self.repo, "add", "b.txt")
        _git(self.repo, "commit", "-q", "-m", "feature work")
        state = gitstate.work_state(self.repo, NOW + 1)
        self.assertEqual(state.branch, "feature")
        self.assertEqual(state.badge(), "unmerged")

    def test_stage_dir_choice(self) -> None:
        self.assertEqual(gitstate._gs_dir(Stage(path="/x/.stage", label="x", cwd=self.repo)), self.repo)
        self.assertEqual(gitstate._gs_dir(Stage(path="/x/.stage", label="x", dir=self.repo)), self.repo)
        self.assertEqual(gitstate._gs_dir(Stage(path=os.path.join(self.repo, ".stage"), label="x")), self.repo)
        self.assertEqual(gitstate._gs_dir(Stage(path="/no/such/dir", label="x")), "")

    def test_attach_work_sets_idle_and_closed_only(self) -> None:
        idle = Stage(path=self.repo, label="idle", cwd=self.repo, state="idle")
        closed = Stage(path=self.repo, label="closed", cwd=self.repo, state="closed")
        active = Stage(path=self.repo, label="active", cwd=self.repo, state="active")
        gitstate.attach_work([idle, closed, active], NOW)
        self.assertEqual(idle.work.badge(), "")
        self.assertEqual(closed.work.badge(), "")
        self.assertIsNone(active.work)

    def test_attach_work_caps_git_calls_and_keeps_cached_stages(self) -> None:
        stage = Stage(path=self.repo, label="idle", cwd=self.repo, state="idle")
        with mock.patch.object(gitstate, "_GS_MAX_CALLS", 3), \
                mock.patch.object(gitstate.subprocess, "run", wraps=subprocess.run) as run:
            gitstate.attach_work([stage], NOW)
            self.assertIsNone(stage.work)
            self.assertLessEqual(run.call_count, 3)
        gitstate.attach_work([stage], NOW)
        self.assertIsNotNone(stage.work)
        other = Stage(path=self.repo, label="again", cwd=self.repo, state="idle")
        with mock.patch.object(gitstate, "_GS_MAX_CALLS", 0), \
                mock.patch.object(gitstate.subprocess, "run", wraps=subprocess.run) as run:
            gitstate.attach_work([other], NOW + 1)
            self.assertEqual(run.call_count, 0)
        self.assertEqual(other.work.badge(), "")

    def test_a_snapshot_out_of_wall_clock_keeps_the_stale_answer(self) -> None:
        fresh = gitstate.work_state(self.repo, NOW)
        slow = _SlowGit()
        with mock.patch.object(gitstate.time, "monotonic", side_effect=slow.clock), \
                mock.patch.object(gitstate.subprocess, "run", slow):
            stale = gitstate.work_state(self.repo, NOW + 60)   # the 30 s cache has expired, the budget has not survived
        self.assertIs(stale, fresh)
        self.assertEqual(slow.calls, 2)                        # rev-parse and the branch, then the deadline stops it
        stage = Stage(path=self.repo, label="idle", cwd=self.repo, state="idle")
        slow = _SlowGit()
        with mock.patch.object(gitstate.time, "monotonic", side_effect=slow.clock), \
                mock.patch.object(gitstate.subprocess, "run", slow):
            gitstate.attach_work([stage], NOW + 120)
        self.assertIs(stage.work, fresh)

    def test_two_stages_in_one_worktree_cost_one_read(self) -> None:
        sub = os.path.join(self.repo, "sub")
        os.makedirs(sub)
        root_stage = Stage(path=self.repo, label="root", cwd=self.repo, state="idle")
        sub_stage = Stage(path=sub, label="sub", cwd=sub, state="idle")
        with mock.patch.object(gitstate.subprocess, "run", wraps=subprocess.run) as run:
            gitstate.attach_work([root_stage, sub_stage], NOW)
        self.assertLessEqual(run.call_count, 6)      # five for the worktree, one to recognise the second path
        self.assertIs(root_stage.work, sub_stage.work)
        self.assertIs(sub_stage.work.dirty, False)


class _SlowGit:
    """A subprocess.run stand-in that spends a whole second of the snapshot's wall clock per call."""

    def __init__(self) -> None:
        self.now = 0.0
        self.calls = 0

    def clock(self) -> float:
        return self.now

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls += 1
        self.now += 1.0
        stdout = "/worktree/.git\n" if command[3:5] == ["rev-parse", "--absolute-git-dir"] else ""
        return subprocess.CompletedProcess(command, 0, stdout, "")


if __name__ == "__main__":
    unittest.main()
