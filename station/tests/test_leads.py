from __future__ import annotations

import json
import os
import shutil
import signal
import tempfile
import unittest
from unittest import mock

import app
import collect
from collect import GlobalCollector
from model import (
    DONE,
    RUNNING,
    ExecutionEvidence,
    ExecutionRecord,
    GlobalSnapshot,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import global_rows, render_global, render_global_plain
from widgets import _wd_SGR_RE

NOW = 1_800_000_000.0
HOME = "/home/user"
BOOT = 1_799_000_000.0
CLK = 100
TTY = 34816      # a pts tty_nr; 0 means no controlling terminal


def strip(text: str) -> str:
    return _wd_SGR_RE.sub("", text).rstrip()


class ExecutionLeadLifecycle(unittest.TestCase):
    def stages(self, *, age: float = 30, live: bool = False, activity: float | None = None,
               workers: bool = True, unattached: bool = False, all_history: bool = False) -> list[Stage]:
        gc = GlobalCollector([], quota=False)
        self.addCleanup(gc.close)
        root_id = "unattached:root" if unattached else "root"
        root = ExecutionRecord(root_id, role="lead", started=NOW - 3600,
                               activity=NOW - age if activity is None else activity)
        child = ExecutionRecord("child", parent_id=root_id, root_id=root_id, state="finished",
                                outcome="success", started=NOW - age, activity=NOW - age)
        items = [ExecutionEvidence(child)] if unattached else [ExecutionEvidence(root)]
        if workers and not unattached:
            items.append(ExecutionEvidence(child))
        with mock.patch.object(gc, "_co_execution_live", side_effect=lambda rec, boot=None: live and rec is root):
            return gc._co_execution_stages(items, NOW, all_history)

    def test_gone_root_keeps_finished_members_and_idles_immediately(self) -> None:
        (stage,) = self.stages()
        self.assertEqual((stage.state, stage.lead_state, stage.solo), ("idle", "gone", False))
        self.assertEqual([worker.state for worker in stage.snap.workers], [DONE])

    def test_gone_empty_root_is_idle_and_not_solo(self) -> None:
        (stage,) = self.stages(workers=False)
        self.assertEqual((stage.state, stage.solo), ("idle", False))

    def test_live_root_without_activity_is_active_from_lead_since(self) -> None:
        (stage,) = self.stages(live=True, activity=0, workers=False)
        self.assertEqual((stage.state, stage.activity, stage.solo), ("active", NOW - 3600, True))

    def test_live_root_with_real_old_activity_is_idle(self) -> None:
        (stage,) = self.stages(live=True, age=1200, workers=False)
        self.assertEqual(stage.state, "idle")

    def test_finished_members_remain_with_parent_for_two_days(self) -> None:
        (stage,) = self.stages(age=2 * 86400)
        self.assertEqual(stage.state, "idle")
        self.assertEqual(len(stage.snap.workers), 1)

    def test_idle_parent_ages_out_after_three_days(self) -> None:
        """Discovery keeps a three-day-old root; the ops window drops it once the work state is clean."""
        stages = self.stages(age=3 * 86400 + 1)
        collect._co_ops_windows(stages, NOW)
        self.assertEqual(stages, [])
        self.assertEqual(len(self.stages(age=3 * 86400 + 1, all_history=True)), 1)

    def test_unattached_finished_workers_still_age_out_after_one_day(self) -> None:
        stages = self.stages(age=2 * 86400, unattached=True)
        collect._co_ops_windows(stages, NOW)
        self.assertEqual(stages, [])


class Kinds(unittest.TestCase):
    """Lead classification from argv and cwd. Every row is a real process seen on this machine, or a near copy."""

    def kind(self, argv: list[str], cwd: str = "/home/user/Projects/proj", *, tty: bool = True,
             environ: dict[str, str] | None = None) -> str:
        return collect._co_lead_kind(argv, cwd, HOME, tty=tty, environ=environ or {})

    def test_terminal_claude_is_a_live_lead(self) -> None:
        self.assertEqual(self.kind(["claude"]), "claude")

    def test_claude_worker_flags_are_not_leads(self) -> None:
        for argv in (["claude", "-p", "--model", "x"], ["claude", "--print", "hi"],
                     ["claude", "--output-format", "stream-json"], ["claude", "--output-format=stream-json"],
                     ["claude", "bg-pty-host", "--bg-pty-host", "x.sock"]):
            self.assertEqual(self.kind(argv), "", argv)

    def test_claude_subcommands_are_not_leads(self) -> None:
        self.assertEqual(self.kind(["claude", "daemon", "run", "--json-path", "x"], HOME), "")

    def test_background_session_is_a_background_lead(self) -> None:
        self.assertEqual(self.kind(["claude", "bg-spare", "--bg-spare", "x.claim.sock"]), "claude bg")

    def test_idle_spares_under_the_daemon_root_are_ignored(self) -> None:
        argv = ["claude", "bg-spare", "--bg-spare", "/tmp/cc-daemon-1000/70d06190/spare/a.claim.sock"]
        self.assertEqual(self.kind(argv, "/tmp/cc-daemon-1000/70d06190/spare"), "")

    def test_a_rewritten_process_title_is_split_into_words(self) -> None:
        self.assertEqual(self.kind(["claude bg-spare", "--bg-spare", "x.claim.sock"]), "claude bg")
        self.assertEqual(self.kind(["claude bg-pty-host", "--bg-pty-host", "x.sock"]), "")
        self.assertEqual(self.kind(["claude -p", "hi"]), "")

    def test_t3_code_is_codex_app_server_with_the_t3_mcp_server(self) -> None:
        argv = ["/usr/bin/codex", "app-server", "-c", "mcp_servers.t3-code.command=t3"]
        self.assertEqual(self.kind(argv), "t3 code")
        self.assertEqual(self.kind(["codex", "app-server", "--listen", "unix://"]), "")
        self.assertEqual(self.kind(["codex", "exec", "-m", "x", "mcp_servers.t3-code"]), "")
        self.assertEqual(self.kind(argv, tty=False), "t3 code")
        self.assertEqual(self.kind(argv, environ={"CODEX_MANAGED_BY_NPM": "1"}), "")

    def test_codex_tui_with_a_terminal_is_a_lead(self) -> None:
        self.assertEqual(self.kind(["codex"]), "codex")
        self.assertEqual(self.kind(["codex", "--model", "luna"]), "codex")

    def test_codex_without_a_terminal_or_run_as_exec_is_not_a_lead(self) -> None:
        self.assertEqual(self.kind(["codex"], tty=False), "")
        self.assertEqual(self.kind(["codex", "exec", "--json"]), "")

    def test_codex_run_by_npm_or_openclaw_is_not_a_lead(self) -> None:
        self.assertEqual(self.kind(["codex"], environ={"CODEX_MANAGED_BY_NPM": "1"}), "")
        self.assertEqual(self.kind(["codex"], environ={"CODEX_HOME": "/home/user/.openclaw/codex"}), "")
        self.assertEqual(self.kind(["codex"], environ={"CODEX_HOME": "/home/user/.codex"}), "codex")

    def test_claude_needs_a_terminal(self) -> None:
        self.assertEqual(self.kind(["claude"], tty=False), "")
        self.assertEqual(self.kind(["claude", "bg-spare", "--bg-spare", "x.sock"], tty=False), "claude bg")

    def test_claude_with_another_config_dir_is_claude_work(self) -> None:
        self.assertEqual(self.kind(["claude"], environ={"CLAUDE_CONFIG_DIR": "/home/user/.claude-team-work"}), "claude work")
        self.assertEqual(self.kind(["claude"], environ={"CLAUDE_CONFIG_DIR": "/home/user/.claude"}), "claude")
        self.assertEqual(self.kind(["claude"], environ={"CLAUDE_CONFIG_DIR": ""}), "claude")

    def test_daemon_versioned_binary_with_a_terminal_is_a_lead(self) -> None:
        # the daemon runs the live session from its versioned install: argv[0]'s basename is the version number
        argv = ["/home/user/.local/share/claude/versions/2.1.296", "--session-id", "abc", "--fork-session",
                "--resume", "x.jsonl", "--reply-on-resume", "--model", "x"]
        work = {"CLAUDE_CONFIG_DIR": "/home/user/.claude-team-work"}
        self.assertEqual(self.kind(argv, environ=work), "claude work")
        self.assertEqual(self.kind(argv, tty=False, environ=work), "")
        self.assertEqual(self.kind(argv + ["-p"], environ=work), "")
        self.assertEqual(self.kind(argv[:1] + ["--print", "hi"], environ=work), "")
        self.assertEqual(self.kind(argv), "claude")

    def test_versioned_binary_subcommands_and_background_rules_are_unchanged(self) -> None:
        base = "/home/user/.local/share/claude/versions/2.1.296"
        self.assertEqual(self.kind([base, "daemon", "run"]), "")
        self.assertEqual(self.kind([base, "bg-spare", "--bg-spare", "x.claim.sock"]), "claude bg")
        self.assertEqual(self.kind([base, "bg-spare", "--bg-spare", "/tmp/cc-daemon-1000/spare/x.sock"],
                                   "/tmp/cc-daemon-1000/spare"), "")

    def test_versioned_binary_headless_run_is_headless_claude(self) -> None:
        argv = ["/home/user/.local/share/claude/versions/2.1.296", "-p", "--model", "x"]
        self.assertEqual(collect._co_headless_kind(argv, environ={}, home=HOME), "claude")
        self.assertEqual(collect._co_headless_kind(argv[:1] + ["--model", "x"], environ={}, home=HOME), "")

    def test_claude_helper_matches_the_named_binary_and_the_versioned_install(self) -> None:
        self.assertTrue(collect._co_is_claude_binary("claude"))
        self.assertTrue(collect._co_is_claude_binary("/usr/bin/claude"))
        self.assertTrue(collect._co_is_claude_binary("/home/user/.local/share/claude/versions/2.1.296"))
        self.assertFalse(collect._co_is_claude_binary("/usr/lib/claude-desktop/claude-desktop"))
        self.assertFalse(collect._co_is_claude_binary("/home/user/.local/share/other/versions/2.1.296"))

    def test_cursor_and_opencode_need_a_terminal(self) -> None:
        self.assertEqual(self.kind(["cursor-agent"], tty=False), "")
        self.assertEqual(self.kind(["opencode"], tty=False), "")

    def test_cursor_agent_without_print_is_a_lead(self) -> None:
        self.assertEqual(self.kind(["/home/user/.local/bin/cursor-agent", "--use-system-ca"]), "cursor")
        self.assertEqual(self.kind(["cursor-agent", "-p", "fix it"]), "")
        self.assertEqual(self.kind(["cursor-agent", "--print", "fix it"]), "")

    def test_agent_running_the_cursor_agent_bundle_is_a_lead(self) -> None:
        argv = ["/home/user/.local/bin/agent", "--use-system-ca",
                "/home/user/.local/share/cursor-agent/versions/2026.10.01/index.js"]
        self.assertEqual(self.kind(argv), "cursor")

    def test_a_shell_that_mentions_cursor_agent_is_not_a_lead(self) -> None:
        self.assertEqual(self.kind(["/usr/bin/bash", "-c", "source snapshot.sh; cursor-agent -p hi"]), "")

    def test_opencode_without_run_is_a_lead(self) -> None:
        self.assertEqual(self.kind(["/home/user/.opencode/bin/opencode"]), "opencode")
        self.assertEqual(self.kind(["opencode", "run", "fix it"]), "")

    def test_opencode_serve_is_ignored_wherever_it_runs(self) -> None:
        self.assertEqual(self.kind(["/home/user/.opencode/bin/opencode", "serve", "--service"], HOME), "")
        self.assertEqual(self.kind(["opencode", "serve", "--service"], "/home/user/Projects/proj"), "")

    def test_unrelated_and_empty_argv(self) -> None:
        self.assertEqual(self.kind([]), "")
        self.assertEqual(self.kind(["/usr/lib/claude-desktop/claude-desktop", "--type=gpu-process"]), "")
        self.assertEqual(self.kind(["codex", "exec", "--json"]), "")


class Headless(unittest.TestCase):
    """Discovery classification: a headless agent run is a worker, whatever its harness."""

    def headless(self, argv: list[str], *, environ: dict[str, str] | None = None) -> str:
        return collect._co_headless_kind(argv, environ=environ or {}, home=HOME)

    def test_headless_agent_runs_are_workers(self) -> None:
        self.assertEqual(self.headless(["claude", "-p", "--model", "x"]), "claude")
        self.assertEqual(self.headless(["claude", "--print", "hi"]), "claude")
        self.assertEqual(self.headless(["claude", "--output-format", "stream-json", "--verbose"]), "claude")
        self.assertEqual(self.headless(["codex", "exec", "-m", "luna"]), "codex")
        self.assertEqual(self.headless(["cursor-agent", "-p", "--trust"]), "cursor")
        self.assertEqual(self.headless(["opencode", "run", "--format", "json", "hi"]), "opencode")

    def test_interactive_and_non_agent_runs_are_not_workers(self) -> None:
        for argv in (["claude"], ["claude", "--model", "sonnet"], ["codex"], ["codex", "--model", "luna"],
                     ["opencode"], ["opencode", "serve", "--service"], ["python3", "worker.py"],
                     ["claude", "bg-spare", "--bg-spare", "x.sock"], ["claude", "bg-pty-host", "--bg-pty-host", "x.sock"],
                     ["claude", "daemon", "run"]):
            self.assertEqual(self.headless(argv), "", argv)

    def test_codex_helpers_are_not_discovered(self) -> None:
        self.assertEqual(self.headless(["codex", "exec", "-m", "x"], environ={"CODEX_MANAGED_BY_NPM": "1"}), "")
        self.assertEqual(self.headless(["codex", "exec"], environ={"CODEX_HOME": "/home/user/.openclaw/codex"}), "")

    def test_codex_exec_after_search_is_still_discovered(self) -> None:
        self.assertEqual(self.headless(["codex", "--search", "exec", "-m", "luna"]), "codex")


class Ownership(unittest.TestCase):
    """The deepest project owns a headless worker, so a parent stage does not claim a nested project's run."""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="owns-cwd-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def stage(self, project: str) -> None:
        stage = os.path.join(project, ".stage")
        os.makedirs(stage, exist_ok=True)
        for name, text in (("station", "#!/bin/bash\n"), ("PROGRESS.md", "09:00 x\n")):
            with open(os.path.join(stage, name), "w", encoding="utf-8") as fh:
                fh.write(text)

    def test_a_nested_project_takes_the_worker(self) -> None:
        self.stage(self.dir)
        self.stage(os.path.join(self.dir, "foo"))
        cwd = os.path.join(self.dir, "foo", "out", "t7")
        self.assertFalse(collect._co_owns_cwd(cwd, self.dir))
        self.assertTrue(collect._co_owns_cwd(cwd, os.path.join(self.dir, "foo")))

    def test_a_worker_at_the_project_root_is_owned(self) -> None:
        self.stage(self.dir)
        self.assertTrue(collect._co_owns_cwd(self.dir, self.dir))


class Scan(unittest.TestCase):
    """The table-driven scan: fake /proc tables in, leads out, no real /proc."""

    def setUp(self) -> None:
        # pid -> (ppid, utime, stime, starttime ticks, tty_nr)
        self.procs = {
            10: (1, 0, 0, 5000, TTY),     # terminal claude
            11: (1, 0, 0, 9000, 0),       # claude bg-spare in ornith-dash
            12: (1, 0, 0, 100, 0),        # claude -p worker
            13: (1, 0, 0, 100, 0),        # idle spare under /tmp
            14: (1, 0, 0, 100, 0),        # codex exec worker
            15: (1, 0, 0, 100, 0),        # claude without a terminal
        }
        self.argv = {
            10: ["claude"],
            11: ["claude", "bg-spare", "--bg-spare", "x.claim.sock"],
            12: ["claude", "-p", "--model", "x"],
            13: ["claude", "bg-spare", "--bg-spare", "/tmp/cc-daemon-1000/spare/x.claim.sock"],
            14: ["codex", "exec", "-m", "x"],
            15: ["claude"],
        }
        self.cwd = {
            10: "/home/user/Projects/ornith-tune",
            11: "/home/user/Projects/ornith-dash",
            12: "/home/user/Projects/proj",
            13: "/tmp/cc-daemon-1000/spare",
            14: "/home/user/Projects/proj",
            15: "/home/user/Projects/proj",
        }

    def leads(self) -> list[collect._co_Lead]:
        return collect._co_lead_scan(self.procs, lambda pid: self.argv.get(pid, []),
                                     lambda pid: self.cwd.get(pid, ""), HOME, CLK, BOOT, lambda pid: {})

    def test_only_interactive_leads_are_found_with_state_and_start_time(self) -> None:
        found = {lead.pid: lead for lead in self.leads()}
        self.assertEqual(set(found), {10, 11})
        self.assertEqual((found[10].kind, found[10].state), ("claude", "live"))
        self.assertEqual((found[11].kind, found[11].state), ("claude bg", "background"))
        self.assertEqual(found[11].since, BOOT + 9000 / CLK)
        self.assertEqual(found[10].cwd, "/home/user/Projects/ornith-tune")

    def test_station_mapping_uses_the_project_dir_the_stage_dir_sits_in(self) -> None:
        stages = ["/home/user/Projects/ornith-dash/.stage", "/home/user/Projects/ornith-tune/.stage"]
        self.assertEqual(collect._co_station_of("/home/user/Projects/ornith-dash", stages), stages[0])
        self.assertEqual(collect._co_station_of("/home/user/Projects/ornith-dash/src/x", stages), stages[0])
        self.assertIsNone(collect._co_station_of("/home/user/Projects/ornith-dash-old", stages))
        self.assertIsNone(collect._co_station_of("/home/user/Projects", stages))

    def test_the_deepest_project_wins_a_nested_match(self) -> None:
        stages = ["/r/mono/.stage", "/r/mono/pkg/web/.stage"]
        self.assertEqual(collect._co_station_of("/r/mono/pkg/web/src", stages), stages[1])
        self.assertEqual(collect._co_station_of("/r/mono/api", stages), stages[0])

    def test_of_two_stage_dirs_in_one_project_the_canonical_stage_wins(self) -> None:
        stages = ["/r/p/.staging", "/r/p/.stage"]
        self.assertEqual(collect._co_station_of("/r/p", stages), "/r/p/.stage")
        self.assertEqual(collect._co_station_of("/r/p", stages[::-1]), "/r/p/.stage")

    def test_a_live_lead_beats_a_background_one_then_the_newest_wins(self) -> None:
        stage = "/home/user/Projects/proj/.stage"
        bg = collect._co_Lead(1, "claude bg", "background", "/home/user/Projects/proj", BOOT + 1)
        live_old = collect._co_Lead(2, "claude", "live", "/home/user/Projects/proj", BOOT + 2)
        live_new = collect._co_Lead(3, "cursor", "live", "/home/user/Projects/proj", BOOT + 3)
        best = collect._co_assign_leads([bg, live_old, live_new], [stage], [])
        self.assertEqual(best[stage].pid, 3)
        best = collect._co_assign_leads([bg], [stage], [])
        self.assertEqual(best[stage].pid, 1)

    def test_a_lead_json_pid_names_its_own_station(self) -> None:
        stage_a, stage_b = "/r/a/.stage", "/r/b/.stage"
        pinned = collect._co_Lead(7, "lead.json", "live", "", BOOT)
        best = collect._co_assign_leads([], [stage_a, stage_b], [(stage_a, pinned)])
        self.assertEqual(best, {stage_a: pinned})


class Dismissals(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="leads-test-")
        self.path = os.path.join(self.dir, "agents", "status-dismissed.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_missing_file_dismisses_nothing(self) -> None:
        self.assertEqual(collect.load_dismissed(self.path), {})

    def test_round_trip_keeps_every_station_and_creates_the_folder(self) -> None:
        collect.dismiss_stage(self.path, "/r/a/.stage", 100.0)
        collect.dismiss_stage(self.path, "/r/b/.stage", 200.5)
        self.assertEqual(collect.load_dismissed(self.path), {"/r/a/.stage": 100.0, "/r/b/.stage": 200.5})
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["status-dismissed.json"])   # no temp file left

    def test_a_corrupt_file_reads_as_nothing_dismissed(self) -> None:
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(collect.load_dismissed(self.path), {})
        collect.dismiss_stage(self.path, "/r/a/.stage", 5.0)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"/r/a/.stage": 5.0})


