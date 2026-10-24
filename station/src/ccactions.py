"""Control center: the actions behind the confirm line. Stop / kill a process, spawn a worker, resume a session.
Nothing here runs without a caller having shown `confirm_text` and got `y`. Every run is appended to
`$STATUS_ACTIONS_LOG`, else Station's ledger under STATION_HOME. Imports model and standard library
only. The runner arguments let tests replace os.kill / subprocess; real sessions are never touched in tests."""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from model import LIVE_STATES, Stage, Worker, home_path, optional_cmd

ACTIONS = ("stop", "kill", "resume", "spawn")

_CX_STOP_GRACE_S = 3.0        # kill is offered this long after a stop went to the same pid
_CX_MIN_HEADROOM = 5.0        # percent; a subscription tighter than this is greyed
_CX_CONFIRM_MAX = 99          # the confirm line must fit one row, under 100 chars
_CX_TMUX_MAX = 64             # tmux session name limit
_CX_BRIEF_HEAD = 60           # title / goal cut in a dispatch brief
_CX_QUOTA_BIN = home_path("bin/quota")
_CX_GUARDS = home_path("picker/guards.json")
_CX_MODELS = home_path("picker/models.json")
_CX_LAUNCH_SESSION = home_path("bin/launch-session")
_CX_DISPATCH = home_path("bin/dispatch")
_CX_LOGFILE = home_path("ledger/status-actions.jsonl")
_CX_TAGS = (("cc", "claude"), ("claude", "claude"), ("cdx", "codex"), ("codex", "codex"),
            ("ocode", "opencode"), ("opencode", "opencode"), ("oclaw", "openclaw"), ("openclaw", "openclaw"),
            ("cur", "cursor"), ("cursor", "cursor"))
_CX_READ_ERRORS = (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError)
_CX_RESUME = re.compile(r"(codex resume|claude --resume) ([A-Za-z0-9][A-Za-z0-9._:-]{0,127})")
_CX_STOPS: dict[int, float] = {}   # pid -> time a stop was sent to it in this process
_CX_LAUNCHED: list[subprocess.Popen] = []   # launches are never waited on; old ones are polled so they do not pile up
_cac_PIDFD = hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")   # Linux 5.3+; tests flip it


@dataclass
class Target:
    """What an action acts on: one row, as plain data."""
    kind: str                            # "stage" (a lead/session row) | "worker"
    label: str
    pid: int | None = None
    harness: str = ""                    # "codex" | "claude" | "opencode" | ...
    session_id: str = ""
    tmux: str = ""
    cwd: str = ""
    resume_cmd: str = ""
    live: bool = False                   # the process is running now
    start: int | None = None             # /proc starttime read with the pid, so a recycled pid is caught


@dataclass
class Choice:
    """One line of the spawn picker."""
    id: str                              # picker id, e.g. "deepseek"
    label: str
    ok: bool                             # False = listed greyed
    reason: str = ""                     # why it is greyed, e.g. "not allowed", "headroom 3%"


# ── /proc reads: the only place a pid is trusted ─────────────────────────────────────────────────────

def _cac_stat(pid: int) -> tuple[str, int, int] | None:
    """(state, ppid, starttime) of a pid from /proc, or None when it is gone. comm may hold spaces."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        rest = raw[raw.rindex(")") + 1:].split()
        return rest[0], int(rest[1]), int(rest[19])
    except (OSError, ValueError, IndexError):
        return None


def _cac_start(pid: int | None) -> int | None:
    """The pid's starttime in clock ticks, or None."""
    if not pid:
        return None
    stat = _cac_stat(pid)
    return None if stat is None else stat[2]


