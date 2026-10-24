from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import collect
from model import (
    ACT_BUCKETS,
    DONE,
    FAILED,
    NOREPORT,
    QUIET,
    RUNNING,
    STALLED,
    TOOL_RING_S,
    Finding,
    LogState,
    Step,
)

DEAD_PID = 4194304  # beyond a normal pid_max, so /proc never has it


class Base(unittest.TestCase):
    def _restore_history(self) -> None:
        if self._old_history is None:
            os.environ.pop("STATUS_USAGE_HISTORY", None)
        else:
            os.environ["STATUS_USAGE_HISTORY"] = self._old_history

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.stage = self._tmp.name
        self._old_quota = os.environ.get("STATUS_QUOTA_CMD")
        self._old_openrouter = os.environ.get("STATUS_OPENROUTER_CMD")
        os.environ["STATUS_QUOTA_CMD"] = "/bin/false"
        os.environ["STATUS_OPENROUTER_CMD"] = "/bin/false"
        # Keep usage samples out of the real state directory.
        self._old_history = os.environ.get("STATUS_USAGE_HISTORY")
        os.environ["STATUS_USAGE_HISTORY"] = os.path.join(self._tmp.name, "usage-history.jsonl")
        self.addCleanup(self._restore_history)
        self.addCleanup(self._restore_quota)

    def _restore_quota(self) -> None:
        if self._old_quota is None:
            os.environ.pop("STATUS_QUOTA_CMD", None)
        else:
            os.environ["STATUS_QUOTA_CMD"] = self._old_quota
        if self._old_openrouter is None:
            os.environ.pop("STATUS_OPENROUTER_CMD", None)
        else:
            os.environ["STATUS_OPENROUTER_CMD"] = self._old_openrouter

    def write(self, rel: str, content, mtime: float | None = None) -> str:
        path = os.path.join(self.stage, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if isinstance(content, bytes):
            with open(path, "wb") as fh:
                fh.write(content)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def collector(self, stage: str | None = None) -> collect.Collector:
        proc = collect.Collector(stage or self.stage)
        self.addCleanup(proc.close)
        return proc

    def add_dead(self, name: str, start: float, end: float | None = None,
                 log_text: str = "line\n") -> None:
        self.write(f"{name}.pid", str(DEAD_PID), mtime=start)
        self.write(f"{name}.log", log_text, mtime=start if end is None else end)

    def anchor(self, start: float) -> None:
        """A dead worker whose early start keeps later milestones in-session."""
        self.add_dead("anchor", start)

    def one(self, snapshot, name: str):
        for worker in snapshot.workers:
            if worker.name == name:
                return worker
        self.fail(f"worker {name!r} not in snapshot")


class TestWorkers(Base):
    def test_live_worker(self) -> None:
        self.write("live.pid", str(os.getpid()))
        self.write("live.log", "hello\nworld\n")
        self.write("briefs/live.md", "# Live worker\n")
        proc = self.collector()
        first = proc.snapshot()
        worker = self.one(first, "live")
        self.assertEqual(worker.state, RUNNING)
        self.assertIsNotNone(worker.rss)
        self.assertIsNotNone(worker.nproc)
        self.assertGreaterEqual(worker.nproc, 1)
        self.assertEqual(len(worker.activity), ACT_BUCKETS)
        self.assertIsNone(worker.cpu)
        second = proc.snapshot()
        self.assertIsInstance(self.one(second, "live").cpu, float)

    def test_dead_log_cache(self) -> None:
        now = time.time()
        self.add_dead("c1", now - 300, now - 300)
        real = collect.parse_log
        calls: list[str] = []

        def counting(path, prev=None, now=None):
            calls.append(path)
            return real(path, prev=prev, now=now)

        proc = self.collector()
        with mock.patch.object(collect, "parse_log", side_effect=counting):
            proc.snapshot(now=now)
            self.assertEqual(len(calls), 1)
            proc.snapshot(now=now)
            self.assertEqual(len(calls), 1)
            self.write("c1.log", "changed\n", mtime=now - 100)
            proc.snapshot(now=now)
            self.assertEqual(len(calls), 2)

    def test_pid_reuse_is_dead(self) -> None:
        old = time.time() - 100000
        self.write("reused.pid", str(os.getpid()), mtime=old)
        self.write("reused.log", "hi\n", mtime=old)
        self.write("briefs/reused.md", "# Reused\n")
        worker = self.one(self.collector().snapshot(), "reused")
        self.assertNotIn(worker.state, (RUNNING, QUIET, STALLED))
        self.assertEqual(worker.state, NOREPORT)

    def test_dead_done_with_report(self) -> None:
        start = time.time() - 500
        self.add_dead("w1", start)
        self.write(
            "w1/REPORT.md",
            "# Report\n\n## Summary\n- did A\n* did B\n   \n- tolerated\n",
        )
        worker = self.one(self.collector().snapshot(), "w1")
        self.assertEqual(worker.state, DONE)
        self.assertTrue(worker.has_report)
        self.assertEqual(worker.summary, ["did A", "did B", "tolerated"])

    def test_dead_done_review(self) -> None:
        start = time.time() - 500
        self.write("reviewkeeper.pid", str(DEAD_PID), mtime=start)
        self.write("reviewkeeper.log", "review output\n", mtime=start)
        worker = self.one(self.collector().snapshot(), "reviewkeeper")
        self.assertEqual(worker.state, DONE)

    def test_dead_failed(self) -> None:
        start = time.time() - 500
        self.add_dead("f1", start)

        def fake(path, prev=None, now=None):
            return LogState(path=path, steps=[Step(0.0, "error", "", "boom")], last_ts=start)

        with mock.patch.object(collect, "parse_log", side_effect=fake):
            worker = self.one(self.collector().snapshot(), "f1")
        self.assertEqual(worker.state, FAILED)

    def test_dead_failed_stdout_only_brief_stays_failed(self) -> None:
        start = time.time() - 500
        self.add_dead("f1", start)
        self.write("briefs/f1.md", "# Plan\n\nJob: plan\nGoal: g\nOutput: stdout only.\n")

        def fake(path, prev=None, now=None):
            return LogState(path=path, steps=[Step(0.0, "error", "", "boom")], last_ts=start)

        with mock.patch.object(collect, "parse_log", side_effect=fake):
            worker = self.one(self.collector().snapshot(), "f1")
        self.assertEqual(worker.state, FAILED)

    def test_dead_noreport(self) -> None:
        self.add_dead("n1", time.time() - 500)
        worker = self.one(self.collector().snapshot(), "n1")
        self.assertEqual(worker.state, NOREPORT)

    def test_dead_stdout_only_brief_without_report_is_done(self) -> None:
        start = time.time() - 500
        self.add_dead("plan-x", start, log_text="")
        self.write("briefs/plan-x.md", "# Plan\n\nJob: plan\nGoal: g\nOutput: stdout only (captured to PLAN.md by the lead).\n")
        self.add_dead("plain-y", start, log_text="")
        self.write("briefs/plain-y.md", "# Plain\n\nGoal: g\nOutput: REPORT.md\n")
        snapshot = self.collector().snapshot()
        self.assertEqual(self.one(snapshot, "plan-x").state, DONE)
        self.assertEqual(self.one(snapshot, "plain-y").state, NOREPORT)

    def test_quiet(self) -> None:
        now = time.time()
        self.write("q1.pid", str(os.getpid()), mtime=now)
        self.write("q1.log", "old\n", mtime=now - 200)
        self.write("briefs/q1.md", "# Q1\n")
        worker = self.one(self.collector().snapshot(now=now), "q1")
        self.assertEqual(worker.state, QUIET)

    def test_stalled(self) -> None:
        now = time.time()
        self.write("s1.pid", str(os.getpid()), mtime=now)
        self.write("s1.log", "old\n", mtime=now - 400)
        self.write("briefs/s1.md", "# S1\n")
        with mock.patch.object(collect.Collector, "_co_cpu", return_value=0.5):
            worker = self.one(self.collector().snapshot(now=now), "s1")
        self.assertEqual(worker.state, STALLED)


class TestBriefAndReport(Base):
    def test_brief_title_goal(self) -> None:
        start = time.time() - 500
        self.add_dead("foo", start)
        self.write("briefs/foo.md", "# Foo Title\n\nGoal: build the widget\nmore\n")
        worker = self.one(self.collector().snapshot(), "foo")
        self.assertEqual(worker.title, "Foo Title")
        self.assertEqual(worker.goal, "build the widget")

    def test_brief_dash_variant(self) -> None:
        self.add_dead("bar", time.time() - 500)
        self.write("briefs/bar-alpha.md", "# Alpha Title\nGoal: alpha work\n")
        worker = self.one(self.collector().snapshot(), "bar")
        self.assertEqual(worker.title, "Alpha Title")
        self.assertEqual(worker.goal, "alpha work")

    def test_missing_brief(self) -> None:
        self.add_dead("baz", time.time() - 500)
        worker = self.one(self.collector().snapshot(), "baz")
        self.assertEqual(worker.title, "")
        self.assertEqual(worker.goal, "")

    def test_model_from_file(self) -> None:
        self.add_dead("m1", time.time() - 500)
        self.write("m1.model", "sonnet-work medium\n")
        worker = self.one(self.collector().snapshot(), "m1")
        self.assertEqual(worker.model, "sonnet-work medium")
        self.assertEqual(worker.family, "claude")

    def test_model_from_log(self) -> None:
        start = time.time() - 500
        self.add_dead("m2", start)

        def fake(path, prev=None, now=None):
            return LogState(path=path, model="gpt-6-luna", steps=[Step(0.0, "text", "", "x")],
                            last_ts=start)

        with mock.patch.object(collect, "parse_log", side_effect=fake):
            worker = self.one(self.collector().snapshot(), "m2")
        self.assertEqual(worker.model, "gpt-6-luna")
        self.assertEqual(worker.family, "codex")

    def test_model_from_cmdline(self) -> None:
        script = self.write("fake.py", "import time\ntime.sleep(30)\n")
        child = subprocess.Popen(["python3", script, "-m", "luna-x", "--effort", "high"])
        self.addCleanup(child.wait)
        self.addCleanup(child.terminate)
        time.sleep(0.05)
        now = time.time()
        self.write("cli.pid", str(child.pid), mtime=now)
        self.write("cli.log", "x\n", mtime=now)
        self.write("briefs/cli.md", "# CLI\n")
        worker = self.one(self.collector().snapshot(now=now), "cli")
        self.assertEqual(worker.model, "luna-x (high)")
        self.assertEqual(worker.family, "codex")


class TestReviews(Base):
    def test_findings(self) -> None:
        now = time.time()
        body = (
            "model: luna\n"
            "- [P2] bee \u2014 second detail\n"
            "- [P1] aye \u2014 first detail\n"
            "- [P2] bee \u2014 other detail\n"
            "- [P1] aye \u2014 duplicate\n"
            "- [P3] no dash here\n"
        )
        self.write("review-1.log", body, mtime=now)
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(len(snapshot.reviews), 1)
        review = snapshot.reviews[0]
        self.assertEqual(review.name, "review-1")
        self.assertEqual(review.model, "luna")
        self.assertEqual(review.family, "codex")
        self.assertEqual(
            review.findings,
            [Finding(2, "bee"), Finding(1, "aye"), Finding(3, "no dash here")],
        )

    def test_running_and_start(self) -> None:
        now = time.time()
        self.write("review-9.log", "- [P1] aye\n", mtime=now)
        self.write("review-9.pid", str(os.getpid()), mtime=now - 5)
        review = self.collector().snapshot(now=now).reviews[0]
        self.assertTrue(review.running)
        self.assertAlmostEqual(review.start, now - 5, places=1)
        self.assertAlmostEqual(review.end, now, places=1)


class TestFamilies(Base):
    def test_family_of(self) -> None:
        self.assertEqual(collect.family_of("grok-4"), "grok")
        self.assertEqual(collect.family_of("deepseek-v4"), "deepseek")
        self.assertEqual(collect.family_of("opencode-go/x"), "deepseek")
        self.assertEqual(collect.family_of("composer-1"), "cursor")
        self.assertEqual(collect.family_of("cursor-agent"), "cursor")
        self.assertEqual(collect.family_of("claude-opus-4"), "claude")
        self.assertEqual(collect.family_of("haiku work"), "claude")
        self.assertEqual(collect.family_of("gpt-6"), "codex")
        self.assertEqual(collect.family_of("gpt-6-luna"), "codex")
        self.assertEqual(collect.family_of("sol-preview"), "codex")
        self.assertEqual(collect.family_of("mystery"), "other")

    def test_account_mapping(self) -> None:
        mapping = collect._co_account_for
        self.assertEqual(mapping("claude", "sonnet-work medium"), "claude-work")
        self.assertEqual(mapping("claude", "opus"), "claude")
        self.assertEqual(mapping("codex", "gpt-6"), "codex")
        self.assertEqual(mapping("deepseek", "opencode-go/x"), "opencode-go")
        self.assertEqual(mapping("grok", "grok-4"), "cursor-models")
        self.assertEqual(mapping("cursor", "composer"), "cursor-models")
        self.assertIsNone(mapping("other", "mystery"))


class TestMilestones(Base):
    def test_timed_and_untimed(self) -> None:
        lt = time.localtime()
        base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0, 0, 0, -1))
        self.anchor(base - 3 * 3600)
        self.write("PROGRESS.md", "10:00 did x\nplain line\n", mtime=base)
        expected_ts = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 10, 0, 0, 0, 0, -1))
        snapshot = self.collector().snapshot(now=base)
        self.assertEqual(len(snapshot.milestones), 2)
        self.assertAlmostEqual(snapshot.milestones[0].ts, expected_ts, places=2)
        self.assertEqual(snapshot.milestones[0].text, "did x")
        self.assertIsNone(snapshot.milestones[1].ts)
        self.assertEqual(snapshot.milestones[1].text, "plain line")

    def test_day_rollback(self) -> None:
        lt = time.localtime()
        base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 2, 0, 0, 0, -1))
        self.anchor(base - 2 * 3600)
        self.write("PROGRESS.md", "23:50 late work\n", mtime=base)
        candidate = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 23, 50, 0, 0, 0, -1))
        snapshot = self.collector().snapshot(now=base)
        self.assertEqual(len(snapshot.milestones), 1)
        self.assertAlmostEqual(snapshot.milestones[0].ts, candidate - 86400, places=2)
        self.assertEqual(snapshot.milestones[0].text, "late work")


