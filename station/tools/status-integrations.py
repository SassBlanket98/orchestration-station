#!/usr/bin/env python3
"""Reversible, append-only harness hook installer: prepare -> apply -> restore.

prepare  writes backups + proposed content under --state; touches no target.
apply    refuses any target whose bytes changed since prepare, then writes.
restore  puts back backups only where the file still equals what apply wrote.
Output never includes configuration contents, only paths, hashes and counts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

# target name -> (path relative to home, harness, account)
TARGETS = {
    "claude": (".claude/settings.json", "claude", "personal"),
    "claude-alt": (".claude-alt/settings.json", "claude", "work"),
    "codex": (".codex/hooks.json", "codex", "personal"),
    "cursor": (".cursor/hooks.json", "cursor", "personal"),
}
CLAUDE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                 "SubagentStart", "SubagentStop", "Stop", "SessionEnd")
EVENTS = {
    "claude": CLAUDE_EVENTS,
    "claude-alt": CLAUDE_EVENTS,
    "codex": ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"),
    "cursor": ("sessionStart", "beforeSubmitPrompt", "preToolUse", "postToolUse",
               "subagentStart", "subagentStop", "stop", "sessionEnd"),
}


class Refused(RuntimeError):
    """Raised when a target is unsafe or changed underneath us."""


def digest(data: bytes | None) -> str:
    return "absent" if data is None else hashlib.sha256(data).hexdigest()


def confine(name: str, entry_path: str, home: Path) -> None:
    """Refuse a plan path whose canonical location (.. and symlinks resolved) is not strictly under home."""
    root = Path(os.path.realpath(home))
    resolved = Path(os.path.realpath(entry_path))
    if root not in resolved.parents:
        raise Refused(f"{name}: path escapes --home, refused")


def load(path: Path) -> bytes | None:
    if path.is_symlink():
        raise Refused(f"{path.name}: symlink refused")
    if not path.exists():
        return None
    return path.read_bytes()


def parse(raw: bytes | None, name: str) -> dict:
    if raw is None or not raw.strip():
        return {}
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Refused(f"{name}: invalid JSON, not modified") from exc
    if not isinstance(doc, dict):
        raise Refused(f"{name}: top level is not an object, not modified")
    return doc


def mentions(node: object, command: str) -> bool:
    """True when an exact command string is present anywhere in one hook group."""
    if isinstance(node, str):
        return node == command
    if isinstance(node, dict):
        return any(mentions(value, command) for value in node.values())
    if isinstance(node, list):
        return any(mentions(value, command) for value in node)
    return False


def command_for(producer: Path, root: str, harness: str, account: str) -> str:
    parts = [sys.executable, "-I", str(producer), "--root", root, "ingest", "--harness", harness, "--account", account]
    return " ".join(shlex.quote(part) for part in parts)


def merge(name: str, doc: dict, producer: Path, root: str) -> tuple[dict, int]:
    _, harness, account = TARGETS[name]
    cursor = name == "cursor"
    command = command_for(producer, root, harness, account)
    hooks = doc.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise Refused(f"{name}: hooks is not an object, not modified")
    if cursor:
        doc.setdefault("version", 1)
    added = 0
    for event in EVENTS[name]:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise Refused(f"{name}: hooks.{event} is not a list, not modified")
        # Idempotent: an entry already invoking this producer for this target is left alone.
        if any(mentions(group, command) for group in groups):
            continue
        groups.append({"command": command} if cursor else {"hooks": [{"type": "command", "command": command}]})
        added += 1
    return doc, added


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=".integration-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(home: Path, state: Path, producer: Path, root: str) -> dict:
    # A re-prepare keeps the first plan's backups and pre-change digest, so restore still reaches original bytes.
    plan_path = state / "plan.json"
    prior = json.loads(plan_path.read_text())["targets"] if plan_path.exists() else {}
    plan: dict = {"targets": {}}
    for name, (rel, _, _) in TARGETS.items():
        path = home / rel
        raw = load(path)
        doc, added = merge(name, parse(raw, name), producer, root)
        proposed = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        original = prior.get(name)
        if original is None:
            if raw is not None:
                write_atomic(state / "backups" / f"{name}.bak", raw)
            before = digest(raw)
        else:
            before = original["before"]
        write_atomic(state / "staged" / f"{name}.json", proposed)
        plan["targets"][name] = {"path": str(path), "before": before, "after": digest(proposed), "added": added}
    write_atomic(state / "plan.json", json.dumps(plan, indent=2).encode("utf-8"))
    return plan


def apply(state: Path, home: Path) -> dict:
    plan = json.loads((state / "plan.json").read_text())
    # Confine every path before any check or write, so one escaping entry stops the whole apply.
    for name, entry in plan["targets"].items():
        confine(name, entry["path"], home)
    # Check every target before writing any, so a stale target stops the whole apply.
    for name, entry in plan["targets"].items():
        if digest(load(Path(entry["path"]))) != entry["before"]:
            raise Refused(f"{name}: target changed since prepare, apply refused")
    for name, entry in plan["targets"].items():
        write_atomic(Path(entry["path"]), (state / "staged" / f"{name}.json").read_bytes())
    applied = {name: entry["after"] for name, entry in plan["targets"].items()}
    write_atomic(state / "applied.json", json.dumps(applied, indent=2).encode("utf-8"))
    return applied


def restore(state: Path, home: Path) -> dict:
    plan = json.loads((state / "plan.json").read_text())
    applied = json.loads((state / "applied.json").read_text())
    # Confine every path before unlinking or writing any, so one escaping entry stops the whole restore.
    for name, entry in plan["targets"].items():
        confine(name, entry["path"], home)
    outcome = {}
    for name, entry in plan["targets"].items():
        path = Path(entry["path"])
        if digest(load(path)) != applied.get(name):
            # Someone else edited it since apply; never clobber that edit.
            outcome[name] = "skipped-concurrent-edit"
            continue
        backup = state / "backups" / f"{name}.bak"
        if entry["before"] == "absent":
            path.unlink(missing_ok=True)
        else:
            write_atomic(path, backup.read_bytes())
        outcome[name] = "restored"
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="status-integrations")
    parser.add_argument("command", choices=("prepare", "apply", "restore"))
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--producer", type=Path, default=Path(__file__).resolve().parent.parent / "src" / "register.py")
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            if not args.root:
                raise Refused("--root is required for prepare")
            plan = prepare(args.home, args.state, args.producer.resolve(), args.root)
            for name, entry in plan["targets"].items():
                print(f"{name}: prepared, {entry['added']} hook entries to add")
        elif args.command == "apply":
            for name in apply(args.state, args.home):
                print(f"{name}: applied")
        else:
            for name, outcome in restore(args.state, args.home).items():
                print(f"{name}: {outcome}")
    except (Refused, OSError, KeyError, ValueError) as exc:
        print(f"status-integrations: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
