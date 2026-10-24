"""Separate execution producer; never imported by the read-only dashboard."""
from __future__ import annotations

import argparse
import fcntl
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path

from model import ExecutionEvidence, ExecutionRecord
from registry import (
    MAX_ARTIFACT_BYTES,
    execution_key,
    read_execution_artifact,
    read_registry,
    registry_root,
    validate_execution,
)

__all__ = ["ingest_hook", "launch_execution", "read_execution_artifact", "read_registry", "reduce_execution", "register_main", "write_execution"]


def reduce_execution(record: ExecutionRecord | None, event: ExecutionEvidence) -> ExecutionRecord:
    incoming = event.record
    if record is None:
        return replace(incoming, revision=1)
    data = asdict(record)
    for key, value in asdict(incoming).items():
        if key in ("execution_id", "schema", "started", "registered", "revision", "state", "outcome", "ended"):
            continue
        present = value not in (None, "", 0, False) or key in ("exit_code", "context_tokens", "tokens_in", "tokens_out", "tokens_cache", "cost") and value is not None
        if present and not (key == "linkage" and value == "unknown"):
            data[key] = value
    for key in ("started", "registered"):
        values = [v for v in (getattr(record, key), getattr(incoming, key)) if v]
        data[key] = min(values) if values else 0.0
    data["activity"] = max(record.activity, incoming.activity)
    data["revision"] = record.revision + 1
    if event.event == "resume" and record.role == "lead":
        data.update(state="running", ended=None)
    elif record.ended is None:
        data["state"] = incoming.state
        if incoming.ended is not None:
            data.update(ended=incoming.ended, outcome=incoming.outcome, state=incoming.state)
    # Original launch purpose is immutable; changing tasks remains in native turn history.
    for key in ("goal", "title", "brief_ref", "purpose_source"):
        if getattr(record, key):
            data[key] = getattr(record, key)
    return validate_execution(data)


_SR_RECORD_LIMIT = 256 * 1024