class TestSession(Base):
    def test_two_bursts(self) -> None:
        now = time.time()
        self.add_dead("old", now - 4 * 3600, now - 4 * 3600 + 60)
        self.add_dead("new", now - 3600, now - 3600 + 60)
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual([w.name for w in snapshot.workers], ["new"])
        self.assertEqual(snapshot.hidden_older, 1)
        self.assertAlmostEqual(snapshot.session_start, now - 3600, places=1)

    def test_all_history(self) -> None:
        now = time.time()
        self.add_dead("old", now - 4 * 3600, now - 4 * 3600 + 60)
        self.add_dead("new", now - 3600, now - 3600 + 60)
        snapshot = self.collector().snapshot(now=now, all_history=True)
        self.assertEqual(sorted(w.name for w in snapshot.workers), ["new", "old"])
        self.assertEqual(snapshot.hidden_older, 0)
        self.assertAlmostEqual(snapshot.session_start, now - 4 * 3600, places=1)


class TestOrderingAndTotals(Base):
    def test_ordering(self) -> None:
        now = time.time()
        self.write("la.pid", str(os.getpid()), mtime=now - 10)
        self.write("la.log", "x\n", mtime=now)
        self.write("briefs/la.md", "# la\n")
        self.write("lb.pid", str(os.getpid()), mtime=now - 5)
        self.write("lb.log", "x\n", mtime=now)
        self.write("briefs/lb.md", "# lb\n")
        self.write("d1.pid", str(DEAD_PID), mtime=now - 120)
        self.write("d1.log", "x\n", mtime=now - 60)
        self.write("d2.pid", str(DEAD_PID), mtime=now - 90)
        self.write("d2.log", "x\n", mtime=now - 30)
        names = [w.name for w in self.collector().snapshot(now=now).workers]
        self.assertEqual(names, ["la", "lb", "d2", "d1"])

    def test_totals(self) -> None:
        now = time.time()
        self.add_dead("t1", now - 300, now - 300)
        self.add_dead("t2", now - 200, now - 200)

        def fake(path, prev=None, now=None):
            if os.path.basename(path).startswith("t1"):
                return LogState(path=path, steps=[Step(0.0, "text", "", "x")],
                                tokens_in=100, tokens_out=50, cost=0.5, last_ts=now)
            return LogState(path=path, steps=[Step(0.0, "text", "", "x")], last_ts=now)

        with mock.patch.object(collect, "parse_log", side_effect=fake), \
                mock.patch.object(collect, "_co_metered", return_value=True):   # billing itself: test_ops_billing
            snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.tokens_total, 150)
        self.assertAlmostEqual(snapshot.cost_total, 0.5, places=6)

    def test_totals_none(self) -> None:
        self.add_dead("t1", time.time() - 300)
        snapshot = self.collector().snapshot()
        self.assertIsNone(snapshot.tokens_total)
        self.assertIsNone(snapshot.cost_total)


