"""Process-table reads (pid scan, argv, cwd, environ, stat, boot id) from /proc, with the macOS provider dispatched per function."""
from __future__ import annotations

import os
import sys
import time

from procs_darwin import (
    _pd_argv,
    _pd_boot_id,
    _pd_boot_time,
    _pd_cwd,
    _pd_environ,
    _pd_resident,
    _pd_scan,
    _pd_stat,
)

_PR_DARWIN = sys.platform == "darwin"


def _pr_clk_tck() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError, AttributeError):
        return 100


def _pr_boot_time() -> float:
    if _PR_DARWIN:
        return _pd_boot_time()
    try:
        with open("/proc/stat", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("btime "):
                    fields = line.split()
                    if len(fields) >= 2:
                        return float(fields[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        with open("/proc/uptime", "r", encoding="utf-8", errors="replace") as fh:
            uptime = float(fh.read().split()[0])
        return time.time() - uptime
    except (OSError, ValueError, IndexError):
        return 0.0


def _pr_scan() -> dict[int, tuple[int, int, int, int, int]]:
    """pid -> (ppid, utime, stime, starttime_ticks, tty_nr) read from /proc/<pid>/stat."""
    if _PR_DARWIN:
        return _pd_scan()
    procs: dict[int, tuple[int, int, int, int, int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return procs
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        close = data.rfind(b")")
        if close < 0:
            continue
        fields = data[close + 2:].split()
        if len(fields) < 20:
            continue
        try:
            procs[pid] = (
                int(fields[1]),
                int(fields[11]),
                int(fields[12]),
                int(fields[19]),
                int(fields[4]),
            )
        except ValueError:
            continue
    return procs


def _pr_stat(pid: int) -> tuple[int, int, str] | None:
    """(ppid, starttime_ticks, comm) from /proc/<pid>/stat; None on any error.

    comm sits between the first "(" and the LAST ")", since it may contain spaces and ")" itself.
    """
    if _PR_DARWIN:
        return _pd_stat(pid)
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    open_paren = data.find(b"(")
    close = data.rfind(b")")
    if open_paren < 0 or close < open_paren:
        return None
    fields = data[close + 2:].split()
    if len(fields) < 20:
        return None
    try:
        ppid = int(fields[1])
        start = int(fields[19])
    except ValueError:
        return None
    comm = data[open_paren + 1:close].decode("utf-8", "replace")
    return (ppid, start, comm)


def _pr_resident(pid: int, page: int) -> int | None:
    if _PR_DARWIN:
        return _pd_resident(pid, page)
    try:
        with open(f"/proc/{pid}/statm", "r", encoding="utf-8", errors="replace") as fh:
            fields = fh.read().split()
        return int(fields[1]) * page
    except (OSError, IndexError, ValueError):
        return None


def _pr_argv(pid: int) -> list[str]:
    """A process's argv from /proc/<pid>/cmdline; empty on any error."""
    if _PR_DARWIN:
        return _pd_argv(pid)
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            data = fh.read()
    except OSError:
        return []
    return [tok.decode("utf-8", "replace") for tok in data.split(b"\x00") if tok]


def _pr_cwd(pid: int) -> str:
    if _PR_DARWIN:
        return _pd_cwd(pid)
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _pr_environ(pid: int, keys: tuple[str, ...]) -> dict[str, str]:
    """The variables named in ``keys`` from /proc/<pid>/environ. The rest is never kept: it holds secrets."""
    if _PR_DARWIN:
        return _pd_environ(pid, keys)
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            data = fh.read()
    except OSError:
        return {}
    found: dict[str, str] = {}
    for item in data.split(b"\0"):
        key, sep, value = item.partition(b"=")
        name = key.decode("utf-8", "replace")
        if sep and name in keys:
            found[name] = value.decode("utf-8", "replace")
    return found


def _pr_boot_id() -> str:
    if _PR_DARWIN:
        return _pd_boot_id()
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""
