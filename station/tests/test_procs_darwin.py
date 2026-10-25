"""macOS process provider: ps, sysctl and lsof parsers on recorded text; caches and failures with subprocess mocked."""
from __future__ import annotations

import subprocess
import time
import unittest
from unittest.mock import patch

import procs_darwin

BOOT = 1761350400.0

# ps -axww -o pid=,ppid=,time=,rss=,tty=,lstart=,command=  (recorded shape, no header)
_PS_LINES = [
    "    1     0     2:03.45  12240 ??       Fri Oct 25 00:00:01 2026 /sbin/launchd",
    "  412     1     0:01.23   1024 ttys003  Fri Oct 25 00:12:34 2026 -zsh",
    ("  530   412 12:34.56 204800 ttys003  Sat Oct  3 09:05:00 2026 "
     "/Applications/Visual Studio Code.app/Contents/MacOS/Electron --type=renderer"),
    "  601     1 1:02:03.45   5120 ??     Mon Oct 26 08:00:00 2026 /usr/libexec/cfprefsd -F",
    "  702     1 2-01:02:03   2048 -      Thu Oct 22 10:10:10 2026 /usr/bin/daemon",
    "  999     1 junk",
    "",
    "   ",
]
PS_TABLE = "\n".join(_PS_LINES)

BOOTTIME = "{ sec = 1761350400, usec = 500000 } Sun Oct 25 00:00:00 2026\n"

# lsof -a -d cwd -Fn -p 412 530 (two pids in one sample)
LSOF = "p412\nfcwd\nn/Users/someone/code/station\np530\nfcwd\nn/tmp/scratch\n"

ENV_TEXT = ("/bin/zsh -l SECRET_TOKEN=abc PATH=/usr/bin:/bin HOME=/Users/someone "
            "TERM_PROGRAM=Apple_Terminal\n")


def _start(lstart: str) -> int:
    """Expected start ticks, computed the way ps prints local time (timezone-safe)."""
    return max(0, round((time.mktime(time.strptime(lstart, "%a %b %d %H:%M:%S %Y")) - BOOT) * 100))


def _done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _fake_run(argv, **kwargs):
    if argv[0] == "ps" and "-axww" in argv:
        return _done(PS_TABLE)
    if argv[0] == "lsof":
        return _done(LSOF)
    if argv[0] == "ps" and "-E" in argv:
        return _done(ENV_TEXT)
    return _done(BOOTTIME)


class _ProcsCase(unittest.TestCase):
    def setUp(self) -> None:
        saved = (procs_darwin._pd_table_memo, procs_darwin._pd_boot_memo)
        self.addCleanup(self._restore, saved)
        procs_darwin._pd_table_memo = None
        procs_darwin._pd_boot_memo = BOOT
        procs_darwin._pd_cwd_memo.clear()
        procs_darwin._pd_env_memo.clear()
        procs_darwin._pd_env_visible = None

    @staticmethod
    def _restore(saved: tuple) -> None:
        procs_darwin._pd_table_memo, procs_darwin._pd_boot_memo = saved
        procs_darwin._pd_cwd_memo.clear()
        procs_darwin._pd_env_memo.clear()
        procs_darwin._pd_env_visible = None


class ParseCpuTimeTests(unittest.TestCase):
    def test_all_recorded_formats(self) -> None:
        cases = {"0:01.23": 1.23, "12:34.56": 754.56, "1:02:03.45": 3723.45, "2-01:02:03": 176523.0}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertAlmostEqual(procs_darwin._pd_parse_cpu_time(text), want, places=6)

    def test_garbage_is_none(self) -> None:
        self.assertIsNone(procs_darwin._pd_parse_cpu_time("soon"))


class ParseBoottimeTests(unittest.TestCase):
    def test_sec_and_usec(self) -> None:
        self.assertEqual(procs_darwin._pd_parse_boottime(BOOTTIME), 1761350400.5)

    def test_garbage_is_none(self) -> None:
        self.assertIsNone(procs_darwin._pd_parse_boottime("no clock here"))