class TestQuota(Base):
    def test_quota_ok(self) -> None:
        now = time.time()
        self.write("wl.pid", str(os.getpid()), mtime=now)
        self.write("wl.log", "x\n", mtime=now)
        self.write("wl.model", "sonnet-work medium\n")
        body = (
            '{"subscriptions":[{"id":"claude-work","stale":false,'
            '"limits":[{"name":"5h","window":"five_hour","usedPct":23,"pacePct":39.2,"stale":false,'
            '"resetsAt":1760053600000,"projectedPct":61.5,"runsOutAt":1760052000000}]}]}\n'
        )
        script = self.write("quota.sh", "#!/bin/sh\nprintf '%s' '" + body + "'\n")
        os.chmod(script, 0o755)
        os.environ["STATUS_QUOTA_CMD"] = script
        proc = self.collector()
        deadline = time.time() + 5
        snapshot = proc.snapshot(now=now)
        while not snapshot.quota_ok and time.time() < deadline:
            time.sleep(0.05)
            snapshot = proc.snapshot(now=now)
        self.assertTrue(snapshot.quota_ok)
        self.assertEqual(len(snapshot.quota), 1)
        account = snapshot.quota[0]
        self.assertEqual(account.name, "claude-work")
        self.assertTrue(account.in_use)
        self.assertEqual(len(account.windows), 1)
        self.assertEqual(account.windows[0].name, "5h")
        self.assertAlmostEqual(account.windows[0].used, 23)
        self.assertAlmostEqual(account.windows[0].pace, 39.2, places=3)
        self.assertEqual(account.windows[0].window, "five_hour")
        self.assertAlmostEqual(account.windows[0].resets_at, 1760053600.0)
        self.assertAlmostEqual(account.windows[0].projected_pct, 61.5)
        self.assertAlmostEqual(account.windows[0].runs_out_at, 1760052000.0)

    def test_quota_fail(self) -> None:
        proc = self.collector()
        time.sleep(0.3)
        snapshot = proc.snapshot()
        self.assertFalse(snapshot.quota_ok)
        self.assertEqual(snapshot.quota, [])

    def test_close_prompt(self) -> None:
        script = self.write("slow.sh", "#!/bin/sh\nsleep 5\n")
        os.chmod(script, 0o755)
        os.environ["STATUS_QUOTA_CMD"] = script
        proc = collect.Collector(self.stage)
        start = time.perf_counter()
        proc.close()
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.5)


