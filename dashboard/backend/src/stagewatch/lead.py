"""Parse explicit lead metadata from a stage directory."""

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

_CAP = 64 * 1024
_PHASE = r"(plan|build|review)"
_CYCLE = r"cycle\s*(?:#\s*)?(\d+)"
_TIME = re.compile(r"\b(\d{4}-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d)?(?:Z|\s*UTC)?)?)\b")
_PHASE_CYCLE = re.compile(rf"\b{_PHASE}\b.*?{_CYCLE}|{_CYCLE}.*?\b{_PHASE}\b", re.I)


def _empty() -> dict[str, Any]:
    return {
        "lead_model": None, "lead_label": None, "harness": None, "account": None,
        "pid": None, "started": None, "phase": None, "cycle": None,
        "over_policy": False, "stream": None, "updated_at": None,
        "lead_source": "unknown", "phase_at": [],
    }


def _valid_cycle(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _metadata(stage: Path) -> dict[str, Any] | None:
    path = stage / "lead.json"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _CAP:
            return None
        raw = path.read_bytes()
        if len(raw) > _CAP:
            return None
        data = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if (
        not isinstance(data, dict)
        or type(data.get("schema")) is not int
        or data.get("schema") != 1
    ):
        return None
    return data


def _text(path: Path) -> str:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _CAP:
            return ""
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > _CAP:
                return ""
            raw = stream.read(_CAP)
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _evidence(text: str, headings_only: bool = False) -> list[tuple[str, int, str | None]]:
    found: list[tuple[str, int, str | None]] = []
    for line in text.splitlines():
        if "nested run" in line.lower() or "nested-run" in line.lower():
            continue
        if headings_only and not line.lstrip().startswith("#"):
            continue
        match = _PHASE_CYCLE.search(line)
        if not match:
            continue
        groups = match.groups()
        phase = (groups[0] or groups[3]).lower()
        cycle = int(groups[1] or groups[2])
        timestamp = _TIME.search(line)
        found.append((phase, cycle, timestamp.group(1) if timestamp else None))
    return found


def parse_lead(stage_dir: str | Path, stream_name: str) -> dict[str, Any]:
    """Return trusted lead fields, falling back to explicit stream/progress prose."""
    stage = Path(stage_dir)
    result = _empty()
    metadata = _metadata(stage)
    if metadata is not None:
        for key in (
            "lead_model", "lead_label", "harness", "account", "pid", "started",
            "stream", "updated_at",
        ):
            value = metadata.get(key)
            result[key] = (
                value
                if isinstance(value, (str, int)) and not isinstance(value, bool)
                else None
            )
        phase = metadata.get("phase")
        cycle = _valid_cycle(metadata.get("cycle"))
        if phase in {"plan", "build", "review"}:
            result["phase"] = phase
        result["cycle"] = min(cycle, 3) if cycle else None
        result["over_policy"] = bool(cycle and cycle > 3)
        if result["phase"] is not None and cycle is not None and result["updated_at"] is not None:
            result["phase_at"] = [
                {
                    "phase": result["phase"],
                    "cycle": result["cycle"],
                    "at": result["updated_at"],
                }
            ]
        result["lead_source"] = "lead.json"
        return result

    result["stream"] = stream_name
    stream = _evidence(_text(stage / stream_name), headings_only=True)
    progress_path = stage / "PROGRESS.md"
    progress = _evidence(_text(progress_path)) if progress_path.exists() else []
    if any("nested run" in line.lower() or "nested-run" in line.lower()
           for path in (stage / stream_name, progress_path) for line in _text(path).splitlines()):
        return result
    latest_stream = stream[-1] if stream else None
    latest_progress = progress[-1] if progress else None
    if latest_stream and latest_progress and latest_stream[:2] != latest_progress[:2]:
        return result
    selected = latest_stream or latest_progress
    if selected:
        phase, raw_cycle, timestamp = selected
        result["phase"] = phase
        result["cycle"] = min(raw_cycle, 3)
        result["over_policy"] = any(item[1] > 3 for item in stream + progress)
        result["lead_source"] = "stream"
        result["phase_at"] = [
            {"phase": phase_name, "cycle": min(cycle, 3), "at": at}
            for phase_name, cycle, at in stream if at is not None
        ]
    return result
