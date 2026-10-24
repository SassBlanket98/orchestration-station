"""Tests for webdata: the JSON payloads served by ``station --serve``."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest

import webdata
from model import (
    DONE,
    FAILED,
    NOREPORT,
    QUIET,
    RUNNING,
    STALLED,
    Finding,
    GlobalSnapshot,
    Group,
    LeadInfo,
    LogState,
    Milestone,
    Review,
    Snapshot,
    Stage,
    Step,
    Turn,
    TurnTool,
    Vitals,
    Worker,
    WorkState,
)


def make_log(**overrides: object) -> LogState:
    fields: dict[str, object] = {
        "harness": "opencode",
        "model": "gpt-6-luna",
        "steps": [Step(ts=1.0, kind="tool", tool="read", text="a.txt")],
        "tool_counts": {"read": 1},
        "errors": 0,
        "tokens_in": 1,
        "tokens_out": 2,
        "tokens_cache": 3,
        "ctx_tokens": 4,
        "cost": 0.01,
    }
    fields.update(overrides)
    return LogState(**fields)  # type: ignore[arg-type]


def make_worker(name: str = "w", state: str = RUNNING, **overrides: object) -> Worker:
    fields: dict[str, object] = {
        "name": name,
        "state": state,
        "title": "title",
        "goal": "goal",
        "model": "luna@medium",
        "family": "codex",
        "pid": None,
        "start": 1.0,
        "end": None,
        "cpu": None,
        "rss": None,
        "nproc": None,
        "log": make_log(),
        "activity": [0, 1, 2],
        "quiet_s": 0.0,
        "summary": ["did the thing"],
        "has_report": False,
        "is_review": False,
        "job": "build",
    }
    fields.update(overrides)
    return Worker(**fields)  # type: ignore[arg-type]


def assert_jsonable(case: unittest.TestCase, obj: object) -> None:
    """Assert every leaf is a plain JSON type: no tuple, set or dataclass leaks."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            case.assertIsInstance(key, str)
            assert_jsonable(case, value)
    elif isinstance(obj, list):
        for value in obj:
            assert_jsonable(case, value)
    elif obj is None or isinstance(obj, (str, bool, int, float)):
        return
    else:
        case.fail(f"non-JSON type leaked: {type(obj).__name__}")


def rich_snapshot() -> Snapshot:
    return Snapshot(
        title="proj",
        now=1_800_000_000.0,
        session_start=1_799_000_000.0,
        workers=[
            make_worker("live", RUNNING, pid=123, cpu=3.2, rss=456, nproc=2),
            make_worker("quiet", QUIET),
            make_worker("dead", DONE, end=1_799_999_999.0, has_report=True),
            make_worker("bad", FAILED, end=1_799_999_998.0),
            make_worker("nr", NOREPORT, end=1_799_999_997.0),
            make_worker("stalled", STALLED),
        ],
        reviews=[
            Review(
                name="review-x",
                model="grok-medium",
                family="cursor",
                start=1.0,
                end=2.0,
                running=False,
                findings=[Finding(prio=2, text="something")],
            )
        ],
        milestones=[Milestone(ts=None, text="no time"), Milestone(ts=5.0, text="later")],
        groups=[
            Group(label="build", workers=["a", "b"], started=0.0, done=1, total=2,
                  live=1, finished=False, current=True)
        ],
        tokens_total=123456,
        cost_total=1.23,
    )


