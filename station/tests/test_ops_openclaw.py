"""D5: Bot turns and vitals from the agent database, per-session rows, subagents under their parent, crons in one project.

The fixture copies the column set of the live database (``session_nodes``, ``session_windows``,
``trajectory_runtime_events``) and writes only synthetic rows. Every read is read-only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import collect
import openclaw
from model import TOOL_RING_S

NOW = 1_792_830_000.0
KEY = "agent:openclaw:main"
PEER = collect.OPENCLAW_OWN_PEERS[0]

SCHEMA = """
CREATE TABLE session_nodes (
  session_key TEXT NOT NULL PRIMARY KEY,
  current_session_id TEXT NOT NULL DEFAULT '',
  entry_json TEXT NOT NULL DEFAULT '{}',
  updated_at INTEGER NOT NULL DEFAULT 0,
  status TEXT, created_at INTEGER, parent_session_key TEXT, spawned_by TEXT,
  label TEXT, display_name TEXT, last_activity_at INTEGER
);
CREATE TABLE session_windows (
  session_id TEXT NOT NULL PRIMARY KEY,
  session_key TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE trajectory_runtime_events (
  session_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  run_id TEXT,
  event_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (session_id, seq)
);
"""


class Db:
    """A synthetic Bot agent database in a temporary home."""

    def __init__(self) -> None:
        self.home = tempfile.mkdtemp()
        self.dir = os.path.join(self.home, ".openclaw", "agents", "openclaw", "agent")
        os.makedirs(self.dir)
        self.path = os.path.join(self.dir, "openclaw-agent.sqlite")
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def window(self, session_id: str, key: str = KEY, created: float = NOW - 3600) -> None:
        self.conn.execute("INSERT OR REPLACE INTO session_windows VALUES (?,?,?,?)",
                          (session_id, key, int(created * 1000), int(created * 1000)))

    def node(self, key: str = KEY, *, entry: dict | None = None, status: str = "done",
             activity: float = NOW - 60, parent: str = "", session_id: str = "s1") -> None:
        ms = int(activity * 1000)
        self.conn.execute(
            "INSERT OR REPLACE INTO session_nodes (session_key, current_session_id, entry_json, updated_at, status,"
            " created_at, parent_session_key, label, display_name, last_activity_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (key, session_id, json.dumps(entry or {}), ms, status, ms - 60_000, parent, "label " + key[-4:], "", ms))

    def events(self, session_id: str, seq: int, events: list[tuple[str, dict, float]],
               run: str | None = None, provider: str = "openai", model: str = "gpt-6-luna") -> int:
        for kind, data, ts in events:
            body = {"type": kind, "ts": ts, "sessionId": session_id, "runId": run,
                    "provider": provider, "modelId": model, "data": data}
            self.conn.execute("INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)",
                              (session_id, seq, run, json.dumps(body), int(ts * 1000)))
            seq += 1
        return seq

    def close(self) -> None:
        self.conn.close()


def prompt(text: str, ts: float, **data: object) -> tuple[str, dict, float]:
    return ("prompt.submitted", {"prompt": text, **data}, ts)


def done(ts: float, *, stop: str = "stop", usage: dict | None = None, **data: object) -> tuple[str, dict, float]:
    return ("model.completed", {"stopReason": stop, "usage": usage or {}, **data}, ts)


def call(name: str, ts: float, args: object, call_id: str = "c1") -> tuple[str, dict, float]:
    return ("tool.call", {"name": name, "toolCallId": call_id, "arguments": args}, ts)


def result(name: str, ts: float, *, ok: bool | None = True, call_id: str = "c1") -> tuple[str, dict, float]:
    data = {"name": name, "toolCallId": call_id}
    if ok is not None:
        data["success"] = ok
    return ("tool.result", data, ts)


def usage(**over: object) -> dict:
    base = {"input": 100, "output": 20, "cacheRead": 5, "cacheWrite": 0, "total": 125, "cost": {"total": 0}}
    base.update(over)
    return base


class Turns(unittest.TestCase):
    """trajectory_runtime_events grouped by run_id; the last model.completed of a run wins."""

    def setUp(self) -> None:
        openclaw.clear_cache()
        self.db = Db()
        self.addCleanup(self.db.close)
        self.addCleanup(openclaw.clear_cache)

    def turns(self, key: str = KEY, **kwargs: object) -> list:
        self.db.conn.commit()
        return openclaw.read_turns(self.db.path, key, **kwargs)

    def test_a_retried_run_is_one_turn_with_two_attempts_and_the_failed_attempts_error(self) -> None:
        self.db.window("s1")
        t0 = NOW - 600
        self.db.events("s1", 1, [
            prompt("fix the parser", t0),
            done(t0 + 10, stop=None, usage=usage(), promptError="Context overflow: prompt too large for the model (precheck).",
                 promptErrorSource="precheck"),
            done(t0 + 20, usage=usage(input=200, output=40, total=240)),
        ], run="r1")
        (turn,) = self.turns()
        self.assertEqual((turn.run_id, turn.attempts, turn.outcome), ("r1", 2, "ok"))
        self.assertEqual(turn.error, "Context overflow: prompt too large for the model (precheck).")
        self.assertEqual((turn.tokens_in, turn.tokens_out, turn.tokens_cache), (200, 40, 5))
        self.assertEqual((turn.start, turn.end, turn.latency), (t0, t0 + 20, 20))
        self.assertEqual(turn.model, "gpt-6-luna")

    def test_an_error_turn_carries_the_reason_and_an_aborted_turn_its_own_outcome(self) -> None:
        self.db.window("s1")
        t0 = NOW - 500
        self.db.events("s1", 1, [
            prompt("go", t0),
            done(t0 + 5, stop="error", usage=usage(),
                 assistantTexts=["OpenClaw stopped this run because tool-loop recovery hit another critical loop."]),
        ], run="bad")
        self.db.events("s1", 10, [prompt("stop it", t0 + 60), done(t0 + 65, stop="aborted", aborted=True, usage=usage())],
                       run="aborted")
        self.db.events("s1", 20, [prompt("still going", t0 + 120)], run="live")
        bad, stopped, live = self.turns()
        self.assertEqual((bad.outcome, bad.error),
                         ("error", "OpenClaw stopped this run because tool-loop recovery hit another critical loop."))
        self.assertEqual((stopped.outcome, stopped.error), ("aborted", ""))
        self.assertEqual((live.outcome, live.end, live.attempts), ("running", None, 1))

    def test_the_ask_is_the_first_line_of_the_user_message_within_120_characters(self) -> None:
        self.db.window("s1")
        t0 = NOW - 400
        self.db.events("s1", 1, [
            prompt("Notes from a plugin\nand more", t0),
            done(t0 + 5, usage=usage(), finalPromptText="Fix the parser\nand then the tests"),
        ], run="r1")
        self.db.events("s1", 10, [prompt("x", t0 + 60),
                                  done(t0 + 61, usage=usage(), finalPromptText="y" * 400)], run="r2")
        self.db.events("s1", 20, [prompt("no final text", t0 + 120), done(t0 + 121, usage=usage())], run="r3")
        first, long_ask, fallback = self.turns()
        self.assertEqual(first.ask, "Fix the parser")
        self.assertLessEqual(len(long_ask.ask), 120)
        self.assertTrue(long_ask.ask.endswith("…"))
        self.assertEqual(fallback.ask, "no final text")

    def test_tools_carry_time_name_outcome_duration_and_an_80_character_gist(self) -> None:
        self.db.window("s1")
        t0 = NOW - 300
        long_command = "run " + "x" * 200
        self.db.events("s1", 1, [
            prompt("do it", t0),
            call("exec", t0 + 1, {"command": "ls -la ~/Projects", "timeoutSeconds": 30}, call_id="a"),
            result("exec", t0 + 4, ok=True, call_id="a"),
            call("read", t0 + 5, {"filePath": "/p/x.py"}, call_id="b"),
            result("read", t0 + 6, ok=False, call_id="b"),
            call("exec", t0 + 7, {"command": long_command}, call_id="c"),
            result("exec", t0 + 8, ok=None, call_id="c"),   # no success flag: unknown, not ok
            call("exec", t0 + 9, {"command": "OPENCLAW_TOKEN=abc make test"}, call_id="d"),
            call("exec", t0 + 10, {"command": "curl -H 'Authorization: Bearer xyz' https://x"}, call_id="e"),
            call("exec", t0 + 11, {"command": "python3 tokenizer.py"}, call_id="f"),
            done(t0 + 12, usage=usage()),
        ], run="r1")
        (turn,) = self.turns()
        self.assertEqual(turn.tools, {"exec": 5, "read": 1})
        self.assertEqual([(c.name, c.ok) for c in turn.calls],
                         [("exec", True), ("read", False), ("exec", None), ("exec", None), ("exec", None), ("exec", None)])
        self.assertAlmostEqual(turn.calls[0].dur, 3.0)
        self.assertIn("ls -la ~/Projects", turn.calls[0].args)
        self.assertLessEqual(len(turn.calls[2].args), 80)
        self.assertEqual(turn.calls[3].args, "")     # an env-like argument is dropped, never shown
        self.assertEqual(turn.calls[4].args, "")     # ... and so is one carrying a credential
        self.assertIn("tokenizer.py", turn.calls[5].args)   # a harmless name is not mistaken for a secret

    def test_a_run_with_no_prompt_event_has_no_latency_and_keeps_its_reply(self) -> None:
        self.db.window("s1")
        t0 = NOW - 350
        self.db.events("s1", 1, [done(t0 + 5, usage=usage(), finalPromptText="came from nowhere")], run="r1")
        (turn,) = self.turns()
        self.assertEqual((turn.start, turn.end, turn.latency), (t0 + 5, t0 + 5, None))
        self.assertEqual(turn.ask, "came from nowhere")

    def test_every_window_of_a_key_is_read_not_only_its_current_one(self) -> None:
        self.db.window("old", created=NOW - 7200)
        self.db.window("new", created=NOW - 600)
        self.db.node(session_id="new")
        first, second, third = NOW - 7000, NOW - 6500, NOW - 500
        self.db.events("old", 1, [prompt("one", first), done(first + 5, usage=usage())], run="r1")
        self.db.events("old", 10, [prompt("two", second), done(second + 5, usage=usage())], run="r2")
        self.db.events("new", 1, [prompt("three", third), done(third + 5, usage=usage())], run="r3")
        self.assertEqual([turn.ask for turn in self.turns()], ["one", "two", "three"])
        self.assertEqual([turn.ask for turn in self.turns(since=NOW - 1000)], ["three"])
        self.assertEqual([turn.ask for turn in self.turns(limit=2)], ["two", "three"])

    def test_a_session_older_than_the_trajectory_has_no_turns_but_keeps_its_vitals(self) -> None:
        self.db.node("agent:openclaw:suite-old", entry={"model": "old-model", "contextTokens": 8000})
        self.db.conn.commit()
        self.assertEqual(openclaw.read_turns(self.db.path, "agent:openclaw:suite-old"), [])
        self.assertEqual(openclaw.read_vitals(self.db.path, "agent:openclaw:suite-old").model, "old-model")

    def test_a_broken_or_missing_database_gives_nothing(self) -> None:
        self.assertEqual(openclaw.read_turns(os.path.join(self.db.dir, "gone.sqlite"), KEY), [])
        broken = os.path.join(self.db.dir, "broken.sqlite")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("not a database")
        self.assertEqual(openclaw.read_turns(broken, KEY), [])
        self.assertIsNone(openclaw.read_vitals(broken, KEY))

    def test_reads_are_read_only_and_the_connection_is_closed(self) -> None:
        self.db.window("s1")
        t0 = NOW - 200
        self.db.events("s1", 1, [prompt("hi", t0), done(t0 + 5, usage=usage())], run="r1")
        self.db.conn.commit()
        calls: list[str] = []
        real = sqlite3.connect

        def connect(database: str, *args: object, **kwargs: object) -> sqlite3.Connection:
            calls.append(database)
            return real(database, *args, **kwargs)

        with mock.patch.object(openclaw.sqlite3, "connect", side_effect=connect):
            self.assertEqual(len(self.turns()), 1)
            openclaw.read_turns(self.db.path, KEY)   # warm: cached by the database and WAL stamp
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("?mode=ro"))

    def test_a_write_to_the_wal_is_read_again(self) -> None:
        self.db.window("s1")
        t0 = NOW - 100
        self.db.events("s1", 1, [prompt("one", t0), done(t0 + 5, usage=usage())], run="r1")
        self.assertEqual(len(self.turns()), 1)
        self.db.events("s1", 10, [prompt("two", t0 + 30), done(t0 + 35, usage=usage())], run="r2")
        self.assertEqual(len(self.turns()), 2)


class Vitals(unittest.TestCase):
    """The vitals strip from session_nodes.entry_json."""

    def setUp(self) -> None:
        openclaw.clear_cache()
        self.db = Db()
        self.addCleanup(self.db.close)
        self.addCleanup(openclaw.clear_cache)

    def vitals(self, key: str = KEY):
        self.db.conn.commit()
        return openclaw.read_vitals(self.db.path, key)

    def test_model_context_compactions_runtime_status_and_error(self) -> None:
        self.db.node(entry={"model": "ornith-1.5-9b", "modelProvider": "llamacpp", "contextTokens": 105_000,
                            "contextBudgetStatus": {"estimatedPromptTokens": 59_296, "contextTokenBudget": 105_000},
                            "compactionCount": 2, "estimatedCostUsd": 0, "runtimeMs": 13_890,
                            "status": "done", "lastRunError": "LLM request failed: network connection error."})
        got = self.vitals()
        self.assertEqual((got.model, got.provider), ("ornith-1.5-9b", "llamacpp"))
        self.assertEqual((got.ctx_used, got.ctx_budget), (59_296, 105_000))
        self.assertEqual((got.compactions, got.runtime, got.status), (2, 13.89, "done"))
        self.assertEqual(got.last_error, "LLM request failed: network connection error.")
        self.assertIsNone(got.cost)   # a local model is unmetered: unknown, never $0.00

    def test_a_metered_provider_reports_its_cost_and_unknown_fields_stay_none(self) -> None:
        self.db.node(entry={"model": "gpt-6-luna", "modelProvider": "openai", "estimatedCostUsd": 0.2534})
        got = self.vitals()
        self.assertEqual(got.cost, 0.2534)
        self.assertIsNone(got.ctx_used)
        self.assertIsNone(got.ctx_budget)
        self.assertIsNone(got.runtime)
        self.assertEqual((got.compactions, got.status, got.last_error), (0, "done", ""))   # done comes from the column

    def test_an_empty_or_broken_entry_gives_none(self) -> None:
        self.db.node(entry={})
        self.assertEqual(self.vitals().model, "")
        self.db.conn.execute("UPDATE session_nodes SET entry_json = 'oops' WHERE session_key = ?", (KEY,))
        self.assertIsNone(self.vitals())
        self.assertIsNone(self.vitals("agent:openclaw:unknown"))


class StageRows(unittest.TestCase):
    """Stage.turns/vitals filled for each Bot row, and their lead metric columns."""

    def setUp(self) -> None:
        openclaw.clear_cache()
        self.db = Db()
        self.addCleanup(self.db.close)
        self.addCleanup(openclaw.clear_cache)

    def stages(self) -> dict[str, collect.Stage]:
        self.db.conn.commit()
        return {stage.session_id: stage for stage in collect._co_openclaw_stages(NOW, False, self.db.home)}

    def add_main_turn(self, t0: float, *, cost: float = 0, tokens: tuple[int, int] = (100, 20),
                      calls: int = 1, seq: int = 1) -> None:
        events: list[tuple[str, dict, float]] = [prompt("hello", t0)]
        for index in range(calls):
            events += [call("exec", t0 + index + 1, {"command": f"step {index}"}, call_id=f"c{index}"),
                       result("exec", t0 + index + 1.5, call_id=f"c{index}")]
        events.append(done(t0 + calls + 2, usage=usage(input=tokens[0], output=tokens[1], cost={"total": cost})))
        self.db.events("s1", seq, events, run=f"run-{seq}")

    def test_turns_vitals_and_the_lead_columns_come_from_the_turn_data(self) -> None:
        self.db.window("s1")
        self.db.node(entry={"model": "gpt-6-luna", "modelProvider": "openai", "contextTokens": 200_000,
                            "contextBudgetStatus": {"estimatedPromptTokens": 12_345}})
        self.add_main_turn(NOW - 300, cost=0.25, tokens=(1000, 200), calls=2, seq=1)
        self.add_main_turn(NOW - 60, cost=0.5, tokens=(500, 100), calls=1, seq=10)
        stage = self.stages()[KEY]
        self.assertEqual([turn.ask for turn in stage.turns], ["hello", "hello"])
        self.assertEqual((stage.vitals.model, stage.vitals.ctx_budget), ("gpt-6-luna", 200_000))
        self.assertEqual(stage.lead_tokens, 1800)
        self.assertEqual(stage.lead_cost, 0.25 * 1 + 0.5 * 1)   # no vitals cost: the turns' own spend
        self.assertEqual(stage.lead_ctx_window, 200_000)
        self.assertEqual(stage.lead_tools, 3)
        self.assertEqual(sum(count for _, count in stage.lead_tool_pulses), 3)

    def test_tool_pulses_come_from_recent_calls_and_are_pruned(self) -> None:
        self.db.window("s1")
        self.db.node()
        self.add_main_turn(NOW - 60, calls=2, seq=1)
        self.add_main_turn(NOW - TOOL_RING_S - 600, calls=1, seq=10)
        stage = self.stages()[KEY]
        self.assertEqual(stage.lead_tools, 3)
        self.assertEqual(sum(count for _, count in stage.lead_tool_pulses), 2)
        self.assertTrue(all(ts >= NOW - TOOL_RING_S for ts, _ in stage.lead_tool_pulses))

    def test_the_turn_list_keeps_the_newest_fifty(self) -> None:
        self.db.window("s1")
        self.db.node()
        for index in range(55):
            self.add_main_turn(NOW - 3000 + index, calls=1, seq=1 + index * 10)
        stage = self.stages()[KEY]
        self.assertEqual(len(stage.turns), 50)
        self.assertEqual(stage.lead_tokens, 55 * 120)   # the count covers every turn read (200), the list keeps 50

    def test_an_unmetered_session_has_no_cost_and_an_older_session_no_turns(self) -> None:
        self.db.window("s1")
        self.db.node(entry={"model": "ornith-1.5-9b", "modelProvider": "llamacpp", "contextTokens": 105_000,
                            "estimatedCostUsd": 0})
        self.add_main_turn(NOW - 60, tokens=(300, 40), calls=1)
        self.db.window("s2", key="agent:openclaw:dashboard:old")
        self.db.node("agent:openclaw:dashboard:old", session_id="s2",
                     entry={"model": "ornith-1.5-9b", "contextTokens": 105_000})
        rows = self.stages()
        self.assertIsNone(rows[KEY].lead_cost)
        old = rows["agent:openclaw:dashboard:old"]
        self.assertEqual((old.turns, old.lead_tokens, old.lead_cost, old.lead_tools), ([], None, None, None))
        self.assertEqual((old.vitals.model, old.lead_ctx_window), ("ornith-1.5-9b", 105_000))


class Tree(unittest.TestCase):
    """Subagents under the parent session that spawned them; crons stay sessions of the one Bot project."""

    def setUp(self) -> None:
        openclaw.clear_cache()
        self.db = Db()
        self.addCleanup(self.db.close)
        self.addCleanup(openclaw.clear_cache)

    def stages(self) -> dict[str, collect.Stage]:
        self.db.conn.commit()
        return {stage.session_id: stage for stage in collect._co_openclaw_stages(NOW, False, self.db.home)}

    def test_a_subagent_hangs_under_its_parent_from_the_column_when_the_entry_has_no_parent(self) -> None:
        self.db.node(KEY, session_id="s1")
        self.db.node("agent:openclaw:subagent:k1", parent=KEY, session_id="s2",
                     entry={"model": "gpt-6-luna", "status": "done", "startedAt": (NOW - 50) * 1000,
                            "endedAt": (NOW - 20) * 1000})
        stages = self.stages()
        parent = stages[KEY]
        (worker,) = parent.snap.workers
        self.assertEqual((worker.native_id, worker.session, worker.linkage), ("agent:openclaw:subagent:k1", KEY, "native"))
        self.assertNotIn("agent:openclaw:subagent:k1", stages)
        collect._co_level_tags([parent])
        self.assertEqual(worker.level_tag, "SUB")

    def test_a_nested_subagent_folds_into_the_top_session_row(self) -> None:
        self.db.node(KEY, session_id="s1")
        self.db.node("agent:openclaw:subagent:k1", parent=KEY, session_id="s2", entry={"status": "running"})
        self.db.node("agent:openclaw:subagent:k2", parent="agent:openclaw:subagent:k1", session_id="s3",
                     entry={"status": "running"})
        parent = self.stages()[KEY]
        self.assertEqual(sorted(w.native_id for w in parent.snap.workers),
                         ["agent:openclaw:subagent:k1", "agent:openclaw:subagent:k2"])

    def test_crons_are_rows_of_the_openclaw_project_with_the_cron_cluster(self) -> None:
        self.db.node(KEY, session_id="s1")
        self.db.node("agent:openclaw:cron:job-1", session_id="c1", entry={"model": "ornith-1.5-9b"})
        rows = self.stages()
        root = os.path.join(self.db.home, ".openclaw", "agents", "openclaw")
        self.assertEqual({stage.label for stage in rows.values()}, {"Bot"})
        self.assertEqual({stage.dir for stage in rows.values()}, {root})
        self.assertEqual((rows["agent:openclaw:cron:job-1"].cluster, rows[KEY].cluster), ("cron", ""))

    def test_the_fifteen_minute_idle_rule_matches_other_sessions(self) -> None:
        self.db.node(KEY, activity=NOW - 60, session_id="s1")
        self.db.node("agent:openclaw:dashboard:a", activity=NOW - 5 * 60, session_id="d1")
        self.db.node("agent:openclaw:dashboard:b", activity=NOW - 20 * 60, session_id="d2")
        self.db.node("agent:openclaw:dashboard:c", activity=NOW - 3 * 3600, session_id="d3")
        rows = self.stages()
        self.assertEqual([rows[key].state for key in (KEY, "agent:openclaw:dashboard:a", "agent:openclaw:dashboard:b",
                                                      "agent:openclaw:dashboard:c")],
                         ["active", "active", "idle", "closed"])

    def test_a_telegram_chat_with_a_subagent_keeps_the_subagent_under_it(self) -> None:
        own = f"agent:openclaw:telegram:direct:{PEER}"
        self.db.node(own, session_id="t1", entry={"model": "ornith-1.5-9b"})
        self.db.node("agent:openclaw:subagent:t1", parent=own, session_id="t2", entry={"status": "done"})
        stage = self.stages()[own]
        self.assertEqual([w.native_id for w in stage.snap.workers], ["agent:openclaw:subagent:t1"])
        self.assertEqual(stage.display_name, "telegram · direct")


if __name__ == "__main__":
    unittest.main()
