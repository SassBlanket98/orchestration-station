"""What a worker has changed: the change summary in the overview's worker detail, and the Files page it links to."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import app
import collect
from model import (
    RUNNING,
    GlobalSnapshot,
    LogState,
    Snapshot,
    Stage,
    Step,
    UIState,
    Worker,
)
from render import global_rows, render_frame, render_global
from widgets import _wd_SGR_RE

NOW = 1_800_000_000.0


def plain(lines: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", ln).rstrip() for ln in lines]


class WorkerChangesCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="changes-test-")
        self.project = os.path.join(self.tmp, "proj")
        self.stage = os.path.join(self.tmp, ".stage")
        os.makedirs(self.project)
        os.makedirs(os.path.join(self.stage, "briefs"))
        collect._co_changes_cache.clear()

    def tearDown(self) -> None:
        collect._co_changes_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", self.project, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       check=True, capture_output=True)

    def init_repo(self) -> None:
        self.git("init", "-q")
        with open(os.path.join(self.project, "tracked.txt"), "w") as fh:
            fh.write("one\ntwo\nthree\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-q", "-m", "init")

    def brief(self, name: str, job: str = "build", repo: bool = True) -> None:
        cwd = f"Repo/worktree: {self.project} (branch main)" if repo else "Repo: none"
        with open(os.path.join(self.stage, "briefs", name + ".md"), "w") as fh:
            fh.write(f"# {name}\nJob: {job}\n{cwd}\nGoal: x\n")


class WorkerChangesTest(WorkerChangesCase):
    def test_counts_modified_tracked_lines_and_untracked_files(self) -> None:
        self.init_repo()
        with open(os.path.join(self.project, "tracked.txt"), "a") as fh:
            fh.write("four\nfive\n")
        with open(os.path.join(self.project, "new.txt"), "w") as fh:
            fh.write("x\n" * 50)
        self.brief("d-live")
        changes = collect.worker_changes(self.stage, Worker("d-live", RUNNING, job="build"), NOW)
        self.assertIsNotNone(changes)
        self.assertEqual((changes.files, changes.added, changes.removed), (2, 2, 0))   # untracked: a file, not lines
        self.assertEqual(changes.scope, "git")
        self.assertFalse(changes.read_only)
        self.assertIsNotNone(changes.last_edit)
        self.assertGreater(changes.last_edit, 0)

    def test_numstat_removed_lines_are_counted(self) -> None:
        self.init_repo()
        with open(os.path.join(self.project, "tracked.txt"), "w") as fh:
            fh.write("one\n")
        self.brief("d-live")
        changes = collect.worker_changes(self.stage, Worker("d-live", RUNNING, job="build"), NOW)
        self.assertEqual((changes.files, changes.added, changes.removed), (1, 0, 2))

    def test_clean_repo_has_no_files(self) -> None:
        self.init_repo()
        self.brief("d-live")
        changes = collect.worker_changes(self.stage, Worker("d-live", RUNNING, job="build"), NOW)
        self.assertEqual((changes.files, changes.last_edit), (0, None))

    def test_folder_scope_counts_the_stage_folder_when_there_is_no_git_dir(self) -> None:
        folder = os.path.join(self.stage, "d-folder")
        os.makedirs(folder)
        with open(os.path.join(folder, "REPORT.md"), "w") as fh:
            fh.write("## Summary\n")
        self.brief("d-folder", repo=False)
        changes = collect.worker_changes(self.stage, Worker("d-folder", RUNNING, job="build"), NOW)
        self.assertEqual((changes.files, changes.added, changes.removed, changes.scope), (1, 0, 0, "folder"))
        self.assertIsNotNone(changes.last_edit)

    def test_read_only_worker_is_flagged_and_runs_no_git(self) -> None:
        self.init_repo()
        self.brief("d-plan", job="plan")
        with mock.patch.object(collect, "_co_git") as git:
            by_job = collect.worker_changes(self.stage, Worker("d-plan", RUNNING, job="plan"), NOW)
            by_review = collect.worker_changes(self.stage, Worker("d-rev", RUNNING, is_review=True, job="build"), NOW)
        git.assert_not_called()
        self.assertTrue(by_job.read_only)
        self.assertTrue(by_review.read_only)

    def test_second_call_within_five_seconds_does_not_run_git(self) -> None:
        self.init_repo()
        self.brief("d-live")
        worker = Worker("d-live", RUNNING, job="build")
        with mock.patch.object(collect, "_co_git", wraps=collect._co_git) as git:
            collect.worker_changes(self.stage, worker, NOW)
            first = git.call_count
            collect.worker_changes(self.stage, worker, NOW + 1)
            self.assertGreater(first, 0)
            self.assertEqual(git.call_count, first)
            with mock.patch.object(collect.time, "monotonic", return_value=collect.time.monotonic() + 6):
                collect.worker_changes(self.stage, worker, NOW + 7)
            self.assertGreater(git.call_count, first)

    def test_git_that_does_not_answer_gives_none(self) -> None:
        self.init_repo()
        self.brief("d-live")
        with mock.patch.object(collect, "_co_git", return_value=(None, "")):
            self.assertIsNone(collect.worker_changes(self.stage, Worker("d-live", RUNNING, job="build"), NOW))

    def test_any_error_gives_none_and_never_raises(self) -> None:
        with mock.patch.object(collect, "_co_brief_path", side_effect=OSError("boom")):
            self.assertIsNone(collect.worker_changes(self.stage, Worker("d-x", RUNNING, job="build"), NOW))


class FilesPageRows(WorkerChangesCase):
    def test_rows_are_newest_first_with_status_counts_and_age(self) -> None:
        self.init_repo()
        with open(os.path.join(self.project, "tracked.txt"), "a") as fh:
            fh.write("four\n")
        new = os.path.join(self.project, "new.txt")
        with open(new, "w") as fh:
            fh.write("x\n")
        os.utime(new, (time.time() - 3600, time.time() - 3600))
        self.brief("d-live")
        text = plain(collect.files_page(self.stage, Worker("d-live", RUNNING, job="build")))
        rows = [ln for ln in text if ln.startswith("  ") and "|" not in ln and "changed" not in ln]
        self.assertEqual(rows[0].split()[:2], ["M", "tracked.txt"])   # the tracked edit is the newest
        self.assertIn("+1 −0", rows[0])
        self.assertEqual(rows[1].split()[:2], ["??", "new.txt"])
        self.assertIn("untracked", rows[1])
        self.assertIn("ago", rows[1])

    def test_read_only_worker_gets_one_line_saying_so(self) -> None:
        self.init_repo()
        self.brief("d-plan", job="plan")
        text = plain(collect.files_page(self.stage, Worker("d-plan", RUNNING, job="plan")))
        self.assertEqual(text.count("read-only worker: no file changes expected"), 1)

    def test_clean_repo_and_git_failure_messages_stay(self) -> None:
        self.init_repo()
        self.brief("d-live")
        text = "\n".join(plain(collect.files_page(self.stage, Worker("d-live", RUNNING, job="build"))))
        self.assertIn("  clean", text)
        with mock.patch.object(collect, "_co_git", return_value=(None, "")):
            text = "\n".join(collect.files_page(self.stage, Worker("d-live", RUNNING, job="build")))
        self.assertIn("git did not answer within 2 s", text)


class DetailLineRender(WorkerChangesCase):
    def frame(self, worker: Worker, opened: bool = True) -> tuple[list[str], Stage]:
        now = time.time()   # the file mtimes are real: the age on the line is measured from the real clock
        snap = Snapshot(now=now, workers=[worker])
        stage = Stage(self.stage, "proj", snap, last_activity=now)
        ui = UIState(gopen={(self.stage, "worker", worker.name)} if opened else set())
        lines = render_global(GlobalSnapshot(now=now, stages=[stage]), ui, 120, 38, now)
        return plain(lines), stage

    def test_live_worker_shows_counts_age_and_the_files_link(self) -> None:
        self.init_repo()
        with open(os.path.join(self.project, "tracked.txt"), "a") as fh:
            fh.write("four\n")
        self.brief("d-live")
        worker = Worker("d-live", RUNNING, job="build", start=time.time() - 240, quiet_s=3,
                        log=LogState(steps=[Step(time.time() - 3, "tool", "read", "src/app.py")]))
        lines, _ = self.frame(worker)
        line = next(ln for ln in lines if "changes" in ln)
        for value in ("1 file", "+1 −0", "last edit", "ago", "f files →"):
            self.assertIn(value, line)
        self.assertIn("output 3s ago", "\n".join(lines))

    def test_zero_changes_says_so(self) -> None:
        self.init_repo()
        self.brief("d-live")
        lines, _ = self.frame(Worker("d-live", RUNNING, job="build", start=NOW - 240))
        line = next(ln for ln in lines if "changes" in ln)
        self.assertIn("no changes yet", line)

    def test_read_only_worker_says_no_changes_are_expected(self) -> None:
        self.init_repo()
        self.brief("d-plan", job="plan")
        lines, _ = self.frame(Worker("d-plan", RUNNING, job="plan", start=NOW - 240))
        line = next(ln for ln in lines if "changes" in ln)
        self.assertIn("read-only: no file changes expected", line)

    def test_closed_worker_gets_no_changes_line_and_no_git(self) -> None:
        self.init_repo()
        self.brief("d-live")
        with mock.patch.object(collect, "_co_git") as git:
            lines, _ = self.frame(Worker("d-live", RUNNING, job="build", start=NOW - 240), opened=False)
        git.assert_not_called()
        self.assertFalse(any("changes" in ln for ln in lines))


class FilesKeyFromOverview(WorkerChangesCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_repo()
        with open(os.path.join(self.project, "tracked.txt"), "a") as fh:
            fh.write("four\n")
        self.brief("d-live")
        self.stage_obj = Stage(self.stage, "proj", Snapshot(now=NOW, workers=[
            Worker("d-live", RUNNING, job="build", start=NOW - 240),
            Worker("d-other", RUNNING, job="build", start=NOW - 120, log=LogState())]), last_activity=NOW)
        self.gs = GlobalSnapshot(now=NOW, stages=[self.stage_obj])
        self.ui = UIState(gopen={(self.stage, "worker", "d-live")})

    def select(self, name: str) -> None:
        rows = global_rows(self.gs, self.ui)
        self.ui.gsel = rows.index((self.stage, "worker", name))

    def test_f_on_a_worker_row_opens_its_files_page_and_esc_steps_back(self) -> None:
        render_global(self.gs, self.ui, 120, 38, NOW)
        self.select("d-live")
        self.assertEqual(app._ap_global_key(self.ui, "f", self.gs, 38), "load_page")
        self.assertEqual((self.ui.gpage, self.ui.view, self.ui.gstage), ("stage", "files", self.stage))
        app._ap_load_page(self.ui, self.stage_obj.dir or self.stage_obj.path, self.stage_obj, self.stage_obj.snap, NOW)
        page = "\n".join(plain(render_frame(self.stage_obj.snap, self.ui, 120, 38, NOW, self.stage_obj)))
        self.assertIn("changed files, newest edit first", page)
        self.assertIn("M   tracked.txt", page)

        app._ap_global_key(self.ui, "esc", self.gs, 38)
        self.assertEqual((self.ui.gpage, self.ui.view), ("stage", "overview"))   # the station's overview
        app._ap_global_key(self.ui, "esc", self.gs, 38)
        self.assertEqual(self.ui.gpage, "")                                     # then the overview tree

    def test_f_on_a_stage_row_still_cycles_the_filter(self) -> None:
        render_global(self.gs, self.ui, 120, 38, NOW)
        self.ui.gsel = 0
        app._ap_global_key(self.ui, "f", self.gs, 38)
        self.assertNotEqual(self.ui.gfilter, "all")
        self.assertEqual(self.ui.gpage, "")

    def test_clicking_the_changes_line_moves_to_that_worker_and_opens_its_files(self) -> None:
        lines = plain(render_global(self.gs, self.ui, 120, 38, NOW))
        self.select("d-other")
        y = next(i for i, ln in enumerate(lines) if "f files →" in ln)
        x = lines[y].index("f files →")
        keys = app._ap_pointer_keys(self.ui, f"click:{x + 2}:{y + 1}", self.gs, None, NOW)
        self.assertEqual(keys[-1], "f")
        for key in keys:
            app._ap_global_key(self.ui, key, self.gs, 38)
        self.assertEqual((self.ui.gpage, self.ui.view), ("stage", "files"))
        self.assertEqual(self.stage_obj.snap.workers[self.ui.sel].name, "d-live")


if __name__ == "__main__":
    unittest.main()


class StagedOnlyAndPathTests(unittest.TestCase):
    def test_a_staged_only_file_is_counted(self) -> None:
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True)
            run("init", "-q")
            run("config", "user.email", "t@t")
            run("config", "user.name", "t")
            with open(os.path.join(tmp, "a.txt"), "w") as fh:
                fh.write("one\n")
            run("add", "a.txt")
            run("commit", "-qm", "init")
            with open(os.path.join(tmp, "a.txt"), "w") as fh:
                fh.write("one\ntwo\nthree\n")
            run("add", "a.txt")      # staged, no further edit
            self.assertEqual(collect._co_numstat(tmp), {"a.txt": (2, 0)})

    def test_a_repo_without_commits_still_answers(self) -> None:
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "-C", tmp, "init", "-q"], check=True, capture_output=True)
            self.assertEqual(collect._co_numstat(tmp), {})
