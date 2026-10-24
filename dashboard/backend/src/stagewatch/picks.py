"""Read and parse bounded quota-pick records from a stage directory."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LEAD_CAP = 64 * 1024
STREAM_CAP = 1024 * 1024


@dataclass
class PickCandidate:
    model: str | None = None
    effort: str | None = None
    group: str | None = None
    reason: str | None = None


@dataclass
class PickRecord:
    at: str | None = None
    job: str | None = None
    slices: list[str] = field(default_factory=list)
    tier: str | None = None
    candidates: list[PickCandidate] = field(default_factory=list)
    assigned: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    is_repick: bool = False
    source: str = "stream"
    raw: str | None = None


def _safe_read(stage: Path, name: str, cap: int) -> tuple[bytes | None, str | None]:
    try:
        root = stage.resolve(strict=True)
        path = stage / name
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            return None, f"{name} escapes stage directory"
        if not resolved.is_file():
            return None, f"{name} is not a regular file"
        if resolved.stat().st_size > cap:
            return None, f"{name} exceeds {cap} byte limit"
        return resolved.read_bytes(), None
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"cannot read {name}: {exc.strerror or 'I/O error'}"


def _as_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _from_json(item: Any) -> PickRecord | None:
    if not isinstance(item, dict):
        return None
    candidates = []
    for candidate in item.get("candidates", []) if isinstance(item.get("candidates"), list) else []:
        if isinstance(candidate, dict):
            values = {
                key: _as_text(candidate.get(key))
                for key in ("model", "effort", "group", "reason")
            }
            candidates.append(PickCandidate(**values))
    return PickRecord(
        at=_as_text(item.get("at")),
        job=_as_text(item.get("job")),
        slices=(
            [x for x in item.get("slices", []) if isinstance(x, str)]
            if isinstance(item.get("slices"), list)
            else []
        ),
        tier=_as_text(item.get("tier")),
        candidates=candidates,
        assigned=(
            [x for x in item.get("assigned", []) if isinstance(x, str)]
            if isinstance(item.get("assigned"), list)
            else []
        ),
        skipped=(
            [x for x in item.get("skipped", []) if isinstance(x, str)]
            if isinstance(item.get("skipped"), list)
            else []
        ),
        is_repick=item.get("is_repick") is True, source="lead.json",
    )


_HEADER = re.compile(r"^job=(\S+)(?:\s+tierUsed=(\S+))?$")
_CANDIDATE = re.compile(r"^(\S+)@(\S+)\s+(ok|runs-out)(?:\s+group:\s*(.*))?$")


def _stream_blocks(text: str) -> list[PickRecord]:
    lines = text.splitlines()
    records: list[PickRecord] = []
    i = 0
    while i < len(lines):
        match = _HEADER.match(lines[i].strip())
        if not match:
            i += 1
            continue
        start = i
        record = PickRecord(job=match.group(1), tier=match.group(2))
        i += 1
        parsed = True
        while i < len(lines) and lines[i].strip():
            line = lines[i].strip()
            candidate = _CANDIDATE.match(line)
            if candidate:
                record.candidates.append(
                    PickCandidate(
                        candidate.group(1),
                        candidate.group(2),
                        candidate.group(4),
                        candidate.group(3),
                    )
                )
            elif line.startswith("assign:"):
                record.assigned = re.findall(r"([\w.-]+)=([\w.-]+)", line[7:])
                record.assigned = [f"{name}={value}" for name, value in record.assigned]
            elif line.startswith("skipped:"):
                record.skipped.append(line[len("skipped:"):].strip())
            else:
                parsed = False
            i += 1
        block = "\n".join(lines[start:i])
        if parsed:
            records.append(record)
        else:
            records.append(PickRecord(job=record.job, tier=record.tier, source="stream", raw=block))
        while i < len(lines) and not lines[i].strip():
            i += 1
    return records


def read_picks(stage: Path) -> tuple[list[PickRecord], str | None]:
    """Prefer schema-1 lead picks; otherwise parse quota-pick blocks from stream.log."""
    data, reason = _safe_read(stage, "lead.json", LEAD_CAP)
    if reason:
        return [], reason
    if data is not None:
        try:
            lead = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            lead = None
        if (
            isinstance(lead, dict)
            and lead.get("schema") == 1
            and isinstance(lead.get("picks"), list)
        ):
            return [
                record
                for item in lead["picks"]
                if (record := _from_json(item)) is not None
            ], None
    data, reason = _safe_read(stage, "stream.log", STREAM_CAP)
    if reason:
        return [], reason
    if data is None:
        return [], None
    return _stream_blocks(data.decode("utf-8", errors="replace")), None