class GlobalPayloadTests(unittest.TestCase):
    def test_envelope_fields(self) -> None:
        snap = rich_snapshot()
        gs = GlobalSnapshot(
            now=1_800_000_001.0,
            stages=[
                Stage(path="/a", label="alpha", snap=snap, live=3, last_activity=9.0,
                      lead=LeadInfo(model="opus", phase="build", stream="web-monitor"),
                      lead_state="live", lead_kind="claude", lead_pid=42, lead_since=7.0),
                Stage(path="/b", label="beta", snap=Snapshot()),
            ],
            hidden_idle=5,
            tokens_total=999,
            cost_total=2.5,
        )
        payload = webdata.global_payload(gs, title="everything", poll_ms=2500)

        self.assertEqual(payload["mode"], "global")
        self.assertEqual(payload["v"], 2)
        self.assertEqual(payload["now"], 1_800_000_001.0)
        self.assertEqual(payload["title"], "everything")
        self.assertEqual(payload["poll_ms"], 2500)
        self.assertEqual(payload["tokens_total"], 999)
        self.assertEqual(payload["cost_total"], 2.5)
        self.assertEqual(payload["hidden_idle"], 5)
        self.assertEqual([stage["path"] for stage in payload["stages"]], ["/a", "/b"])
        self.assertEqual(payload["stages"][0]["lead"],
                         {"model": "opus", "phase": "build", "stream": "web-monitor"})
        self.assertEqual(payload["stages"][1]["lead"], None)
        self.assertEqual(payload["stages"][1]["lead_state"], "gone")
        assert_jsonable(self, payload)
        json.dumps(payload)

    def test_default_title(self) -> None:
        payload = webdata.global_payload(GlobalSnapshot())
        self.assertEqual(payload["title"], "all orchestrators")
        self.assertEqual(payload["poll_ms"], 3000)
        self.assertEqual(payload["stages"], [])

    def test_counts(self) -> None:
        stage = Stage(path="/a", label="a", snap=rich_snapshot(), live=3, last_activity=9.0)
        counts = webdata.global_payload(GlobalSnapshot(stages=[stage]))["stages"][0]["counts"]
        self.assertEqual(counts, {"live": 3, "failed": 1, "noreport": 1, "done": 1, "total": 6})

    def test_worker_fields(self) -> None:
        snap = Snapshot(workers=[
            make_worker("live", RUNNING, pid=123, start=5.0, end=None, cpu=3.2, rss=456, nproc=2,
                        activity=[3, 0, 1], summary=["s1", "s2"]),
            make_worker("nulls", DONE, end=7.0,
                        log=make_log(tokens_in=None, tokens_out=None, tokens_cache=None,
                                     ctx_tokens=None, cost=None, tool_counts={}, steps=[])),
        ])
        workers = webdata.global_payload(GlobalSnapshot(stages=[Stage(path="/a", label="a", snap=snap)])) \
            ["stages"][0]["workers"]
        live, nulls = workers
        self.assertEqual(live["name"], "live")
        self.assertEqual(live["tokens_total"], 4)   # the worker's own window (ctx) wins over in+out+cache
        self.assertIsNone(live["end"])
        self.assertEqual(live["quiet_s"], 0.0)
        self.assertEqual(live["cost"], 0.01)
        self.assertFalse({"summary", "activity", "tool_counts"} & set(live))   # goal is intended in summary rows now
        self.assertEqual(nulls["end"], 7.0)
        self.assertIsNone(nulls["tokens_total"])
        self.assertIsNone(nulls["cost"])

    def test_milestones_reviews_groups(self) -> None:
        stage = Stage(path="/a", label="a", snap=rich_snapshot())
        element = webdata.global_payload(GlobalSnapshot(stages=[stage]))["stages"][0]
        self.assertEqual(element["latest_milestone"], {"ts": 5.0, "text": "later"})
        self.assertEqual(element["reviews"], {"total": 1, "running": 0, "p1": 0, "p2": 1, "p3": 0, "clean": 0})
        self.assertEqual(element["groups"], [{
            "label": "build", "workers": ["a", "b"], "started": 0.0, "done": 1, "total": 2,
            "live": 1, "finished": False, "current": True,
        }])
        self.assertEqual(element["tokens_total"], 123456)
        self.assertEqual(element["cost_total"], 1.23)


class SinglePayloadTests(unittest.TestCase):
    def test_envelope_and_wrapped_stage(self) -> None:
        snap = rich_snapshot()
        payload = webdata.single_payload(snap, stage_dir="/stage", label="proj-label", poll_ms=4000)

        self.assertEqual(payload["mode"], "stage")
        self.assertEqual(payload["now"], snap.now)
        self.assertEqual(payload["title"], "proj")
        self.assertEqual(payload["poll_ms"], 4000)
        self.assertEqual(payload["hidden_idle"], 0)
        self.assertEqual(payload["tokens_total"], snap.tokens_total)
        self.assertEqual(payload["cost_total"], snap.cost_total)
        self.assertEqual(len(payload["stages"]), 1)
        stage = payload["stages"][0]
        self.assertEqual(stage["path"], "/stage")
        self.assertEqual(stage["label"], "proj-label")
        self.assertEqual(stage["lead"], None)
        self.assertEqual(stage["lead_state"], "gone")
        self.assertEqual(stage["lead_kind"], "")
        self.assertEqual(stage["lead_pid"], None)
        self.assertEqual(stage["lead_since"], 0.0)
        self.assertEqual(stage["counts"]["total"], 6)
        self.assertEqual(stage["live"], 3)
        self.assertEqual([worker["name"] for worker in stage["workers"]],
                         ["live", "quiet", "bad", "nr", "stalled", "dead"])
        assert_jsonable(self, payload)
        json.dumps(payload)

    def test_title_falls_back_to_label(self) -> None:
        payload = webdata.single_payload(Snapshot(), stage_dir="/s", label="fallback")
        self.assertEqual(payload["title"], "fallback")
        self.assertEqual(payload["stages"][0]["last_activity"], 0.0)

    def test_last_activity_uses_workers(self) -> None:
        snap = Snapshot(workers=[make_worker("a", DONE, start=5.0, end=9.0)])
        payload = webdata.single_payload(snap, stage_dir="/s", label="x")
        self.assertEqual(payload["stages"][0]["last_activity"], 9.0)


