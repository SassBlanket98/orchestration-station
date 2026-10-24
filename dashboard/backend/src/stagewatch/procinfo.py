"""Read-only /proc helpers. No signals, no subprocesses, no writes."""

import os
from pathlib import Path

PROC = Path("/proc")
MAX_CHILD_DEPTH = 3
_CLK_TCK = os.sysconf("SC_CLK_TCK")


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


_boot_cache: list[float] = []


def _boot_time() -> float | None:
    """Boot epoch from /proc/stat, caching only successful reads: a transient
    /proc/stat failure must not disable the PID-reuse guard for good."""
    if _boot_cache:
        return _boot_cache[0]
    text = _read_text(PROC / "stat")
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("btime "):
            _boot_cache.append(float(line.split()[1]))
            return _boot_cache[0]
    return None


def _stat_fields(pid: int) -> list[str] | None:
    text = _read_text(PROC / str(pid) / "stat")
    if text is None:
        return None
    # comm (field 2) can hold spaces and parens, so split after the last ')'.
    # The remaining fields start at field 3 (state).
    return text[text.rfind(")") + 2 :].split()


def start_time(pid: int) -> float | None:
    """Epoch seconds when the process started, or None if it is gone."""
    if pid <= 0:
        return None
    fields = _stat_fields(pid)
    boot = _boot_time()
    if fields is None or boot is None:
        return None
    try:
        ticks = int(fields[19])  # field 22, starttime, in clock ticks since boot
    except (IndexError, ValueError):
        return None
    return boot + ticks / _CLK_TCK


def is_alive(pid: int, not_after: float | None) -> bool:
    """True when /proc/<pid> exists and the process started at or before `not_after`.

    `not_after` guards against PID reuse: a pid file written at time T cannot
    belong to a process that started after T, so a later start means reuse.
    """
    if pid <= 0 or not (PROC / str(pid)).is_dir():
        return False
    if not_after is None:
        return True
    started = start_time(pid)
    return started is not None and started <= not_after


def _argv(pid: int) -> list[str]:
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]


def _child_map() -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        fields = _stat_fields(int(entry.name))
        if fields is None or len(fields) < 2:
            continue
        try:
            ppid = int(fields[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(int(entry.name))
    return children


def _effort_from(argv: list[str]) -> str | None:
    for i, tok in enumerate(argv):
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if tok == "--effort" and nxt:
            return nxt
        if tok.startswith("--effort="):
            return tok.split("=", 1)[1] or None
        if "model_reasoning_effort=" in tok:
            return tok.split("model_reasoning_effort=", 1)[1].strip("\"' ") or None
    return None


def _model_from(argv: list[str]) -> str | None:
    for i, tok in enumerate(argv):
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if tok in ("-m", "--model") and nxt:
            return nxt
        if tok.startswith("--model=") and tok.split("=", 1)[1]:
            return tok.split("=", 1)[1]
    return None


def model_from_argv(pid: int) -> str | None:
    """Model from the process or a child (depth <= 3) command line.

    Looks at `-m`, `--model` and `--model=`. Effort comes from `--effort` or
    `model_reasoning_effort=`, and is appended as "model (effort)".
    """
    if pid <= 0:
        return None
    children: dict[int, list[int]] | None = None
    frontier = [pid]
    for _depth in range(MAX_CHILD_DEPTH + 1):
        next_frontier: list[int] = []
        for current in frontier:
            argv = _argv(current)
            model = _model_from(argv)
            if model is not None:
                effort = _effort_from(argv)
                return f"{model} ({effort})" if effort else model
            if children is None:
                children = _child_map()
            next_frontier.extend(children.get(current, []))
        frontier = next_frontier
        if not frontier:
            break
    return None