class Stations(unittest.TestCase):
    """GlobalCollector over a temp root with fake /proc tables: lead state, dismissal and the signal re-check."""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="leads-stations-")
        self.root = os.path.join(self.dir, "Projects")
        self.dismissed = os.path.join(self.dir, "agents", "status-dismissed.json")
        self.procs: dict[int, tuple[int, int, int, int, int]] = {}
        self.env: dict[int, dict[str, str]] = {}
        self.argv: dict[int, list[str]] = {}
        self.cwd: dict[int, str] = {}
        self.stage = self.make_stage("proj")
        self.touch(self.stage, NOW - 60)
        self.patches = [
            mock.patch.object(collect, "_co_scan_procs", side_effect=lambda: dict(self.procs)),
            mock.patch.object(collect, "_co_argv", side_effect=lambda pid: list(self.argv.get(pid, []))),
            mock.patch.object(collect, "_co_cwd", side_effect=lambda pid: self.cwd.get(pid, "")),
            mock.patch.object(collect, "_co_environ", side_effect=lambda pid: dict(self.env.get(pid, {}))),
            mock.patch.object(collect, "_co_pid_live", return_value=False),
            mock.patch.object(collect, "_co_boot_time", return_value=BOOT),
            mock.patch.object(collect, "_co_clk_tck", return_value=CLK),
        ]
        for patch in self.patches:
            patch.start()
        self.gc = GlobalCollector([self.root], ledger=os.path.join(self.dir, "none.jsonl"), quota=False,
                                  dismissed=self.dismissed)

    def tearDown(self) -> None:
        self.gc.close()
        for patch in self.patches:
            patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_stage(self, name: str) -> str:
        stage = os.path.join(self.root, name, ".stage")
        os.makedirs(stage, exist_ok=True)
        with open(os.path.join(stage, "station"), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\n")
        return stage

    def touch(self, stage: str, mtime: float) -> None:
        path = os.path.join(stage, "PROGRESS.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("09:00 started\n")
        os.utime(path, (mtime, mtime))

    def project(self, name: str = "proj") -> str:
        return os.path.join(self.root, name)

    def add_lead(self, pid: int, argv: list[str], cwd: str, start: float = NOW - 3600, tty: int = TTY) -> None:
        self.procs[pid] = (1, 0, 0, int((start - BOOT) * CLK), tty)
        self.argv[pid] = argv
        self.cwd[pid] = cwd

    def snap(self, all_history: bool = True) -> GlobalSnapshot:
        return self.gc.snapshot(NOW, all_history=all_history)

    def station(self, gs: GlobalSnapshot, name: str = "proj") -> Stage:
        return next(s for s in gs.stages if s.path == self.stage)

    def test_terminal_claude_in_the_project_makes_the_station_live(self) -> None:
        self.add_lead(10, ["claude"], self.project())
        stage = self.station(self.snap())
        self.assertEqual((stage.lead_state, stage.lead_pid, stage.lead_kind), ("live", 10, "claude"))
        self.assertEqual(stage.lead_since, NOW - 3600)

    def test_live_station_without_activity_is_active_from_lead_since(self) -> None:
        self.add_lead(10, ["claude", "-n", "lead"], self.project())
        gs = self.snap(all_history=False)
        stage = self.station(gs)
        self.assertEqual((stage.state, stage.activity), ("active", NOW - 3600))
        rendered = strip("\n".join(render_global(gs, UIState(), 140, 40, NOW)))
        self.assertIn("1 live", rendered)
        self.assertNotIn("Workers be working", rendered)   # the console masthead carries no banner

    def test_live_virtual_lead_without_activity_is_active_from_lead_since(self) -> None:
        self.add_lead(20, ["claude", "rc"], self.repo("scratch"))
        (stage,) = self.virtual(self.snap(all_history=False))
        self.assertEqual((stage.state, stage.activity), ("active", NOW - 3600))

    def test_background_station_is_idle_immediately(self) -> None:
        self.add_lead(11, ["claude", "bg-spare", "--bg-spare", "x.sock"], self.project())
        self.assertEqual(self.station(self.snap()).state, "idle")

    def test_background_station_with_fresh_activity_is_active(self) -> None:
        self.add_lead(11, ["claude", "bg-spare", "--bg-spare", "x.sock"], self.project())
        real = collect._co_lead_session

        def fresh(*args, **kwargs):
            meta = dict(real(*args, **kwargs))
            meta["activity"] = NOW - 10   # a hosted or background lead that is working right now
            return meta

        with mock.patch.object(collect, "_co_lead_session", side_effect=fresh):
            self.assertEqual(self.station(self.snap()).state, "active")

    def test_background_session_makes_the_station_background(self) -> None:
        self.add_lead(11, ["claude", "bg-spare", "--bg-spare", "x.sock"], self.project(), NOW - 17 * 3600)
        stage = self.station(self.snap())
        self.assertEqual((stage.lead_state, stage.lead_kind), ("background", "claude bg"))

    def test_a_station_without_a_lead_is_gone(self) -> None:
        stage = self.station(self.snap())
        self.assertEqual((stage.lead_state, stage.lead_pid, stage.lead_since, stage.lead_kind), ("gone", None, 0.0, ""))

    def test_a_worker_process_never_becomes_the_lead(self) -> None:
        self.add_lead(14, ["codex", "exec", "-m", "x"], self.project())
        rows = [s for s in self.snap().stages if s.dir == self.stage]
        self.assertTrue(rows)
        self.assertTrue(all(s.lead_state == "gone" for s in rows))   # a headless run is a worker, never a lead
        self.assertEqual([w.name for s in rows for w in s.snap.workers], ["codex-14"])

    def test_a_lead_json_pid_names_the_lead_even_without_a_cwd_match(self) -> None:
        self.add_lead(15, ["opencode", "run", "x"], "/elsewhere")
        with open(os.path.join(self.stage, "lead.json"), "w", encoding="utf-8") as fh:
            json.dump({"pid": 15, "lead_label": "Luna"}, fh)
        stage = self.station(self.snap())
        self.assertEqual((stage.lead_state, stage.lead_pid), ("live", 15))   # argv says worker, lead.json says lead
        self.assertEqual(stage.lead_kind, "lead.json")

    def test_a_headless_run_under_the_project_shows_under_its_lead(self) -> None:
        self.add_lead(10, ["claude"], self.project())
        self.add_lead(30, ["claude", "-p", "--model", "claude-haiku-5-5", "--effort", "medium"],
                      os.path.join(self.project(), "out", "t7"), NOW - 120)
        # No same-cwd/time adoption: the run is listed for the folder but unlinked from the lead.
        stages = [s for s in self.snap().stages if s.dir == self.stage]
        self.assertTrue(self.station(self.snap()).solo)
        workers = [w for s in stages for w in s.snap.workers]
        self.assertEqual([w.name for w in workers], ["t7"])
        worker = workers[0]
        self.assertEqual((worker.state, worker.family, worker.model, worker.effort),
                         (RUNNING, "claude", "claude-haiku-5-5", "medium"))
        self.assertEqual(sum(s.live for s in stages), 1)

    def test_a_headless_run_outside_the_project_is_not_discovered(self) -> None:
        self.add_lead(10, ["claude"], self.project())
        self.add_lead(30, ["claude", "-p", "--model", "x"], "/elsewhere")
        self.assertEqual(self.station(self.snap()).snap.workers, [])

    def test_a_registered_worker_is_not_discovered_twice(self) -> None:
        self.add_lead(10, ["claude"], self.project())
        pid_path = os.path.join(self.stage, "w1.pid")
        with open(pid_path, "w", encoding="utf-8") as fh:
            fh.write("30")
        os.utime(pid_path, (NOW - 60, NOW - 60))
        self.add_lead(30, ["claude", "-p", "--model", "x"], self.project())
        rows = [s for s in self.snap().stages if s.dir == self.stage]
        self.assertEqual([w.name for s in rows for w in s.snap.workers], ["w1"])

    def test_a_parent_stage_does_not_claim_a_nested_projects_worker(self) -> None:
        parent = self.make_stage("")            # <root>/.stage: a stage for the projects root itself
        self.touch(parent, NOW - 60)
        self.add_lead(10, ["claude"], self.project())
        self.add_lead(30, ["claude", "-p", "--model", "x"], os.path.join(self.project(), "out", "t7"), NOW - 120)
        gs = self.snap()
        parent_stage = next(s for s in gs.stages if s.path == parent)
        self.assertEqual(parent_stage.snap.workers, [])                       # the deeper project owns it
        rows = [s for s in gs.stages if s.dir == self.stage]
        self.assertEqual([w.name for s in rows for w in s.snap.workers], ["t7"])

    def test_a_headless_run_the_lead_classifier_claims_is_not_double_counted(self) -> None:
        self.add_lead(30, ["codex", "--search", "exec", "-m", "luna"], self.project())   # a terminal: a lead
        stage = self.station(self.snap())
        self.assertEqual((stage.lead_state, stage.lead_kind), ("live", "codex"))
        self.assertEqual(stage.snap.workers, [])

    def test_a_headless_worker_keeps_an_otherwise_idle_stage_visible(self) -> None:
        stage = self.make_stage("old")
        self.touch(stage, NOW - 100 * 3600)     # no lead, nothing moved for more than 72 h: idle on its own
        self.add_lead(30, ["claude", "-p", "--model", "x"], os.path.join(self.root, "old", "out", "t1"))
        gs = self.snap(all_history=False)
        self.assertEqual(gs.hidden_idle, 0)
        rows = [s for s in gs.stages if s.dir == stage]
        self.assertEqual([w.name for s in rows for w in s.snap.workers], ["t1"])

    def test_a_headless_worker_un_dismisses_its_station(self) -> None:
        self.snap()
        self.gc.dismiss(self.stage, NOW - 10)
        self.assertNotIn(self.stage, [s.path for s in self.snap().stages])
        self.add_lead(30, ["claude", "-p", "--model", "x"], os.path.join(self.project(), "out", "t1"))
        self.assertTrue([s for s in self.snap().stages if s.dir == self.stage])

    def test_dismissed_gone_station_is_hidden_until_its_files_move_on(self) -> None:
        self.snap()
        self.gc.dismiss(self.stage, NOW - 10)
        self.assertNotIn(self.stage, [s.path for s in self.snap().stages])
        self.touch(self.stage, NOW - 5)
        self.assertIn(self.stage, [s.path for s in self.snap().stages])

    def test_a_dismissed_station_comes_back_when_a_lead_appears(self) -> None:
        self.snap()
        self.gc.dismiss(self.stage, NOW - 10)
        self.assertNotIn(self.stage, [s.path for s in self.snap().stages])
        self.add_lead(10, ["claude"], self.project())
        self.assertEqual(self.station(self.snap()).lead_state, "live")

    def test_dismiss_writes_the_file_read_back_by_the_collector(self) -> None:
        self.snap()
        self.assertTrue(self.gc.dismiss(self.stage, NOW - 10))
        self.assertEqual(collect.load_dismissed(self.dismissed), {self.stage: NOW - 10})

    def repo(self, name: str, sub: str = "") -> str:
        """A git project with no stage dir (a directory named .git is enough); returns the cwd inside it."""
        root = os.path.join(self.root, name)
        os.makedirs(os.path.join(root, ".git"), exist_ok=True)
        os.makedirs(os.path.join(root, sub), exist_ok=True)
        return os.path.join(root, sub)

    def virtual(self, gs: GlobalSnapshot) -> list[Stage]:
        return [s for s in gs.stages if "#" in s.path]

    def test_a_t3_code_lead_reads_its_codex_rollout_and_is_tagged_t3(self) -> None:
        # a T3 session is Codex underneath: its rollout must supply the activity, or the station looks idle and hides
        with tempfile.TemporaryDirectory() as tmp:
            project = os.path.join(tmp, "micro1")
            os.makedirs(project)
            codex_home = os.path.join(tmp, "codex")
            day = os.path.join(codex_home, "sessions", "2026", "10", "11")
            os.makedirs(day)
            rollout = os.path.join(day, "rollout-2026-10-11T20-00-00-abc.jsonl")
            with open(rollout, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "session_meta", "payload": {"id": "abc", "cwd": project}}) + "\n")
                fh.write(json.dumps({"type": "turn_context", "payload": {"cwd": project, "model": "gpt-6-luna",
                                                                         "effort": "high"}}) + "\n")
            with open(os.path.join(codex_home, "session_index.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"thread_id": "abc", "thread_name": "interview study"}) + "\n")
            lead = collect._co_Lead(2693815, "t3 code", "background", project, NOW - 60)
            with mock.patch.dict(os.environ, {"CODEX_HOME": codex_home}):
                meta = collect._co_lead_session(os.path.join(project, ".stage"), lead)
        self.assertEqual(meta["tag"], "T3")
        self.assertEqual(meta["session_id"], "abc")
        self.assertEqual(meta["session_name"], "interview study")
        self.assertEqual(meta["lead_model"], "gpt-6-luna")
        self.assertGreater(float(meta["activity"]), 0.0)

    def test_a_lead_with_no_stage_dir_becomes_a_solo_station(self) -> None:
        cwd = self.repo("scratch")
        self.add_lead(20, ["claude"], cwd)
        gs = self.snap(all_history=False)
        (stage,) = self.virtual(gs)
        self.assertEqual((stage.label, stage.solo, stage.lead_state, stage.lead_pid, stage.lead_kind),
                         ("scratch", True, "live", 20, "claude"))
        self.assertEqual((stage.live, stage.last_activity, stage.snap.workers), (0, 0.0, []))
        self.assertEqual(stage.lead_since, NOW - 3600)
        self.assertEqual(stage.snap.session_start, NOW - 3600)

    def test_the_virtual_key_is_root_dot_stage_hash_pid_and_a_subdir_maps_to_the_root(self) -> None:
        cwd = self.repo("scratch", "src/deep")
        self.add_lead(20, ["claude"], cwd)
        (stage,) = self.virtual(self.snap())
        self.assertEqual(stage.path, os.path.join(self.root, "scratch", ".stage#20"))

    def test_two_leads_in_one_repo_make_two_rows(self) -> None:
        cwd = self.repo("scratch")
        self.add_lead(20, ["claude"], cwd)
        self.add_lead(21, ["codex"], cwd)
        stages = self.virtual(self.snap())
        self.assertEqual(sorted(os.path.basename(s.path) for s in stages), [".stage#20", ".stage#21"])
        self.assertEqual(sorted(s.label for s in stages), ["scratch", "scratch · codex"])

    def test_a_second_lead_in_a_repo_with_a_real_stage_gets_its_own_row(self) -> None:
        os.makedirs(os.path.join(self.project(), ".git"))
        cwd = os.path.join(self.project(), "src")
        self.add_lead(10, ["claude"], cwd, NOW - 3600)   # older: loses the one-lead contest
        self.add_lead(11, ["codex"], cwd, NOW - 60)       # newer: takes the real station
        gs = self.snap()
        self.assertEqual(self.station(gs).lead_pid, 11)
        (virtual,) = self.virtual(gs)
        self.assertEqual((virtual.lead_pid, virtual.lead_kind, virtual.solo), (10, "claude", True))
        self.assertEqual(len(gs.stages), 2)

    def test_a_label_clash_adds_the_harness_then_a_number(self) -> None:
        cwd = self.repo("scratch")
        for pid in (20, 21, 22):
            self.add_lead(pid, ["claude"], cwd)
        self.assertEqual(sorted(s.label for s in self.virtual(self.snap())),
                         ["scratch", "scratch · claude", "scratch · claude #2"])

    def test_a_label_matching_a_real_station_is_disambiguated(self) -> None:
        elsewhere = os.path.join(self.dir, "x", "proj")          # the real station is also "proj"
        os.makedirs(os.path.join(elsewhere, ".git"))
        self.add_lead(20, ["claude"], elsewhere)
        (stage,) = self.virtual(self.snap())
        self.assertEqual(stage.label, "proj · claude")

    def test_a_lead_in_home_is_a_station_labelled_tilde(self) -> None:
        os.makedirs(os.path.join(self.dir, ".git"))                  # a .git in $HOME itself does not count
        self.add_lead(20, ["claude"], self.dir)
        with mock.patch.dict(os.environ, {"HOME": self.dir}):
            (stage,) = self.virtual(self.snap())
        self.assertEqual(stage.label, "~")

    def test_a_lead_without_a_git_project_is_a_station_of_its_own_folder(self) -> None:
        loose = os.path.join(self.dir, "loose")
        os.makedirs(os.path.join(loose, "sub"))
        self.add_lead(21, ["claude"], os.path.join(loose, "sub"))
        with mock.patch.dict(os.environ, {"HOME": self.dir}):
            (stage,) = self.virtual(self.snap())
        self.assertEqual((stage.label, stage.path), ("sub", os.path.join(loose, "sub", ".stage#21")))

    def test_a_lead_matched_to_a_real_stage_is_not_also_virtual(self) -> None:
        os.makedirs(os.path.join(self.project(), ".git"))
        self.add_lead(10, ["claude"], os.path.join(self.project(), "src"))
        gs = self.snap()
        self.assertEqual(self.virtual(gs), [])
        self.assertEqual(self.station(gs).lead_pid, 10)

    def test_a_lead_json_pinned_lead_is_not_also_virtual(self) -> None:
        cwd = self.repo("scratch")
        self.add_lead(15, ["claude"], cwd)
        with open(os.path.join(self.stage, "lead.json"), "w", encoding="utf-8") as fh:
            json.dump({"pid": 15}, fh)
        gs = self.snap()
        self.assertEqual(self.virtual(gs), [])
        self.assertEqual(self.station(gs).lead_pid, 15)

    def test_a_solo_station_is_ordered_before_a_stage_with_no_lead_and_no_workers(self) -> None:
        self.add_lead(20, ["claude"], self.repo("scratch"))
        gs = self.snap(all_history=True)
        self.assertEqual([s.label for s in gs.stages], ["scratch", "proj"])

    def test_live_lead_rows_with_no_activity_sort_by_oldest_lead_first(self) -> None:
        scratch_stage = self.make_stage("scratch")
        os.unlink(os.path.join(self.stage, "PROGRESS.md"))
        self.add_lead(20, ["claude"], self.project("scratch"), start=NOW - 500)
        self.add_lead(21, ["claude"], self.project("proj"), start=NOW - 100)
        gs = self.snap(all_history=True)
        live = [row for row in gs.stages if row.lead_state == "live"]
        self.assertEqual([row.path for row in live], [self.stage, scratch_stage])
        self.assertEqual([row.last_activity for row in live], [0.0, 0.0])

    def test_current_lead_finds_a_virtual_station(self) -> None:
        self.add_lead(20, ["claude"], self.repo("scratch"))
        (stage,) = self.virtual(self.snap())
        self.assertEqual(self.gc.current_lead(stage.path).pid, 20)

    def test_a_virtual_station_is_gone_with_its_lead(self) -> None:
        before = self.snap(all_history=False)
        self.add_lead(20, ["claude"], self.repo("scratch"))
        self.assertEqual(len(self.virtual(self.snap(all_history=False))), 1)
        del self.procs[20]
        gs = self.snap(all_history=False)
        self.assertEqual(self.virtual(gs), [])
        self.assertEqual((gs.hidden_idle, gs.dismissed), (before.hidden_idle, before.dismissed))

    def test_a_claude_work_lead_is_labelled_and_a_terminal_less_one_is_skipped(self) -> None:
        cwd = self.repo("scratch")
        self.add_lead(20, ["claude"], cwd)
        self.env[20] = {"CLAUDE_CONFIG_DIR": os.path.expanduser("~/.claude-team-work")}
        self.add_lead(21, ["claude"], cwd, tty=0)
        (stage,) = self.virtual(self.snap())
        self.assertEqual(stage.lead_kind, "claude work")

    def test_solo_is_true_with_a_lead_and_no_workers_and_false_once_a_worker_exists(self) -> None:
        self.add_lead(10, ["claude"], self.project())
        self.assertTrue(self.station(self.snap()).solo)
        with open(os.path.join(self.stage, "w1.pid"), "w", encoding="utf-8") as fh:
            fh.write("999999\n")
        with open(os.path.join(self.stage, "w1.log"), "w", encoding="utf-8") as fh:
            fh.write("done\n")
        os.utime(os.path.join(self.stage, "w1.pid"), (NOW - 30, NOW - 30))
        os.makedirs(os.path.join(self.stage, "briefs"), exist_ok=True)
        with open(os.path.join(self.stage, "briefs", "w1.md"), "w", encoding="utf-8") as fh:
            fh.write("Cwd: ~/Projects/worker-repo\n")
        rows = [s for s in self.snap().stages if s.dir == self.stage]
        stage = self.station(self.snap())
        self.assertEqual([worker.name for s in rows for worker in s.snap.workers], ["w1"])
        self.assertEqual((stage.state, stage.activity, stage.cwd), ("active", NOW - 30, self.project()))
        self.assertEqual([d for s in rows for d in s.workdirs], ["~/Projects/worker-repo"])

    def test_unlinked_worker_stays_separate_when_two_live_leads_share_a_stage(self) -> None:
        self.add_lead(10, ["claude"], self.project(), NOW - 120)
        self.add_lead(11, ["codex"], self.project(), NOW - 60)
        with open(os.path.join(self.stage, "w.pid"), "w", encoding="utf-8") as fh:
            fh.write("999999\n")
        os.utime(os.path.join(self.stage, "w.pid"), (NOW - 30, NOW - 30))
        gs = self.snap()
        unlinked = next(row for row in gs.stages if row.unlinked)
        self.assertEqual([worker.name for worker in unlinked.snap.workers], ["w"])

    def test_unlinked_worker_started_before_lead_stays_separate(self) -> None:
        self.add_lead(10, ["claude"], self.project(), NOW - 60)
        with open(os.path.join(self.stage, "w.pid"), "w", encoding="utf-8") as fh:
            fh.write("999999\n")
        os.utime(os.path.join(self.stage, "w.pid"), (NOW - 120, NOW - 120))
        gs = self.snap()
        unlinked = next(row for row in gs.stages if row.unlinked)
        self.assertEqual([worker.name for worker in unlinked.snap.workers], ["w"])

    def test_open_session_is_active_at_two_minutes_and_idle_at_twenty(self) -> None:
        self.add_lead(10, ["claude"], self.project(), NOW - 3600)
        pid_path = os.path.join(self.stage, "w.pid")
        log_path = os.path.join(self.stage, "w.log")
        for age, expected in ((120, "active"), (840, "active"), (1200, "idle")):
            with open(pid_path, "w", encoding="utf-8") as fh:
                fh.write("999999\n")
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write("finished\n")
            os.utime(pid_path, (NOW - age, NOW - age))
            os.utime(log_path, (NOW - age, NOW - age))
            self.assertEqual(self.station(self.snap()).state, expected)

    def test_a_station_without_a_lead_is_never_solo(self) -> None:
        self.assertFalse(self.station(self.snap()).solo)

    def test_dismiss_refuses_a_path_that_is_not_a_found_stage(self) -> None:
        self.add_lead(20, ["claude"], self.repo("scratch"))
        (stage,) = self.virtual(self.snap())
        self.assertFalse(self.gc.dismiss(stage.path, NOW))
        self.assertFalse(self.gc.dismiss("/nowhere/.stage", NOW))
        self.assertEqual(collect.load_dismissed(self.dismissed), {})

    def test_current_lead_is_a_fresh_read(self) -> None:
        self.add_lead(10, ["claude"], self.project())
        self.snap()
        self.assertEqual(self.gc.current_lead(self.stage).pid, 10)
        self.argv[10] = ["claude", "-p", "--model", "x"]     # the same pid is now a worker
        self.assertIsNone(self.gc.current_lead(self.stage))


class Confirm(unittest.TestCase):
    """The end-session flow: the signal goes only to a pid that is still the station's lead at that moment."""

    def setUp(self) -> None:
        self.station = Stage(path="/r/proj/.stage", label="proj", last_activity=NOW,
                             lead_state="live", lead_pid=10, lead_since=NOW - 3600, lead_kind="claude")
        self.gs = GlobalSnapshot(now=NOW, stages=[self.station])
        self.gc = mock.Mock()
        self.gc.current_lead.return_value = collect._co_Lead(10, "claude", "live", "/r/proj", NOW - 3600)

    def test_y_signals_the_lead_pid_and_marks_the_station_closing(self) -> None:
        ui = UIState(allow_actions=True, close_ask=("/r/proj/.stage", 10))
        with mock.patch("app.os.kill") as kill:
            app._ap_end_lead(ui, self.gc)
        kill.assert_called_once_with(10, signal.SIGTERM)
        self.assertEqual(ui.closing, {"/r/proj/.stage"})
        self.assertIsNone(ui.close_ask)

    def test_a_pid_that_stopped_being_the_lead_is_never_signalled(self) -> None:
        self.gc.current_lead.return_value = None
        ui = UIState(allow_actions=True, close_ask=("/r/proj/.stage", 10))
        with mock.patch("app.os.kill") as kill:
            app._ap_end_lead(ui, self.gc)
        kill.assert_not_called()
        self.assertEqual(ui.closing, set())

    def test_a_different_pid_than_the_one_asked_about_is_never_signalled(self) -> None:
        ui = UIState(allow_actions=True, close_ask=("/r/proj/.stage", 99))
        with mock.patch("app.os.kill") as kill:
            app._ap_end_lead(ui, self.gc)
        kill.assert_not_called()

    def test_n_and_esc_cancel_without_signalling(self) -> None:
        for key in ("n", "esc"):
            ui = UIState(allow_actions=True, close_ask=("/r/proj/.stage", 10))
            with mock.patch("app.os.kill") as kill:
                app._ap_global_key(ui, key, self.gs, 30)
            self.assertIsNone(ui.close_ask)
            kill.assert_not_called()

    def test_other_keys_do_not_leave_the_box_or_act(self) -> None:
        ui = UIState(allow_actions=True, close_ask=("/r/proj/.stage", 10))
        self.assertEqual(app._ap_global_key(ui, "down", self.gs, 30), "")
        self.assertEqual(ui.close_ask, ("/r/proj/.stage", 10))

    def test_x_on_a_running_lead_asks_and_on_a_gone_station_dismisses(self) -> None:
        ui = UIState(allow_actions=True, gsel=1)
        app._ap_close_selected(ui, self.gs, self.gc)
        self.assertEqual(ui.close_ask, ("/r/proj/.stage", 10))
        self.gc.dismiss.assert_not_called()
        gone = Stage(path="/r/quiet/.stage", label="quiet", last_activity=NOW)
        ui = UIState(allow_actions=True, gsel=2, gopen={("", "more", "finished:/r/quiet")})
        app._ap_close_selected(ui, GlobalSnapshot(now=NOW, stages=[gone]), self.gc)
        self.gc.dismiss.assert_called_once_with("/r/quiet/.stage")
        self.assertIsNone(ui.close_ask)

    def test_x_on_a_gone_lead_with_running_workers_changes_nothing(self) -> None:
        busy = Stage(path="/r/busy/.stage", label="busy", last_activity=NOW, live=1,
                     snap=Snapshot(workers=[Worker("w", RUNNING, start=NOW - 5)]))
        ui = UIState()
        app._ap_close_selected(ui, GlobalSnapshot(now=NOW, stages=[busy]), self.gc)
        self.assertIsNone(ui.close_ask)
        self.gc.dismiss.assert_not_called()

    def test_a_closed_station_is_dismissed_once_its_lead_and_workers_are_gone(self) -> None:
        ui = UIState(closing={"/r/proj/.stage"})
        still = Stage(path="/r/proj/.stage", label="proj", last_activity=NOW, live=1, lead_state="gone")
        app._ap_settle_closing(ui, GlobalSnapshot(now=NOW, stages=[still]), self.gc)
        self.gc.dismiss.assert_not_called()
        done = Stage(path="/r/proj/.stage", label="proj", last_activity=NOW, live=0, lead_state="gone")
        app._ap_settle_closing(ui, GlobalSnapshot(now=NOW, stages=[done]), self.gc)
        self.gc.dismiss.assert_called_once_with("/r/proj/.stage")
        self.assertEqual(ui.closing, set())

    def test_the_mouse_on_a_card_title_selects_it_and_presses_x(self) -> None:
        other = Stage(path="/r/other/.stage", label="other", last_activity=NOW - 1)
        gs = GlobalSnapshot(now=NOW, stages=[self.station, other])
        ui = UIState(gsel=1, gopen={("", "more", "finished:/r/other")})
        ui.hits = [(2, 2, 9, 12, "close", "/r/other/.stage")]
        # the closed heading sits between the live station and the other one: two steps down
        self.assertEqual(app._ap_pointer_keys(ui, "click:10:2", gs, None, 0.0), ["down", "down", "x"])


class Overview(unittest.TestCase):
    """Badges, close targets and order on the overview cards."""

    def stage(self, path: str, label: str, state: str, live: int = 0, since: float = 0.0,
              last: float = NOW - 120) -> Stage:
        return Stage(path=path, label=label, last_activity=last, live=live,
                     snap=Snapshot(now=NOW), lead_state=state,
                     lead_since=since, lead_pid=5 if state != "gone" else None,
                     lead_kind="claude" if state != "gone" else "")

    def test_station_marks_show_active_stations_and_the_age_of_closed_ones(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[
            self.stage("/r/a/.stage", "alpha", "live"),
            self.stage("/r/b/.stage", "beta", "background", since=NOW - 17 * 3600),
            self.stage("/r/c/.stage", "gamma", "gone"),
        ])
        frame = [strip(line) for line in render_global(gs, UIState(gopen={("", "more", "finished:/r/c")}), 110, 30, NOW)]
        row = lambda name: next(line for line in frame if f" {name} " in line + " "
                                and any(mark in line for mark in "◆◐○◇"))
        self.assertIn("◆", row("alpha"))
        self.assertIn("◆", row("beta"))      # a background lead is still an active station
        self.assertIn("◇", row("gamma"))
        self.assertIn("finished 1", "\n".join(frame))

    def test_rows_put_active_stations_first_by_name_then_idle_newest_first(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[
            self.stage("/r/gone/.stage", "gone-one", "gone", last=NOW - 900),
            self.stage("/r/bg/.stage", "bg-one", "background", since=NOW - 50, last=NOW - 600),
            self.stage("/r/old/.stage", "old-one", "gone", last=NOW - 9000),
            self.stage("/r/live/.stage", "live-one", "live", live=0, last=NOW - 900),
        ])
        self.assertEqual([row[0] for row in global_rows(gs, UIState()) if row[1] == "stage"],
                         ["/r/bg/.stage", "/r/live/.stage"])

    def test_every_station_row_is_a_click_target(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[self.stage("/r/a/.stage", "alpha", "live"),
                                             self.stage("/r/b/.stage", "beta", "gone")])
        ui = UIState()
        frame = render_global(gs, ui, 100, 30, NOW)
        navs = [hit for hit in ui.hits if hit[4] == "nav"]
        self.assertEqual([hit[5] for hit in navs], ["0", "1", "2"])   # project, session, finished fold
        for r0, r1, _c0, c1, _, _ in navs:
            self.assertLessEqual(r0, r1)   # a row's now sub-line belongs to the same click target
            self.assertLessEqual(c1, 100)
        self.assertRegex(strip(frame[navs[1][0] - 1]), r"[◆◇◐] alpha")   # hits are 1-based once published
        self.assertIn("finished 1", strip(frame[navs[2][0] - 1]))

    def test_the_confirm_box_offers_y_and_n_as_click_targets(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[self.stage("/r/a/.stage", "ornith-tune", "live", since=NOW - 3600)])
        ui = UIState(close_ask=("/r/a/.stage", 5))
        raw = render_global(gs, ui, 100, 30, NOW)
        frame = [strip(line) for line in raw]
        self.assertIn("End Claude Code session in ornith-tune? (pid 5, running 1h)", "\n".join(frame))
        row = next(i for i, line in enumerate(frame) if "[y] end" in line)
        for key, text in (("y", "[y]"), ("n", "[n]")):
            r0, r1, c0, c1, _, _ = next(h for h in ui.hits if h[4] == "key" and h[5] == key)
            col = frame[row].index(text)   # hits are 1-based once published; the plain line has no wide cells here
            self.assertEqual((r0, r1), (row + 1, row + 1))
            self.assertTrue(c0 <= col + 1 <= c1, (key, c0, c1, col))
        self.assertTrue(any(hit[4] == "box" for hit in ui.hits))

    def test_plain_output_adds_the_lead_word_after_the_label(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[self.stage("/r/a/.stage", "alpha", "background", since=NOW - 3600)])
        first = render_global_plain(gs, 100).splitlines()[1]
        self.assertTrue(first.startswith("alpha   background"), first)


class SiblingStages(unittest.TestCase):
    def test_one_lead_is_credited_to_every_stage_dir_of_its_project(self):
        from collect import _co_assign_leads, _co_Lead
        lead = _co_Lead(42, "claude", "live", "/p/proj", 1.0)
        got = _co_assign_leads([lead], ["/p/proj/.stage", "/p/proj/.staging", "/p/other/.stage"], [])
        self.assertEqual(set(got), {"/p/proj/.stage", "/p/proj/.staging"})


class HomeFolderSession(unittest.TestCase):
    def test_a_session_in_home_is_its_own_project_but_one_above_home_is_not(self) -> None:
        self.assertEqual(collect._co_plain_root(HOME, HOME), HOME)
        self.assertEqual(collect._co_plain_root(HOME + "/", HOME), HOME)
        self.assertIsNone(collect._co_plain_root("/home", HOME))
        self.assertIsNone(collect._co_plain_root("/", HOME))
        self.assertEqual(collect._co_plain_root(HOME + "/agent-work", HOME), HOME + "/agent-work")

    def test_home_row_is_labelled_tilde(self) -> None:
        lead = collect._co_Lead(7, "opencode", "live", HOME, 0.0)
        labels = collect._co_virtual_labels({collect._co_virtual_path(HOME, 7): (HOME, lead)}, set(), HOME)
        self.assertEqual(list(labels.values()), ["~"])


if __name__ == "__main__":
    unittest.main()