class TtyNumTests(unittest.TestCase):
    def test_no_tty_is_zero(self) -> None:
        self.assertEqual(procs_darwin._pd_tty_num("??"), 0)
        self.assertEqual(procs_darwin._pd_tty_num("-"), 0)

    def test_ttys_number_is_stable(self) -> None:
        self.assertEqual(procs_darwin._pd_tty_num("ttys003"), 3 + 0x10000)
        self.assertEqual(procs_darwin._pd_tty_num("console"), procs_darwin._pd_tty_num("console"))
        self.assertNotEqual(procs_darwin._pd_tty_num("console"), 0)


class ParseTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = procs_darwin._pd_parse_table(PS_TABLE, BOOT)

    def test_blank_and_malformed_lines_are_skipped(self) -> None:
        self.assertEqual(sorted(self.rows), [1, 412, 530, 601, 702])

    def test_plain_process_with_no_tty(self) -> None:
        ppid, utime, stime, start, tty, argv, rss = self.rows[1]
        self.assertEqual((ppid, utime, stime, tty), (0, 12345, 0, 0))
        self.assertEqual(start, _start("Fri Oct 25 00:00:01 2026"))
        self.assertEqual(argv, ("/sbin/launchd",))
        self.assertEqual(rss, 12240 * 1024)

    def test_ttys_process_with_m_ss_time(self) -> None:
        self.assertEqual(self.rows[412], (1, 123, 0, _start("Fri Oct 25 00:12:34 2026"),
                                          3 + 0x10000, ("-zsh",), 1024 * 1024))

    def test_command_with_spaces_is_split_on_whitespace(self) -> None:
        # Lossy by design: the space in "Visual Studio Code.app" splits the argument.
        ppid, utime, _, _start_ticks, tty, argv, rss = self.rows[530]
        self.assertEqual((ppid, utime, tty), (412, 75456, 3 + 0x10000))
        self.assertEqual(argv, ("/Applications/Visual", "Studio", "Code.app/Contents/MacOS/Electron",
                                "--type=renderer"))
        self.assertEqual(rss, 204800 * 1024)

    def test_h_mm_ss_time(self) -> None:
        self.assertEqual(self.rows[601][1], 372345)

    def test_d_hh_mm_ss_time_with_dash_tty(self) -> None:
        self.assertEqual(self.rows[702][1], 17652300)
        self.assertEqual(self.rows[702][4], 0)

    def test_unknown_boot_gives_zero_start(self) -> None:
        rows = procs_darwin._pd_parse_table(PS_TABLE, None)
        self.assertEqual(rows[412][3], 0)

    def test_empty_text_is_empty(self) -> None:
        self.assertEqual(procs_darwin._pd_parse_table("\n   \n", BOOT), {})


class ParseLsofTests(unittest.TestCase):
    def test_two_pids(self) -> None:
        self.assertEqual(procs_darwin._pd_parse_lsof_cwd(LSOF),
                         {412: "/Users/someone/code/station", 530: "/tmp/scratch"})

    def test_empty_is_empty(self) -> None:
        self.assertEqual(procs_darwin._pd_parse_lsof_cwd(""), {})


class ParseEnvTests(unittest.TestCase):
    def test_only_requested_keys_are_returned(self) -> None:
        got = procs_darwin._pd_parse_env(ENV_TEXT, ("PATH", "HOME", "MISSING"))
        self.assertEqual(got, {"PATH": "/usr/bin:/bin", "HOME": "/Users/someone"})
        self.assertNotIn("SECRET_TOKEN", got)

    def test_last_occurrence_wins(self) -> None:
        self.assertEqual(procs_darwin._pd_parse_env("cmd PATH=/a PATH=/b", ("PATH",)),
                         {"PATH": "/b"})