def _cac_uid(pid: int) -> int | None:
    """The real uid that owns a pid: /proc/<pid>/status, else the owner of /proc/<pid>."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("Uid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return None


def _cac_ancestor(pid: int) -> bool:
    """True when pid is this process or sits above it (the terminal, its shell, the TUI): never signal those."""
    seen: set[int] = set()
    cur: int | None = os.getpid()
    while cur and cur not in seen:
        if cur == pid:
            return True
        seen.add(cur)
        stat = _cac_stat(cur)
        cur = None if stat is None else stat[1]
    return False


def _cac_harness(*hints: str) -> str:
    """The harness name from the first usable tag/kind/family hint ("CC·W", "CDX", "OCode", "claude")."""
    for hint in hints:
        text = (hint or "").strip().lower()
        if not text:
            continue
        for tag, name in _CX_TAGS:
            if text.startswith(tag):
                return name
    return ""


def _cac_signal_pid(pid: int) -> int:
    """The pid to hand os.kill: -pid when it leads its own process group and that group is not ours."""
    try:
        pgid, mine = os.getpgid(pid), os.getpgid(0)
    except OSError:
        return pid
    if pgid == pid and pgid > 1 and pgid != mine:
        return -pid
    return pid


def _cac_sig_error(exc: OSError) -> str:
    """The refusal text for a failed signal: a vanished pid reads as gone, anything else as a failure."""
    if isinstance(exc, ProcessLookupError):
        return "process already gone"
    return f"signal failed: {exc.strerror or exc}"


def _cac_pidfd_signal(pid: int, start: int | None, sig: int) -> str | None:
    """Signal a pid through a pidfd, returning the refusal reason or None when it went. The fd is opened first,
    so it pins whichever process holds the pid; the /proc start time is re-read after it and must still match.
    A process group has no fd, so a group leader is only checked through the pidfd and then signalled with killpg:
    the group id stays unpinned, so a pid recycled as a new group leader in that last gap could still be hit."""
    try:
        fd = os.pidfd_open(pid)
    except OSError as exc:
        return _cac_sig_error(exc)
    try:
        stat = _cac_stat(pid)
        if stat is None:
            return "process already gone"
        if start is not None and stat[2] != start:
            return "pid changed"
        try:
            if _cac_signal_pid(pid) < 0:
                os.killpg(pid, sig)
            else:
                signal.pidfd_send_signal(fd, sig)
        except OSError as exc:
            return _cac_sig_error(exc)
        return None
    finally:
        os.close(fd)


def _cac_numeric_signal(pid: int, start: int | None, sig: int) -> str | None:
    """The fallback for a platform without pidfd: re-read /proc, then a numeric os.kill. The read and the kill are
    not atomic, so a pid recycled in between would still be signalled: that is the old window, kept only here."""
    stat = _cac_stat(pid)
    if stat is None:
        return "process already gone"
    if start is not None and stat[2] != start:
        return "pid changed"
    try:
        os.kill(_cac_signal_pid(pid), sig)
    except OSError as exc:
        return _cac_sig_error(exc)
    return None


# ── Target ───────────────────────────────────────────────────────────────────────────────────────────

def target_of(stage: Stage, worker: Worker | None = None) -> Target:
    """The Target for a lead row (worker None) or a worker row."""
    cwd = stage.cwd or stage.dir or stage.path
    if worker is None:
        return Target(kind="stage", label=stage.label, pid=stage.lead_pid,
                      harness=_cac_harness(stage.harness, stage.tag, stage.lead_kind),
                      session_id=stage.session_id, tmux=stage.tmux, cwd=cwd,
                      resume_cmd=stage.resume_cmd, live=stage.lead_state in ("live", "background"),
                      start=_cac_start(stage.lead_pid))
    return Target(kind="worker", label=worker.name, pid=worker.pid,
                  harness=_cac_harness(worker.harness, worker.family),
                  session_id=worker.native_id, cwd=cwd, resume_cmd=worker.resume_cmd,
                  live=worker.state in LIVE_STATES, start=_cac_start(worker.pid))


# ── can ──────────────────────────────────────────────────────────────────────────────────────────────

def can(action: str, target: Target, *, now: float | None = None,
        uid: Callable[[int], int | None] | None = None) -> tuple[bool, str]:
    """Whether `action` is offered for `target`, and why not. stop/kill need a live pid of this user that is not
    pid 1, this process or one of its ancestors; kill only after a stop was sent or when the row is stuck; resume needs
    resume_cmd and no live process."""
    if action not in ACTIONS:
        return False, "unknown action"
    if action == "spawn":
        if target.cwd and os.path.isdir(target.cwd):
            return True, ""
        return False, "no such directory"
    if action == "resume":
        if not target.resume_cmd.strip():
            return False, "no resume command"
        if _cac_resume_parts(target.resume_cmd) is None:
            return False, "unsafe resume command"
        if target.live:
            return False, "session is running"
        return True, ""
    pid = target.pid
    if not pid:
        return False, "no pid"
    if not target.live:
        return False, "not running"
    if pid == 1:
        return False, "pid 1 is not ours"
    if pid == os.getpid():
        return False, "that is this terminal"
    if not os.path.isdir("/proc"):
        return False, "no /proc on this machine"
    stat = _cac_stat(pid)
    if stat is None:
        return False, "process is gone"
    owner = (uid or _cac_uid)(pid)
    if owner is None:
        return False, "cannot check the owner"
    if owner != os.getuid():
        return False, "another user's process"
    if _cac_ancestor(pid):
        return False, "that is this terminal"
    if target.start is not None and stat[2] != target.start:
        return False, "pid was reused"
    if action == "kill":
        stopped = _CX_STOPS.get(pid)
        if stopped is None:
            if stat[0] in ("D", "Z"):     # stuck in the kernel or already a zombie: stop cannot land
                return True, ""
            return False, "send stop first"
        waited = (time.time() if now is None else now) - stopped
        if waited < _CX_STOP_GRACE_S:
            return False, f"stop was {waited:.0f}s ago, wait {_CX_STOP_GRACE_S:.0f}s"
    return True, ""


# ── confirm line ─────────────────────────────────────────────────────────────────────────────────────

def _cac_cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


def confirm_text(action: str, target: Target) -> str:
    """The one-line question shown on the confirm line, ending in "(y/n)"."""
    label = _cac_cut(target.label or "(row)", 40)
    if action in ("stop", "kill"):
        pid = f" (pid {target.pid})" if target.pid else ""
        line = (f"Stop {label}{pid}?  any other key cancels (y/n)" if action == "stop"
                else f"Kill {label}{pid}?  SIGKILL, any other key cancels (y/n)")
        return line if len(line) <= _CX_CONFIRM_MAX else _cac_cut(line, _CX_CONFIRM_MAX)
    if action == "resume":
        head = f"Resume {label} in a new terminal: "
        tail = "?  (y/n)"
        room = max(8, _CX_CONFIRM_MAX - len(head) - len(tail) - 1)
        return head + _cac_cut(target.resume_cmd, room) + tail
    if action == "spawn":
        line = f"Spawn {label} in {target.cwd}?  (y/n)"
        return line if len(line) <= _CX_CONFIRM_MAX else _cac_cut(line, _CX_CONFIRM_MAX)
    return ""


# ── actions log ─────────────────────────────────────────────────────────────────────────────────────

def _cac_log(record: dict[str, object]) -> None:
    """Append one JSON line; a missing or read-only ledger is not worth failing an action over."""
    path = os.environ.get("STATUS_ACTIONS_LOG") or _CX_LOGFILE
    try:
        path = os.path.expanduser(path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def _cac_result(action: str, target: Target, text: str, ts: float) -> str:
    """Log and return the one-line result. Never carries prompt text: only row fields."""
    _cac_log({"ts": round(ts, 3), "action": action, "label": target.label, "pid": target.pid,
             "harness": target.harness, "session_id": target.session_id, "result": text})
    return text


# ── run ─────────────────────────────────────────────────────────────────────────────────────────────

def _cac_tmux_name(name: str) -> str:
    """A safe tmux session name: the allowed charset only, at most 64 chars."""
    clean = re.sub(r"[^A-Za-z0-9._:-]", "-", name).strip("-")
    return (clean or "cc")[:_CX_TMUX_MAX]


def _cac_popen(argv: list[str], cwd: str, log: str = "") -> subprocess.Popen:
    """The default launch: detached, never waited on. `log` collects the child's output when given."""
    _CX_LAUNCHED[:] = [proc for proc in _CX_LAUNCHED if proc.poll() is None]
    if not log:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "a", encoding="utf-8") as out:
            proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
    _CX_LAUNCHED.append(proc)
    return proc


