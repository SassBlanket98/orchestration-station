from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import app
import collect
from collect import GlobalCollector
from model import Stage

NOW = 1_800_000_000.0
HOME = "/home/user"
BOOT = 1_799_000_000.0
CLK = 100
TTY = 34816      # a pts tty_nr; 0 means no controlling terminal


class Overrides(unittest.TestCase):
    """Manual idle/active pins: GlobalCollector over a temp root with fake /proc tables, and the overrides file."""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="idle-override-")
        self.root = os.path.join(self.dir, "Projects")
        self.dismissed = os.path.join(self.dir, "agents", "status-dismissed.json")
        self.overrides = os.path.join(self.dir, "agents", "status-overrides.json")
        self.procs: dict[int, tuple[int, int, int, int, int]] = {}
        self.argv: dict[int, list[str]] = {}
        self.cwd: dict[int, str] = {}
        self.stage = self.make_stage("proj")
        self.touch(self.stage, NOW - 60)
        self.patches = [
            mock.patch.object(collect, "_co_scan_procs", side_effect=lambda: dict(self.procs)),
            mock.patch.object(collect, "_co_argv", side_effect=lambda pid: list(self.argv.get(pid, []))),
            mock.patch.object(collect, "_co_cwd", side_effect=lambda pid: self.cwd.get(pid, "")),
            mock.patch.object(collect, "_co_environ", side_effect=lambda pid: {}),
            mock.patch.object(collect, "_co_pid_live", return_value=False),
            mock.patch.object(collect, "_co_boot_time", return_value=BOOT),
            mock.patch.object(collect, "_co_clk_tck", return_value=CLK),
        ]
        for patch in self.patches:
            patch.start()
        self.gc = self.collector()

    def tearDown(self) -> None:
        self.gc.close()
        for patch in self.patches:
            patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def collector(self) -> GlobalCollector:
        return GlobalCollector([self.root], ledger=os.path.join(self.dir, "none.jsonl"), quota=False,
                               dismissed=self.dismissed, overrides=self.overrides)

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

    def project(self) -> str:
        return os.path.join(self.root, "proj")

    def add_lead(self, pid: int, argv: list[str], cwd: str, start: float = NOW - 3600, tty: int = TTY) -> None:
        self.procs[pid] = (1, 0, 0, int((start - BOOT) * CLK), tty)
        self.argv[pid] = argv
        self.cwd[pid] = cwd

    def set_activity(self, age: float) -> None:
        # a worker's pid and log files are what make an open session active or idle (see test_leads)
        for name, text in (("w.pid", "999999\n"), ("w.log", "finished\n")):
            path = os.path.join(self.stage, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.utime(path, (NOW - age, NOW - age))

    def make_active(self) -> None:
        self.add_lead(10, ["claude"], self.project())
        self.set_activity(60)

    def make_idle(self) -> None:
        self.add_lead(10, ["claude"], self.project())
        self.set_activity(1200)

    def snap(self, all_history: bool = True):
        return self.gc.snapshot(NOW, all_history=all_history)

    def station(self, all_history: bool = True) -> Stage:
        return next(s for s in self.snap(all_history).stages if s.path == self.stage)

    def test_toggling_an_active_station_makes_it_idle_and_pinned(self) -> None:
        self.make_active()
        self.assertEqual(self.station().state, "active")
        self.assertEqual(self.gc.toggle_idle(self.stage, NOW), "idle")
        stage = self.station()
        self.assertEqual((stage.state, stage.pinned), ("idle", "idle"))
        self.assertEqual(collect.load_overrides(self.overrides)[self.stage]["state"], "idle")

    def test_toggling_again_clears_the_pin(self) -> None:
        self.make_active()
        self.gc.toggle_idle(self.stage, NOW)
        self.assertEqual(self.gc.toggle_idle(self.stage, NOW + 1), "")
        stage = self.station()
        self.assertEqual((stage.state, stage.pinned), ("active", ""))
        self.assertEqual(collect.load_overrides(self.overrides), {})

    def test_an_idle_station_toggles_to_active(self) -> None:
        self.make_idle()
        self.assertEqual(self.station().state, "idle")
        self.assertEqual(self.gc.toggle_idle(self.stage, NOW), "active")
        stage = self.station()
        self.assertEqual((stage.state, stage.pinned), ("active", "active"))

    def test_a_pinned_idle_station_is_hidden_like_any_idle_one(self) -> None:
        self.touch(self.stage, NOW - 60)     # no lead, recent files: shown as closed
        self.assertEqual(self.snap(all_history=False).hidden_idle, 0)
        collect.set_override(self.overrides, self.stage, "idle", NOW)   # the pin toggle_idle writes for an idle station
        gs = self.snap(all_history=False)
        self.assertFalse(any(s.path == self.stage for s in gs.stages))
        self.assertEqual(gs.hidden_idle, 1)

    def test_a_pinned_active_station_is_not_hidden_or_idle(self) -> None:
        self.add_lead(10, ["claude"], self.project())
        self.set_activity(1200)   # an idle lead, pinned active
        self.assertEqual(self.station().state, "idle")
        self.assertEqual(self.gc.toggle_idle(self.stage, NOW), "active")
        gs = self.snap(all_history=False)
        self.assertEqual((self.station(False).state, gs.hidden_idle), ("active", 0))

    def test_a_web_pin_moves_an_active_station_to_idle_and_clear_undoes_it(self) -> None:
        self.make_active()
        self.assertEqual(self.station().state, "active")
        self.assertEqual(app._ap_control(self.gc, self.stage, "idle", NOW), {"pinned": "idle", "dismissed": False})
        stage = self.station()
        self.assertEqual((stage.state, stage.pinned), ("idle", "idle"))
        self.assertEqual(app._ap_control(self.gc, self.stage, "clear", NOW), {"pinned": "", "dismissed": False})
        self.assertEqual(self.station().state, "active")

    def test_a_web_dismiss_reports_dismissed(self) -> None:
        self.touch(self.stage, NOW - 60)
        self.station()   # the collector must know the station before it can be dismissed
        self.assertEqual(app._ap_control(self.gc, self.stage, "dismiss"), {"pinned": "", "dismissed": True})

    def test_a_missing_overrides_file_changes_nothing(self) -> None:
        self.make_active()
        before = (self.station().state, self.station().pinned)
        self.assertFalse(os.path.exists(self.overrides))
        self.assertEqual(collect.load_overrides(self.overrides), {})
        self.assertEqual((self.station().state, self.station().pinned), before)
        self.assertEqual(before, ("active", ""))

    def test_a_corrupt_overrides_file_changes_nothing(self) -> None:
        self.make_active()
        os.makedirs(os.path.dirname(self.overrides), exist_ok=True)
        with open(self.overrides, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(collect.load_overrides(self.overrides), {})
        stage = self.station()
        self.assertEqual((stage.state, stage.pinned), ("active", ""))
        with open(self.overrides, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{not json")     # reading never rewrites the file

    def test_invalid_entries_in_the_file_are_skipped(self) -> None:
        os.makedirs(os.path.dirname(self.overrides), exist_ok=True)
        data = {"/r/a/.stage": {"state": "idle", "set": 5},
                "/r/b/.stage": {"state": "closed", "set": 5},
                "/r/c/.stage": {"state": "active", "set": True},
                "/r/d/.stage": "idle",
                "/r/e/.stage": {"state": "active", "set": 7}}
        with open(self.overrides, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.assertEqual(collect.load_overrides(self.overrides),
                         {"/r/a/.stage": {"state": "idle", "set": 5.0},
                          "/r/e/.stage": {"state": "active", "set": 7.0}})

    def test_the_written_file_survives_a_new_collector(self) -> None:
        self.make_active()
        self.assertEqual(self.station().state, "active")
        self.gc.toggle_idle(self.stage, NOW)
        self.gc.close()
        self.gc = self.collector()
        stage = self.station()
        self.assertEqual((stage.state, stage.pinned), ("idle", "idle"))
        self.assertEqual(os.listdir(os.path.dirname(self.overrides)), ["status-overrides.json"])   # no temp file left


if __name__ == "__main__":
    unittest.main()


class PinExpiry(Overrides):
    def test_activity_after_the_pin_clears_it(self) -> None:
        self.make_active()
        self.station()
        self.gc.toggle_idle(self.stage, NOW)
        self.assertEqual(self.station().pinned, "idle")
        later = NOW + 600
        collect.set_override(self.overrides, self.stage, "idle", NOW - 3600)   # a pin older than the activity
        stage = next(s for s in self.gc.snapshot(later, all_history=True).stages if s.path == self.stage)
        self.assertEqual(stage.pinned, "")
