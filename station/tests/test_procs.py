from __future__ import annotations

import os
import unittest
from unittest import mock

import procs


class ProcsLinuxTests(unittest.TestCase):
    def test_scan_contains_self_with_parent(self) -> None:
        scan = procs._pr_scan()
        pid = os.getpid()
        self.assertIn(pid, scan)
        self.assertEqual(scan[pid][0], os.getppid())

    def test_argv_of_self_is_nonempty(self) -> None:
        self.assertTrue(procs._pr_argv(os.getpid()))

    def test_cwd_of_self_matches_getcwd(self) -> None:
        self.assertEqual(procs._pr_cwd(os.getpid()), os.getcwd())

    def test_environ_keeps_only_requested_keys(self) -> None:
        env = procs._pr_environ(os.getpid(), ("HOME",))
        self.assertLessEqual(set(env), {"HOME"})
        self.assertEqual(env.get("HOME"), os.environ.get("HOME"))

    def test_stat_of_self_matches_scan(self) -> None:
        pid = os.getpid()
        stat = procs._pr_stat(pid)
        self.assertIsNotNone(stat)
        ppid, start, comm = stat
        self.assertEqual(ppid, os.getppid())
        self.assertEqual(start, procs._pr_scan()[pid][3])
        self.assertTrue(comm)

    def test_stat_of_missing_pid_is_none(self) -> None:
        self.assertIsNone(procs._pr_stat(999999999))

    def test_stat_comm_may_contain_parens_and_spaces(self) -> None:
        rest = ["S", "7"] + ["0"] * 17 + ["98765"]
        data = b"123 (a (b) c) " + " ".join(rest).encode()
        with mock.patch.object(procs, "_PR_DARWIN", False), \
                mock.patch("builtins.open", mock.mock_open(read_data=data)):
            self.assertEqual(procs._pr_stat(123), (7, 98765, "a (b) c"))

    def test_boot_id_is_nonempty(self) -> None:
        self.assertTrue(procs._pr_boot_id())

    def test_boot_time_is_positive(self) -> None:
        self.assertGreater(procs._pr_boot_time(), 0)

    def test_resident_of_self_is_positive(self) -> None:
        page = os.sysconf("SC_PAGE_SIZE")
        resident = procs._pr_resident(os.getpid(), page)
        self.assertIsNotNone(resident)
        self.assertGreater(resident, 0)


class ProcsDarwinDispatchTests(unittest.TestCase):
    def test_argv_calls_darwin_provider(self) -> None:
        with mock.patch.object(procs, "_PR_DARWIN", True), \
                mock.patch.object(procs, "_pd_argv", return_value=["mac-argv"]) as darwin_argv:
            self.assertEqual(procs._pr_argv(42), ["mac-argv"])
        darwin_argv.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