def _cac_resume_parts(cmd: str) -> tuple[str, str, str] | None:
    """(executable, flag, id) when the stored command is exactly `codex resume <id>` or `claude --resume <id>` with a
    plain one-token id; None otherwise. Only these two shapes are accepted, so no stored option can be smuggled in."""
    found = _CX_RESUME.fullmatch(cmd or "")
    if found is None:
        return None
    exe, flag = found.group(1).split()
    return exe, flag, found.group(2)


def _cac_resume_argv(target: Target) -> list[str]:
    """The visible-terminal argv for a resume: `<terminal> -e tmux new-session -A -s <name> -c <cwd> <cmd...>`.
    The argv is built from the validated parts, never from a general split; a shell never sees it."""
    parts = _cac_resume_parts(target.resume_cmd)
    if parts is None:
        raise ValueError("unsafe resume command")
    cwd = target.cwd
    if not cwd or not os.path.isdir(cwd):
        raise ValueError("no such directory")
    terminal = os.environ.get("LAUNCH_TERMINAL") or "ghostty"
    name = _cac_tmux_name("resume-" + (target.label or "row"))
    return [terminal, "-e", "tmux", "new-session", "-A", "-s", name, "-c", cwd, *parts]


def run(action: str, target: Target, now: float | None = None, *, kill: Callable[[int, int], None] | None = None,
        launch: Callable[[list[str], str], None] | None = None) -> str:
    """Do a confirmed stop, kill or resume and return a one-line result for ui.note. Re-checks `can` and re-reads /proc
    for the pid right before signalling. Appends to the actions log whatever the outcome."""
    ts = time.time() if now is None else now
    ok, why = can(action, target, now=ts)
    if not ok:
        return _cac_result(action, target, f"refused: {why}", ts)
    if action == "spawn":
        return _cac_result(action, target, "refused: spawn goes through spawn()", ts)
    if action in ("stop", "kill"):
        pid = int(target.pid or 0)
        stat = _cac_stat(pid)
        if stat is None:
            return _cac_result(action, target, "refused: process is gone", ts)
        if target.start is not None and stat[2] != target.start:
            return _cac_result(action, target, "refused: pid was reused", ts)
        sig = signal.SIGTERM if action == "stop" else signal.SIGKILL
        if kill is not None:                       # tests: the injected callable is used exactly as before
            try:
                kill(_cac_signal_pid(pid), sig)
            except OSError as exc:
                why = f"signal failed: {exc.strerror or exc}"
            else:
                why = None
        elif _cac_PIDFD:
            why = _cac_pidfd_signal(pid, target.start, sig)
        else:
            why = _cac_numeric_signal(pid, target.start, sig)
        if why is not None:
            return _cac_result(action, target, f"refused: {why}", ts)
        if action == "stop":
            _CX_STOPS[pid] = ts
        done = "stopped" if action == "stop" else "killed"
        return _cac_result(action, target, f"{done} {target.label} ({signal.Signals(sig).name}, pid {pid})", ts)
    try:
        argv = _cac_resume_argv(target)
        (launch or _cac_popen)(argv, target.cwd)
    except (OSError, ValueError) as exc:
        return _cac_result(action, target, f"refused: {exc}", ts)
    return _cac_result(action, target, f"resumed {target.label} (tmux {argv[argv.index('-s') + 1]})", ts)


