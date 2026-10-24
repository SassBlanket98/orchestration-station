from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import collect
import register
import registry
from model import home_path, optional_cmd


class StationPaths(unittest.TestCase):
    def test_home_path_uses_station_home(self) -> None:
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"STATION_HOME": home}):
            self.assertEqual(home_path("ledger/runs.jsonl"), os.path.join(home, "ledger/runs.jsonl"))

    def test_home_path_expands_tilde_station_home_for_registry_writer_and_reader(self) -> None:
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(
                os.environ, {"HOME": home, "STATION_HOME": "~/st"}, clear=True):
            expected = os.path.join(home, "st")
            self.assertEqual(home_path("ledger/runs.jsonl"), os.path.join(expected, "ledger/runs.jsonl"))
            self.assertEqual(registry.registry_root(), os.path.join(expected, "status-registry"))
            self.assertEqual(register.registry_root(), os.path.join(expected, "status-registry"))

    def test_default_quota_argv_preserves_station_home_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="station home ") as home:
            quota = os.path.join(home, "bin", "quota")
            os.makedirs(os.path.dirname(quota))
            with open(quota, "w", encoding="utf-8") as stream:
                stream.write("#!/bin/sh\nexit 0\n")
            os.chmod(quota, 0o755)
            with mock.patch.dict(os.environ, {"STATION_HOME": home}, clear=True):
                collector = collect.Collector("", quota=False)
                try:
                    self.assertEqual(collector._co_quota_cmd, [quota, "status", "--json"])
                finally:
                    collector.close()

    def test_global_collector_uses_patchable_dismissed_and_overrides_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as settings:
            dismissed = os.path.join(settings, "dismissed.json")
            overrides = os.path.join(settings, "overrides.json")
            with mock.patch.object(collect, "_co_dismissed_file", dismissed), \
                    mock.patch.object(collect, "_co_overrides_file", overrides):
                collector = collect.GlobalCollector([root], quota=False)
                try:
                    self.assertEqual(collector._co_dismissed_path, dismissed)
                    self.assertEqual(collector._co_overrides_path, overrides)
                finally:
                    collector.close()

    def test_home_path_defaults_to_agents_directory(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": "/tmp/station-home-test"}, clear=True):
            self.assertEqual(home_path("status-dismissed.json"), "/tmp/station-home-test/.local/state/station/status-dismissed.json")

    def test_optional_cmd_returns_none_for_missing_file(self) -> None:
        self.assertIsNone(optional_cmd("/definitely/missing/station-command"))

    def test_optional_cmd_returns_existing_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = os.path.join(directory, "quota")
            with open(command, "w", encoding="utf-8") as stream:
                stream.write("#!/bin/sh\nexit 0\n")
            os.chmod(command, 0o755)
            self.assertEqual(optional_cmd(command), command)

    def test_openclaw_integration_is_disabled_without_configuration(self) -> None:
        with mock.patch.dict(os.environ, {"STATION_OPENCLAW_AGENT": "", "STATION_OPENCLAW_PEERS": ""}):
            self.assertEqual(collect.openclaw_agent(), "")
            self.assertEqual(collect.openclaw_peers(), ())

    def test_openclaw_workspace_mapping_is_disabled_without_agent(self) -> None:
        with mock.patch.object(collect, "OPENCLAW_AGENT", ""):
            self.assertEqual(collect._co_openclaw_project_root("/tmp/.openclaw/workspace-/project", "/tmp"), "")


if __name__ == "__main__":
    unittest.main()