def opencode_log() -> str:
    lines = [
        {"type": "tool_use", "timestamp": 1_001_000,
         "part": {"tool": "read", "state": {"input": {"filePath": "/a.txt"}, "title": "read"}}},
        {"type": "text", "timestamp": 1_004_000, "part": {"text": "hello world"}},
    ]
    return "".join(json.dumps(line) + "\n" for line in lines)


class WorkerDetailTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.stage = tmp.name

    def write(self, rel: str, text: str) -> str:
        path = os.path.join(self.stage, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_rejects_unsafe_names(self) -> None:
        for bad in ["a/b", "a\\b", "a..b", "..", "../x", "x/.."]:
            with self.assertRaises(ValueError):
                webdata.worker_detail(self.stage, bad)

    def test_rejects_missing_stage_dir(self) -> None:
        with self.assertRaises(ValueError):
            webdata.worker_detail(os.path.join(self.stage, "nope"), "w")

    def test_rejects_stage_dir_file(self) -> None:
        path = self.write("afile", "x")
        with self.assertRaises(ValueError):
            webdata.worker_detail(path, "w")

    def test_brief_md_and_report(self) -> None:
        self.write("briefs/w.md", "# title\nGoal: do it\n")
        self.write("w/REPORT.md", "## Summary\n- ok\n")
        detail = webdata.worker_detail(self.stage, "w")
        self.assertEqual(detail["name"], "w")
        self.assertEqual(detail["brief"], ["# title", "Goal: do it"])
        self.assertEqual(detail["report"], ["## Summary", "- ok"])
        self.assertEqual(detail["state"], DONE)
        self.assertEqual(detail["steps"], [])
        assert_jsonable(self, detail)

    def test_brief_txt_and_lowercase_report(self) -> None:
        self.write("briefs/w.txt", "brief txt")
        self.write("w/report.md", "report lc")
        detail = webdata.worker_detail(self.stage, "w")
        self.assertEqual(detail["brief"], ["brief txt"])
        self.assertEqual(detail["report"], ["report lc"])

    def test_missing_files_give_empty_lists(self) -> None:
        os.makedirs(os.path.join(self.stage, "w"))
        detail = webdata.worker_detail(self.stage, "w")
        self.assertEqual(detail["brief"], [])
        self.assertEqual(detail["report"], [])
        self.assertEqual(detail["steps"], [])
        self.assertEqual(detail["state"], "")

    def test_steps_from_opencode_log(self) -> None:
        self.write("w.log", opencode_log())
        detail = webdata.worker_detail(self.stage, "w")
        self.assertEqual(detail["steps"], [
            {"ts": 1001.0, "kind": "tool", "tool": "read", "text": "/a.txt", "dur": None, "outcome": None},
            {"ts": 1004.0, "kind": "text", "tool": "", "text": "hello world", "dur": None, "outcome": None},
        ])
        self.assertEqual(detail["state"], "")

    def test_steps_from_txt_log(self) -> None:
        self.write("w.txt", opencode_log())
        detail = webdata.worker_detail(self.stage, "w")
        self.assertEqual(len(detail["steps"]), 2)

    def test_index_cursor_incremental_plain_log(self) -> None:
        path = self.write("w.log", "first\n")
        first = webdata.worker_detail(self.stage, "w")
        self.assertTrue(first["reset"])
        self.assertEqual(first["next"], 1)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("second\n")
        second = webdata.worker_detail(self.stage, "w", str(first["next"]))
        self.assertFalse(second["reset"])
        self.assertEqual(second["next"], 2)
        self.assertEqual([step["text"] for step in second["steps"]], ["second"])
        third = webdata.worker_detail(self.stage, "w", str(second["next"]))
        self.assertEqual(third["steps"], [])
        self.assertEqual(third["next"], 2)
        self.assertNotIn("brief", second)

    def test_index_cursor_reset_on_shrink_and_future_index(self) -> None:
        path = self.write("w.log", "one\ntwo\n")
        first = webdata.worker_detail(self.stage, "w")
        self.assertEqual(webdata.worker_detail(self.stage, "w", "99")["reset"], True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("short\n")
        shrunk = webdata.worker_detail(self.stage, "w", str(first["next"]))
        self.assertTrue(shrunk["reset"])
        self.assertEqual([s["text"] for s in shrunk["steps"]], ["short"])

    def test_index_cursor_resets_when_prefix_is_rewritten_and_file_grows(self) -> None:
        path = self.write("w.log", "first\n")
        first = webdata.worker_detail(self.stage, "w")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("other\n" + ("padding\n" * 1000))
        rewritten = webdata.worker_detail(self.stage, "w", str(first["next"]))
        self.assertTrue(rewritten["reset"])

    def test_large_log_sets_truncated(self) -> None:
        self.write("w.log", "x" * (256 * 1024 + 100) + "\nlast\n")
        detail = webdata.worker_detail(self.stage, "w")
        self.assertTrue(detail["truncated"])

    def test_cursor_advances_past_the_kept_step_cap(self) -> None:
        # the parser keeps only the newest MAX_STEPS steps; the cursor must still advance and never repeat
        def line(n: int) -> str:
            return json.dumps({"type": "text", "part": {"text": f"step {n}"}}) + "\n"
        init = json.dumps({"type": "step_start", "part": {}}) + "\n"
        path = self.write("w.log", init + "".join(line(n) for n in range(250)))
        first = webdata.worker_detail(self.stage, "w")
        self.assertEqual(first["next"], 250)
        self.assertEqual(len(first["steps"]), 200)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("".join(line(n) for n in range(250, 253)))
        again = webdata.worker_detail(self.stage, "w", since=first["next"])
        self.assertFalse(again["reset"])
        self.assertEqual([step["text"] for step in again["steps"]], ["step 250", "step 251", "step 252"])
        self.assertEqual(again["next"], 253)
        stale = webdata.worker_detail(self.stage, "w", since=10)    # fell off the kept window
        self.assertTrue(stale["reset"])
        self.assertEqual(len(stale["steps"]), 200)

    def test_lru_eviction_resets(self) -> None:
        first_stage = tempfile.TemporaryDirectory()
        self.addCleanup(first_stage.cleanup)
        p = os.path.join(first_stage.name, "w.log")
        with open(p, "w", encoding="utf-8") as handle:
            handle.write("one\n")
        webdata.worker_detail(first_stage.name, "w")
        for i in range(9):
            stage = tempfile.TemporaryDirectory()
            self.addCleanup(stage.cleanup)
            with open(os.path.join(stage.name, "w.log"), "w", encoding="utf-8") as handle:
                handle.write("x\n")
            webdata.worker_detail(stage.name, "w")
        self.assertTrue(webdata.worker_detail(first_stage.name, "w", "1")["reset"])

    def test_concurrent_detail_calls(self) -> None:
        self.write("w.log", "one\ntwo\n")
        results = []
        threads = [threading.Thread(target=lambda: results.append(webdata.worker_detail(self.stage, "w")))
                   for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["next"] == 2 for result in results))

    def test_failed_state_from_error_log(self) -> None:
        self.write("w.log", "Error: boom\n")
        detail = webdata.worker_detail(self.stage, "w")
        self.assertEqual(detail["state"], FAILED)

    def test_report_wins_over_error(self) -> None:
        self.write("w.log", "Error: boom\n")
        self.write("w/REPORT.md", "done anyway")
        detail = webdata.worker_detail(self.stage, "w")
        self.assertEqual(detail["state"], DONE)

    def test_detail_is_jsonable(self) -> None:
        detail = webdata.worker_detail(self.stage, "absent")
        json.dumps(detail)
        assert_jsonable(self, detail)


class SerialisationTests(unittest.TestCase):
    def test_full_global_payload_round_trips(self) -> None:
        gs = GlobalSnapshot(
            now=1.0,
            stages=[
                Stage(path="/a", label="a", snap=rich_snapshot(),
                      lead=LeadInfo(model="m", phase="p", stream="s"),
                      lead_state="background", lead_kind="claude bg", lead_pid=1, lead_since=2.0),
            ],
        )
        payload = webdata.global_payload(gs)
        assert_jsonable(self, payload)
        self.assertEqual(json.loads(json.dumps(payload))["mode"], "global")


class SoloPayloadTests(unittest.TestCase):
    def test_stage_summary_carries_solo(self) -> None:
        solo = Stage(path="/p/.stage#7", label="p", snap=Snapshot(), live=0, lead_state="live", lead_pid=7, solo=True)
        plain = Stage(path="/q", label="q", snap=Snapshot())
        summaries = webdata.global_payload(GlobalSnapshot(stages=[solo, plain]))["stages"]
        self.assertIs(summaries[0]["solo"], True)
        self.assertIs(summaries[1]["solo"], False)

    def test_virtual_path_stage_payload_has_no_workers(self) -> None:
        solo = Stage(path="/p/.stage#7", label="p", snap=Snapshot(), live=0, lead_state="live", lead_pid=7, solo=True)
        item = webdata.stage_payload(GlobalSnapshot(stages=[solo]), "/p/.stage#7")
        self.assertEqual(item["workers_full"], [])
        self.assertEqual(item["counts"]["total"], 0)
        self.assertIs(item["solo"], True)

    def test_single_payload_stage_is_not_solo(self) -> None:
        payload = webdata.single_payload(rich_snapshot(), stage_dir="/stage", label="proj")
        self.assertIs(payload["stages"][0]["solo"], False)


class ReadableModelPayloadTests(unittest.TestCase):
    def test_lead_model_is_sent_as_its_readable_name(self) -> None:
        stage = Stage(path="/p/.stage", label="p", snap=Snapshot(), lead_state="live", lead_model="opencode/claude-haiku-5-5")
        element = webdata.global_payload(GlobalSnapshot(stages=[stage]))["stages"][0]
        self.assertEqual(element["lead_model"], "Haiku 5.5")


# -- /api/state ops fields (additive, schema v2) -----------------------------------

NOW = 1_800_000_000.0

# key sets as they were before the ops fields (frozen from the pre-change payload)
BEFORE_ENVELOPE = {"cost_total", "dismissed", "errors", "generated", "hidden_idle", "mode", "now", "openrouter", "poll_ms", "spend", "stages", "title", "tokens_total", "v"}
BEFORE_SINGLE_ENVELOPE = BEFORE_ENVELOPE - {"openrouter", "spend"}
BEFORE_STAGE = {"account", "app", "cost_total", "counts", "cwd", "effort", "execution_id", "goal", "groups", "label", "last_activity", "latest_milestone", "launch_depth", "lead", "lead_ctx", "lead_kind", "lead_model", "lead_pid", "lead_since", "lead_state", "linkage", "live", "more_done", "native_id", "outcome", "output_available", "parent_id", "parent_session", "path", "project", "purpose_source", "reviews", "root_id", "root_session", "section", "session_id", "session_name", "solo", "tag", "tmux", "tokens_total", "workers"}
BEFORE_ROW = {"account", "cost", "depth", "end", "errors", "execution_id", "family", "goal", "harness", "has_report", "is_review", "job", "level", "linkage", "model", "name", "native_id", "outcome", "output_available", "parent", "parent_id", "parent_session", "purpose_source", "quiet_s", "root_id", "root_session", "start", "state", "title", "tokens_total"}
BEFORE_STAGE_PAYLOAD = {"account", "app", "brief_text", "cost_total", "counts", "cwd", "effort", "execution_id", "goal", "groups", "label", "last_activity", "latest_milestone", "launch_depth", "lead", "lead_ctx", "lead_kind", "lead_model", "lead_pid", "lead_since", "lead_state", "linkage", "live", "milestones", "native_id", "outcome", "output_available", "parent_id", "parent_session", "path", "project", "purpose_source", "reviews", "reviews_full", "root_id", "root_session", "section", "session_id", "session_name", "solo", "tag", "tmux", "tokens_total", "workers_full"}
BEFORE_WORKER_FULL = {"account", "activity", "brief_text", "cost", "cpu", "depth", "end", "errors", "execution_id", "family", "goal", "harness", "has_report", "is_review", "job", "level", "linkage", "log_model", "model", "name", "native_id", "nproc", "outcome", "output_available", "parent", "parent_id", "parent_session", "pid", "purpose_source", "quiet_s", "result_text", "root_id", "root_session", "rss", "start", "state", "summary", "title", "tokens", "tool_counts"}
BEFORE_REVIEWS = {"clean", "p1", "p2", "p3", "running", "total"}
BEFORE_DETAIL = {"account", "brief", "cost", "end", "execution_id", "goal", "linkage", "model", "name", "native_id", "next", "outcome", "output_available", "parent_id", "purpose_source", "report", "result", "root_id", "start", "state", "steps", "title", "tokens", "truncated"}
BEFORE_STEP = {"kind", "text", "tool", "ts"}

# the additive keys
NEW_STAGE = {"level_tag", "display_name", "lead_ctx_window", "lead_tokens", "lead_cost", "lead_tools", "lead_tool_bins", "work", "vitals", "turns"}
NEW_ROW = {"tool_bins", "ctx_window", "level_tag", "flags", "failure"}
NEW_STEP = {"dur", "outcome"}
BIN_COUNT = 15


def make_turn(k: int) -> Turn:
    calls = [TurnTool(ts=NOW - 60 + c, name="exec", ok=True, dur=1.0, args="ls") for c in range(3)]
    return Turn(run_id=f"run{k}", start=NOW - 3600 + k * 60, end=NOW - 3590 + k * 60, ask="ask", model="opus",
                provider="openclaw", tokens_in=100, tokens_out=50, tokens_cache=10, cost=0.01, latency=10.0,
                tools={"exec": 3}, calls=calls)


def synthetic_global(count: int) -> GlobalSnapshot:
    """The size-report fixture (same shape as the generator): three workers per stage, OpenClaw turns on the first ten."""
    stages = []
    for i in range(count):
        workers = []
        for j in range(3):
            steps = [Step(ts=NOW - 600 + k, kind="tool", tool="read", text=f"f{k}") for k in range(20)]
            pulses = [(NOW - 1800 + 60 * k, 1) for k in range(30)]
            log = make_log(steps=steps, steps_total=20, tool_counts={"read": 20}, tool_pulses=pulses, last_ts=NOW)
            workers.append(make_worker(
                f"w{i}-{j}", RUNNING if j == 0 else DONE, start=NOW - 900, end=None if j == 0 else NOW - 100,
                title="t" * 40, goal="g", model="luna@medium", family="codex", log=log, activity=[1] * 60,
                quiet_s=3.0, summary=["s"], job="build", brief_text=["b"] * 5, result_text=["r"] * 5,
                execution_id=f"x{i}-{j}"))
        turns = [make_turn(k) for k in range(60)] if i < 10 else []
        vitals = Vitals(model="gpt", provider="openclaw", ctx_used=1000, ctx_budget=8000, cost=0.1,
                        runtime=5.0, status="done") if i < 10 else None
        snap = Snapshot(title=f"stage {i}", now=NOW, workers=workers, tokens_total=100, cost_total=0.5)
        stages.append(Stage(
            tag="CC·W", path=f"/p/{i}/.stage", label=f"stage-{i}", snap=snap, live=1, last_activity=NOW,
            lead=LeadInfo(model="Opus 5.5", phase="build", stream="stream-x"), lead_state="live",
            lead_kind="claude", lead_pid=1, lead_since=NOW - 3600, session_id=f"s{i}", session_name=f"sess-{i}",
            lead_model="opus-5.5", effort="high", cwd=f"/p/{i}",
            work=WorkState(branch="feat/x", dirty=True, ahead=1, unmerged=True, checked=NOW),
            vitals=vitals, turns=turns))
    return GlobalSnapshot(now=NOW, stages=stages, tokens_total=100, cost_total=50.0)


def summary_stage(stage: Stage) -> dict:
    return webdata.global_payload(GlobalSnapshot(now=NOW, stages=[stage]))["stages"][0]


class OpsKeySetTests(unittest.TestCase):
    def test_old_key_sets_are_unchanged(self) -> None:
        gs = synthetic_global(12)
        envelope = webdata.global_payload(gs)
        single = webdata.single_payload(gs.stages[0].snap, stage_dir="/p/0/.stage", label="x")
        detail_stage = webdata.stage_payload(gs, "/p/0/.stage")
        stage = envelope["stages"][0]
        self.assertLessEqual(BEFORE_ENVELOPE, set(envelope))
        self.assertLessEqual(BEFORE_SINGLE_ENVELOPE, set(single))
        self.assertLessEqual(BEFORE_STAGE, set(stage))
        self.assertLessEqual(BEFORE_ROW, set(stage["workers"][0]))
        self.assertLessEqual(BEFORE_STAGE_PAYLOAD, set(detail_stage))
        self.assertLessEqual(BEFORE_WORKER_FULL, set(detail_stage["workers_full"][0]))
        self.assertLessEqual(BEFORE_REVIEWS, set(detail_stage["reviews"]))
        detail = webdata.execution_detail(gs.stages[0].snap.workers, "x0-0")
        self.assertLessEqual(BEFORE_DETAIL, set(detail))
        self.assertLessEqual(BEFORE_STEP, set(detail["steps"][0]))

    def test_new_keys_are_exactly_the_additive_set(self) -> None:
        gs = synthetic_global(12)
        stage = webdata.global_payload(gs)["stages"][0]
        detail_stage = webdata.stage_payload(gs, "/p/0/.stage")
        self.assertEqual(set(stage), BEFORE_STAGE | NEW_STAGE - {"turns"} | {"turns_summary"})
        self.assertEqual(set(stage["workers"][0]), BEFORE_ROW | NEW_ROW)
        self.assertEqual(set(detail_stage), BEFORE_STAGE_PAYLOAD | NEW_STAGE | {"ops"})
        self.assertEqual(set(detail_stage["workers_full"][0]), BEFORE_WORKER_FULL | NEW_ROW)
        self.assertEqual(set(detail_stage["reviews"]), BEFORE_REVIEWS)
        detail = webdata.execution_detail(gs.stages[0].snap.workers, "x0-0")
        self.assertEqual(set(detail), BEFORE_DETAIL)
        self.assertEqual(set(detail["steps"][0]), BEFORE_STEP | NEW_STEP)

    def test_envelopes_stay_v2_with_ops_marker(self) -> None:
        gs = synthetic_global(3)
        envelope = webdata.global_payload(gs)
        single = webdata.single_payload(gs.stages[0].snap, stage_dir="/p/0/.stage", label="x")
        detail_stage = webdata.stage_payload(gs, "/p/0/.stage")
        self.assertEqual((envelope["v"], envelope["ops"]), (2, 1))
        self.assertEqual((single["v"], single["ops"]), (2, 1))
        self.assertEqual(detail_stage["ops"], 1)
        assert_jsonable(self, envelope)
        assert_jsonable(self, detail_stage)


class OpsUnknownTests(unittest.TestCase):
    def test_unknown_stage_fields_are_null(self) -> None:
        element = summary_stage(Stage(path="/p", label="p", snap=Snapshot()))
        for key in ("level_tag", "display_name", "lead_ctx_window", "lead_tokens", "lead_cost", "lead_tools",
                    "lead_tool_bins", "work", "vitals"):
            self.assertIsNone(element[key], key)
        self.assertEqual(element["turns_summary"], {"count": 0, "retries": 0, "errors": 0, "last": None})

    def test_unknown_worker_fields_are_null(self) -> None:
        worker = make_worker("w", RUNNING, log=LogState())
        row = summary_stage(Stage(path="/p", label="p", snap=Snapshot(workers=[worker])))["workers"][0]
        self.assertIsNone(row["tool_bins"])
        self.assertIsNone(row["ctx_window"])
        self.assertIsNone(row["level_tag"])
        self.assertIsNone(row["failure"])
        self.assertEqual(row["flags"], [])

    def test_known_zeros_stay_zero(self) -> None:
        element = summary_stage(Stage(path="/p", label="p", snap=Snapshot(), lead_tools=0, lead_cost=0.0, lead_tokens=0))
        self.assertEqual(element["lead_tools"], 0)
        self.assertEqual(element["lead_cost"], 0.0)
        self.assertEqual(element["lead_tokens"], 0)
        self.assertEqual(element["lead_tool_bins"], [0] * BIN_COUNT)

    def test_step_unknown_and_zero_duration(self) -> None:
        plain = webdata._step_dict(Step(ts=1.0, kind="tool", tool="read", text="a"))
        self.assertIsNone(plain["dur"])
        self.assertIsNone(plain["outcome"])
        zero = webdata._step_dict(Step(ts=1.0, kind="tool", tool="read", text="a", dur=0.0, outcome="ok"))
        self.assertEqual(zero["dur"], 0.0)
        self.assertEqual(zero["outcome"], "ok")

    def test_unknown_turn_fields_are_null(self) -> None:
        turn = webdata._turn_dict(Turn(run_id="r", start=1.0), calls=False)
        for key in ("end", "ask", "model", "provider", "tokens_in", "tokens_out", "tokens_cache", "cost", "latency", "error"):
            self.assertIsNone(turn[key], key)
        self.assertEqual(turn["outcome"], "ok")
        self.assertEqual(turn["attempts"], 1)
        self.assertEqual(turn["tools"], {})

    def test_unknown_vitals_fields_are_null(self) -> None:
        element = summary_stage(Stage(path="/p", label="p", snap=Snapshot(),
                                      vitals=Vitals(model="gpt", ctx_budget=8000, compactions=0)))
        vitals = element["vitals"]
        self.assertIsNone(vitals["ctx_used"])
        self.assertIsNone(vitals["cost"])
        self.assertIsNone(vitals["runtime"])
        self.assertIsNone(vitals["status"])
        self.assertEqual(vitals["ctx_budget"], 8000)
        self.assertEqual(vitals["compactions"], 0)


class OpsToolBinTests(unittest.TestCase):
    def test_fifteen_two_minute_bins_ending_now(self) -> None:
        start = NOW - 1800
        pulses = [(start, 1), (start + 119.9, 1), (start + 120, 1), (start + 1200, 4),
                  (NOW, 2), (start + 1799, 1), (start - 1, 5), (NOW + 1, 7)]
        bins = webdata._tool_bins(pulses, NOW)
        expect = [0] * BIN_COUNT
        expect[0], expect[1], expect[10], expect[14] = 2, 1, 4, 3
        self.assertEqual(webdata.TOOL_BINS, BIN_COUNT)
        self.assertEqual(webdata.TOOL_BIN_S, 120)
        self.assertEqual(bins, expect)

    def test_worker_row_bins_match_the_helper(self) -> None:
        pulses = [(NOW - 1800, 1), (NOW - 60, 3)]
        worker = make_worker("w", RUNNING, log=make_log(tool_pulses=pulses))
        row = summary_stage(Stage(path="/p", label="p", snap=Snapshot(workers=[worker])))["workers"][0]
        expect = [0] * BIN_COUNT
        expect[0], expect[14] = 1, 3
        self.assertEqual(row["tool_bins"], expect)

    def test_known_log_without_calls_gives_zero_bins(self) -> None:
        worker = make_worker("w", RUNNING, log=make_log(tool_pulses=[]))
        row = summary_stage(Stage(path="/p", label="p", snap=Snapshot(workers=[worker])))["workers"][0]
        self.assertEqual(row["tool_bins"], [0] * BIN_COUNT)

    def test_lead_bins_come_from_lead_pulses(self) -> None:
        element = summary_stage(Stage(path="/p", label="p", snap=Snapshot(), lead_tools=3,
                                      lead_tool_pulses=[(NOW, 3)]))
        self.assertEqual(element["lead_tool_bins"][14], 3)
        self.assertEqual(sum(element["lead_tool_bins"]), 3)


class OpsWorkerFieldTests(unittest.TestCase):
    def test_level_flags_failure_and_ctx_window(self) -> None:
        worker = make_worker("bad", FAILED, level_tag="SPAWN", flags=frozenset({"no log", "empty log"}),
                             failure="exit 1", log=make_log(ctx_window=200000))
        row = summary_stage(Stage(path="/p", label="p", snap=Snapshot(workers=[worker])))["workers"][0]
        self.assertEqual(row["level_tag"], "SPAWN")
        self.assertEqual(row["flags"], ["empty log", "no log"])
        self.assertEqual(row["failure"], "exit 1")
        self.assertEqual(row["ctx_window"], 200000)

    def test_execution_detail_steps_carry_dur_and_outcome(self) -> None:
        step = Step(ts=1.0, kind="tool", tool="exec", text="ls", dur=2.5, outcome="failed")
        worker = make_worker("w", execution_id="x1", log=make_log(steps=[step], steps_total=1))
        detail = webdata.execution_detail([worker], "x1")
        self.assertEqual(detail["steps"], [{"ts": 1.0, "kind": "tool", "tool": "exec", "text": "ls",
                                            "dur": 2.5, "outcome": "failed"}])


class OpsStageFieldTests(unittest.TestCase):
    def test_stage_names_and_level(self) -> None:
        element = summary_stage(Stage(path="/p", label="p", snap=Snapshot(), level_tag="LEAD",
                                      display_name="Bot · main"))
        self.assertEqual(element["level_tag"], "LEAD")
        self.assertEqual(element["display_name"], "Bot · main")


class OpsWorkTests(unittest.TestCase):
    def test_badge_is_worst_state_first(self) -> None:
        cases = [
            (WorkState(branch="f", dirty=True, ahead=2, unmerged=True, checked=NOW), "uncommitted"),
            (WorkState(branch="f", dirty=False, ahead=2, unmerged=True, checked=NOW), "2 unpushed"),
            (WorkState(branch="f", dirty=False, ahead=0, unmerged=True, checked=NOW), "unmerged"),
            (WorkState(branch="f", dirty=False, ahead=0, unmerged=False, checked=NOW), "merged"),
        ]
        for work, badge in cases:
            with self.subTest(badge=badge):
                element = summary_stage(Stage(path="/p", label="p", snap=Snapshot(), work=work))
                self.assertEqual(element["work"]["badge"], badge)
                self.assertEqual(element["work"]["checked"], NOW)

    def test_clean_work_has_empty_badge_and_null_checked(self) -> None:
        element = summary_stage(Stage(path="/p", label="p", snap=Snapshot(),
                                      work=WorkState(branch="main", dirty=False, ahead=0, unmerged=None)))
        self.assertEqual(element["work"]["badge"], "")
        self.assertIsNone(element["work"]["checked"])

    def test_unknown_work_has_null_badge(self) -> None:
        element = summary_stage(Stage(path="/p", label="p", snap=Snapshot(), work=WorkState()))
        self.assertIsNone(element["work"]["badge"])
        self.assertIsNone(element["work"]["dirty"])
        self.assertIsNone(element["work"]["branch"])


class OpsTurnTests(unittest.TestCase):
    def test_summary_carries_turn_counts_not_turns(self) -> None:
        stage = Stage(path="/p", label="p", snap=Snapshot(), turns=[make_turn(k) for k in range(60)])
        element = summary_stage(stage)
        self.assertNotIn("turns", element)
        summary = element["turns_summary"]
        self.assertEqual(summary["count"], 60)
        self.assertEqual(summary["last"]["run_id"], "run59")
        self.assertEqual(summary["last"]["tools"], {"exec": 3})
        self.assertNotIn("calls", summary["last"])

    def test_stage_detail_turns_carry_calls(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[Stage(path="/p", label="p", snap=Snapshot(),
                                                   turns=[make_turn(k) for k in range(3)])])
        turn = webdata.stage_payload(gs, "/p")["turns"][-1]
        self.assertEqual(turn["calls"], [{"ts": NOW - 60 + c, "name": "exec", "ok": True, "dur": 1.0, "args": "ls"}
                                         for c in range(3)])


class PayloadSizeTests(unittest.TestCase):
    def test_hundred_stage_payload_sizes(self) -> None:
        gs = synthetic_global(100)
        global_bytes = len(json.dumps(webdata.global_payload(gs)).encode("utf-8"))
        stage_bytes = len(json.dumps(webdata.stage_payload(gs, "/p/0/.stage")).encode("utf-8"))
        # guards against runaway growth; the measured sizes are in docs/contract-web.md
        self.assertLess(global_bytes, 600_000)
        self.assertLess(stage_bytes, 60_000)


if __name__ == "__main__":
    unittest.main()