class TestRobustness(Base):
    def test_title(self) -> None:
        proc = self.collector()
        expected = os.path.basename(os.path.dirname(os.path.abspath(self.stage)))
        self.assertEqual(proc.snapshot().title, expected)
        os.environ["STATUS_TITLE"] = "Custom Title"
        try:
            self.assertEqual(proc.snapshot().title, "Custom Title")
        finally:
            os.environ.pop("STATUS_TITLE", None)

    def test_garbage_directory(self) -> None:
        self.write("random.txt", b"\x00\xff\xfe binary garbage \x01\x02")
        self.write("binary.log", b"\x00\xff\x01\x02\x03")
        self.write("binary.pid", "not-a-pid-at-all")
        self.write("garbage.pid", "12ab34")
        self.write("notes.md", "# not a worker\n")
        os.makedirs(os.path.join(self.stage, "a.pid"), exist_ok=True)
        proc = self.collector()
        snapshot = proc.snapshot()
        self.assertIsInstance(snapshot.workers, list)
        self.assertEqual(proc.detail("missing", "brief"), [])
        self.assertEqual(proc.detail("binary", "live"), [])

    def test_detail(self) -> None:
        self.add_dead("dd", time.time() - 500)
        self.write("briefs/dd.md", "# DD\n\ta\tb\n")
        report = "# R\n\n## Summary\nok\n"
        self.write("dd/REPORT.md", report)
        proc = self.collector()
        brief = proc.detail("dd", "brief")
        self.assertIn("    a    b", brief)
        self.assertEqual(proc.detail("dd", "report"), report.splitlines())
        self.assertEqual(proc.detail("nope", "report"), [])

    def test_speed(self) -> None:
        now = time.time()
        for i in range(150):
            self.write(f"dead{i}.pid", str(DEAD_PID), mtime=now - 600)
            self.write(f"dead{i}.log", "x\n" * 5, mtime=now - 600)
        for i in range(5):
            self.write(f"live{i}.pid", str(os.getpid()), mtime=now)
            self.write(f"live{i}.log", "x\n", mtime=now)
            self.write(f"briefs/live{i}.md", f"# live{i}\n")
        proc = self.collector()
        proc.snapshot()
        start = time.perf_counter()
        snapshot = proc.snapshot()
        elapsed = time.perf_counter() - start
        self.assertEqual(len(snapshot.workers), 155)
        self.assertLess(elapsed, 1.0, f"steady snapshot took {elapsed:.3f}s")   # generous: CI and loaded machines


