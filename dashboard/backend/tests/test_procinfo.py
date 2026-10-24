import os
import subprocess
import sys
import time

import pytest

from stagewatch.procinfo import is_alive, model_from_argv, start_time

DEAD_PID = 999999999


def _spawn(args: list[str]) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen([sys.executable, *args], stdin=subprocess.DEVNULL)
    # Between fork and exec, /proc/<pid>/cmdline still holds this pytest process's
    # own argv, so give the child a moment to exec before reading it.
    time.sleep(0.3)
    return proc


def test_start_time_of_self_is_a_recent_epoch() -> None:
    started = start_time(os.getpid())

    assert started is not None
    assert 0 < started <= time.time() + 1


def test_start_time_unknown_for_dead_or_invalid_pid() -> None:
    assert start_time(DEAD_PID) is None
    assert start_time(0) is None
    assert start_time(-5) is None


def test_is_alive_for_self_without_guard() -> None:
    assert is_alive(os.getpid(), None) is True


def test_is_alive_false_for_dead_pid() -> None:
    assert is_alive(DEAD_PID, None) is False
    assert is_alive(DEAD_PID, time.time()) is False


def test_is_alive_guard_rejects_pid_reuse() -> None:
    started = start_time(os.getpid())
    assert started is not None

    # A pid file written before the process started cannot belong to it.
    assert is_alive(os.getpid(), started - 1000) is False


def test_is_alive_guard_accepts_start_before_pid_file() -> None:
    started = start_time(os.getpid())
    assert started is not None

    assert is_alive(os.getpid(), started) is True
    assert is_alive(os.getpid(), time.time() + 5) is True


def test_model_and_effort_from_own_argv() -> None:
    proc = _spawn(["-c", "import time; time.sleep(30)", "--model=test-model-x", "--effort", "high"])
    try:
        assert model_from_argv(proc.pid) == "test-model-x (high)"
    finally:
        proc.kill()
        proc.wait()


def test_short_model_flag_without_effort() -> None:
    proc = _spawn(["-c", "import time; time.sleep(30)", "-m", "short-flag-model"])
    try:
        assert model_from_argv(proc.pid) == "short-flag-model"
    finally:
        proc.kill()
        proc.wait()


def test_model_found_in_child_process() -> None:
    script = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)',"
        " '--model', 'child-model'])\n"
        "time.sleep(30)\n"
    )
    parent = _spawn(["-c", script])
    try:
        deadline = time.monotonic() + 10
        found = None
        while found is None and time.monotonic() < deadline:
            found = model_from_argv(parent.pid)
            if found is None:
                time.sleep(0.1)
        assert found == "child-model"
    finally:
        parent.kill()
        parent.wait()


def test_no_model_argument_gives_none() -> None:
    proc = _spawn(["-c", "import time; time.sleep(30)", "--plain"])
    try:
        assert model_from_argv(proc.pid) is None
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("pid", [DEAD_PID, 0])
def test_model_from_argv_none_for_missing_pid(pid: int) -> None:
    assert model_from_argv(pid) is None
