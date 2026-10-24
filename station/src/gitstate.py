"""Git work state of an idle or closed session's directory: uncommitted, unpushed, unmerged.

Read-only git calls, 2 s each, no prompts, no fetch. A result is reused for 30 s while HEAD, the
index and the branch ref keep their mtimes; one snapshot makes at most 64 git calls and takes at
most 1.5 s, and a worktree is read once however many stages sit in it.
"""
from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass

from model import Stage, WorkState

_GS_TTL_S = 30.0
_GS_TIMEOUT_S = 2.0
_GS_DEADLINE_S = 1.5     # wall-clock budget for one snapshot, however many stages it covers
_GS_MAX_CALLS = 64
_GS_STATES = frozenset({"idle", "closed"})
_GS_DEFAULT_BRANCHES = ("main", "master")
_GS_SKIP_DIRS = frozenset({".stage", ".sessions"})   # untracked tool state, never a session's own work


class _GsBudget(Exception):
    """The snapshot has no git calls or no wall-clock time left; the stage keeps its last answer."""


class _GsCalls:
    """The git calls one snapshot may still make, and the worktrees it has already read."""

    def __init__(self) -> None:
        self.left = _GS_MAX_CALLS
        self.deadline = time.monotonic() + _GS_DEADLINE_S
        self.shared: dict[str, _GsEntry] = {}


@dataclass
class _GsEntry:
    gitdir: str                  # "" when the path is not inside a worktree
    branch: str
    mtimes: tuple[float, ...]    # HEAD, index, branch ref at check time; () when not a worktree
    state: WorkState | None
    checked: float


_gs_cache: dict[str, _GsEntry] = {}


def work_state(path: str, now: float) -> WorkState | None:
    """Git state of the worktree at path; None when path is not inside one.

    A snapshot with nothing left to spend answers with the cached result, however old, rather than
    with nothing.
    """
    try:
        return _gs_state(path, now, _GsCalls())
    except _GsBudget:
        return _gs_cached(path)


def attach_work(stages: Iterable[Stage], now: float) -> None:
    """Set stage.work for idle and closed stages. Stalest cache entries go first, so every stage gets its turn."""
    todo = [(stage, _gs_dir(stage)) for stage in stages if stage.state in _GS_STATES]
    todo.sort(key=lambda item: _gs_checked(item[1]))
    calls = _GsCalls()
    for stage, path in todo:
        try:
            stage.work = _gs_state(path, now, calls)
        except _GsBudget:
            stage.work = _gs_cached(path)


def _gs_dir(stage: Stage) -> str:
    """The directory to check: the session's cwd, else its stage dir, else the stage path itself."""
    if stage.cwd:
        return stage.cwd
    if stage.dir:
        return stage.dir
    path = stage.path.removesuffix("/.stage")
    return path if os.path.isdir(path) else ""


def _gs_checked(path: str) -> float:
    entry = _gs_cache.get(path)
    return entry.checked if entry is not None else 0.0


def _gs_cached(path: str) -> WorkState | None:
    entry = _gs_cache.get(path)
    return entry.state if entry is not None else None


def _gs_state(path: str, now: float, calls: _GsCalls) -> WorkState | None:
    if not path:
        return None
    entry = _gs_cache.get(path)
    if entry is not None and now - entry.checked < _GS_TTL_S and entry.mtimes == _gs_mtimes(entry.gitdir, entry.branch):
        return entry.state
    entry = _gs_fresh(path, now, calls)
    _gs_cache[path] = entry
    return entry.state


def _gs_fresh(path: str, now: float, calls: _GsCalls) -> _GsEntry:
    rc, out = _gs_git(path, ["rev-parse", "--absolute-git-dir"], calls)
    gitdir = out.strip() if rc == 0 else ""
    if not gitdir:
        return _GsEntry("", "", (), None, now)
    first = calls.shared.get(gitdir)   # another stage of this snapshot already read this worktree
    if first is not None:
        return _GsEntry(gitdir, first.branch, first.mtimes, first.state, now)
    rc, out = _gs_git(path, ["rev-parse", "--abbrev-ref", "HEAD"], calls)
    branch = out.strip() if rc == 0 else ""
    rc, out = _gs_git(path, ["status", "--porcelain", "--untracked-files=normal"], calls)
    dirty = _gs_any_change(out) if rc == 0 else None
    rc, out = _gs_git(path, ["rev-list", "--count", "@{u}..HEAD"], calls)
    ahead = _gs_count(rc, out)
    unmerged = None
    default = _gs_default(path, calls) if branch else None
    if default and branch != default:
        rc, out = _gs_git(path, ["rev-list", "--count", f"{default}..HEAD"], calls)
        ahead_of_default = _gs_count(rc, out)
        unmerged = None if ahead_of_default is None else ahead_of_default > 0
    state = WorkState(branch=branch, dirty=dirty, ahead=ahead, unmerged=unmerged, checked=now)
    entry = _GsEntry(gitdir, branch, _gs_mtimes(gitdir, branch), state, now)
    calls.shared[gitdir] = entry
    return entry


def _gs_any_change(porcelain: str) -> bool:
    """Whether ``git status --porcelain`` (untracked files listed) shows any line but our own tool state."""
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        if line.startswith("??") and _gs_tool_state(line[3:]):
            continue
        return True
    return False


def _gs_tool_state(path: str) -> bool:
    """True for an untracked path under a ``.stage`` or ``.sessions`` directory: status's own scratch, not work."""
    path = path.strip().strip('"')
    return any(part in _GS_SKIP_DIRS for part in path.replace("\\", "/").split("/"))


def _gs_default(path: str, calls: _GsCalls) -> str | None:
    refs = [f"refs/heads/{name}" for name in _GS_DEFAULT_BRANCHES]
    _, out = _gs_git(path, ["for-each-ref", "--format=%(refname:short)", *refs], calls)
    found = set(out.split())
    return next((name for name in _GS_DEFAULT_BRANCHES if name in found), None)


def _gs_count(rc: int, out: str) -> int | None:
    text = out.strip()
    return int(text) if rc == 0 and text.isdigit() else None


def _gs_git(path: str, args: list[str], calls: _GsCalls) -> tuple[int, str]:
    """One git call as (exit code, stdout); 1 with no output when git cannot run, is out of budget or times out."""
    if calls.left <= 0 or time.monotonic() >= calls.deadline:
        raise _GsBudget()
    calls.left -= 1
    env = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        done = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, errors="replace",
                              stdin=subprocess.DEVNULL, env=env, timeout=_GS_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return done.returncode, done.stdout


def _gs_mtimes(gitdir: str, branch: str) -> tuple[float, ...]:
    """Mtimes of HEAD, the index and the branch ref: they move on commit, checkout and staging."""
    if not gitdir:
        return ()
    names = ("HEAD", "index", os.path.join("refs", "heads", branch))
    return tuple(_gs_mtime(os.path.join(gitdir, name)) for name in names)


def _gs_mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0