class TestLogRewrite(Base):
    def test_rewrite_same_size_resets(self) -> None:
        now = time.time()
        self.add_dead("rw", now - 300, log_text="aaaa\n")
        proc = self.collector()
        first = self.one(proc.snapshot(now=now), "rw")
        self.assertEqual([step.text for step in first.log.steps], ["aaaa"])
        self.write("rw.log", "bbbb\n", mtime=now - 100)
        worker = self.one(proc.snapshot(now=now), "rw")
        self.assertEqual([step.text for step in worker.log.steps], ["bbbb"])

    def test_replaced_inode_resets(self) -> None:
        now = time.time()
        self.add_dead("ri", now - 300, log_text="line\n")
        proc = self.collector()
        self.one(proc.snapshot(now=now), "ri")
        tmp = self.write("ri.new", "fresh\n", mtime=now - 100)
        os.replace(tmp, os.path.join(self.stage, "ri.log"))
        worker = self.one(proc.snapshot(now=now), "ri")
        self.assertEqual([step.text for step in worker.log.steps], ["fresh"])

    def test_append_keeps_steps(self) -> None:
        now = time.time()
        self.write("ap.pid", str(DEAD_PID), mtime=now - 300)
        self.write("ap.log", "one\n", mtime=now - 300)
        proc = self.collector()
        self.one(proc.snapshot(now=now), "ap")
        path = os.path.join(self.stage, "ap.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("two\n")
        os.utime(path, (now - 100, now - 100))
        worker = self.one(proc.snapshot(now=now), "ap")
        self.assertEqual([step.text for step in worker.log.steps], ["one", "two"])


class TestCacheEviction(Base):
    def test_worker_caches_evicted_when_pid_gone(self) -> None:
        now = time.time()
        self.write("ev.pid", str(os.getpid()), mtime=now)
        self.write("ev.log", "x\n", mtime=now)
        self.write("briefs/ev.md", "# ev\n")
        proc = self.collector()
        proc.snapshot(now=now)
        self.assertIn("ev", proc._co_logs)
        self.assertIn("ev", proc._co_log_fp)
        self.assertIn("ev", proc._co_prev_cpu)
        os.remove(os.path.join(self.stage, "ev.pid"))
        proc.snapshot(now=now)
        self.assertNotIn("ev", proc._co_logs)
        self.assertNotIn("ev", proc._co_log_fp)
        self.assertNotIn("ev", proc._co_prev_cpu)

    def test_review_cache_evicted_when_log_gone(self) -> None:
        now = time.time()
        self.write("review-7.log", "- [P1] x\n", mtime=now)
        proc = self.collector()
        proc.snapshot(now=now)
        self.assertIn("review-7", proc._co_review_cache)
        os.remove(os.path.join(self.stage, "review-7.log"))
        proc.snapshot(now=now)
        self.assertNotIn("review-7", proc._co_review_cache)


class TestReviewCache(Base):
    def test_findings_cached_by_fingerprint(self) -> None:
        now = time.time()
        self.write("review-3.log", "- [P1] one\n", mtime=now)
        real = collect._co_findings
        calls: list[int] = []

        def counting(lines):
            calls.append(1)
            return real(lines)

        proc = self.collector()
        with mock.patch.object(collect, "_co_findings", side_effect=counting):
            proc.snapshot(now=now)
            self.assertEqual(len(calls), 1)
            proc.snapshot(now=now)
            self.assertEqual(len(calls), 1)
            self.write("review-3.log", "- [P1] two\n", mtime=now + 1)
            proc.snapshot(now=now)
            self.assertEqual(len(calls), 2)

    def test_only_tail_is_parsed(self) -> None:
        now = time.time()
        body = "- [P1] old\n" + ("filler line\n" * 220000) + "- [P2] recent\n"
        self.write("review-big.log", body, mtime=now)
        review = self.collector().snapshot(now=now).reviews[0]
        texts = [finding.text for finding in review.findings]
        self.assertIn("recent", texts)
        self.assertNotIn("old", texts)


class TestServices(Base):
    def spawn_sleep(self) -> subprocess.Popen:
        child = subprocess.Popen(["sleep", "30"])
        self.addCleanup(child.wait)
        self.addCleanup(child.terminate)
        time.sleep(0.05)
        return child

    def test_live_non_agent_pid_is_service(self) -> None:
        child = self.spawn_sleep()
        now = time.time()
        self.write("srv.pid", str(child.pid), mtime=now)
        self.write("srv.log", "listening\n", mtime=now)
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [("srv", True)])
        self.assertNotIn("srv", [worker.name for worker in snapshot.workers])

    def test_service_liveness_ignores_pid_mtime(self) -> None:
        child = self.spawn_sleep()
        now = time.time()
        self.write("srv.pid", str(child.pid), mtime=now - 100000)
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [("srv", True)])

    def test_brief_makes_it_a_worker(self) -> None:
        child = self.spawn_sleep()
        now = time.time()
        self.write("w1.pid", str(child.pid), mtime=now)
        self.write("briefs/w1.md", "# w1\n")
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [])
        self.one(snapshot, "w1")

    def test_dash_brief_makes_it_a_worker(self) -> None:
        child = self.spawn_sleep()
        now = time.time()
        self.write("w2.pid", str(child.pid), mtime=now)
        self.write("briefs/w2-alpha.md", "# w2\n")
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [])
        self.one(snapshot, "w2")

    def test_model_file_makes_it_a_worker(self) -> None:
        child = self.spawn_sleep()
        now = time.time()
        self.write("w3.pid", str(child.pid), mtime=now)
        self.write("w3.model", "sonnet medium\n")
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [])
        self.one(snapshot, "w3")

    def test_directory_makes_it_a_worker(self) -> None:
        child = self.spawn_sleep()
        now = time.time()
        self.write("w4.pid", str(child.pid), mtime=now)
        self.write("w4/REPORT.md", "# r\n")
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [])
        self.one(snapshot, "w4")

    def test_dead_pid_without_brief_is_worker(self) -> None:
        self.write("dead.pid", str(DEAD_PID), mtime=time.time() - 500)
        snapshot = self.collector().snapshot()
        self.assertEqual(snapshot.services, [])
        self.assertEqual(self.one(snapshot, "dead").state, NOREPORT)

    def test_services_sorted(self) -> None:
        first = self.spawn_sleep()
        second = self.spawn_sleep()
        now = time.time()
        self.write("zeta.pid", str(first.pid), mtime=now)
        self.write("alpha.pid", str(second.pid), mtime=now)
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [("alpha", True), ("zeta", True)])

    def test_service_log_not_parsed(self) -> None:
        child = self.spawn_sleep()
        now = time.time()
        self.write("quiet.pid", str(child.pid), mtime=now)
        self.write("quiet.log", '{"model":"gpt-6","type":"text"}\n', mtime=now)
        calls: list[str] = []
        real = collect.parse_log

        def counting(path, prev=None, now=None):
            calls.append(path)
            return real(path, prev=prev, now=now)

        with mock.patch.object(collect, "parse_log", side_effect=counting):
            snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [("quiet", True)])
        self.assertEqual(calls, [])

    def test_service_ignored_by_session_and_totals(self) -> None:
        now = time.time()
        self.add_dead("old", now - 4 * 3600, now - 4 * 3600 + 60)
        child = self.spawn_sleep()
        self.write("srv.pid", str(child.pid), mtime=now)
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [("srv", True)])
        self.assertEqual([worker.name for worker in snapshot.workers], ["old"])
        self.assertEqual(snapshot.hidden_older, 0)
        self.assertAlmostEqual(snapshot.session_start, now - 4 * 3600, places=1)
        self.assertIsNone(snapshot.tokens_total)
        self.assertIsNone(snapshot.cost_total)


