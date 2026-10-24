"""Stable ids for stage paths supplied by Monitor."""

import base64
import hashlib
import os
import re
from pathlib import Path

STAGE_DIRNAMES = (".stage", ".staging")
_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def is_run_stage(path: Path) -> bool:
    """True for a nested run path listed by Monitor."""
    return path.parent.name in STAGE_DIRNAMES


def run_id(run: Path) -> str:
    """Stable id for a nested run: `run:` + base64url(absolute path), no padding."""
    raw = os.path.abspath(run).encode("utf-8")
    return "run:" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def session_id(project_name: str) -> str:
    """Sanitize a stage's parent directory name into a session id."""
    return _ID_SAFE.sub("-", project_name)


def assign_ids(stages: list[Path]) -> dict[str, Path]:
    """Map Monitor paths to stable session ids, disambiguating basename clashes."""
    by_id: dict[str, list[Path]] = {}
    result: dict[str, Path] = {}
    for stage in stages:
        if is_run_stage(stage):
            result[run_id(stage)] = stage
            continue
        by_id.setdefault(session_id(stage.parent.name), []).append(stage)
    for base_id, paths in by_id.items():
        if len(paths) == 1:
            result[base_id] = paths[0]
            continue
        for stage in paths:
            digest = hashlib.sha1(os.path.abspath(stage).encode("utf-8")).hexdigest()[:6]
            result[f"{base_id}-{digest}"] = stage
    return result
