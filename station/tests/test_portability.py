"""Portability fixes: worker-session without /proc (ps fallback) and separator checks, the process identity
helper, and the STATUS_ROOTS parsing that splits on os.pathsep. Temporary dirs and patched env only."""
from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
from register import _sr_process_identity

TOOL = Path(__file__).resolve().parents[1] / "tools" / "worker-session"
loader = importlib.machinery.SourceFileLoader("worker_session_portability", str(TOOL))
spec = importlib.util.spec_from_loader("worker_session_portability", loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


class WorkerSessionName(unittest.TestCase):
    def test_rejects_name_with_os_sep(self) -> None:
        with tempfile.TemporaryDirectory() as stage:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = tool.main(["worker-session", stage, "bad" + os.sep + "name"])
            self.assertEqual(code, 2)
            self.assertIn("bad stage dir or worker name", err.getvalue())
            self.assertFalse(any(Path(stage).iterdir()))


class WorkerSessionAncestors(unittest.TestCase):
    def test_ps_fallback_when_proc_is_missing(self) -> None:
        # /proc reads fail with FileNotFoundError (macOS); the parent chain comes from ps: 500 -> 400 -> 1.
        ps_out = [subprocess.CompletedProcess([], 0, stdout="400\n", stderr=""),
                  subprocess.CompletedProcess([], 0, stdout="1\n", stderr="")]
        with mock.patch("builtins.open", side_effect=FileNotFoundError("no /proc")), \
                mock.patch.object(tool.os, "getppid", return_value=500), \
                mock.patch.object(tool.subprocess, "run", side_effect=ps_out) as run:
            self.assertEqual(tool.ancestors(), [500, 400])
        self.assertEqual(run.call_count, 2)
        first = run.call_args_list[0]
        self.assertEqual(first.args[0], ["ps", "-o", "ppid=", "-p", "500"])
        self.assertIs(first.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(first.kwargs["timeout"], 2)
        self.assertTrue(first.kwargs["capture_output"])
        self.assertTrue(first.kwargs["text"])
        self.assertFalse(first.kwargs["check"])
        self.assertEqual(run.call_args_list[1].args[0], ["ps", "-o", "ppid=", "-p", "400"])

    def test_ps_failure_stops_the_chain(self) -> None:
        with mock.patch("builtins.open", side_effect=FileNotFoundError("no /proc")), \
                mock.patch.object(tool.os, "getppid", return_value=500), \
                mock.patch.object(tool.subprocess, "run", side_effect=subprocess.TimeoutExpired("ps", 2)):
            self.assertEqual(tool.ancestors(), [500])


class ProcessIdentity(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "needs /proc")
    def test_own_process_identity_on_linux(self) -> None:
        start, boot = _sr_process_identity(os.getpid())
        self.assertIsNotNone(start)
        self.assertIsInstance(start, int)
        self.assertTrue(boot)


class StatusRootsParsing(unittest.TestCase):
    def test_status_roots_split_on_pathsep(self) -> None:
        roots = os.pathsep.join(["/tmp/alpha", "/tmp/beta dir"])
        with mock.patch.dict(os.environ, {"STATUS_ROOTS": roots}):
            self.assertEqual(app._ap_roots(argparse.Namespace(roots=None)), [os.path.realpath(p) for p in ("/tmp/alpha", "/tmp/beta dir")])

    def test_status_roots_use_the_platform_separator(self) -> None:
        # ";" as on Windows: a literal ":" split would leave one entry
        with mock.patch.object(os, "pathsep", ";"), \
                mock.patch.dict(os.environ, {"STATUS_ROOTS": "/tmp/alpha;/tmp/beta"}):
            self.assertEqual(len(app._ap_roots(argparse.Namespace(roots=None))), 2)

    def test_status_roots_drop_empty_entries(self) -> None:
        roots = os.pathsep + "/tmp/alpha" + os.pathsep + os.pathsep + "/tmp/beta" + os.pathsep
        with mock.patch.dict(os.environ, {"STATUS_ROOTS": roots}):
            self.assertEqual(app._ap_roots(argparse.Namespace(roots=None)), [os.path.realpath(p) for p in ("/tmp/alpha", "/tmp/beta")])



class RootsResolveTests(unittest.TestCase):
    def test_symlinked_root_is_resolved(self) -> None:
        import argparse
        import tempfile

        import app
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "real")
            os.mkdir(real)
            link = os.path.join(tmp, "link")
            os.symlink(real, link)
            args = argparse.Namespace(roots=[link])
            self.assertEqual(app._ap_roots(args), [os.path.realpath(real)])

class MacNotificationTests(unittest.TestCase):
    def test_text_is_passed_as_argv_not_quoted_script(self) -> None:
        import app
        notifier = app._ap_Notifier()
        which = {"osascript": "/usr/bin/osascript"}
        with mock.patch.object(app.shutil, "which", side_effect=which.get), \
                mock.patch.object(app.sys, "platform", "darwin"), \
                mock.patch.object(app.subprocess, "Popen") as popen:
            notifier.send('3 "done"', "caf\u00e9 \u2014 two left", "normal")
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], "osascript")
        self.assertEqual(argv[-3:], ["--", "caf\u00e9 \u2014 two left", '3 "done"'])
        self.assertNotIn("caf", " ".join(argv[:-2]))


if __name__ == "__main__":
    unittest.main()
