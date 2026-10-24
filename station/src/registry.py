"""Validated, read-only execution records. Producers live in register.py."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import fields
from pathlib import Path

from model import ExecutionRecord, RegistrySnapshot, home_path

REGISTRY_SCHEMA = 1
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_rg_id = re.compile(r"^[a-f0-9]{64}$")
_rg_harnesses = {"codex", "t3", "claude", "cursor", "opencode", "openclaw", "other"}
_rg_numbers = {"registered", "started", "activity", "ended", "cost"}
_rg_integers = {"pid", "pid_start", "exit_code", "context_tokens", "tokens_in", "tokens_out", "tokens_cache", "revision"}
_rg_booleans = {"output_available"}
_rg_cache: dict[str, tuple[tuple[int, int, int], ExecutionRecord]] = {}


def registry_root() -> str:
    override = os.environ.get("STATUS_REGISTRY_ROOT")
    return os.path.expanduser(override) if override else home_path("status-registry")


default_registry_root = registry_root   # alias so the joined station has the same name collect.py imports


def execution_key(harness: str, account: str, native_id: str) -> str:
    return hashlib.sha256(json.dumps([harness, account, native_id], separators=(",", ":")).encode()).hexdigest()


def validate_execution(data: object) -> ExecutionRecord:
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != REGISTRY_SCHEMA:
        raise ValueError("unsupported record schema")
    allowed = {f.name for f in fields(ExecutionRecord)}
    values = {k: v for k, v in data.items() if k in allowed}
    for key, value in values.items():
        if key == "schema":
            continue
        if key in _rg_integers:
            if key == "revision" and value is None:
                raise ValueError("invalid revision")
            if value is not None and type(value) is not int:
                raise ValueError("invalid integer")
        elif key in _rg_numbers:
            if key in ("registered", "started", "activity") and value is None:
                raise ValueError("invalid required timestamp")
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError("invalid timestamp or cost")
        elif key in _rg_booleans:
            if type(value) is not bool:
                raise ValueError("invalid boolean")
        elif not isinstance(value, str):
            raise ValueError("invalid string")
    identity = values.get("execution_id", "")
    if not _rg_id.fullmatch(identity):
        raise ValueError("invalid execution identity")
    if values.get("harness") not in _rg_harnesses or values.get("role", "worker") not in ("lead", "worker"):
        raise ValueError("invalid harness or role")
    for key in ("parent_id", "root_id"):
        if values.get(key) and not _rg_id.fullmatch(values[key]):
            raise ValueError("invalid parent identity")
    for kind, key in (("brief", "brief_ref"), ("result", "result_ref")):
        if values.get(key) and values[key] != f"artifacts/{identity}/{kind}.txt":
            raise ValueError("invalid artifact reference")
    if values.get("state", "running") not in ("running", "idle", "closed", "finished"):
        raise ValueError("invalid execution state")
    if values.get("outcome", "unknown") not in ("unknown", "success", "failed", "cancelled"):
        raise ValueError("invalid execution outcome")
    if values.get("pid") is not None and values["pid"] <= 0:
        raise ValueError("invalid process identity")
    return ExecutionRecord(**values)


def _rg_regular(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
        here = root
        for part in relative.parts:
            here = here / part
            if here.is_symlink():
                return False
        return path.is_file() and path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def read_registry(root: str) -> RegistrySnapshot:
    base = Path(root)
    snap = RegistrySnapshot()
    folder = base / "records"
    if folder.is_symlink():
        snap.diagnostics.append("registry records directory is a symlink")
        return snap
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        snap.diagnostics.append("registry records unavailable")
        return snap
    for path in paths:
        try:
            if not _rg_regular(base, path) or path.stat().st_size > 256 * 1024:
                raise ValueError("unsafe or oversized record")
            stat = path.stat()
            fingerprint = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            cached = _rg_cache.get(str(path))
            record = cached[1] if cached and cached[0] == fingerprint else validate_execution(json.loads(path.read_text()))
            if path.stem != record.execution_id:
                raise ValueError("record identity does not match filename")
            _rg_cache[str(path)] = (fingerprint, record)
            snap.records.append(record)
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            snap.diagnostics.append(f"invalid execution record {path.stem[:12]}")
    present = {str(p) for p in paths}
    for key in list(_rg_cache):
        if str(Path(key).parent) == str(folder) and key not in present:
            del _rg_cache[key]
    return snap


def read_execution_artifact(root: str, execution_id: str, kind: str) -> list[str]:
    if not _rg_id.fullmatch(execution_id) or kind not in ("brief", "result"):
        return []
    base = Path(root)
    path = base / "artifacts" / execution_id / (kind + ".txt")
    if not _rg_regular(base, path):
        return []
    try:
        if path.stat().st_size > MAX_ARTIFACT_BYTES:
            return ["artifact exceeds the supported 16 MiB limit; full content was not loaded"]
        return path.read_text(encoding="utf-8").replace("\t", "    ").splitlines()
    except (OSError, UnicodeError):
        return []
