from __future__ import annotations

import unittest

import webdata
from demo import DemoCollector
from model import (
    ACT_BUCKETS,
    DONE,
    FAILED,
    LIVE_STATES,
    NOREPORT,
    QUIET,
    RUNNING,
    STALLED,
    TOOL_CATS,
    GlobalSnapshot,
)

# Integer-valued so that (t0 + e) - t0 == e exactly at the boundaries (e = 22, 35, 60, ...).
T0 = 1_700_000_000.0

BASE = {
    "map-routes": DONE,
    "api-routes": RUNNING,
    "db-schema": RUNNING,
    "ui-cards": RUNNING,
    "seed-data": FAILED,
    "fix-lint": NOREPORT,
}
EXPECTED = {
    0: BASE,
    10: BASE,
    25: {**BASE, "db-schema": DONE},
    40: {**BASE, "db-schema": DONE, "ui-cards": QUIET, "review-api": RUNNING},
    65: {**BASE, "db-schema": DONE, "ui-cards": QUIET, "review-api": DONE},
    80: {**BASE, "db-schema": DONE, "ui-cards": STALLED, "review-api": DONE},
    600: {**BASE, "db-schema": DONE, "ui-cards": STALLED, "review-api": DONE},
}
ALL_NAMES = sorted(set(BASE) | {"review-api"})


class DemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.collector = DemoCollector()
        self.collector._t0 = T0

    def tearDown(self) -> None:
        self.collector.close()

    def snap(self, e: float, all_history: bool = False):
        return self.collector.snapshot(now=T0 + e, all_history=all_history)

    def test_snapshot_never_raises(self) -> None:
        for e in EXPECTED:
            self.snap(e)
            self.snap(e, all_history=True)
        DemoCollector().snapshot()
        DemoCollector().snapshot(all_history=True)

    def test_states_follow_table(self) -> None:
        for e, expected in EXPECTED.items():
            with self.subTest(e=e):
                got = {w.name: w.state for w in self.snap(e).workers}
                self.assertEqual(got, expected)

    def test_activity_length_and_tool_keys(self) -> None:
        for e in EXPECTED:
            for w in self.snap(e).workers:
                with self.subTest(e=e, worker=w.name):
                    self.assertEqual(len(w.activity), ACT_BUCKETS)
                    self.assertTrue(set(w.log.tool_counts) <= set(TOOL_CATS))

    def test_live_and_dead_fields(self) -> None:
        for e in EXPECTED:
            for w in self.snap(e).workers:
                with self.subTest(e=e, worker=w.name):
                    if w.state in LIVE_STATES:
                        self.assertIsNone(w.end)
                    else:
                        self.assertIsNotNone(w.end)
                        self.assertIsNone(w.cpu)
                        self.assertIsNone(w.pid)
                        self.assertEqual(w.activity, [0] * ACT_BUCKETS)

    def test_quiet_and_stalled_cpu_under_threshold(self) -> None:
        for e in (40, 80):
            ui = next(w for w in self.snap(e).workers if w.name == "ui-cards")
            self.assertLess(ui.cpu, 1.0)
            self.assertLessEqual(ui.log.steps[-1].ts, T0 + 30)

    def test_equal_snapshots_at_same_now(self) -> None:
        for e in (0, 40, 600):
            self.assertEqual(self.snap(e), self.snap(e))

    def test_api_routes_step_count_grows_and_is_capped(self) -> None:
        def api(e: float):
            return next(w for w in self.snap(e).workers if w.name == "api-routes")

        early, late = api(10), api(40)
        self.assertGreater(sum(late.log.tool_counts.values()) + late.log.errors,
                           sum(early.log.tool_counts.values()) + early.log.errors)
        self.assertGreater(late.log.ctx_tokens, early.log.ctx_tokens)
        for w in (early, late):
            self.assertLessEqual(len(w.log.steps), 40)

    def test_review_api_findings_only_from_60(self) -> None:
        def review_api(e: float):
            return next(r for r in self.snap(e).reviews if r.name == "review-api")

        self.assertNotIn("review-api", [r.name for r in self.snap(34).reviews])
        for e in (35, 40, 59):
            with self.subTest(e=e):
                r = review_api(e)
                self.assertTrue(r.running)
                self.assertEqual(r.findings, [])
        final = review_api(60)
        self.assertFalse(final.running)
        self.assertEqual(sorted(f.prio for f in final.findings), [1, 2, 2, 3])

    def test_review_plan_always_present_and_newest_first(self) -> None:
        for e in EXPECTED:
            reviews = self.snap(e).reviews
            with self.subTest(e=e):
                self.assertIn("review-plan", [r.name for r in reviews])
                ends = [r.end for r in reviews]
                self.assertEqual(ends, sorted(ends, reverse=True))

    def test_db_schema_milestone_only_from_22(self) -> None:
        def texts(e: float) -> list[str]:
            return [m.text for m in self.snap(e).milestones]

        self.assertNotIn("db-schema accepted", texts(21))
        self.assertIn("db-schema accepted", texts(22))

    def test_ordering_rule(self) -> None:
        for e in EXPECTED:
            workers = self.snap(e).workers
            live = [w for w in workers if w.state in LIVE_STATES]
            dead = [w for w in workers if w.state not in LIVE_STATES]
            with self.subTest(e=e):
                self.assertEqual(workers, live + dead)
                starts = [w.start for w in live]
                self.assertEqual(starts, sorted(starts))
                ends = [w.end for w in dead]
                self.assertEqual(ends, sorted(ends, reverse=True))

    def test_detail_brief_and_report(self) -> None:
        for name in ALL_NAMES:
            with self.subTest(name=name):
                brief = self.collector.detail(name, "brief")
                self.assertTrue(brief)
                self.assertTrue(brief[0].startswith("# "))
                self.assertTrue(any(line.startswith("Goal: ") for line in brief))
        self.assertIn("## Summary", self.collector.detail("map-routes", "report"))
        self.assertEqual(self.collector.detail("api-routes", "report"), [])
        self.assertEqual(self.collector.detail("map-routes", "live"), [])
        self.assertEqual(self.collector.detail("no-such-worker", "brief"), [])

    def test_tool_step_text_is_argument_only(self) -> None:
        for e in (40, 600):
            for w in self.snap(e).workers:
                for step in w.log.steps:
                    if step.kind == "tool":
                        with self.subTest(e=e, worker=w.name, tool=step.tool):
                            self.assertNotEqual(step.text.split(" ", 1)[0], step.tool)
                            self.assertFalse(step.text.startswith(step.tool + " "))
                    else:
                        self.assertEqual(step.tool, "")

    def test_consecutive_steps_have_different_text(self) -> None:
        for e in (40, 600, 3600):
            for w in self.snap(e).workers:
                for prev, cur in zip(w.log.steps, w.log.steps[1:]):
                    with self.subTest(e=e, worker=w.name):
                        self.assertNotEqual(prev.text, cur.text)

    def test_healthy_workers_have_at_most_two_errors(self) -> None:
        for e in (600, 3600):
            for w in self.snap(e).workers:
                if w.name != "seed-data":
                    with self.subTest(e=e, worker=w.name):
                        self.assertLessEqual(w.log.errors, 2)
        api = next(w for w in self.snap(3600).workers if w.name == "api-routes")
        self.assertGreater(sum(api.log.tool_counts.values()) + api.log.errors, 500)

    def test_only_seed_data_ends_on_error(self) -> None:
        workers = {w.name: w for w in self.snap(600).workers}
        self.assertEqual(workers["seed-data"].log.steps[-1].kind, "error")
        self.assertGreaterEqual(workers["seed-data"].log.errors, 1)
        for name, w in workers.items():
            if name != "seed-data" and w.log.steps:
                with self.subTest(worker=name):
                    self.assertNotEqual(w.log.steps[-1].kind, "error")

    def test_worker_cost_scale_for_400k_input(self) -> None:
        for e in (600, 3600):
            for w in self.snap(e).workers:
                if w.log.tokens_in:
                    with self.subTest(e=e, worker=w.name):
                        scaled = w.log.cost * 400_000 / w.log.tokens_in
                        self.assertGreaterEqual(scaled, 0.05)
                        self.assertLessEqual(scaled, 0.60)

    def test_cache_tokens_several_times_input(self) -> None:
        for w in self.snap(600).workers:
            if w.log.tokens_in:
                with self.subTest(worker=w.name):
                    self.assertGreaterEqual(w.log.tokens_cache, 3 * w.log.tokens_in)

    def test_session_cost_under_two_dollars(self) -> None:
        for e in (600, 3600):
            with self.subTest(e=e):
                self.assertLess(self.snap(e).cost_total, 2.0)

    def test_solo_station_is_a_live_lead_with_no_workers(self) -> None:
        (station,) = self.collector.solo_stages()
        self.assertTrue(station.solo)
        self.assertEqual(station.lead_state, "live")
        self.assertEqual(station.live, 0)
        self.assertEqual(station.snap.workers, [])
        self.assertIn(".stage#", station.path)

    def test_solo_station_in_the_global_payload(self) -> None:
        payload = webdata.global_payload(GlobalSnapshot(stages=self.collector.solo_stages()))
        (item,) = payload["stages"]
        self.assertIs(item["solo"], True)
        self.assertEqual(item["counts"]["total"], 0)
