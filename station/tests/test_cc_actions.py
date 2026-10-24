"""Control center actions: refusals, signals, resume argv, the spawn picker and spawn. Real pids are only ours."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ccactions
from ccactions import (
    Choice,
    Target,
    can,
    confirm_text,
    run,
    spawn,
    spawn_choices,
    target_of,
)
from model import DONE, RUNNING, Stage, Worker

# ignores SIGTERM until killed: lets one child take a stop and then a kill
STUBBORN = ("import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(120)\n")


class Actions(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = self.root / "ledger" / "actions.jsonl"
        ccactions._CX_STOPS.clear()
        env = mock.patch.dict(os.environ, {"STATUS_ACTIONS_LOG": str(self.log)})
        env.start()
        self.addCleanup(env.stop)

    # ── helpers ──────────────────────────────────────────────────────────────

    def proc(self, argv: list[str], **kwargs: object) -> subprocess.Popen:
        """Start a process this test owns and clean it up however the test ends."""
        child = subprocess.Popen(argv, **kwargs)
        self.addCleanup(self._reap, child)
        return child

    def _reap(self, child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def sleep_proc(self, **kwargs: object) -> subprocess.Popen:
        return self.proc(["sleep", "60"], **kwargs)

    def stubborn(self) -> subprocess.Popen:
        child = self.proc([sys.executable, "-c", STUBBORN], stdout=subprocess.PIPE, text=True)
        assert child.stdout is not None
        self.assertEqual(child.stdout.readline().strip(), "ready")
        child.stdout.close()
        return child

    def target(self, pid: int | None, *, live: bool = True, start: int | None = None,
               label: str = "c2-events") -> Target:
        return Target(kind="worker", label=label, pid=pid, live=live,
                      start=ccactions._cac_start(pid) if start is None else start)

    def lines(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    # ── target_of ────────────────────────────────────────────────────────────

    def test_target_of_stage(self) -> None:
        child = self.sleep_proc()
        stage = Stage(path=str(self.root), label="c5-actions", lead_pid=child.pid, lead_state="live",
                      tag="CC·W", session_id="sid-1", tmux="tmux-1", cwd=str(self.root),
                      resume_cmd="claude --resume sid-1")
        target = target_of(stage)
        self.assertEqual((target.kind, target.label, target.pid, target.harness, target.session_id,
                          target.tmux, target.cwd, target.live, target.resume_cmd),
                         ("stage", "c5-actions", child.pid, "claude", "sid-1", "tmux-1",
                          str(self.root), True, "claude --resume sid-1"))
        self.assertEqual(target.start, ccactions._cac_start(child.pid))

    def test_target_of_harness_hints(self) -> None:
        cases = {"CDX": "codex", "CC·P": "claude", "CC·W": "claude", "OCode": "opencode",
                 "OClaw": "openclaw", "CUR": "cursor"}
        for tag, want in cases.items():
            self.assertEqual(target_of(Stage(path="/x", label=tag, tag=tag, lead_state="gone")).harness, want, tag)
        self.assertEqual(target_of(Stage(path="/x", label="k", lead_kind="codex", lead_state="gone")).harness,
                         "codex")
        self.assertEqual(target_of(Stage(path="/x", label="c", lead_kind="claude work",
                                        lead_state="live")).harness, "claude")
        self.assertEqual(target_of(Stage(path="/x", label="h", harness="opencode", tag="CC·W")).harness,
                         "opencode")
        self.assertFalse(target_of(Stage(path="/x", label="g", lead_state="gone")).live)

    def test_target_of_worker(self) -> None:
        child = self.sleep_proc()
        stage = Stage(path="/base", label="row", dir="/base/.stage")
        worker = Worker(name="w1", state=RUNNING, pid=child.pid, harness="codex", native_id="nid-1",
                        resume_cmd="codex resume nid-1")
        target = target_of(stage, worker)
        self.assertEqual((target.kind, target.label, target.pid, target.harness, target.session_id,
                          target.cwd, target.live, target.start),
                         ("worker", "w1", child.pid, "codex", "nid-1", "/base/.stage", True,
                          ccactions._cac_start(child.pid)))
        worker.state = DONE
        self.assertFalse(target_of(stage, worker).live)

    # ── can: refusals ────────────────────────────────────────────────────────

    def test_refuses_unknown_actions_and_missing_pids(self) -> None:
        self.assertEqual(can("frobnicate", Target(kind="worker", label="x")), (False, "unknown action"))
        self.assertEqual(can("stop", Target(kind="worker", label="x")), (False, "no pid"))
        self.assertEqual(can("stop", Target(kind="worker", label="x", pid=os.getpid())), (False, "not running"))

    def test_refuses_pid_1(self) -> None:
        self.assertEqual(can("stop", Target(kind="worker", label="init", pid=1, live=True)),
                         (False, "pid 1 is not ours"))
        self.assertEqual(can("kill", Target(kind="worker", label="init", pid=1, live=True)),
                         (False, "pid 1 is not ours"))
        result = run("stop", Target(kind="worker", label="init", pid=1, live=True))
        self.assertEqual(result, "refused: pid 1 is not ours")

    def test_refuses_this_process_and_its_parents(self) -> None:
        me = self.target(os.getpid())
        self.assertEqual(can("stop", me), (False, "that is this terminal"))
        self.assertEqual(can("kill", me), (False, "that is this terminal"))
        self.assertEqual(run("stop", me), "refused: that is this terminal")
        parent = os.getppid()
        if parent > 1:
            target = self.target(parent)
            self.assertEqual(can("stop", target), (False, "that is this terminal"))

    def test_refuses_another_users_process(self) -> None:
        child = self.sleep_proc()
        seen: list[int] = []

        def other_uid(pid: int) -> int:
            seen.append(pid)
            return os.getuid() + 1

        self.assertEqual(can("stop", self.target(child.pid), uid=other_uid),
                         (False, "another user's process"))
        self.assertEqual(seen, [child.pid])

    def test_refuses_a_real_foreign_pid(self) -> None:
        foreign = self._foreign_pid()
        if foreign is None:
            self.skipTest("no process of another user on this machine")
        self.assertEqual(can("stop", Target(kind="worker", label="other", pid=foreign, live=True)),
                         (False, "another user's process"))

    def _foreign_pid(self) -> int | None:
        mine = os.getuid()
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or entry in ("1", str(os.getpid())):
                continue
            try:
                for line in (Path("/proc") / entry / "status").read_text().splitlines():
                    if line.startswith("Uid:"):
                        if int(line.split()[1]) != mine:
                            return int(entry)
                        break
            except (OSError, ValueError):
                continue
        return None

    def test_refuses_a_pid_that_is_gone(self) -> None:
        child = self.sleep_proc()
        pid = child.pid
        child.kill()
        child.wait()
        self.assertEqual(can("stop", Target(kind="worker", label="gone", pid=pid, live=True)),
                         (False, "process is gone"))

    def test_refuses_a_recycled_pid(self) -> None:
        child = self.sleep_proc()
        target = self.target(child.pid, start=(ccactions._cac_start(child.pid) or 0) + 1)
        self.assertEqual(can("stop", target), (False, "pid was reused"))
        sent: list[tuple[int, int]] = []
        result = run("stop", target, kill=lambda pid, sig: sent.append((pid, sig)))
        self.assertEqual(result, "refused: pid was reused")
        self.assertEqual(sent, [])
        self.assertIsNone(child.poll())

    def test_signal_failure_is_refused_and_not_recorded(self) -> None:
        child = self.sleep_proc()

        def boom(pid: int, sig: int) -> None:
            raise PermissionError(1, "Operation not permitted")

        result = run("stop", self.target(child.pid), kill=boom)
        self.assertTrue(result.startswith("refused: signal failed:"))
        self.assertIn("Operation not permitted", result)
        self.assertIsNone(child.poll())
        self.assertNotIn(child.pid, ccactions._CX_STOPS)

    # ── can/run: stop and kill ───────────────────────────────────────────────

    def test_stop_terminates_a_sleep(self) -> None:
        child = self.sleep_proc()
        result = run("stop", self.target(child.pid))
        self.assertEqual(result, f"stopped c2-events (SIGTERM, pid {child.pid})")
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)

    def test_stop_then_kill_reach_a_real_process(self) -> None:
        child = self.stubborn()
        target = self.target(child.pid, label="c5-actions")
        result = run("stop", target, now=time.time() - 4)
        self.assertEqual(result, f"stopped c5-actions (SIGTERM, pid {child.pid})")
        time.sleep(0.2)
        self.assertIsNone(child.poll())          # it ignores SIGTERM
        self.assertEqual(can("kill", target), (True, ""))
        result = run("kill", target)
        self.assertEqual(result, f"killed c5-actions (SIGKILL, pid {child.pid})")
        self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)

    def test_kill_needs_a_stop_first(self) -> None:
        child = self.stubborn()
        target = self.target(child.pid)
        self.assertEqual(can("kill", target), (False, "send stop first"))
        sent: list[tuple[int, int]] = []
        self.assertEqual(run("kill", target, kill=lambda pid, sig: sent.append((pid, sig))),
                         "refused: send stop first")
        self.assertEqual(sent, [])
        ccactions._CX_STOPS[child.pid] = time.time() - 1
        ok, why = can("kill", target)
        self.assertFalse(ok)
        self.assertIn("wait 3s", why)
        ccactions._CX_STOPS[child.pid] = time.time() - 4
        self.assertEqual(can("kill", target), (True, ""))
        self.assertIsNone(child.poll())

    def test_group_signal_only_when_the_pid_leads_its_own_group(self) -> None:
        plain = self.sleep_proc()
        sent: list[tuple[int, int]] = []
        self.assertTrue(run("stop", self.target(plain.pid), kill=lambda pid, sig: sent.append((pid, sig))))
        self.assertEqual(sent, [(plain.pid, signal.SIGTERM)])
        leader = self.sleep_proc(start_new_session=True)
        sent.clear()
        self.assertTrue(run("stop", self.target(leader.pid), kill=lambda pid, sig: sent.append((pid, sig))))
        self.assertEqual(sent, [(-leader.pid, signal.SIGTERM)])

    # ── pidfd signalling ─────────────────────────────────────────────────────

    @unittest.skipUnless(hasattr(os, "pidfd_open"), "needs os.pidfd_open (Python 3.13+ on Linux)")
    def test_pidfd_refuses_a_start_time_mismatch(self) -> None:
        """The re-read after pidfd_open: can() refuses a stale Target first, so the helper is exercised directly."""
        child = self.sleep_proc()
        start = ccactions._cac_start(child.pid)
        self.assertIsNotNone(start)
        assert start is not None
        self.assertEqual(ccactions._cac_pidfd_signal(child.pid, start + 1, signal.SIGTERM), "pid changed")
        self.assertIsNone(child.poll())

    @unittest.skipUnless(hasattr(os, "pidfd_open"), "needs os.pidfd_open (Python 3.13+ on Linux)")
    def test_pidfd_refuses_a_vanished_pid(self) -> None:
        with mock.patch.object(ccactions.os, "pidfd_open", create=True,
                               side_effect=ProcessLookupError(3, "No such process")):
            self.assertEqual(ccactions._cac_pidfd_signal(999999, None, signal.SIGTERM), "process already gone")
        child = self.sleep_proc()
        pid, start = child.pid, ccactions._cac_start(child.pid)
        child.kill()
        child.wait()
        # the recorded start can never match a recycled pid, so this refusal can never signal anything
        self.assertIn(ccactions._cac_pidfd_signal(pid, start, signal.SIGTERM),
                      ("process already gone", "pid changed"))

    def test_pidfd_stop_of_a_group_leader(self) -> None:
        """A group cannot be signalled via pidfd: the leader is checked through the fd, then killpg."""
        child = self.sleep_proc(start_new_session=True)
        self.assertEqual(run("stop", self.target(child.pid)),
                         f"stopped c2-events (SIGTERM, pid {child.pid})")
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)

    def test_fallback_numeric_signal_without_pidfd(self) -> None:
        child = self.sleep_proc()
        with mock.patch.object(ccactions, "_cac_PIDFD", False), \
                mock.patch.object(ccactions.os, "pidfd_open", create=True, side_effect=AssertionError("pidfd used")):
            self.assertEqual(run("stop", self.target(child.pid)),
                             f"stopped c2-events (SIGTERM, pid {child.pid})")
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)

    def test_injected_kill_is_used_even_when_pidfd_exists(self) -> None:
        child = self.sleep_proc()
        sent: list[tuple[int, int]] = []
        with mock.patch.object(ccactions.os, "pidfd_open", create=True, side_effect=AssertionError("pidfd used")):
            self.assertTrue(run("stop", self.target(child.pid), kill=lambda pid, sig: sent.append((pid, sig))))
        self.assertEqual(sent, [(child.pid, signal.SIGTERM)])
        self.assertIsNone(child.poll())

    # ── resume ───────────────────────────────────────────────────────────────

    def test_resume_builds_a_shell_free_argv(self) -> None:
        target = Target(kind="worker", label="c1-codexread", cwd=str(self.root),
                        resume_cmd="codex resume 019e-abc_def:1.2-3")
        self.assertEqual(can("resume", target), (True, ""))
        seen: list[tuple[list[str], str]] = []
        with mock.patch.dict(os.environ, {"LAUNCH_TERMINAL": "kitty"}):
            result = run("resume", target, launch=lambda argv, cwd: seen.append((argv, cwd)))
        self.assertEqual(result, "resumed c1-codexread (tmux resume-c1-codexread)")
        self.assertEqual(seen, [(["kitty", "-e", "tmux", "new-session", "-A", "-s", "resume-c1-codexread",
                                  "-c", str(self.root), "codex", "resume", "019e-abc_def:1.2-3"],
                                 str(self.root))])

    def test_resume_defaults_to_ghostty_and_sanitizes_the_name(self) -> None:
        target = Target(kind="worker", label="weird row/../x", cwd=str(self.root),
                        resume_cmd="claude --resume abc")
        seen: list[list[str]] = []
        with mock.patch.dict(os.environ) as env:
            env.pop("LAUNCH_TERMINAL", None)
            run("resume", target, launch=lambda argv, cwd: seen.append(argv))
        argv = seen[0]
        self.assertEqual(argv[0], "ghostty")
        name = argv[argv.index("-s") + 1]
        self.assertRegex(name, r"^[A-Za-z0-9._:-]{1,64}$")
        self.assertTrue(name.startswith("resume-"))
        self.assertEqual(argv[argv.index("-c") + 1], str(self.root))
        self.assertEqual(argv[-2:], ["--resume", "abc"])

    def test_resume_refuses_a_live_target(self) -> None:
        target = Target(kind="stage", label="row", cwd=str(self.root), resume_cmd="codex resume abc",
                        live=True)
        self.assertEqual(can("resume", target), (False, "session is running"))
        seen: list[list[str]] = []
        self.assertEqual(run("resume", target, launch=lambda argv, cwd: seen.append(argv)),
                         "refused: session is running")
        self.assertEqual(seen, [])

    def test_resume_refusals(self) -> None:
        self.assertEqual(can("resume", Target(kind="worker", label="x")), (False, "no resume command"))
        seen: list[list[str]] = []
        launch = lambda argv, cwd: seen.append(argv)
        missing = Target(kind="worker", label="x", cwd=str(self.root / "nope"), resume_cmd="codex resume a")
        self.assertEqual(run("resume", missing, launch=launch), "refused: no such directory")
        quoted = Target(kind="worker", label="x", cwd=str(self.root), resume_cmd="codex resume 'unclosed")
        self.assertEqual(run("resume", quoted, launch=launch), "refused: unsafe resume command")
        self.assertEqual(seen, [])

    def test_resume_accepts_only_the_two_known_shapes(self) -> None:
        """An id is one plain token: nothing can smuggle an option into the new terminal."""
        for cmd in ("codex resume 019e-abc", "codex resume A",
                    "claude --resume abc.def:1_2-3", "codex resume " + "a" * 128):
            target = Target(kind="worker", label="x", cwd=str(self.root), resume_cmd=cmd)
            self.assertEqual(can("resume", target), (True, ""), cmd)
        bad = ["abc --config foo=bar", "codex resume abc --config foo=bar", "codex resume -x",
               "claude --resume -x", "codex resume a;b", "codex resume a b", "codex resume",
               "codex  resume abc", " codex resume abc", "codex resume abc ",
               "codex --resume abc", "claude resume abc", "sh -c id", "codex resume " + "a" * 129]
        seen: list[list[str]] = []
        for cmd in bad:
            target = Target(kind="worker", label="x", cwd=str(self.root), resume_cmd=cmd)
            self.assertEqual(can("resume", target), (False, "unsafe resume command"), repr(cmd))
            self.assertEqual(run("resume", target, launch=lambda argv, cwd: seen.append(argv)),
                             "refused: unsafe resume command", repr(cmd))
        self.assertEqual(seen, [])

    def test_run_does_not_spawn(self) -> None:
        target = Target(kind="stage", label="x", cwd=str(self.root))
        self.assertEqual(can("spawn", target), (True, ""))
        self.assertEqual(can("spawn", Target(kind="stage", label="x")), (False, "no such directory"))
        self.assertEqual(run("spawn", target), "refused: spawn goes through spawn()")

    # ── confirm line ─────────────────────────────────────────────────────────

    def test_confirm_text(self) -> None:
        target = Target(kind="worker", label="c2-events", pid=4121, live=True,
                        resume_cmd="codex resume 019e-abc")
        stop = confirm_text("stop", target)
        self.assertEqual(stop, "Stop c2-events (pid 4121)?  any other key cancels (y/n)")
        self.assertTrue(stop.endswith("(y/n)"))
        self.assertLess(len(stop), 100)
        kill = confirm_text("kill", target)
        self.assertIn("Kill c2-events (pid 4121)", kill)
        self.assertTrue(kill.endswith("(y/n)"))
        self.assertLess(len(kill), 100)
        resume = confirm_text("resume", Target(kind="worker", label="c1-codexread", cwd="/tmp",
                                               resume_cmd="codex resume 019e-abc"))
        self.assertEqual(resume, "Resume c1-codexread in a new terminal: codex resume 019e-abc?  (y/n)")

    def test_confirm_text_cuts_a_long_command_and_keeps_the_label(self) -> None:
        line = confirm_text("resume", Target(kind="worker", label="a" * 60, resume_cmd="claude --resume " + "9" * 200))
        self.assertLessEqual(len(line), 99)
        self.assertTrue(line.endswith("?  (y/n)"))
        self.assertIn("…", line)
        self.assertIn("Resume ", line)
        long_stop = confirm_text("stop", Target(kind="worker", label="z" * 80, pid=99))
        self.assertLess(len(long_stop), 100)
        self.assertIn("z" * 20, long_stop)

    # ── the actions log ──────────────────────────────────────────────────────

    def test_every_run_appends_one_json_line(self) -> None:
        target = Target(kind="worker", label="c2-events", pid=1, live=True, harness="claude",
                        session_id="sid-1")
        result = run("stop", target, now=1000.0)
        self.assertTrue(result.startswith("refused:"))
        self.assertEqual(self.lines(), [{"ts": 1000.0, "action": "stop", "label": "c2-events", "pid": 1,
                                         "harness": "claude", "session_id": "sid-1", "result": result}])
        run("stop", target, now=1001.0)
        self.assertEqual(len(self.lines()), 2)

    def test_log_failure_is_swallowed(self) -> None:
        blocker = self.root / "file"
        blocker.write_text("x", encoding="utf-8")
        os.environ["STATUS_ACTIONS_LOG"] = str(blocker / "sub" / "actions.jsonl")
        result = run("stop", Target(kind="worker", label="x", pid=1, live=True), now=1.0)
        self.assertTrue(result.startswith("refused:"))

    # ── spawn choices ────────────────────────────────────────────────────────

    def picker_fixture(self) -> tuple[Path, Path]:
        guards = {"allowedModels": {"models": ["tight", "rev", "never", "paid", "missing", "good",
                                               "freeok", "edge", "nobal"]},
                  "payPerToken": {"wallet": {"creditAccount": "wallet-account", "minBalanceUsd": 2},
                                  "wallet2": {"creditAccount": "empty-wallet", "minBalanceUsd": 2}},
                  "minHeadroomPct": 5}
        models = {"subscriptions": {"go": {"account": "go"}, "free": {"unmetered": True},
                                    "wallet": {"account": "wallet-account", "credits": True},
                                    "wallet2": {"account": "empty-wallet", "credits": True}},
                  "models": [
                      {"id": "good", "cli": "opencode", "subscription": "go"},
                      {"id": "tight", "cli": "opencode", "subscription": "tight-sub"},
                      {"id": "edge", "cli": "codex", "subscription": "edge-sub"},
                      {"id": "rev", "cli": "opencode", "subscription": "free", "onlyJobs": ["review"]},
                      {"id": "never", "cli": "codex", "subscription": "go", "neverJobs": ["build"]},
                      {"id": "paid", "cli": "opencode", "subscription": "wallet"},
                      {"id": "nobal", "cli": "opencode", "subscription": "wallet2"},
                      {"id": "freeok", "cli": "opencode", "subscription": "free"},
                  ]}
        quota = {"subscriptions": [
            {"id": "go", "limits": [{"window": "five_hour", "name": "5h", "usedPct": 20},
                                    {"window": "seven_day", "name": "Week", "usedPct": 40}]},
            {"id": "tight-sub", "limits": [{"window": "five_hour", "name": "5h", "usedPct": 97}]},
            {"id": "edge-sub", "limits": [{"window": "five_hour", "name": "5h", "usedPct": 95}]},
            {"id": "wallet", "limits": [{"window": "five_hour", "name": "5h", "usedPct": 1}]},
        ], "credits": [{"account": "wallet-account", "balanceUsd": 1.5}]}
        guards_path = self.root / "guards.json"
        models_path = self.root / "models.json"
        guards_path.write_text(json.dumps(guards), encoding="utf-8")
        models_path.write_text(json.dumps(models), encoding="utf-8")
        self.quota = quota
        return guards_path, models_path

    def test_spawn_choices_marks_what_is_not_usable(self) -> None:
        guards, models = self.picker_fixture()
        calls: list[list[str]] = []

        def runner(argv: list[str]) -> str:
            calls.append(argv)
            return json.dumps(self.quota)

        picks = spawn_choices(runner, guards_path=str(guards), models_path=str(models))
        self.assertEqual(calls[0][1:], ["status", "--json"])
        self.assertEqual([p.id for p in picks],
                         ["good", "freeok", "edge", "tight", "rev", "never", "paid", "missing", "nobal"])
        by_id = {p.id: p for p in picks}
        self.assertTrue(by_id["good"].ok)
        self.assertTrue(by_id["freeok"].ok)
        self.assertTrue(by_id["edge"].ok)            # exactly 5% headroom is still offered
        self.assertEqual(by_id["tight"].reason, "headroom 3%")
        self.assertEqual(by_id["rev"].reason, "review only")
        self.assertEqual(by_id["never"].reason, "never builds")
        self.assertEqual(by_id["paid"].reason, "credits $1.50")
        self.assertEqual(by_id["nobal"].reason, "credits unknown")
        self.assertEqual(by_id["missing"].reason, "not in models.json")
        self.assertEqual(by_id["good"].label, "good  go Week 40%")     # the tightest window
        self.assertEqual(by_id["paid"].label, "paid  wallet wallet-account $1.50")
        self.assertEqual(by_id["nobal"].label, "nobal  wallet2 empty-wallet")
        for pick in picks:
            self.assertLess(len(pick.label), 60)

    def test_spawn_choices_unavailable(self) -> None:
        guards, models = self.picker_fixture()
        unavailable = [Choice(id="", label="picker unavailable", ok=False)]

        def boom(argv: list[str]) -> str:
            raise OSError("quota is down")

        self.assertEqual(spawn_choices(boom, guards_path=str(guards), models_path=str(models)), unavailable)
        self.assertEqual(spawn_choices(lambda argv: "not json", guards_path=str(guards),
                                       models_path=str(models)), unavailable)
        self.assertEqual(spawn_choices(lambda argv: json.dumps(self.quota),
                                       guards_path=str(self.root / "no-guards.json"),
                                       models_path=str(models)), [])
        broken = self.root / "broken.json"
        broken.write_text("{", encoding="utf-8")
        self.assertEqual(spawn_choices(lambda argv: json.dumps(self.quota), guards_path=str(broken),
                                       models_path=str(models)), unavailable)
        no_allow = self.root / "no-allow.json"
        no_allow.write_text(json.dumps({"payPerToken": {}}), encoding="utf-8")
        self.assertEqual(spawn_choices(lambda argv: json.dumps(self.quota), guards_path=str(no_allow),
                                       models_path=str(models)), unavailable)

    def test_spawn_choices_are_hidden_when_picker_files_are_missing(self) -> None:
        with mock.patch.object(ccactions, "_CX_GUARDS", str(self.root / "absent-guards.json")), \
                mock.patch.object(ccactions, "_CX_MODELS", str(self.root / "absent-models.json")):
            self.assertEqual(spawn_choices(), [])

    def test_spawn_choices_are_hidden_when_required_binaries_are_missing(self) -> None:
        guards, models = self.picker_fixture()
        with mock.patch.object(ccactions, "_CX_GUARDS", str(guards)), \
                mock.patch.object(ccactions, "_CX_MODELS", str(models)), \
                mock.patch.object(ccactions, "_CX_QUOTA_BIN", str(self.root / "no-quota")):
            self.assertEqual(spawn_choices(), [])
        with mock.patch.object(ccactions, "_CX_GUARDS", str(guards)), \
                mock.patch.object(ccactions, "_CX_MODELS", str(models)), \
                mock.patch.object(ccactions, "_CX_QUOTA_BIN", sys.executable), \
                mock.patch.object(ccactions, "_CX_DISPATCH", str(self.root / "no-dispatch")), \
                mock.patch.object(ccactions, "_CX_LAUNCH_SESSION", str(self.root / "no-launch-session")), \
                mock.patch.object(ccactions, "_cac_default_runner", return_value=json.dumps(self.quota)):
            self.assertEqual(spawn_choices(), [])

    # ── spawn ────────────────────────────────────────────────────────────────

    def spawn_fixture(self, model_id: str, cli: str) -> tuple[Path, Path]:
        models_path = self.root / "spawn-models.json"
        models_path.write_text(json.dumps({"models": [{"id": model_id, "cli": cli,
                                                       "subscription": "go"}]}), encoding="utf-8")
        project = self.root / "proj"
        project.mkdir(exist_ok=True)
        return models_path, project

    def test_spawn_writes_a_dispatch_brief(self) -> None:
        models, project = self.spawn_fixture("deepseek", "opencode")
        seen: list[tuple[list[str], str]] = []
        task = "Parse the codex rollouts\nand keep it short"
        picks = [Choice(id="deepseek", label="deepseek  go 5h 0%", ok=True)]
        result = spawn("deepseek", str(project), task, choices=picks, models_path=str(models),
                       launch=lambda argv, cwd: seen.append((argv, cwd)))
        argv, cwd = seen[0]
        name = argv[-1]
        self.assertRegex(name, r"^cc-[0-9a-f]{6}$")
        self.assertEqual(result, f"spawned deepseek ({name}) via dispatch in {project}")
        self.assertEqual(argv, [os.path.expanduser("~/.local/state/station/bin/dispatch"), "run",
                                str(project / ".stage"), name])
        self.assertEqual(cwd, str(project))
        brief = (project / ".stage" / "briefs" / f"{name}.md").read_text(encoding="utf-8")
        head = brief.splitlines()
        self.assertEqual(head[0], "# Parse the codex rollouts and keep it short")
        self.assertIn("Goal: Parse the codex rollouts and keep it short", head)
        self.assertIn("Job: build", head)
        self.assertIn("Model: deepseek", head)
        self.assertIn("Mode: write", head)
        self.assertIn(f"Cwd: {project}", head)
        self.assertTrue(brief.endswith(task + "\n"))

    def test_spawn_writes_a_claude_prompt_and_launches_it(self) -> None:
        models, project = self.spawn_fixture("haiku-api", "claude")
        seen: list[tuple[list[str], str]] = []
        picks = [Choice(id="haiku-api", label="haiku-api  claude 5h 10%", ok=True)]
        result = spawn("haiku-api", str(project), "Fix the failing tests", choices=picks,
                       models_path=str(models), launch=lambda argv, cwd: seen.append((argv, cwd)))
        argv, cwd = seen[0]
        name = argv[3]
        self.assertRegex(name, r"^cc-[0-9a-f]{6}$")
        self.assertEqual(argv[:3], [os.path.expanduser("~/.local/state/station/bin/launch-session"),
                                    "--model", "haiku"])        # the picker id up to "-"
        self.assertEqual(argv[3:], [name, str(project), str(project / ".stage" / "ops-spawn" / f"{name}.md")])
        self.assertEqual(cwd, str(project))
        prompt = (project / ".stage" / "ops-spawn" / f"{name}.md").read_text(encoding="utf-8")
        self.assertEqual(prompt, "Fix the failing tests\n")
        self.assertEqual(result, f"spawned haiku-api ({name}) as a claude session in {project}")

    def test_spawn_refusals_write_nothing(self) -> None:
        models, project = self.spawn_fixture("deepseek", "opencode")
        seen: list[list[str]] = []
        launch = lambda argv, cwd: seen.append(argv)
        ok = [Choice(id="deepseek", label="", ok=True)]
        bad = [Choice(id="deepseek", label="", ok=False, reason="review only")]
        self.assertEqual(spawn("mystery", str(project), "task", choices=ok, models_path=str(models),
                               launch=launch), "refused: unknown model 'mystery'")
        self.assertEqual(spawn("deepseek", str(project), "task", choices=bad, models_path=str(models),
                               launch=launch), "refused: deepseek: review only")
        self.assertEqual(spawn("deepseek", str(project / "nope"), "task", choices=ok,
                               models_path=str(models), launch=launch),
                         f"refused: no such directory: {project / 'nope'}")
        self.assertEqual(spawn("deepseek", str(project), " \n\t", choices=ok, models_path=str(models),
                               launch=launch), "refused: empty task")
        self.assertEqual(seen, [])
        self.assertFalse((project / ".stage").exists())

    def test_default_launch_is_detached_and_writes_its_log(self) -> None:
        """The Popen path spawn uses for dispatch: no wait, output into the log file."""
        log = self.root / "dispatch-cc-test.log"
        child = ccactions._cac_popen([sys.executable, "-c", "print('hello from worker')"],
                                    str(self.root), str(log))
        self.assertEqual(child.wait(timeout=10), 0)
        self.assertIn("hello from worker", log.read_text(encoding="utf-8"))

    def test_spawn_reports_a_failed_launch(self) -> None:
        models, project = self.spawn_fixture("deepseek", "opencode")

        def boom(argv: list[str], cwd: str) -> None:
            raise OSError(13, "Permission denied")

        result = spawn("deepseek", str(project), "task", choices=[Choice(id="deepseek", label="", ok=True)],
                       models_path=str(models), launch=boom)
        self.assertTrue(result.startswith("refused: launch failed:"))
        self.assertIn("Permission denied", result)

    def test_spawn_names_are_unique_and_logged_without_the_task(self) -> None:
        models, project = self.spawn_fixture("deepseek", "opencode")
        picks = [Choice(id="deepseek", label="", ok=True)]
        task = "SECRET-TASK-TEXT do the thing"
        names = set()
        for _ in range(3):
            result = spawn("deepseek", str(project), task, choices=picks, models_path=str(models),
                           launch=lambda argv, cwd: None, now=2000.0)
            names.add(result.split("(")[1].split(")")[0])
        self.assertEqual(len(names), 3)
        for name in names:
            self.assertTrue((project / ".stage" / "briefs" / f"{name}.md").is_file())
        logged = self.lines()
        self.assertEqual(len(logged), 3)
        self.assertEqual(logged[0]["ts"], 2000.0)
        self.assertEqual((logged[0]["action"], logged[0]["label"], logged[0]["harness"]),
                         ("spawn", "deepseek", "opencode"))
        self.assertIn(logged[0]["session_id"], names)
        self.assertNotIn("SECRET-TASK-TEXT", self.log.read_text(encoding="utf-8"))