class ScanCacheTests(_ProcsCase):
    def test_scan_shape_matches_linux_reader(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run):
            scan = procs_darwin._pd_scan()
        self.assertEqual(scan[412], (1, 123, 0, _start("Fri Oct 25 00:12:34 2026"), 3 + 0x10000))

    def test_per_pid_reads_reuse_the_scan(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            procs_darwin._pd_scan()
            self.assertEqual(procs_darwin._pd_resident(412, 4096), 1024 * 1024)
            self.assertEqual(procs_darwin._pd_argv(412), ["-zsh"])
            self.assertEqual(procs_darwin._pd_stat(530)[2], "Visual")
        self.assertEqual(run.call_count, 1)

    def test_each_scan_reads_ps_again(self) -> None:
        # a process started since the last scan must show up, even inside the TTL
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            procs_darwin._pd_scan()
            procs_darwin._pd_scan()
        self.assertEqual(run.call_count, 2)

    def test_stale_cache_is_rescanned(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            procs_darwin._pd_scan()
            procs_darwin._pd_table_memo = (time.monotonic() - 2.0, procs_darwin._pd_table_memo[1])
            procs_darwin._pd_scan()
        self.assertEqual(run.call_count, 2)

    def test_unknown_pid_is_empty_from_fresh_cache(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            self.assertIsNone(procs_darwin._pd_resident(99999, 4096))
            self.assertEqual(procs_darwin._pd_argv(99999), [])
            self.assertIsNone(procs_darwin._pd_stat(99999))
        self.assertEqual(run.call_count, 1)


class CwdCacheTests(_ProcsCase):
    def test_first_miss_looks_up_every_process_in_one_lsof(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            self.assertEqual(procs_darwin._pd_cwd(412), "/Users/someone/code/station")
            self.assertEqual(procs_darwin._pd_cwd(530), "/tmp/scratch")
            self.assertEqual(procs_darwin._pd_cwd(601), "")    # lsof showed nothing: hidden, not retried
            self.assertEqual(procs_darwin._pd_cwd(412), "/Users/someone/code/station")
        lsof = [c.args[0] for c in run.call_args_list if c.args[0][0] == "lsof"]
        self.assertEqual(len(lsof), 1)
        self.assertEqual(lsof[0][-1].split(",")[0], "412")
        self.assertEqual(sorted(lsof[0][-1].split(",")), sorted(["1", "412", "530", "601", "702"]))

    def test_reused_pid_is_looked_up_again(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            procs_darwin._pd_cwd(412)
            ppid, cpu, stime, start, tty, argv, rss = procs_darwin._pd_table_memo[1][412]
            procs_darwin._pd_table_memo[1][412] = (ppid, cpu, stime, start + 1, tty, argv, rss)
            procs_darwin._pd_cwd(412)
        lsof = [c.args[0] for c in run.call_args_list if c.args[0][0] == "lsof"]
        self.assertEqual([c[-1] for c in lsof][1], "412")      # only the new process

    def test_lsof_exit_one_still_reads_its_output(self) -> None:
        def run(argv, **kwargs):
            return _done(LSOF, 1) if argv[0] == "lsof" else _fake_run(argv)
        with patch("procs_darwin.subprocess.run", side_effect=run):
            self.assertEqual(procs_darwin._pd_cwd(530), "/tmp/scratch")

    def test_failed_lsof_is_retried_later(self) -> None:
        def run(argv, **kwargs):
            if argv[0] == "lsof":
                raise subprocess.TimeoutExpired(cmd="lsof", timeout=5)
            return _fake_run(argv)
        with patch("procs_darwin.subprocess.run", side_effect=run) as failing:
            self.assertEqual(procs_darwin._pd_cwd(412), "")
            self.assertEqual(procs_darwin._pd_cwd(530), "")        # the batch failure covers it: no second timeout
        self.assertEqual(sum(1 for c in failing.call_args_list if c.args[0][0] == "lsof"), 1)
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as ok:
            self.assertEqual(procs_darwin._pd_cwd(412), "")        # inside the retry window
            self.assertEqual(ok.call_count, 0)
            for key in list(procs_darwin._pd_cwd_memo):
                procs_darwin._pd_cwd_memo[key] = ("", time.monotonic() - 1)
            self.assertEqual(procs_darwin._pd_cwd(412), "/Users/someone/code/station")

    def test_empty_lsof_output_is_retried_not_kept(self) -> None:
        def run(argv, **kwargs):
            return _done("", 1) if argv[0] == "lsof" else _fake_run(argv)
        with patch("procs_darwin.subprocess.run", side_effect=run):
            procs_darwin._pd_cwd(412)
        self.assertTrue(all(until != float("inf") for _cwd, until in procs_darwin._pd_cwd_memo.values()))

    def test_pid_missing_from_the_table_is_not_cached(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run):
            procs_darwin._pd_cwd(4242)
            procs_darwin._pd_environ(4242, ("PATH",))
        self.assertNotIn((4242, -1), procs_darwin._pd_cwd_memo)
        self.assertFalse(any(key[0] == 4242 for key in procs_darwin._pd_env_memo))

    def test_cache_is_cleared_when_full(self) -> None:
        with patch.object(procs_darwin, "_PD_CWD_MAX", 3), \
                patch("procs_darwin.subprocess.run", side_effect=_fake_run):
            procs_darwin._pd_cwd(412)
            self.assertLessEqual(len(procs_darwin._pd_cwd_memo), 5)
            procs_darwin._pd_cwd_memo[(4242, 1)] = ("", float("inf"))   # an old entry
            procs_darwin._pd_table_memo[1][4242] = (1, 0, 0, 2, 0, ("x",), 0)
            procs_darwin._pd_cwd(4242)
            self.assertNotIn((4242, 1), procs_darwin._pd_cwd_memo)   # cleared before the new batch was stored


class DecodingTests(_ProcsCase):
    def test_undecodable_byte_keeps_the_table(self) -> None:
        bad = PS_TABLE.encode() + b"\n  777     1   0:00.01   100 ??  Sun Oct 25 00:00:02 2026 /bin/x\xff\n"
        real = subprocess.run

        def run(argv, **kwargs):
            if argv[0] == "ps" and "-axww" in argv:
                return real(["/bin/sh", "-c", 'cat "$0"', path], **kwargs)
            return _fake_run(argv)
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as stream:
            stream.write(bad)
            path = stream.name
        self.addCleanup(os.unlink, path)
        with patch("procs_darwin.subprocess.run", side_effect=run):
            scan = procs_darwin._pd_scan()
        self.assertIn(412, scan)
        self.assertIn(777, scan)


class ScanFailureTests(_ProcsCase):
    def test_failed_ps_is_not_retried_every_refresh(self) -> None:
        with patch("procs_darwin.subprocess.run", return_value=_done("", 1)) as run:
            self.assertEqual(procs_darwin._pd_scan(), {})
            procs_darwin._pd_table_memo = (procs_darwin._pd_table_memo[0] - 2.0, {})   # past the normal TTL
            self.assertEqual(procs_darwin._pd_scan(), {})
        self.assertEqual(run.call_count, 1)


class StartTimeTests(unittest.TestCase):
    def test_start_ticks_against_a_fixed_clock(self) -> None:
        import calendar
        import os
        if not hasattr(time, "tzset"):
            self.skipTest("needs time.tzset")
        saved = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            boot = float(calendar.timegm((2026, 10, 25, 0, 0, 0)))
            rows = procs_darwin._pd_parse_table("  5  1  0:00.00  1 ??  Sun Oct 25 00:00:12 2026 /bin/x", boot)
        finally:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()
        self.assertEqual(rows[5][3], 1200)


class EnvironTests(_ProcsCase):
    def test_environ_runs_ps_for_pid_and_drops_other_keys(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            got = procs_darwin._pd_environ(412, ("PATH", "HOME"))
        self.assertEqual(got, {"PATH": "/usr/bin:/bin", "HOME": "/Users/someone"})
        self.assertNotIn("SECRET_TOKEN", got)
        self.assertEqual(run.call_args.args[0], ["ps", "-E", "-ww", "-o", "command=", "-p", "412"])

    def test_hidden_environment_is_probed_once_then_skipped(self) -> None:
        # macOS 15 prints no environment: one probe, then no ps per call.
        with patch("procs_darwin.subprocess.run", return_value=_done("/bin/zsh -l\n")) as run:
            self.assertEqual(procs_darwin._pd_environ(412, ("PATH",)), {})
            self.assertEqual(procs_darwin._pd_environ(530, ("PATH",)), {})
        self.assertEqual(run.call_count, 1)

    def test_visible_environment_is_read_once_per_process(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            procs_darwin._pd_environ(412, ("PATH",))
            procs_darwin._pd_environ(412, ("PATH",))
        env_calls = [c for c in run.call_args_list if "-E" in c.args[0]]
        self.assertEqual(len(env_calls), 2)      # the probe and one read

    def test_no_keys_means_no_subprocess(self) -> None:
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            self.assertEqual(procs_darwin._pd_environ(412, ()), {})
        self.assertEqual(run.call_count, 0)


class RecordedMacosTests(unittest.TestCase):
    """Real output recorded on macOS 15.5 (arm64)."""

    LINES = (
        "    1     0  64:36.51  26624 ??       Tue Oct 13 12:33:35 2026     /sbin/launchd",
        "  828     1 1077:31.78  64096 ??       Thu Oct 15 12:17:17 2026     /usr/libexec/logd",
        "66631 66630   0:00.01   2512 ttys000  Sun Oct 25 00:37:10 2026     -zsh",
    )
    TABLE = "\n".join(LINES)

    def test_table(self) -> None:
        boot = procs_darwin._pd_parse_boottime("{ sec = 1791887618, usec = 551821 } Tue Oct 13 12:33:38 2026")
        self.assertAlmostEqual(boot, 1791887618.551821, places=5)
        rows = procs_darwin._pd_parse_table(self.TABLE, boot)
        self.assertEqual(rows[1][1], 387651)            # 64:36.51
        self.assertEqual(rows[828][1], 6465178)         # minutes never roll into hours
        self.assertEqual(rows[66631][4], 0x10000)       # ttys000
        self.assertEqual(rows[66631][5], ("-zsh",))
        self.assertEqual(rows[1][3], 0)                 # launchd starts before kern.boottime: clamped

    def test_lsof(self) -> None:
        self.assertEqual(procs_darwin._pd_parse_lsof_cwd("p67090\nfcwd\nn/Users/someone\n"), {67090: "/Users/someone"})


class BootCacheTests(_ProcsCase):
    def test_boot_is_read_from_sysctl_once(self) -> None:
        procs_darwin._pd_boot_memo = None
        with patch("procs_darwin.subprocess.run", side_effect=_fake_run) as run:
            self.assertEqual(procs_darwin._pd_boot(), 1761350400.5)
            self.assertEqual(procs_darwin._pd_boot(), 1761350400.5)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["sysctl", "-n", "kern.boottime"])

    def test_boot_id_and_time(self) -> None:
        self.assertEqual(procs_darwin._pd_boot_id(), "1761350400")
        self.assertEqual(procs_darwin._pd_boot_time(), BOOT)


class FailureTests(_ProcsCase):
    """Non-zero exit, missing binary and timeout all give empty results and never raise."""

    def _failing(self, behaviour):
        if isinstance(behaviour, BaseException):
            return patch("procs_darwin.subprocess.run", side_effect=behaviour)
        return patch("procs_darwin.subprocess.run", return_value=_done("", behaviour))

    def test_every_reader_is_empty_on_failure(self) -> None:
        cases = {
            "nonzero": 1,
            "missing binary": FileNotFoundError("ps"),
            "timeout": subprocess.TimeoutExpired(cmd="ps", timeout=5),
        }
        for label, behaviour in cases.items():
            with self.subTest(failure=label):
                procs_darwin._pd_table_memo = None
                procs_darwin._pd_boot_memo = None
                procs_darwin._pd_cwd_memo.clear()
                with self._failing(behaviour):
                    self.assertEqual(procs_darwin._pd_scan(), {})
                    self.assertEqual(procs_darwin._pd_argv(412), [])
                    self.assertIsNone(procs_darwin._pd_resident(412, 4096))
                    self.assertIsNone(procs_darwin._pd_stat(412))
                    self.assertIsNone(procs_darwin._pd_boot())
                    self.assertEqual(procs_darwin._pd_cwd(412), "")
                    self.assertEqual(procs_darwin._pd_environ(412, ("PATH",)), {})

    def test_run_passes_no_shell_and_closed_stdin(self) -> None:
        with patch("procs_darwin.subprocess.run", return_value=_done("")) as run:
            procs_darwin._pd_run(["ps", "-axww"])
        kwargs = run.call_args.kwargs
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["timeout"], 5)
        self.assertFalse(kwargs["check"])


if __name__ == "__main__":
    unittest.main()
