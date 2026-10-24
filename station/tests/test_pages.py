"""Station pages (L lead, C cost, F files) and the collectors behind them."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import app
import collect
from model import RUNNING, GlobalSnapshot, LogState, Snapshot, Stage, UIState, Worker
from render import render_frame
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0


def plain(line: str) -> str:
    return _wd_SGR_RE.sub("", line).rstrip()


def worker(name: str, model: str, tin: int, tout: int, cost: float | None) -> Worker:
    return Worker(name=name, state=RUNNING, model=model, start=NOW - 60, log=LogState(
        tokens_in=tin, tokens_out=tout, cost=cost))


def station(workers: list[Worker], lead_state: str = "live", lead_kind: str = "t3 code",
            path: str = "/home/user/Projects/proj/.stage") -> Stage:
    snap = Snapshot(title="proj", now=NOW, workers=workers)
    return Stage(path=path, label="proj", snap=snap, live=len(workers), lead_state=lead_state,
                 lead_pid=4242 if lead_state != "gone" else None, lead_since=NOW - 300 if lead_state != "gone" else 0.0,
                 lead_kind=lead_kind if lead_state != "gone" else "")


def costed() -> Snapshot:
    return Snapshot(title="proj", now=NOW, workers=[
        worker("cheap-one", "luna@medium", 1000, 200, 0.5),
        worker("dear-two", "opus", 90_000, 12_000, 4.25),
        worker("no-cost", "", 300, 40, None),
    ])


class PageRender(unittest.TestCase):
    def frame(self, snap: Snapshot, ui: UIState, cols: int, rows: int = 30, stage: Stage | None = None) -> list[str]:
        return [plain(x) for x in render_frame(snap, ui, cols, rows, NOW, stage)]

    def test_cost_page_lists_the_costliest_worker_first_and_bars_the_models(self) -> None:
        lines = self.frame(costed(), UIState(view="cost"), 100)
        text = "\n".join(lines)
        self.assertLess(text.index("dear-two"), text.index("cheap-one"))
        self.assertLess(text.index("cheap-one"), text.index("no-cost"))
        self.assertIn("$4.25", text)
        self.assertIn("█", text)
        self.assertIn("░", text)
        model_rows = [ln for ln in lines if "░" in ln or "█" in ln]   # only the per-model rows carry a bar
        self.assertEqual(len(model_rows), 3)
        opus = next(ln for ln in model_rows if "opus" in ln)
        luna = next(ln for ln in model_rows if "luna@medium" in ln)
        self.assertEqual(opus.count("░"), 0)                    # the top model fills its bar
        self.assertGreater(opus.count("█"), luna.count("█"))    # bars are scaled to the top cost
        self.assertFalse(any("$0.000" in ln for ln in lines))   # a model no worker priced shows a dash, not $0

    def test_pages_fit_narrow_and_wide_frames(self) -> None:
        for cols in (57, 100):
            for view in ("cost", "lead", "files"):
                ui = UIState(view=view, detail_text=["kind   t3 code", "  " + "x" * 200])
                for line in self.frame(costed(), ui, cols, 24, station(costed().workers)):
                    self.assertLessEqual(_wd_width(line), cols, (view, cols, line))

    def test_lead_and_files_pages_show_the_loaded_text_with_the_footer_keys(self) -> None:
        ui = UIState(view="lead", detail_text=["kind   t3 code", "  {\"pid\": 4242}", "quota picks (PROGRESS.md)"])
        text = "\n".join(self.frame(costed(), ui, 100, 20, station(costed().workers)))
        self.assertIn("kind   t3 code", text)
        self.assertIn("{\"pid\": 4242}", text)
        self.assertIn("[Esc] back", text)
        self.assertIn("[r] reload", text)

    def test_footer_offers_the_pages_and_no_compact(self) -> None:
        live = "\n".join(self.frame(costed(), UIState(), 160, 30, station(costed().workers, "live")))
        self.assertIn("[L] lead", live)
        self.assertIn("[C] cost", live)
        self.assertIn("[F] files", live)
        self.assertNotIn("[K] compact", live)
        gone = "\n".join(self.frame(costed(), UIState(), 160, 30, station(costed().workers, "gone")))
        self.assertIn("[L] lead", gone)
        self.assertNotIn("[K] compact", gone)
        single = "\n".join(self.frame(costed(), UIState(), 160, 30))
        self.assertNotIn("[K] compact", single)

    def test_status_line_draws_over_the_station(self) -> None:
        stage = station(costed().workers)
        ui = UIState(note="lead closed")
        self.assertIn("lead closed", "\n".join(self.frame(costed(), ui, 100, 30, stage)))


class PageKeys(unittest.TestCase):
    def test_station_keys_open_the_pages_and_ask_for_a_load(self) -> None:
        snap = costed()
        for key, view in (("L", "lead"), ("C", "cost"), ("F", "files")):
            ui = UIState(scroll=5, detail_text=["old"])
            self.assertEqual(app._ap_handle_key(ui, key, snap, 40), "load_page")
            self.assertEqual((ui.view, ui.scroll, ui.detail_text), (view, 0, []))

    def test_esc_goes_back_to_the_station_and_the_keys_scroll_the_page(self) -> None:
        ui = UIState(view="lead", detail_text=["a"] * 100)
        app._ap_handle_key(ui, "down", None, 20)
        app._ap_handle_key(ui, "j", None, 20)
        self.assertEqual(ui.scroll, 2)
        app._ap_handle_key(ui, "pgdn", None, 20)
        self.assertEqual(ui.scroll, 2 + 12)
        app._ap_handle_key(ui, "end", None, 20)
        self.assertEqual(ui.scroll, app._AP_FAR)
        self.assertEqual(app._ap_handle_key(ui, "r", None, 20), "load_page")
        self.assertEqual(app._ap_handle_key(ui, "esc", None, 20), "")
        self.assertEqual((ui.view, ui.scroll, ui.detail_text), ("overview", 0, []))

    def test_a_page_switches_to_another_page_by_its_key(self) -> None:
        ui = UIState(view="cost")
        self.assertEqual(app._ap_handle_key(ui, "F", costed(), 30), "load_page")
        self.assertEqual(ui.view, "files")

    def test_global_station_keys_route_to_the_pages_and_worker_pages_ignore_them(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[station(costed().workers)])
        ui = UIState(gpage="stage", gstage=gs.stages[0].path, view="overview")
        self.assertEqual(app._ap_global_key(ui, "C", gs, 30), "load_page")
        self.assertEqual(ui.view, "cost")
        self.assertEqual(app._ap_global_key(ui, "esc", gs, 30), "")
        self.assertEqual((ui.gpage, ui.view), ("stage", "overview"))
        worker_ui = UIState(gpage="worker", gstage=gs.stages[0].path, view="overview")
        self.assertEqual(app._ap_global_key(worker_ui, "L", gs, 30), "")
        self.assertEqual(worker_ui.view, "overview")


class CollectPages(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="pages-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = os.path.join(self.tmp, "proj")
        self.stage = os.path.join(self.project, ".stage")
        os.makedirs(os.path.join(self.stage, "briefs"))
        os.makedirs(os.path.join(self.stage, "d-pages"))

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", self.project, *args], check=True, capture_output=True)

    def test_lead_page_has_the_lead_lead_json_stream_and_pick_lines(self) -> None:
        os.makedirs(os.path.join(self.project, "docs", "streams"))
        self.git("init", "-q")
        self.git("checkout", "-q", "-b", "slice/sweep")
        with open(os.path.join(self.project, "docs", "streams", "slice-sweep.md"), "w") as fh:
            fh.write("# sweep\nstate: open\n")
        with open(os.path.join(self.stage, "lead.json"), "w") as fh:
            json.dump({"pid": 4242, "phase": "build", "lead_label": "Luna"}, fh)
        with open(os.path.join(self.stage, "PROGRESS.md"), "w") as fh:
            fh.write("10:00 dispatch\n10:01 quota pick luna\n10:05 quota pick sonnet\n")
        stage = Stage(path=self.stage, label="proj", lead_state="live", lead_pid=4242, lead_kind="t3 code",
                      lead_since=NOW - 125)
        text = "\n".join(collect.lead_page(self.stage, stage, NOW))
        self.assertIn("kind   t3 code", text)
        self.assertIn("pid    4242", text)
        self.assertIn("age    2m 05s", text)
        self.assertIn('"lead_label": "Luna"', text)
        self.assertIn("# sweep", text)
        self.assertIn("quota pick sonnet", text)

    def test_lead_page_without_a_git_branch_says_so(self) -> None:
        text = "\n".join(collect.lead_page(self.stage, None, NOW))
        self.assertIn("lead state is only known", text)
        self.assertIn("no git branch here", text)
        self.assertIn("  none", text)

    def test_files_page_shows_status_and_diff_stat_of_the_brief_cwd(self) -> None:
        self.git("init", "-q")
        with open(os.path.join(self.project, "tracked.txt"), "w") as fh:
            fh.write("one\n")
        self.git("add", "tracked.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
        with open(os.path.join(self.project, "tracked.txt"), "a") as fh:
            fh.write("two\n")
        with open(os.path.join(self.project, "new.txt"), "w") as fh:
            fh.write("x\n")
        with open(os.path.join(self.stage, "briefs", "d-pages.md"), "w") as fh:
            fh.write(f"# D\nJob: build\nRepo/worktree: {self.project} (branch main)\nGoal: x\n")
        text = "\n".join(collect.files_page(self.stage, Worker(name="d-pages", state=RUNNING)))
        self.assertIn("changed files, newest edit first", text)
        self.assertIn("??  new.txt", text)
        self.assertIn("M   tracked.txt", text)
        self.assertIn("+1 −0", text)
        self.assertIn("git diff --stat", text)
        self.assertIn("1 insertion", text)

    def test_files_page_lists_the_stage_folder_without_a_git_dir(self) -> None:
        folder = os.path.join(self.stage, "d-pages")
        with open(os.path.join(folder, "REPORT.md"), "w") as fh:
            fh.write("## Summary\n" + "x" * 2048)
        text = "\n".join(collect.files_page(self.stage, Worker(name="d-pages", state=RUNNING)))
        self.assertIn("REPORT.md", text)
        self.assertIn("2.0 KB", text)

    def test_no_selected_worker_says_so(self) -> None:
        self.assertEqual(collect.files_page(self.stage, None),
                         ["no worker selected: move the cursor onto a worker row"])

    def test_codex_session_is_the_newest_rollout_whose_cwd_is_the_project(self) -> None:
        home = os.path.join(self.tmp, "home")
        day = os.path.join(home, ".codex", "sessions", "2026", "10", "09")
        os.makedirs(day)

        def rollout(stamp: str, cwd: str, sid: str) -> None:
            meta = {"type": "session_meta", "payload": {"id": sid, "cwd": cwd}}
            with open(os.path.join(day, f"rollout-{stamp}-{sid}.jsonl"), "w") as fh:
                fh.write(json.dumps(meta) + "\n")

        rollout("2026-10-09T10-00-00", self.project, "older")
        rollout("2026-10-09T11-00-00", "/elsewhere", "other")
        rollout("2026-10-09T12-00-00", self.project, "newest")
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(collect.codex_session_id(self.project), "newest")
            self.assertIsNone(collect.codex_session_id("/no/such/project"))


if __name__ == "__main__":
    unittest.main()
