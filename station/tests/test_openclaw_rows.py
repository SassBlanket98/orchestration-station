"""OpenClaw sessions as overview rows: Bot's Telegram, dashboard and cron sessions (subagents are workers of their parent), sorted by age into the sections."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
import unittest.mock

import collect

NOW = 1_760_050_000.0


def make_home(agent: str, sessions: list[tuple[str, float, str]]) -> str:
    """A fake home with one OpenClaw agent database: (session_key, age in seconds, created_via)."""
    home = tempfile.mkdtemp()
    agent_dir = os.path.join(home, ".openclaw", "agents", agent, "agent")
    os.makedirs(agent_dir)
    conn = sqlite3.connect(os.path.join(agent_dir, "openclaw-agent.sqlite"))
    conn.execute("CREATE TABLE session_nodes (session_key TEXT, display_name TEXT, label TEXT, last_activity_at INTEGER, "
                 "created_at INTEGER, updated_at INTEGER, entry_json TEXT, created_via TEXT)")
    for key, age, via in sessions:
        ms = int((NOW - age) * 1000)
        conn.execute("INSERT INTO session_nodes VALUES (?,?,?,?,?,?,?,?)",
                     (key, "", "chat " + key[-4:], ms, ms - 60_000, ms, json.dumps({"model": "m1", "totalTokens": 1234}), via))
    conn.commit()
    conn.close()
    return home


class OpenClawRows(unittest.TestCase):
    def rows(self, home: str, all_history: bool = False) -> dict[str, collect.Stage]:
        return {s.session_id: s for s in collect._co_openclaw_stages(NOW, all_history, home)}

    def test_openclaw_stage_label_uses_configured_agent_name(self) -> None:
        home = make_home("zephyr", [("agent:zephyr:main", 60, "")])
        with unittest.mock.patch.object(collect, "OPENCLAW_AGENT", "zephyr"):
            stages = collect._co_openclaw_stages(NOW, False, home)
        self.assertTrue(stages)
        self.assertEqual(stages[0].label, "Zephyr")
        self.assertEqual(stages[0].tag, "Zephyr")

    def test_telegram_dashboard_and_cron_sessions_only_and_a_subagent_is_never_a_row(self) -> None:
        home = make_home("openclaw", [("agent:openclaw:main", 60, ""), ("agent:openclaw:dashboard:aaaa", 90, "operator"),
                                  ("agent:openclaw:suite1", 60, "run"), ("agent:openclaw:cron:x", 60, "cron"),
                                  ("agent:openclaw:subagent:y", 60, "spawn"),
                                  ("agent:openclaw:telegram:direct:1", 60, "channel"),
                                  ("agent:openclaw:telegram:openclaw:direct:2", 60, "channel")])
        rows = self.rows(home)
        self.assertEqual(sorted(rows), ["agent:openclaw:cron:x", "agent:openclaw:dashboard:aaaa", "agent:openclaw:main",
                                        "agent:openclaw:orphans:none"])   # a subagent with no parent still shows, as a worker of the orphans row
        self.assertEqual([w.title for w in rows["agent:openclaw:orphans:none"].snap.workers], ["chat nt:y"])

    def test_own_direct_chat_shows_and_a_peers_only_as_a_parent(self) -> None:
        own = f"agent:openclaw:telegram:direct:{collect.OPENCLAW_OWN_PEERS[0]}"
        home = make_home("openclaw", [(own, 60, "channel"), ("agent:openclaw:telegram:direct:555", 60, "channel"),
                                  ("agent:openclaw:telegram:direct:666", 60, "channel"), ("agent:openclaw:subagent:s", 60, "spawn")])
        self.set_entry(home, "agent:openclaw:subagent:s", {"parentSessionKey": "agent:openclaw:telegram:direct:666"})
        rows = self.rows(home)
        self.assertEqual(sorted(rows), sorted([own, "agent:openclaw:telegram:direct:666"]))
        self.assertEqual([w.title for w in rows["agent:openclaw:telegram:direct:666"].snap.workers], ["chat nt:s"])
        self.assertTrue(rows[own].solo and not rows["agent:openclaw:telegram:direct:666"].solo)

    def test_empty_last_activity_falls_back_to_updated_at_not_created_at(self) -> None:
        own = f"agent:openclaw:telegram:direct:{collect.OPENCLAW_OWN_PEERS[0]}"
        home = make_home("openclaw", [(own, 60, "channel")])
        conn = sqlite3.connect(os.path.join(home, ".openclaw", "agents", "openclaw", "agent", "openclaw-agent.sqlite"))
        conn.execute("UPDATE session_nodes SET last_activity_at = NULL, created_at = ? WHERE session_key = ?",
                     (int((NOW - 9 * 86400) * 1000), own))   # created long ago, updated a minute ago
        conn.commit()
        conn.close()
        self.assertIn(own, self.rows(home))

    def set_entry(self, home: str, key: str, entry: dict) -> None:
        conn = sqlite3.connect(os.path.join(home, ".openclaw", "agents", "openclaw", "agent", "openclaw-agent.sqlite"))
        conn.execute("UPDATE session_nodes SET entry_json = ? WHERE session_key = ?", (json.dumps(entry), key))
        conn.commit()
        conn.close()

    def test_subagent_is_a_worker_named_by_its_label_and_cron_is_named_without_its_prefix(self) -> None:
        home = make_home("openclaw", [("agent:openclaw:cron:x", 60, "cron"), ("agent:openclaw:subagent:y", 30, "spawn"),
                                  ("agent:openclaw:main", 60, "")])
        self.set_entry(home, "agent:openclaw:subagent:y", {"parentSessionKey": "agent:openclaw:main", "model": "gpt-6-luna",
                                                        "status": "done", "startedAt": (NOW - 50) * 1000, "endedAt": (NOW - 30) * 1000})
        for key, label in (("agent:openclaw:cron:x", "Automation: morning-brief"), ("agent:openclaw:subagent:y", "usage worker")):
            conn = sqlite3.connect(os.path.join(home, ".openclaw", "agents", "openclaw", "agent", "openclaw-agent.sqlite"))
            conn.execute("UPDATE session_nodes SET label = ? WHERE session_key = ?", (label, key))
            conn.commit()
            conn.close()
        got = self.rows(home)
        self.assertEqual(got["agent:openclaw:cron:x"].session_name, "morning-brief")
        main = got["agent:openclaw:main"]
        self.assertEqual([(w.name, w.state, w.model, w.end) for w in main.snap.workers],
                         [("usage worker", "done", "gpt-6-luna", NOW - 30)])
        self.assertEqual((main.live, main.solo), (0, False))

    def test_running_failed_and_stale_subagents_and_a_nested_one(self) -> None:
        main = "agent:openclaw:main"
        home = make_home("openclaw", [(main, 600, ""), ("agent:openclaw:subagent:run", 20, ""), ("agent:openclaw:subagent:bad", 200, ""),
                                  ("agent:openclaw:subagent:old", 3000, ""), ("agent:openclaw:subagent:kid", 20, "")])
        self.set_entry(home, "agent:openclaw:subagent:run", {"parentSessionKey": main, "status": "running"})
        self.set_entry(home, "agent:openclaw:subagent:bad", {"parentSessionKey": main, "status": "failed"})
        self.set_entry(home, "agent:openclaw:subagent:old", {"parentSessionKey": main})
        self.set_entry(home, "agent:openclaw:subagent:kid", {"parentSessionKey": "agent:openclaw:subagent:run", "status": "running"})
        row = self.rows(home)[main]
        states = sorted(w.state for w in row.snap.workers)
        self.assertEqual(states, ["failed", "noreport", "running", "running"])
        self.assertEqual((row.live, row.state), (2, "active"))

    def test_orphans_corrupt_stamps_and_equal_labels_degrade_without_raising(self) -> None:
        home = make_home("openclaw", [("agent:openclaw:main", 60, ""), ("agent:openclaw:subagent:o", 20, ""),
                                  ("agent:openclaw:subagent:p", 20, ""), ("agent:openclaw:subagent:q", 20, "")])
        self.set_entry(home, "agent:openclaw:subagent:o", {"parentSessionKey": "agent:openclaw:dashboard:ghost", "status": "running"})
        self.set_entry(home, "agent:openclaw:subagent:p", {"parentSessionKey": "agent:openclaw:main", "status": "done"})
        self.set_entry(home, "agent:openclaw:subagent:q", {"parentSessionKey": "agent:openclaw:main", "status": "done"})
        conn = sqlite3.connect(os.path.join(home, ".openclaw", "agents", "openclaw", "agent", "openclaw-agent.sqlite"))
        conn.execute("UPDATE session_nodes SET label = 'same' WHERE session_key IN (?, ?)",
                     ("agent:openclaw:subagent:p", "agent:openclaw:subagent:q"))
        conn.execute("INSERT INTO session_nodes VALUES ('agent:openclaw:dashboard:bad', '', 'bad', 'oops', 'oops', 'oops', '{}', '')")
        conn.commit()
        conn.close()
        rows = self.rows(home)
        self.assertIn("agent:openclaw:orphans:ghost", rows)
        self.assertEqual([w.state for w in rows["agent:openclaw:orphans:ghost"].snap.workers], ["running"])
        names = [w.name for w in rows["agent:openclaw:main"].snap.workers]
        self.assertEqual(len(set(names)), 2)
        self.assertNotIn("agent:openclaw:dashboard:bad", rows)   # a non-numeric stamp drops that row only

    def test_a_run_session_that_spawned_subagents_shows_as_their_row(self) -> None:
        home = make_home("openclaw", [("agent:openclaw:subagent-proof", 60, "run"), ("agent:openclaw:subagent:a", 30, "spawn"),
                                  ("agent:openclaw:suite1", 60, "run")])
        self.set_entry(home, "agent:openclaw:subagent:a", {"parentSessionKey": "agent:openclaw:subagent-proof", "status": "done"})
        rows = self.rows(home)
        self.assertEqual(sorted(rows), ["agent:openclaw:subagent-proof"])
        self.assertEqual(len(rows["agent:openclaw:subagent-proof"].snap.workers), 1)

    def test_other_agents_never_show(self) -> None:
        self.assertEqual(self.rows(make_home("mani", [("agent:mani:main", 60, "")])), {})
        self.assertEqual(self.rows(make_home("scully", [("agent:scully:dashboard:z", 60, "operator")])), {})

    def test_sections_follow_age(self) -> None:
        home = make_home("openclaw", [("agent:openclaw:main", 120, ""), ("agent:openclaw:dashboard:b", 2 * 3600, "operator"),
                                  ("agent:openclaw:dashboard:c", 7 * 3600, "operator")])
        got = self.rows(home)
        self.assertEqual([got[k].state for k in ("agent:openclaw:main", "agent:openclaw:dashboard:b", "agent:openclaw:dashboard:c")],
                         ["active", "idle", "closed"])

    def test_older_than_a_day_is_hidden_unless_all_history(self) -> None:
        home = make_home("openclaw", [("agent:openclaw:main", 60, ""), ("agent:openclaw:dashboard:d", 30 * 3600, "operator")])
        self.assertEqual(sorted(self.rows(home)), ["agent:openclaw:main"])
        self.assertEqual(sorted(self.rows(home, True)), ["agent:openclaw:dashboard:d", "agent:openclaw:main"])

    def test_row_shape(self) -> None:
        home = make_home("openclaw", [("agent:openclaw:main", 60, "")])
        st = self.rows(home)["agent:openclaw:main"]
        self.assertEqual((st.tag, st.session_name, st.lead_model, st.lead_ctx, st.label), ("Bot", "telegram", "m1", 1234, "Bot"))

    def test_missing_or_broken_database_gives_no_rows(self) -> None:
        self.assertEqual(self.rows(tempfile.mkdtemp()), {})
        home = tempfile.mkdtemp()
        d = os.path.join(home, ".openclaw", "agents", "openclaw", "agent")
        os.makedirs(d)
        with open(os.path.join(d, "openclaw-agent.sqlite"), "w") as f:
            f.write("not a database")
        self.assertEqual(self.rows(home), {})


class OpenClawInTheCollector(unittest.TestCase):
    def test_collector_adds_the_rows_only_when_given_a_home(self) -> None:
        home = make_home("openclaw", [("agent:openclaw:main", 60, "")])
        with tempfile.TemporaryDirectory() as empty:
            ledger = os.path.join(empty, "none.jsonl")
            off = collect.GlobalCollector([empty], ledger=ledger, quota=False).snapshot(NOW)
            on = collect.GlobalCollector([empty], ledger=ledger, quota=False, openclaw_home=home).snapshot(NOW)
        self.assertEqual([s.tag for s in off.stages if s.tag == "OClaw"], [])
        self.assertEqual([(s.tag, s.state) for s in on.stages if s.tag == "Bot"], [("Bot", "active")])


class OpenClawTree(unittest.TestCase):
    def layout(self, spec: list[tuple[str, float]], parents: dict[str, str]) -> list[tuple[str, str, str]]:
        import render
        from model import GlobalSnapshot, UIState
        home = make_home("openclaw", [(k, a, "") for k, a in spec])
        helper = OpenClawRows()
        for key, parent in parents.items():
            helper.set_entry(home, key, {"parentSessionKey": parent})
        stages = collect._co_openclaw_stages(NOW, False, home)
        return render.global_rows(GlobalSnapshot(now=NOW, stages=stages), UIState())

    def test_subagents_are_workers_of_their_parent_row_and_crons_get_a_group(self) -> None:
        own = f"agent:openclaw:telegram:direct:{collect.OPENCLAW_OWN_PEERS[0]}"
        rows = self.layout([(own, 60), ("agent:openclaw:subagent:a", 90), ("agent:openclaw:subagent:b", 30),
                            ("agent:openclaw:main", 20), ("agent:openclaw:cron:c", 10)],
                           {"agent:openclaw:subagent:a": own, "agent:openclaw:subagent:b": own})
        ids = [p.rsplit("#", 1)[-1] for p, kind, _ in rows if kind == "stage"]
        self.assertFalse([i for i in ids if i.startswith("subagent")])
        groups = [n for _, kind, n in rows if kind == "group"]
        self.assertEqual(len(groups), 1)   # one Bot project: chats and crons are its sessions
        self.assertFalse(any("·cron" in g for g in groups))


class LaunchedFromClaude(unittest.TestCase):
    """An Bot session started by `openclaw agent` from a Claude session is workers of that Claude row."""

    ARGV = {5: ["/usr/bin/node", "/x/openclaw/dist/index.js", "agent", "--agent", "openclaw", "--session-key", "agent:openclaw:proof", "--json"],
            6: ["/usr/bin/node", "/x/openclaw/dist/index.js", "agent", "--session-key=short"],
            7: ["claude", "--model", "sonnet"], 8: ["/usr/bin/node", "/x/openclaw/dist/index.js", "gateway"]}

    def launches(self) -> dict:
        envs = {5: {"CLAUDE_CODE_SESSION_ID": "cc-1"}, 6: {"CLAUDE_CODE_SESSION_ID": "cc-2"}}
        with unittest.mock.patch.object(collect, "_co_argv", lambda pid: self.ARGV.get(pid, [])), \
                unittest.mock.patch.object(collect, "_co_environ", lambda pid: envs.get(pid, {})):
            return collect._co_openclaw_launches({pid: (1, 0, 0, 0, 0) for pid in self.ARGV})

    def test_the_launching_claude_session_is_read_from_the_runs_environment(self) -> None:
        self.assertEqual(self.launches(), {"agent:openclaw:proof": ("cc-1", True), "agent:openclaw:short": ("cc-2", True)})

    def test_the_run_and_its_subagents_become_workers_of_the_claude_row_and_stay_after_the_run_exits(self) -> None:
        home = make_home("openclaw", [("agent:openclaw:proof", 60, "run"), ("agent:openclaw:subagent:a", 30, "spawn")])
        OpenClawRows().set_entry(home, "agent:openclaw:subagent:a", {"parentSessionKey": "agent:openclaw:proof", "status": "done"})
        host = collect.Stage(path="/p/.stage", label="p", session_id="cc-1", lead_kind="claude", tag="CC·W", state="active")
        with tempfile.TemporaryDirectory() as empty:
            gc = collect.GlobalCollector([empty], quota=False, openclaw_home=home)
            self.addCleanup(gc.close)
            with unittest.mock.patch.object(collect, "_co_openclaw_launches", lambda procs: {"agent:openclaw:proof": ("cc-1", True)}), \
                    unittest.mock.patch.object(gc, "_co_proc_table", lambda: {}):
                oc = collect._co_openclaw_stages(NOW, False, home)
                self.assertEqual(gc._co_attach_openclaw([host], oc, NOW), [])
                self.assertEqual(sorted(w.state for w in host.snap.workers), ["done", "running"])
                self.assertEqual(len(host.snap.workers), 2)
                self.assertEqual((host.live, host.solo), (1, False))
            host2 = collect.Stage(path="/p/.stage", label="p", session_id="cc-1", lead_kind="claude", tag="CC·W", state="active")
            with unittest.mock.patch.object(collect, "_co_openclaw_launches", lambda procs: {}), \
                    unittest.mock.patch.object(gc, "_co_proc_table", lambda: {}):
                self.assertEqual(gc._co_attach_openclaw([host2], collect._co_openclaw_stages(NOW + 5, False, home), NOW + 5), [])
            self.assertEqual(sorted(w.state for w in host2.snap.workers), ["done", "done"])
            other = collect.Stage(path="/q/.stage", label="q", session_id="cc-9", lead_kind="claude", tag="CC·W")
            kept = gc._co_attach_openclaw([other], collect._co_openclaw_stages(NOW, False, home), NOW)
            self.assertEqual([s.session_id for s in kept], ["agent:openclaw:proof"])   # nobody launched it that is known here: its own row stays


class OpenCodeSessionTable(unittest.TestCase):
    """OpenCode renamed its `session` table to `session_v2` (model now a JSON column); both still resolve."""

    def lookup(self, table: str, create: str, insert: str) -> dict:
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".local/share/opencode"))
        conn = sqlite3.connect(os.path.join(home, ".local/share/opencode/opencode.db"))
        conn.execute(create)
        conn.execute(insert)
        if table == "session":
            conn.execute("CREATE TABLE message (session_id TEXT, data TEXT, time_created INTEGER)")
            conn.execute("INSERT INTO message VALUES ('s1', '{\"modelID\": \"old-model\"}', 1)")
        conn.commit()
        conn.close()
        lead = collect._co_Lead(1, "opencode", "live", "/p/x", NOW)
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            return collect._co_lead_session("/p/x/.stage", lead)

    def test_session_v2_gives_name_id_and_model(self) -> None:
        got = self.lookup("session_v2", "CREATE TABLE session_v2 (id TEXT, title TEXT, directory TEXT, time_updated INTEGER, model TEXT)",
                          "INSERT INTO session_v2 VALUES ('s1', 'fix the thing now please', '/p/x', 1760049000000, '{\"id\": \"muse-1\"}')")
        self.assertEqual((got["tag"], got["session_id"], got["lead_model"], got["session_name"]), ("OCode", "s1", "muse-1", "fix the thing now please"))
        self.assertEqual(got["activity"], 1760049000.0)

    def test_the_old_session_table_still_works(self) -> None:
        got = self.lookup("session", "CREATE TABLE session (id TEXT, title TEXT, directory TEXT, time_updated INTEGER)",
                          "INSERT INTO session VALUES ('s1', 'old one', '/p/x', 1760049000000)")
        self.assertEqual((got["session_id"], got["lead_model"]), ("s1", "old-model"))


class OpenCodeDoing(unittest.TestCase):
    def test_latest_prompts_tools_and_replies_oldest_first(self) -> None:
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".local/share/opencode"))
        conn = sqlite3.connect(os.path.join(home, ".local/share/opencode/opencode.db"))
        conn.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INTEGER, time_created INTEGER, data TEXT)")
        msgs = [("user", 1, {"text": "fix it\nplease"}),
                ("assistant", 2, {"content": [{"type": "reasoning", "text": "hmm"},
                                              {"type": "tool", "name": "edit", "state": {"input": {"filePath": "/p/render.py"}}},
                                              {"type": "text", "text": "Done."}]}),
                ("idle", 3, {})]
        for kind, seq, data in msgs:
            conn.execute("INSERT INTO session_message VALUES ('s1', ?, ?, ?, ?)", (kind, seq, 1760049000000 + seq * 1000, json.dumps(data)))
        conn.commit()
        conn.close()
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            doing = collect._co_lead_doing(1, "opencode", "s1")
            self.assertEqual(collect._co_lead_doing(1, "opencode", ""), [])
        self.assertEqual([t for _, t in doing], ["you: fix it", "edit render.py", "says: Done."])


if __name__ == "__main__":
    unittest.main()
