"""macOS process provider in the Linux table shape, read from ps, sysctl and lsof.

Every reader returns empty on any failure (missing binary, timeout, non-zero exit, odd output).
Known limits:
- argv is split on whitespace, so an argument with spaces comes back as several words (comm is
  then the first word of argv[0]); environ values with spaces are cut short.
- macOS 15 and later print no environment for other processes (`ps -E`), so every environ read is
  empty there: leads started with CLAUDE_CONFIG_DIR, CODEX_HOME or similar fall back to defaults.
- start times come from `lstart` (local wall clock, whole seconds); a process started in the
  repeated hour of a daylight-saving fall-back can be placed an hour off.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import zlib
from collections.abc import Iterable
from subprocess import DEVNULL

_PD_CLK = 100                  # clock ticks per second, as ps and the Linux shape count them
_PD_TIMEOUT = 5                # seconds per subprocess call
_PD_TTL = 1.0                  # seconds one process table scan is reused
_PD_FAIL_TTL = 10.0            # seconds a failed scan is reused before ps is tried again
_PD_CWD_MAX = 2000             # cwd cache entries; the cache is cleared when it is full
_PD_CWD_RETRY = 30.0           # seconds before a cwd lookup that failed (timeout, no lsof) is retried
_PD_ENV_MAX = 2000             # environ cache entries; cleared when full
_PD_TTY_BASE = 0x10000         # ttysNNN -> BASE + NNN (stable and non-zero)
_PD_TTY_OTHER = 0x20000        # other tty names -> OTHER + crc32 of the name (stable, non-zero)
_PD_LSTART_FMT = "%a %b %d %H:%M:%S %Y"
_PD_TIME_RE = re.compile(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)(?:\.(\d+))?$")
_PD_SEC_RE = re.compile(r"\bsec\s*=\s*(\d+)")
_PD_USEC_RE = re.compile(r"\busec\s*=\s*(\d+)")
_PD_TTY_DIGITS_RE = re.compile(r"(\d+)$")

# pid -> (ppid, utime, stime, start_ticks, tty, argv, rss_bytes)
_pd_table_memo: tuple[float, dict[int, tuple]] | None = None
_pd_boot_memo: float | None = None
_pd_cwd_memo: dict[tuple[int, int], tuple[str, float]] = {}   # (pid, start) -> (cwd, retry_at)
_pd_env_memo: dict[tuple[int, int, tuple[str, ...]], dict[str, str]] = {}
_pd_env_visible: bool | None = None   # does `ps -E` show any environment here? (macOS 15: no)


def _pd_run(argv: list[str], ok: tuple[int, ...] = (0,)) -> str | None:
    """Stdout of a command, or None on any error or an exit code outside `ok`.

    Output is decoded as UTF-8 with replacement: argv is arbitrary bytes, and one bad byte must not
    lose the whole table.
    """
    try:
        done = subprocess.run(argv, stdin=DEVNULL, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=_PD_TIMEOUT, check=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if done.returncode not in ok:
        return None
    return done.stdout


def _pd_parse_cpu_time(text: str) -> float | None:
    """Seconds from ps time: m:ss.cc, mm:ss.cc, h:mm:ss.cc or d-hh:mm:ss (fraction optional)."""
    m = _PD_TIME_RE.match(text.strip())
    if m is None:
        return None
    days, hours, minutes, seconds, frac = m.groups()
    total = int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    secs = float(total)
    if frac:
        secs += int(frac) / 10 ** len(frac)
    return secs


def _pd_parse_boottime(text: str) -> float | None:
    """Epoch seconds from `sysctl -n kern.boottime` ({ sec = N, usec = M } ...)."""
    m = _PD_SEC_RE.search(text)
    if m is None:
        return None
    usec = _PD_USEC_RE.search(text)
    return int(m.group(1)) + (int(usec.group(1)) / 1_000_000 if usec else 0.0)


def _pd_tty_num(tty: str) -> int:
    """A non-zero number for a controlling tty, 0 for none (`??` or `-`)."""
    if tty in ("", "??", "-"):
        return 0
    m = _PD_TTY_DIGITS_RE.search(tty)
    if m is not None:
        return _PD_TTY_BASE + int(m.group(1))
    return _PD_TTY_OTHER + zlib.crc32(tty.encode()) % 0x10000


def _pd_parse_table(text: str, boot: float | None) -> dict[int, tuple]:
    """Rows of `ps -axww -o pid=,ppid=,time=,rss=,tty=,lstart=,command=`, keyed by pid.

    The start tick is 0 when boot is unknown. Malformed lines are skipped, never raised on.
    """
    table: dict[int, tuple] = {}
    for line in text.splitlines():
        parts = line.split(None, 10)
        if len(parts) < 10:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            rss = int(parts[3])
            epoch = time.mktime(time.strptime(" ".join(parts[5:10]), _PD_LSTART_FMT))
        except (ValueError, OverflowError):
            continue
        cpu = _pd_parse_cpu_time(parts[2])
        if cpu is None:
            continue
        # The command is split on whitespace; argument spaces are lost (documented limit).
        argv = tuple(parts[10].split()) if len(parts) > 10 else ()
        start = 0 if boot is None else max(0, round((epoch - boot) * _PD_CLK))
        table[pid] = (ppid, round(cpu * _PD_CLK), 0, start, _pd_tty_num(parts[4]),
                      argv, rss * 1024)
    return table


def _pd_boot() -> float | None:
    """Boot time in epoch seconds; read once and kept for the process lifetime."""
    global _pd_boot_memo
    if _pd_boot_memo is None:
        out = _pd_run(["sysctl", "-n", "kern.boottime"])
        if out is not None:
            _pd_boot_memo = _pd_parse_boottime(out)
    return _pd_boot_memo


def _pd_table() -> dict[int, tuple]:
    """The full process table, reused for _PD_TTL seconds; a stale or empty memo is re-read."""
    global _pd_table_memo
    now = time.monotonic()
    memo = _pd_table_memo
    if memo is not None and now - memo[0] < _PD_TTL:
        return memo[1]
    out = _pd_run(["ps", "-axww", "-o", "pid=,ppid=,time=,rss=,tty=,lstart=,command="])
    if out is None:
        # ps failed: keep the empty table longer so a broken or slow ps is not retried every refresh
        _pd_table_memo = (now + _PD_FAIL_TTL - _PD_TTL, {})
        return {}
    rows = _pd_parse_table(out, _pd_boot())
    _pd_table_memo = (now, rows)
    return rows


def _pd_boot_time() -> float:
    """Boot time in epoch seconds; 0.0 when unknown."""
    return _pd_boot() or 0.0


def _pd_boot_id() -> str:
    """kern.boottime whole seconds: the same for one boot, different after a reboot; "" when unknown."""
    boot = _pd_boot()
    return str(int(boot)) if boot else ""


def _pd_scan() -> dict[int, tuple[int, int, int, int, int]]:
    """pid -> (ppid, utime, stime, start_ticks, tty) for every process, as the Linux reader does."""
    return {pid: row[:5] for pid, row in _pd_table().items()}


def _pd_resident(pid: int, page: int) -> int | None:
    """Resident set size in bytes; `page` is ignored (ps reports bytes already scaled)."""
    row = _pd_table().get(pid)
    return None if row is None else row[6]


def _pd_argv(pid: int) -> list[str]:
    """argv of a process, split on whitespace; [] when unknown."""
    row = _pd_table().get(pid)
    return [] if row is None else list(row[5])


def _pd_stat(pid: int) -> tuple[int, int, str] | None:
    """(ppid, start_ticks, comm) with comm the basename of argv[0]; None when unknown."""
    row = _pd_table().get(pid)
    if row is None:
        return None
    argv = row[5]
    comm = os.path.basename(argv[0]) if argv else ""
    return (row[0], row[3], comm)


def _pd_parse_env(text: str, keys: Iterable[str]) -> dict[str, str]:
    """Values of the requested KEY=VALUE tokens only; the last occurrence wins."""
    names = frozenset(keys)
    found: dict[str, str] = {}
    for token in text.split():
        name, sep, value = token.partition("=")
        if sep and name in names:
            found[name] = value
    return found


def _pd_environ(pid: int, keys: Iterable[str]) -> dict[str, str]:
    """The requested environment variables of a process (own processes only); {} otherwise."""
    global _pd_env_visible
    wanted = tuple(keys)
    if not wanted:
        return {}
    if _pd_env_visible is None:
        # One probe of our own process: macOS 15 prints no environment, so skip a ps per call there.
        probe = _pd_run(["ps", "-E", "-ww", "-o", "command=", "-p", str(os.getpid())])
        _pd_env_visible = probe is not None and "PATH=" in probe
    if not _pd_env_visible:
        return {}
    row = _pd_table().get(pid)
    key = (pid, row[3] if row is not None else -1, wanted)
    if key in _pd_env_memo:   # ps -E shows the environment at exec, so one read per process lifetime
        return dict(_pd_env_memo[key])
    out = _pd_run(["ps", "-E", "-ww", "-o", "command=", "-p", str(pid)])
    if out is None:
        return {}
    found = _pd_parse_env(out, wanted)
    if row is not None:   # no start time: not cached, so a reused pid cannot inherit it
        if len(_pd_env_memo) >= _PD_ENV_MAX:
            _pd_env_memo.clear()
        _pd_env_memo[key] = found
    return dict(found)


def _pd_parse_lsof_cwd(text: str) -> dict[int, str]:
    """pid -> cwd from `lsof -a -d cwd -Fn` output (p, f and n lines); pids may be several."""
    found: dict[int, str] = {}
    pid: int | None = None
    for line in text.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and len(line) > 1 and pid is not None and pid not in found:
            found[pid] = line[1:]
    return found


def _pd_cwd(pid: int) -> str:
    """Working directory of a process, cached per (pid, start_ticks).

    A miss looks up this pid and every other uncached pid in the table with one lsof, so the first
    snapshot costs one fork, not one per process. A process lsof cannot see (another user's) is
    cached as "" for its lifetime; a failed lsof (missing, timeout) is retried after _PD_CWD_RETRY.
    """
    table = _pd_table()
    now = time.monotonic()

    def key_of(p: int) -> tuple[int, int]:
        row = table.get(p)
        return (p, row[3] if row is not None else -1)

    def fresh(k: tuple[int, int]) -> bool:
        hit = _pd_cwd_memo.get(k)
        return hit is not None and now < hit[1]

    key = key_of(pid)
    if fresh(key):
        return _pd_cwd_memo[key][0]
    want = [pid] + [p for p in table if p != pid and not fresh(key_of(p))]
    # lsof exits 1 when some pids are gone or hidden but still prints the rest
    out = _pd_run(["lsof", "-a", "-d", "cwd", "-Fn", "-p", ",".join(map(str, want))], ok=(0, 1))
    if len(_pd_cwd_memo) + len(want) > _PD_CWD_MAX:
        _pd_cwd_memo.clear()
    found = {} if out is None else _pd_parse_lsof_cwd(out)
    # nothing at all came back (timeout, no lsof, or lsof read no pid): a failure for the whole batch,
    # retried later, so the next caller does not pay the same timeout again
    until = float("inf") if found else now + _PD_CWD_RETRY
    for p in want:
        if p in table:   # a pid missing from the table has no start time, so a reused pid could inherit it
            _pd_cwd_memo[key_of(p)] = (found.get(p, ""), until)
    return found.get(pid, "")