def _sr_dir(path: Path, base: Path) -> None:
    # Refuse symlinks at the base and at every component between base and path.
    if base.is_symlink():
        raise ValueError("registry path is a symlink")
    here = base
    for part in path.relative_to(base).parts:
        here = here / part
        if here.is_symlink():
            raise ValueError("registry path is a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def _sr_read_previous(path: Path) -> ExecutionRecord | None:
    """Existing record, or None when it is oversized or invalid so it gets replaced."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(_SR_RECORD_LIMIT + 1)
        if len(raw) > _SR_RECORD_LIMIT:
            return None
        return validate_execution(json.loads(raw.decode("utf-8")))
    except (ValueError, TypeError, RecursionError):
        return None


def _sr_atomic(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_execution(root: str, data: dict, event: str = "observe") -> ExecutionRecord:
    payload = dict(data)
    brief, result = payload.pop("brief", ""), payload.pop("result", "")
    for artifact in (brief, result):
        if not isinstance(artifact, str) or len(artifact.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise ValueError("invalid or oversized execution artifact")
    incoming = validate_execution(payload)
    base = Path(root)
    _sr_dir(base, base)
    for folder in ("records", "locks", "artifacts"):
        _sr_dir(base / folder, base)
    identity = incoming.execution_id
    lock_fd = os.open(base / "locks" / (identity + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = base / "records" / (identity + ".json")
        previous = None
        if path.exists():
            if path.is_symlink():
                raise ValueError("record is a symlink")
            previous = _sr_read_previous(path)
        artifacts = base / "artifacts" / identity
        _sr_dir(artifacts, base)
        if brief and not (previous and previous.brief_ref):
            if not isinstance(brief, str):
                raise ValueError("invalid brief")
            _sr_atomic(artifacts / "brief.txt", brief)
            incoming.brief_ref = f"artifacts/{identity}/brief.txt"
        if result:
            if not isinstance(result, str):
                raise ValueError("invalid result")
            _sr_atomic(artifacts / "result.txt", result)
            incoming.result_ref = f"artifacts/{identity}/result.txt"
            incoming.output_available = True
        record = reduce_execution(previous, ExecutionEvidence(incoming, event))
        _sr_atomic(path, json.dumps(asdict(record), ensure_ascii=False, allow_nan=False))
        return record


def _sr_process_identity(pid: int) -> tuple[int | None, str]:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return int(raw[19]), Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (OSError, ValueError, IndexError):
        return None, ""


def launch_execution(root: str, metadata: dict, command: list[str]) -> int:
    if not command:
        raise ValueError("missing child command")
    now = time.time()
    data = {**metadata, "registered": now, "started": now}
    record = write_execution(root, data, "start")
    env = {**os.environ, "STATUS_REGISTRY_ROOT": root, "STATUS_EXECUTION_ID": record.execution_id,
           "STATUS_PARENT_EXECUTION_ID": record.parent_id, "STATUS_ROOT_EXECUTION_ID": record.root_id or record.execution_id}
    try:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, env=env)
    except OSError:
        write_execution(root, {**metadata, "ended": time.time(), "state": "finished", "outcome": "failed", "exit_code": 127}, "finish")
        return 127
    start, boot = _sr_process_identity(proc.pid)
    data = {k: v for k, v in metadata.items() if k != "brief"}
    write_execution(root, {**data, "pid": proc.pid, "pid_start": start, "boot_id": boot}, "start")
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        code = proc.wait()
    outcome = "success" if code == 0 else "cancelled" if code < 0 else "failed"
    write_execution(root, {**data, "ended": time.time(), "state": "finished", "outcome": outcome, "exit_code": code}, "finish")
    return code if code >= 0 else 128 - code


HARNESS_PROCESSES = frozenset({"claude", "codex", "cursor-agent", "opencode", "openclaw"})
CURSOR_ALLOW = json.dumps({"permission": "allow", "continue": True})


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _caller_process(max_depth: int = 8) -> tuple[int, int, str] | None:
    """Nearest ancestor whose comm is an allowlisted harness CLI; never cwd, env or full cmdline."""
    pid = os.getppid()
    for _ in range(max_depth):
        if pid <= 1:
            return None
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            if comm in HARNESS_PROCESSES:
                start, boot = _sr_process_identity(pid)
                return (pid, start, boot) if start is not None else None
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
    return None


def _lead_process(payload: dict) -> tuple[int, int, str] | None:
    raw = payload.get("pid")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        start, boot = _sr_process_identity(raw)
        return (raw, start, boot) if start is not None else None
    return _caller_process()


def ingest_hook(root: str, harness: str, account: str, payload: dict) -> ExecutionRecord | None:
    name = str(payload.get("hook_event_name", payload.get("event", "")))
    kind = name.lower()
    sid = str(payload.get("session_id", payload.get("conversation_id", payload.get("sessionID", ""))))
    # Any event carrying a native agent id belongs to that subagent, not to the lead session.
    child = "" if kind == "sessionstart" else _text(payload.get("agent_id")) or _text(payload.get("subagent_id"))
    parent_native = str(payload.get("parent_conversation_id", sid)) if child else ""
    if child:
        sid = child
    if not sid:
        return None
    identity = execution_key(harness, account, sid)
    explicit = os.environ.get("STATUS_EXECUTION_ID", "") if not child else ""
    if explicit:
        identity = explicit
    parent = execution_key(harness, account, parent_native) if parent_native else os.environ.get("STATUS_PARENT_EXECUTION_ID", "")
    prompt = payload.get("prompt", payload.get("task", ""))
    prompt = prompt if isinstance(prompt, str) else ""
    now = time.time()
    data = {"schema": 1, "execution_id": identity, "harness": harness, "account": account, "native_id": sid,
            "role": "worker" if child or explicit else "lead", "parent_id": parent,
            "root_id": os.environ.get("STATUS_ROOT_EXECUTION_ID", "") or parent or identity,
            "linkage": "native" if child else "registered" if parent else "unknown",
            "cwd": str(payload.get("cwd", "")), "registered": now, "started": now,
            "model": str(payload.get("model", payload.get("subagent_model", ""))),
            "current_action": str(payload.get("tool_name", name)), "state": "running"}
    if not child and not explicit:
        # Lead process identity comes from the payload pid or safe ancestry, never from cwd.
        found = _lead_process(payload)
        if found:
            data.update(pid=found[0], pid_start=found[1], boot_id=found[2])
    if prompt:
        data.update(brief=prompt, goal=" ".join(prompt.split())[:240], purpose_source="native task", activity=now)
    if kind in ("posttooluse", "posttoolusefailure", "afteragentresponse", "afteragentthought", "aftershellexecution"):
        data.update(activity=now, output_available=True)
    if kind in ("stop", "interrupt"):
        data["state"] = "idle"
    elif kind == "sessionend":
        data.update(state="closed", ended=now)
    elif kind == "subagentstop":
        # A callback ending is not in itself proof of model success.
        outcome = payload.get("status", payload.get("outcome", "unknown"))
        data.update(state="finished", ended=now, outcome="success" if outcome in ("success", "completed", "ok")
                    else "failed" if outcome in ("error", "failed") else "unknown")
        result = payload.get("last_assistant_message", payload.get("result", ""))
        if isinstance(result, str) and result:
            data["result"] = result
    return write_execution(root, data, "resume" if kind == "sessionstart" else "observe")


def register_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="station-register")
    parser.add_argument("--root", default=registry_root())
    subs = parser.add_subparsers(dest="command", required=True)
    ingest = subs.add_parser("ingest")
    ingest.add_argument("--harness", required=True)
    ingest.add_argument("--account", default="personal")
    for name in ("prepare", "launch"):
        sub = subs.add_parser(name)
        sub.add_argument("--record-file", required=True)
        if name == "launch":
            sub.add_argument("child", nargs=argparse.REMAINDER)
    words = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        # Cursor blocks on hook stdout, so a usage error on its ingest hook still answers neutrally.
        cursor_harness = any(a == "--harness" and b == "cursor" for a, b in itertools.pairwise(words)) or "--harness=cursor" in words
        if "ingest" in words and cursor_harness:
            print(CURSOR_ALLOW)
            return 0
        raise
    try:
        if args.command == "ingest":
            raw = sys.stdin.read(MAX_ARTIFACT_BYTES + 256 * 1024 + 1)
            if len(raw.encode("utf-8")) > MAX_ARTIFACT_BYTES + 256 * 1024:
                raise ValueError("oversized hook event")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("hook payload is not an object")
            ingest_hook(args.root, args.harness, args.account, payload)
            # Cursor permission hooks require a valid neutral allow response.
            if args.harness == "cursor":
                print(CURSOR_ALLOW)
            return 0
        data = json.loads(Path(args.record_file).read_text())
        if args.command == "prepare":
            write_execution(args.root, data, "start")
            return 0
        command = args.child[1:] if args.child[:1] == ["--"] else args.child
        return launch_execution(args.root, data, command)
    except Exception as exc:  # noqa: BLE001
        # Cursor blocks on hook stdout; every failure must still yield the neutral allow response.
        # Report the exception type only; messages may echo payload content.
        print(f"station-register: invalid or unavailable execution metadata ({type(exc).__name__})", file=sys.stderr)
        if args.command == "ingest" and args.harness == "cursor":
            print(CURSOR_ALLOW)
            return 0
        return 1


if __name__ == "__main__":
    raise SystemExit(register_main())
