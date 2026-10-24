"""Installer tests use only temporary homes and state dirs; no live config is read or written."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "status-integrations.py"
spec = importlib.util.spec_from_file_location("status_integrations", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

PRODUCER = Path("/synthetic/register.py")
ROOT = "/synthetic/registry"


class Installer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.state = Path(self._tmp.name) / "state"
        self.home.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def target(self, name: str) -> Path:
        return self.home / tool.TARGETS[name][0]

    def seed(self, name: str, text: str) -> None:
        self.target(name).parent.mkdir(parents=True, exist_ok=True)
        self.target(name).write_text(text)

    def test_prepare_does_not_touch_targets(self):
        self.seed("claude", '{"model": "keep"}')
        before = self.target("claude").read_bytes()
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        self.assertEqual(self.target("claude").read_bytes(), before)
        self.assertFalse(self.target("codex").exists())

    def test_apply_appends_hooks_and_keeps_unrelated_settings(self):
        self.seed("claude", json.dumps({"model": "keep", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-tool"}]}]}}))
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        tool.apply(self.state, self.home)
        doc = json.loads(self.target("claude").read_text())
        self.assertEqual(doc["model"], "keep")
        stop = doc["hooks"]["Stop"]
        self.assertEqual(stop[0]["hooks"][0]["command"], "other-tool")
        self.assertEqual(len(stop), 2)
        self.assertIn(str(PRODUCER), stop[1]["hooks"][0]["command"])

    def test_reprepare_after_apply_adds_nothing(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        tool.apply(self.state, self.home)
        plan = tool.prepare(self.home, self.state, PRODUCER, ROOT)
        self.assertTrue(all(entry["added"] == 0 for entry in plan["targets"].values()))

    def test_cursor_entries_use_flat_shape_and_version(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        tool.apply(self.state, self.home)
        doc = json.loads(self.target("cursor").read_text())
        self.assertEqual(doc["version"], 1)
        self.assertIn("command", doc["hooks"]["stop"][0])

    def test_apply_refuses_stale_target_and_writes_nothing(self):
        self.seed("codex", "{}")
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        self.seed("codex", '{"edited": true}')
        with self.assertRaises(tool.Refused):
            tool.apply(self.state, self.home)
        self.assertEqual(json.loads(self.target("codex").read_text()), {"edited": True})
        self.assertFalse(self.target("claude").exists())

    def test_restore_returns_original_bytes_and_removes_created_files(self):
        original = '{"keep":  1}'
        self.seed("claude", original)
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        tool.apply(self.state, self.home)
        outcome = tool.restore(self.state, self.home)
        self.assertEqual(outcome["claude"], "restored")
        self.assertEqual(self.target("claude").read_text(), original)
        self.assertFalse(self.target("cursor").exists())
        self.assertEqual(outcome["cursor"], "restored")

    def test_restore_does_not_clobber_concurrent_edit(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        tool.apply(self.state, self.home)
        self.seed("codex", '{"someone_else": true}')
        outcome = tool.restore(self.state, self.home)
        self.assertEqual(outcome["codex"], "skipped-concurrent-edit")
        self.assertEqual(json.loads(self.target("codex").read_text()), {"someone_else": True})

    def test_invalid_json_is_refused_and_not_modified(self):
        self.seed("claude", "{not json")
        with self.assertRaises(tool.Refused):
            tool.prepare(self.home, self.state, PRODUCER, ROOT)
        self.assertEqual(self.target("claude").read_text(), "{not json")


class InstallerSafety(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_reprepare_after_apply_keeps_original_backup_for_restore(self):
        original = b'{"model": "keep"}'
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_bytes(original)
        tool.prepare(self.home, self.base / "state", PRODUCER, ROOT)
        tool.apply(self.base / "state", self.home)
        tool.prepare(self.home, self.base / "state", PRODUCER, ROOT)
        tool.restore(self.base / "state", self.home)
        self.assertEqual((self.home / ".claude" / "settings.json").read_bytes(), original)

    def test_reprepare_after_apply_adds_nothing(self):
        tool.prepare(self.home, self.base / "state", PRODUCER, ROOT)
        tool.apply(self.base / "state", self.home)
        plan = tool.prepare(self.home, self.base / "state2", PRODUCER, ROOT)
        self.assertEqual({entry["added"] for entry in plan["targets"].values()}, {0})

    def test_command_with_quotes_is_still_detected_as_present(self):
        command = tool.command_for(Path('/synthetic/with "quote"/register.py'), '/synthetic/root', "claude", "personal")
        group = {"hooks": [{"type": "command", "command": command}]}
        self.assertTrue(tool.mentions(group, command))
        self.assertFalse(tool.mentions({"hooks": [{"command": command + " extra"}]}, command))


class PlanConfinement(unittest.TestCase):
    """A plan.json edited to point outside --home must be refused before anything is written or unlinked."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.state = self.base / "state"

    def tearDown(self):
        self._tmp.cleanup()

    def edit_plan(self, name: str, path: str) -> None:
        plan_path = self.state / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["targets"][name]["path"] = path
        plan_path.write_text(json.dumps(plan))

    def test_apply_refuses_dotdot_escape_and_writes_nothing(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        escape = self.home / ".." / "outside" / "settings.json"
        self.edit_plan("codex", str(escape))
        with self.assertRaises(tool.Refused):
            tool.apply(self.state, self.home)
        self.assertFalse((self.outside / "settings.json").exists())
        self.assertFalse((self.home / ".claude" / "settings.json").exists())
        self.assertFalse((self.state / "applied.json").exists())

    def test_apply_refuses_absolute_path_outside_home(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        self.edit_plan("claude", str(self.outside / "settings.json"))
        with self.assertRaises(tool.Refused):
            tool.apply(self.state, self.home)
        self.assertFalse((self.outside / "settings.json").exists())
        self.assertFalse((self.home / ".codex" / "hooks.json").exists())

    def test_apply_refuses_symlinked_directory_escape(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        (self.home / ".codex").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(tool.Refused):
            tool.apply(self.state, self.home)
        self.assertFalse((self.outside / "hooks.json").exists())
        self.assertFalse((self.home / ".claude" / "settings.json").exists())

    def test_restore_refuses_escape_and_unlinks_nothing(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        tool.apply(self.state, self.home)
        victim = self.outside / "hooks.json"
        victim.write_text("{}")
        self.edit_plan("cursor", str(self.home / ".." / "outside" / "hooks.json"))
        with self.assertRaises(tool.Refused):
            tool.restore(self.state, self.home)
        self.assertTrue(victim.exists())
        self.assertTrue((self.home / ".claude" / "settings.json").exists())
        self.assertIn(str(PRODUCER), (self.home / ".claude" / "settings.json").read_text())

    def test_restore_refuses_symlinked_directory_escape(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        tool.apply(self.state, self.home)
        moved = self.outside / "codex-dir"
        (self.home / ".codex").rename(moved)
        (self.home / ".codex").symlink_to(moved, target_is_directory=True)
        with self.assertRaises(tool.Refused):
            tool.restore(self.state, self.home)
        self.assertTrue((moved / "hooks.json").exists())

    def test_cli_exits_nonzero_with_message_on_escape(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        self.edit_plan("claude", str(self.outside / "settings.json"))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = tool.main(["apply", "--state", str(self.state), "--home", str(self.home)])
        self.assertEqual(code, 1)
        self.assertIn("claude", stderr.getvalue())
        self.assertIn("--home", stderr.getvalue())
        self.assertFalse((self.outside / "settings.json").exists())

    def test_in_home_dotdot_that_stays_inside_is_allowed(self):
        tool.prepare(self.home, self.state, PRODUCER, ROOT)
        self.edit_plan("claude", str(self.home / ".codex" / ".." / ".claude" / "settings.json"))
        tool.apply(self.state, self.home)
        self.assertIn(str(PRODUCER), (self.home / ".claude" / "settings.json").read_text())


if __name__ == "__main__":
    unittest.main()
