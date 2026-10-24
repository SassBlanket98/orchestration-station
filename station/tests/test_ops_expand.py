from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app import (
    _ap_expand_key_changed,
    _ap_global_key,
    _ap_load_expand,
    _ap_reconcile_expansion,
    _ap_save_expand,
)
from collect import _co_ops_windows
from model import RUNNING, GlobalSnapshot, Snapshot, Stage, UIState, Worker, WorkState
from render import _rd_g_is_open, _rd_g_rowdata, global_rows, render_global
from widgets import _wd_SGR_RE

NOW = 1_800_000_000.0


def stage(path: str, state: str, activity: float, *, session_id: str = "", workers=None, work=None) -> Stage:
    root = path.split("/.stage/", 1)[0] if "/.stage/" in path else path
    return Stage(path, root.rsplit("/", 1)[-1], Snapshot(workers=workers or []),
                 last_activity=activity, activity=activity, state=state,
                 session_id=session_id, work=work, dir=root if "/.stage/" in path else "")


class OpsExpandTests(unittest.TestCase):
    def test_live_defaults_open_and_idle_closed_default_shut(self):
        live = stage("/work/p/.stage/a", "active", NOW, workers=[Worker("w", RUNNING)])
        idle = stage("/work/q/.stage/a", "idle", NOW - 60)
        ui = UIState()
        self.assertTrue(_rd_g_is_open(ui, _rd_g_rowdata(live)))
        self.assertFalse(_rd_g_is_open(ui, _rd_g_rowdata(idle)))
        ui.gopen_keys.add("s:" + idle.path)
        self.assertTrue(_rd_g_is_open(ui, _rd_g_rowdata(idle)))

    def test_project_keys_use_normalised_project_root_and_default_live(self):
        live = stage("/work/p/.stage/one", "active", NOW, workers=[Worker("w", RUNNING)])
        idle = stage("/work/p/.stage/two", "idle", NOW - 60)
        gs = GlobalSnapshot(now=NOW, stages=[live, idle])
        rows = global_rows(gs, UIState())
        self.assertEqual([r for r in rows if r[1] == "group"], [("", "group", "p:/work/p")])
        ui = UIState(gshut_keys={"p:/work/p"})
        self.assertNotIn((live.path, "stage", ""), global_rows(gs, ui))

    def test_enter_and_e_c_write_project_and_session_choice_keys(self):
        idle = stage("/work/p/.stage/a", "idle", NOW - 60, session_id="abc")
        gs = GlobalSnapshot(now=NOW, stages=[idle])
        ui = UIState(gsel=global_rows(gs, UIState()).index(("", "group", "p:/work/p")))
        _ap_global_key(ui, "enter", gs, 60)
        self.assertIn("p:/work/p", ui.gopen_keys)
        _ap_global_key(ui, "e", gs, 60)
        self.assertIn("s:abc", ui.gopen_keys)
        _ap_global_key(ui, "c", gs, 60)
        self.assertIn("p:/work/p", ui.gshut_keys)
        self.assertIn("s:abc", ui.gshut_keys)

    def test_live_to_idle_drops_open_unless_idle_pin(self):
        before_stage = stage("/work/p/.stage/a", "active", NOW, session_id="abc", workers=[Worker("w", RUNNING)])
        after_stage = stage("/work/p/.stage/a", "idle", NOW, session_id="abc")
        before = GlobalSnapshot(stages=[before_stage])
        after = GlobalSnapshot(stages=[after_stage])
        ui = UIState(gopen_keys={"s:abc", "p:/work/p"})
        self.assertTrue(_ap_reconcile_expansion(ui, before, after))
        self.assertFalse(ui.gopen_keys)
        pinned = UIState(gopen_keys={"s:abc", "p:/work/p"}, gpin_keys={"s:abc", "p:/work/p"})
        self.assertFalse(_ap_reconcile_expansion(pinned, before, after))
        self.assertEqual(pinned.gopen_keys, {"s:abc", "p:/work/p"})

    def test_idle_two_hour_window_and_closed_retention(self):
        idle_recent = stage("/recent", "idle", NOW - 600)
        idle_old = stage("/old", "idle", NOW - 2 * 3600 - 1)
        closed_clean = stage("/clean", "closed", NOW - 24 * 3600 - 1, work=WorkState(dirty=False, unmerged=False, branch="main"))
        closed_dirty = stage("/dirty", "closed", NOW - 30 * 3600, work=WorkState(dirty=True))
        live_old = stage("/live", "active", NOW - 100 * 3600, workers=[Worker("w", RUNNING)])
        stages = [idle_recent, idle_old, closed_clean, closed_dirty, live_old]
        _co_ops_windows(stages, NOW)
        self.assertEqual([s.path for s in stages], ["/recent", "/old", "/dirty", "/live"])
        self.assertEqual(idle_old.state, "closed")

    def test_expand_overrides_round_trip_preserves_other_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "status-overrides.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({"station": {"state": "idle", "set": 3}, "other": [1]}, stream)
            ui = UIState(gopen_keys={"s:abc"}, gshut_keys={"p:/work/p"})
            _ap_save_expand(ui, path)
            loaded = UIState()
            _ap_load_expand(loaded, path)
            self.assertEqual(loaded.gopen_keys, ui.gopen_keys)
            self.assertEqual(loaded.gshut_keys, ui.gshut_keys)
            with open(path, encoding="utf-8") as stream:
                saved = json.load(stream)
            self.assertEqual(saved["station"], {"state": "idle", "set": 3})
            self.assertEqual(saved["other"], [1])

    def test_live_project_starts_open_and_lists_its_session(self):
        live = stage("/work/p/.stage/a", "active", NOW, workers=[Worker("w", RUNNING)])
        rows = global_rows(GlobalSnapshot(now=NOW, stages=[live]), UIState())
        self.assertIn(("", "group", "p:/work/p"), rows)
        self.assertIn((live.path, "stage", ""), rows)

    def test_recent_finished_stays_as_a_finished_fold_under_its_project(self):
        done = stage("/work/p/.stage/a", "closed", NOW - 600)
        gs = GlobalSnapshot(now=NOW, stages=[done])
        rows = global_rows(gs, UIState())
        self.assertIn(("", "more", "finished:/work/p"), rows)
        self.assertNotIn((done.path, "stage", ""), rows)
        opened = UIState(gopen={("", "more", "finished:/work/p")})
        self.assertIn((done.path, "stage", ""), global_rows(gs, opened))
        frame = [_wd_SGR_RE.sub("", line) for line in render_global(gs, UIState(), 140, 40, NOW)]
        self.assertIn("finished 1", "\n".join(frame))

    def test_three_hour_old_finished_goes_to_the_count_line(self):
        old = stage("/work/p/.stage/a", "closed", NOW - 3 * 3600)
        gs = GlobalSnapshot(now=NOW, stages=[old])
        frame = "\n".join(_wd_SGR_RE.sub("", line) for line in render_global(gs, UIState(), 140, 40, NOW))
        self.assertIn("1 closed · 0 need sweep", frame)
        self.assertNotIn("finished 1", frame)
        self.assertEqual([kind for _, kind, _ in global_rows(gs, UIState())], ["closed-count"])

    def test_old_finished_with_unpushed_work_is_kept_and_needs_sweep(self):
        old = stage("/work/p/.stage/a", "closed", NOW - 30 * 3600, work=WorkState(ahead=2))
        stages = [old]
        _co_ops_windows(stages, NOW)
        self.assertEqual(stages, [old])
        frame = "\n".join(_wd_SGR_RE.sub("", line)
                          for line in render_global(GlobalSnapshot(now=NOW, stages=stages), UIState(), 140, 40, NOW))
        self.assertIn("1 closed · 1 need sweep", frame)

    def test_manual_frame_has_all_idle_project_headings_collapsed_and_closed_count(self):
        live = stage("/projects/live/.stage/a", "active", NOW, workers=[Worker("w", RUNNING)])
        idle = [stage(f"/projects/idle{i}/.stage/a", "idle", NOW - i * 60) for i in range(5)]
        closed = [stage(f"/projects/closed{i}/.stage/a", "closed", NOW - 3 * 3600,
                        work=WorkState(dirty=(i == 0))) for i in range(30)]
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            frame = render_global(GlobalSnapshot(now=NOW, stages=[live, *idle, *closed]), UIState(), 140, 60, NOW)
        plain = [_wd_SGR_RE.sub("", line).rstrip() for line in frame]
        joined = "\n".join(plain)
        for i in range(5):
            self.assertIn(f"idle{i}", joined)   # one heading each, never a stage path
            self.assertNotIn(f"/projects/idle{i}/.stage/a", joined)
        self.assertIn("30 closed · 1 need sweep", joined)
        self.assertNotIn("closed0", joined)

    def test_corrupt_override_file_loads_empty_and_open_shut_choices_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "overrides.json")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("{")
            ui = UIState()
            _ap_load_expand(ui, path)
            ui.gopen_keys.add("s:one")
            self.assertTrue(_ap_expand_key_changed(ui, set(), set()))
            _ap_save_expand(ui, path)
            loaded = UIState()
            _ap_load_expand(loaded, path)
            self.assertEqual(loaded.gopen_keys, {"s:one"})


if __name__ == "__main__":
    unittest.main()
