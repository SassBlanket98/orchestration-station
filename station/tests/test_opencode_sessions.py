"""OpenCode sessions: one row per hosted session, subagent sessions as workers, a grace period for headless runs."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
import unittest.mock

import collect
from model import DONE, FAILED, NOREPORT, RUNNING

NOW = time.time()
MS = 1000


def make_home(sessions: list[tuple]) -> str:
    """A fake $HOME whose opencode.db holds ``sessions``: (id, parent, directory, updated_ago_s, idle_ago_s|None, outcome, title)."""
    home = tempfile.mkdtemp()
    os.makedirs(os.path.join(home, ".local/share/opencode"))
    conn = sqlite3.connect(os.path.join(home, ".local/share/opencode/opencode.db"))
    conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, agent TEXT, model TEXT,"
                 " time_created INTEGER, time_updated INTEGER, time_idle INTEGER, idle_outcome TEXT, time_archived INTEGER)")
    for sid, parent, directory, updated_ago, idle_ago, outcome, title in sessions:
        updated = int((NOW - updated_ago) * MS)
        idle = None if idle_ago is None else int((NOW - idle_ago) * MS)
        conn.execute("INSERT INTO session_v2 VALUES (?, ?, ?, ?, 'general', ?, ?, ?, ?, ?, NULL)",
                     (sid, parent, directory, title, json.dumps({"id": "deepseek-v4"}), updated - 60 * MS, updated, idle, outcome))
    conn.commit()
    conn.close()
    return home


DAEMON = {7: (1, 0, 0, 100, 0)}


def daemon_argv(pid: int) -> list[str]:
    return ["/home/u/.opencode/bin/opencode", "serve", "--service"] if pid == 7 else []


class HostedSessions(unittest.TestCase):
    def leads(self, sessions: list[tuple], terminals: list[str] | None = None) -> list[collect._co_Lead]:
        home = make_home(sessions)
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            return collect._co_service_leads(DAEMON, daemon_argv, terminals or [], 100, NOW - 10_000)

    def test_every_session_in_one_folder_gets_its_own_lead(self) -> None:
        leads = self.leads([("s1", None, "/p/x", 60, 30, "succeeded", "a"), ("s2", None, "/p/x", 90, 40, "interrupted", "b"),
                            ("s3", None, "/p/x", 120, 50, "interrupted", "c")])
        self.assertEqual([lead.sid for lead in leads], ["s1", "s2", "s3"])
        self.assertTrue(all(lead.pid == 0 and lead.state == "background" and lead.cwd == "/p/x" for lead in leads))

    def test_old_archived_stage_and_subagent_sessions_are_left_out(self) -> None:
        leads = self.leads([("old", None, "/p/x", 3 * 3600, 3 * 3600, "succeeded", ""),
                            ("w", None, "/p/x/.stage/wt", 60, 30, "succeeded", ""),
                            ("kid", "s1", "/p/x", 60, 30, "succeeded", "child"),
                            ("s1", None, "/p/y", 60, 30, "succeeded", "")])
        self.assertEqual([lead.sid for lead in leads], ["s1"])

    def test_an_opencode_run_logged_in_the_stage_is_a_worker_not_a_lead(self) -> None:
        folder = tempfile.mkdtemp()
        os.makedirs(os.path.join(folder, ".stage"))
        with open(os.path.join(folder, ".stage", "deepseek-a.log"), "w", encoding="utf-8") as fh:
            fh.write('{"type":"step_start","timestamp":1,"sessionID":"ses_run1","part":{}}\n')
        leads = self.leads([("orch", None, folder, 60, 30, "succeeded", "lead"),
                            ("ses_run1", None, folder, 20, 10, "succeeded", "worker run"),
                            ("run2", None, "/p/y", 20, 10, "succeeded", "other folder")])
        self.assertEqual([lead.sid for lead in leads], ["run2", "orch"])

    def test_a_session_with_a_running_subagent_outlives_the_window(self) -> None:
        leads = self.leads([("s1", None, "/p/x", 3 * 3600, 3 * 3600, "succeeded", ""),
                            ("kid", "s1", "/p/x", 20, None, "", "child")])
        self.assertEqual([lead.sid for lead in leads], ["s1"])

    def test_a_session_that_idled_recently_stays_even_if_its_update_stamp_is_old(self) -> None:
        # time_updated stays at the first prompt while the session works; the idle stamp is the fresher clock
        leads = self.leads([("worked", None, "/p/x", 3 * 3600, 10 * 60, "succeeded", ""),
                            ("quiet", None, "/p/y", 3 * 3600, 3 * 3600, "succeeded", "")])
        self.assertEqual([lead.sid for lead in leads], ["worked"])

    def test_a_folder_with_a_terminal_opencode_keeps_that_terminals_session_off_the_list(self) -> None:
        leads = self.leads([("mine", None, "/p/x", 30, 5, "", ""), ("fresh", None, "/p/x", 120, 60, "", ""),
                            ("stale", None, "/p/x", 20 * 60, 20 * 60, "", "")], terminals=["/p/x"])
        self.assertEqual([lead.sid for lead in leads], ["fresh"])


class SessionByLead(unittest.TestCase):
    def test_a_hosted_lead_resolves_its_own_session_not_the_newest_in_the_folder(self) -> None:
        home = make_home([("new", None, "/p/x", 10, 5, "", "newest one"), ("mine", None, "/p/x", 90, 80, "", "my session")])
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            got = collect._co_lead_session("/p/x/.stage", collect._co_Lead(-5, "opencode", "background", "/p/x", NOW, "mine"))
            newest = collect._co_lead_session("/p/x/.stage", collect._co_Lead(9, "opencode", "live", "/p/x", NOW))
        self.assertEqual((got["session_id"], got["lead_model"]), ("mine", "deepseek-v4"))
        self.assertEqual(newest["session_id"], "new")


    def test_a_terminal_lead_never_resolves_to_a_subagent_session(self) -> None:
        home = make_home([("top", None, "/p/x", 600, 590, "", "the session"), ("kid", "top", "/p/x", 5, None, "", "newest child")])
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            got = collect._co_lead_session("/p/x/.stage", collect._co_Lead(9, "opencode", "live", "/p/x", NOW))
        self.assertEqual(got["session_id"], "top")


class OlderSchema(unittest.TestCase):
    def test_a_database_without_time_idle_still_lists_hosted_sessions(self) -> None:
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".local/share/opencode"))
        conn = sqlite3.connect(os.path.join(home, ".local/share/opencode/opencode.db"))
        conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, time_created INTEGER,"
                     " time_updated INTEGER, time_archived INTEGER)")
        conn.execute("INSERT INTO session_v2 VALUES ('s1', NULL, '/p/x', ?, ?, NULL)", (int((NOW - 99) * MS), int((NOW - 60) * MS)))
        conn.commit()
        conn.close()
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            leads = collect._co_service_leads(DAEMON, daemon_argv, [], 100, NOW - 10_000)
        self.assertEqual([lead.sid for lead in leads], ["s1"])


class SubagentSessions(unittest.TestCase):
    def kids(self, rows: list[tuple]) -> list:
        home = make_home([("p", None, "/p/x", 10, 5, "", "parent")] + rows)
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            return collect._co_opencode_children("p", NOW)

    def test_state_follows_idle_and_outcome(self) -> None:
        kids = {w.name: w for w in self.kids([
            ("k1", "p", "/p/x", 30, None, "", "running one"), ("k2", "p", "/p/x", 600, 590, "succeeded", "finished one"),
            ("k3", "p", "/p/x", 600, 590, "failed", "broke one"), ("k4", "p", "/p/x", 600, 590, "interrupted", "stopped one"),
            ("k5", "p", "/p/x", 7200, None, "", "abandoned one")])}
        self.assertEqual({name: w.state for name, w in kids.items()},
                         {"running one": RUNNING, "finished one": DONE, "broke one": FAILED,
                          "stopped one": NOREPORT, "abandoned one": NOREPORT})
        self.assertEqual(kids["running one"].session, "p")
        self.assertIsNone(kids["running one"].end)
        self.assertEqual(kids["running one"].model, "deepseek-v4")

    def test_other_outcome_spellings_and_missing_titles(self) -> None:
        got = {w.name: w.state for w in self.kids([("k1", "p", "/p/x", 600, 590, "error", "oops"),
                                                   ("k2", "p", "/p/x", 600, 590, "succeeded", None)])}
        self.assertEqual(got, {"oops": FAILED, "general": DONE})

    def test_other_sessions_children_are_not_listed(self) -> None:
        self.assertEqual(self.kids([("k", "someone-else", "/p/x", 30, None, "", "not mine")]), [])

    def test_a_database_without_the_columns_gives_nothing(self) -> None:
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".local/share/opencode"))
        conn = sqlite3.connect(os.path.join(home, ".local/share/opencode/opencode.db"))
        conn.execute("CREATE TABLE session_v2 (id TEXT, title TEXT, directory TEXT, time_updated INTEGER, model TEXT)")
        conn.commit()
        conn.close()
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(collect._co_opencode_children("p", NOW), [])


class RootSession(unittest.TestCase):
    def test_a_subagents_run_belongs_to_the_top_level_session(self) -> None:
        home = make_home([("top", None, "/p/x", 10, 5, "", ""), ("kid", "top", "/p/x", 10, 5, "", ""),
                          ("grandkid", "kid", "/p/x", 10, 5, "", "")])
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(collect._co_opencode_roots(["grandkid", "kid", "top", "unknown"]),
                             {"grandkid": "top", "kid": "top", "top": "top", "unknown": "unknown"})


class HeadlessGrace(unittest.TestCase):
    """A headless run is tied to its OpenCode session by the session id in its environment, wherever its cwd is, and
    lingers (as done) for a short while after it exits."""

    def setUp(self) -> None:
        self.col = collect.Collector("", quota=False)
        self.procs = {7: (1, 0, 0, 100, 0), 50: (1, 5, 5, 200, 0)}   # the run's shell is gone: init adopted it
        argv = {7: ["opencode", "serve", "--service"], 50: ["claude", "-p", "count", "--model", "haiku"]}
        patches = [unittest.mock.patch.object(collect, "_co_argv", lambda pid: argv.get(pid, [])),
                   unittest.mock.patch.object(collect, "_co_cwd", lambda pid: "/tmp/mon-test-1" if pid == 50 else "/"),
                   unittest.mock.patch.object(collect, "_co_environ", lambda pid: {"OPENCODE_SESSION_ID": "sid1"} if pid == 50 else {}),
                   unittest.mock.patch.object(collect, "_co_resident", lambda pid, page: 1024)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_pass(self, now: float, procs: dict, slices: dict | None = None) -> list:
        children: dict[int, list[int]] = {}
        for pid, info in procs.items():
            children.setdefault(info[0], []).append(pid)
        found = collect._co_headless_by_session(procs, "/home/u").get("sid1", [])
        return self.col._co_procs_as_workers(now, found, procs, children, set(), set(), "/p/x", "oc:test", "sid1", slices)

    def test_a_run_outside_the_project_is_found_through_its_session_id(self) -> None:
        workers = self.run_pass(NOW, self.procs)
        self.assertEqual([(w.name, w.state, w.session, w.pid) for w in workers], [("mon-test-1", RUNNING, "sid1", 50)])

    def test_a_run_a_stage_names_keeps_the_slice_name(self) -> None:
        workers = self.run_pass(NOW, self.procs, {50: "deepseek-taskcost"})
        self.assertEqual([(w.name, w.pid) for w in workers], [("deepseek-taskcost", 50)])
        stage = tempfile.mkdtemp()
        for name, pid in (("deepseek-taskcost", "50"), ("haiku-a", "51"), ("junk", "x")):
            with open(os.path.join(stage, name + ".pid"), "w", encoding="utf-8") as fh:
                fh.write(pid)
        self.assertEqual(collect._co_stage_pid_names(stage), {50: "deepseek-taskcost", 51: "haiku-a"})

    def test_an_exited_run_stays_as_done_then_goes(self) -> None:
        self.run_pass(NOW, self.procs)
        gone = {7: self.procs[7]}   # pid 50 has exited
        during = self.run_pass(NOW + 60, gone)
        self.assertEqual([(w.name, w.state) for w in during], [("mon-test-1", NOREPORT)])   # disappearance is not success
        self.assertEqual(self.run_pass(NOW + 60 + collect._co_grace_s + 1, gone), [])
        self.assertEqual(self.col._co_ghosts, {})

    def test_a_run_still_alive_is_never_shown_twice(self) -> None:
        self.run_pass(NOW, self.procs)
        again = self.run_pass(NOW + 30, self.procs)
        self.assertEqual([w.state for w in again], [RUNNING])


if __name__ == "__main__":
    unittest.main()