# ── spawn choices ───────────────────────────────────────────────────────────────────────────────────

def _cac_json(path: str) -> dict:
    with open(os.path.expanduser(path), encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise TypeError(f"not a JSON object: {path}")
    return data


def _cac_default_runner(argv: list[str]) -> str:
    """`quota status --json` stdout. Read-only; never a shell."""
    done = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    if done.returncode != 0:
        raise OSError(f"{argv[0]} exited {done.returncode}")
    return done.stdout


def _cac_pct(value: object) -> float:
    """A percentage or balance as float; junk reads as 0.0 so one bad row cannot sink the picker."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _cac_job_reason(model: dict) -> str:
    """Why a model may not build a job, "" when it may."""
    only = model.get("onlyJobs")
    if isinstance(only, list) and only and "build" not in only:
        return f"{only[0]} only"
    never = model.get("neverJobs")
    if isinstance(never, list) and "build" in never:
        return "never builds"
    return ""


def _cac_fit(sub: str, limits: list, credits: dict, pay: dict, meta: dict,
            floor_pct: float) -> tuple[bool, str, str]:
    """(ok, reason, label tail) for a subscription's headroom: credits first, then its tightest window."""
    rule = pay.get(sub) if isinstance(pay, dict) else None
    if isinstance(rule, dict):
        account = str(rule.get("creditAccount") or "")
        floor = _cac_pct(rule.get("minBalanceUsd"))
        entry = credits.get(account)
        balance = entry.get("balanceUsd") if isinstance(entry, dict) else None
        if not isinstance(balance, (int, float)):
            return False, "credits unknown", (f" {account}" if account else "")
        tail = f" {account} ${balance:.2f}"
        return balance >= floor, "" if balance >= floor else f"credits ${balance:.2f}", tail
    if limits:
        worst = max(limits, key=lambda win: _cac_pct(win.get("usedPct")))
        used = _cac_pct(worst.get("usedPct"))
        head = 100.0 - used
        tail = f" {worst.get('name') or worst.get('window') or ''} {used:.0f}%"
        if head < floor_pct:
            return False, f"headroom {head:.0f}%", tail
        return True, "", tail
    if meta.get("unmetered") or meta.get("credits"):
        return True, "", ""
    return False, "no quota data", ""


def spawn_choices(runner: Callable[[list[str]], str] | None = None, *, guards_path: str | None = None,
                  models_path: str | None = None) -> list[Choice]:
    """The spawn picker: every id in $STATION_HOME/picker/guards.json allowedModels, ok=False with a reason for models under
    5% headroom or never-jobs, using `quota pick`/`quota status` output (runner replaces the subprocess)."""
    guards_path = guards_path or _CX_GUARDS
    models_path = models_path or _CX_MODELS
    if not os.path.isfile(os.path.expanduser(guards_path)) or not os.path.isfile(os.path.expanduser(models_path)):
        return []
    quota_cmd = optional_cmd(_CX_QUOTA_BIN)
    if runner is None and quota_cmd is None:
        return []
    try:
        guards = _cac_json(guards_path)
        models = _cac_json(models_path)
        allowed = (guards.get("allowedModels") or {}).get("models")
        if not isinstance(allowed, list):
            raise TypeError("guards.json has no allowedModels.models")
        entries = {str(mod["id"]): mod for mod in models.get("models") or []
                   if isinstance(mod, dict) and isinstance(mod.get("id"), str)}
        subs = models.get("subscriptions") if isinstance(models.get("subscriptions"), dict) else {}
        status = json.loads((runner or _cac_default_runner)([quota_cmd or _CX_QUOTA_BIN, "status", "--json"]))
        if not isinstance(status, dict):
            raise TypeError("quota status is not an object")
        windows = {str(row["id"]): [win for win in row.get("limits") or [] if isinstance(win, dict)]
                   for row in status.get("subscriptions") or [] if isinstance(row, dict) and "id" in row}
        credits = {str(row["account"]): row for row in status.get("credits") or []
                   if isinstance(row, dict) and "account" in row}
        pay = guards.get("payPerToken") if isinstance(guards.get("payPerToken"), dict) else {}
        floor_pct = float(guards.get("minHeadroomPct") or _CX_MIN_HEADROOM)
    except FileNotFoundError:
        return []
    except _CX_READ_ERRORS:
        return [Choice(id="", label="picker unavailable", ok=False)]
    out: list[Choice] = []
    for mid in allowed:
        if not isinstance(mid, str) or not mid:
            continue
        model = entries.get(mid)
        if model is None:
            out.append(Choice(id=mid, label=f"{mid}  unknown model", ok=False, reason="not in models.json"))
            continue
        sub = str(model.get("subscription") or "")
        meta = subs.get(sub) if isinstance(subs.get(sub), dict) else {}
        reason = _cac_job_reason(model)
        if reason:
            out.append(Choice(id=mid, label=f"{mid}  {sub}".strip(), ok=False, reason=reason))
            continue
        ok, why, tail = _cac_fit(sub, windows.get(sub) or [], credits, pay, meta, floor_pct)
        out.append(Choice(id=mid, label=f"{mid}  {sub}{tail}".strip(), ok=ok, reason=why))
    if runner is None:
        available = {model_id: model for model_id, model in entries.items()
                     if optional_cmd(_CX_LAUNCH_SESSION if model.get("cli") == "claude" else _CX_DISPATCH)}
        out = [choice for choice in out if choice.id in available]
    return sorted(out, key=lambda choice: not choice.ok)


# ── spawn ───────────────────────────────────────────────────────────────────────────────────────────

def _cac_brief(model_id: str, task: str, project_dir: str) -> str:
    """The dispatch brief: the fixed header dispatch reads, then the task text as typed."""
    title = _cac_cut(task, _CX_BRIEF_HEAD)
    return (f"# {title}\n\nGoal: {title}\nJob: build\nModel: {model_id}\nMode: write\nCwd: {project_dir}\n\n{task}\n")


def _cac_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")


def spawn(model_id: str, project_dir: str, task_text: str, now: float | None = None, *,
          launch: Callable[[list[str], str], None] | None = None, choices: list[Choice] | None = None,
          runner: Callable[[list[str]], str] | None = None, guards_path: str | None = None,
          models_path: str | None = None) -> str:
    """Start a new worker/session through dispatch / launch-session with the chosen model; refuses ids that are not ok."""
    ts = time.time() if now is None else now
    project_dir = os.path.abspath(os.path.expanduser(project_dir or "."))

    def refuse(why: str, cli: str = "", name: str = "") -> str:
        _cac_log({"ts": round(ts, 3), "action": "spawn", "label": str(model_id or ""), "pid": None,
                 "harness": cli, "session_id": name, "result": f"refused: {why}"})
        return f"refused: {why}"

    picks = choices if choices is not None else spawn_choices(
        runner, guards_path=guards_path, models_path=models_path)
    pick = next((choice for choice in picks if choice.id == model_id and choice.id), None)
    if pick is None:
        return refuse(f"unknown model {model_id!r}")
    if not pick.ok:
        return refuse(f"{model_id}: {pick.reason or 'not allowed'}")
    try:
        models = _cac_json(models_path or _CX_MODELS)
    except _CX_READ_ERRORS:
        return refuse("models.json unreadable")
    entry = next((mod for mod in models.get("models") or []
                  if isinstance(mod, dict) and mod.get("id") == model_id), None)
    if entry is None:
        return refuse(f"{model_id} is not in models.json")
    cli = str(entry.get("cli") or "")
    if not os.path.isdir(project_dir):
        return refuse(f"no such directory: {project_dir}", cli)
    task = task_text.strip()
    if not task:
        return refuse("empty task", cli)
    if not cli:
        return refuse(f"{model_id} has no cli", cli)
    if launch is None:
        required = _CX_LAUNCH_SESSION if cli == "claude" else _CX_DISPATCH
        if optional_cmd(required) is None:
            return refuse(f"{os.path.basename(required)} is unavailable", cli)
    name = "cc-" + secrets.token_hex(3)
    try:
        if cli == "claude":
            brief = os.path.join(project_dir, ".stage", "ops-spawn", f"{name}.md")
            _cac_write(brief, task)
            alias = model_id.split("-")[0]
            argv = [optional_cmd(_CX_LAUNCH_SESSION) or _CX_LAUNCH_SESSION, "--model", alias, name, project_dir, brief]
            (launch or _cac_popen)(argv, project_dir)
            result = f"spawned {model_id} ({name}) as a claude session in {project_dir}"
        else:
            brief = os.path.join(project_dir, ".stage", "briefs", f"{name}.md")
            _cac_write(brief, _cac_brief(model_id, task, project_dir))
            stage = os.path.join(project_dir, ".stage")
            log = os.path.join(stage, f"dispatch-{name}.log")
            argv = [optional_cmd(_CX_DISPATCH) or _CX_DISPATCH, "run", stage, name]
            if launch is not None:
                launch(argv, project_dir)
            else:
                _cac_popen(argv, project_dir, log)
            result = f"spawned {model_id} ({name}) via dispatch in {project_dir}"
    except OSError as exc:
        return refuse(f"launch failed: {exc.strerror or exc}", cli, name)
    _cac_log({"ts": round(ts, 3), "action": "spawn", "label": model_id, "pid": None,
             "harness": cli, "session_id": name, "result": result})
    return result