class TestServiceDetection(Base):
    def test_argv_agent_tokens(self) -> None:
        for argv in (
            ["python3", "/opt/opencode"],
            ["/usr/bin/codex", "exec"],
            ["claude", "-p"],
            ["cursor-agent", "run"],
            ["/x/my-cursor-wrapper"],
        ):
            with mock.patch.object(collect, "_co_argv", return_value=argv):
                self.assertTrue(collect._co_argv_is_agent(1), argv)
        with mock.patch.object(collect, "_co_argv", return_value=["sleep", "30"]):
            self.assertFalse(collect._co_argv_is_agent(1))
        with mock.patch.object(collect, "_co_argv", return_value=["python3", "/w/worker.py"]):
            self.assertFalse(collect._co_argv_is_agent(1))

    def test_proc_agent_in_tree_depth(self) -> None:
        children = {100: [200], 200: [300], 300: [400], 400: [500]}
        near = {100: ["sh"], 200: ["python3"], 300: ["/usr/bin/opencode"]}
        far = {100: ["sh"], 200: ["sh"], 300: ["sh"], 400: ["sh"], 500: ["opencode"]}
        with mock.patch.object(collect, "_co_argv", side_effect=lambda pid: near.get(pid, [])):
            self.assertTrue(collect._co_proc_is_agent(100, children))
        with mock.patch.object(collect, "_co_argv", side_effect=lambda pid: far.get(pid, [])):
            self.assertFalse(collect._co_proc_is_agent(100, children))

    def test_live_agent_argv_is_worker(self) -> None:
        script = self.write("opencode", "import time\ntime.sleep(30)\n")
        child = subprocess.Popen(["python3", script])
        self.addCleanup(child.wait)
        self.addCleanup(child.terminate)
        time.sleep(0.05)
        now = time.time()
        self.write("agent.pid", str(child.pid), mtime=now)
        self.write("agent.log", "x\n", mtime=now)
        snapshot = self.collector().snapshot(now=now)
        self.assertEqual(snapshot.services, [])
        self.assertEqual(self.one(snapshot, "agent").state, RUNNING)


class FinishedLogRing(Base):
    """A finished worker's log is not re-parsed; its tool ring must still age out against now."""

    def test_the_unchanged_log_of_a_finished_worker_is_pruned(self) -> None:
        start = 1_760_000_000.0
        line = ('{"type":"tool_use","timestamp":' + str(int(start) * 1000) + ',"part":{"tool":"read",'
                '"state":{"status":"completed","input":{"path":"/a"}}}}')
        log = self.write("old.log", line + "\n")
        proc = self.collector()
        first = proc._co_log_for("old", log, False, start + 1)
        self.assertEqual(len(first.tool_pulses), 1)
        later = proc._co_log_for("old", log, False, start + TOOL_RING_S + 2)
        self.assertIs(later, first)
        self.assertEqual(first.tool_pulses, [])


if __name__ == "__main__":
    unittest.main()
