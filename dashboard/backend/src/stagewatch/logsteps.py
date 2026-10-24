"""Extract compact step, token, and worker events from engine logs."""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stagewatch.logparse import MAX_LINE_CHARS
from stagewatch.tail import read_tail

_READ_CAP = 256 * 1024


@dataclass(frozen=True)
class StepEvent:
    seq: int
    kind: str
    text: str
    duration_ms: int | None
    ok: bool | None
    at: int | None


@dataclass(frozen=True)
class WorkerEvent:
    kind: str
    at: int | None
    text: str


@dataclass(frozen=True)
class LogSteps:
    steps: list[StepEvent]
    tokens: int | None
    tokens_source: str | None
    events: list[WorkerEvent]
    cost: float | None = None


_SUCCESS = re.compile(r"^\s*succeeded in ([\d,]+)ms:")
_TOKENS = re.compile(r"^tokens used\s*$", re.I)
_COUNT = re.compile(r"^\s*([\d,]+)\s*$")
_TIMESTAMP = re.compile(r"^\s*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+")
_PERMISSION = re.compile(r"permission requested.*(?:auto-reject|auto reject)", re.I)


def _json(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("command", "path", "filePath", "pattern", "query", "url", "description"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item[:MAX_LINE_CHARS]
    return ""


def _claude_cost(path: Path) -> float | None:
    """`total_cost_usd` from the last Claude `result` record.

    The record is the run's final line, so it is read from the tail: a long run
    can push it past the head read. Lines are not cut to MAX_LINE_CHARS here,
    because a result record that carries its full text is longer than that.
    """
    try:
        lines = read_tail(path, _READ_CAP).lines
    except OSError:
        return None
    for line in reversed(lines):
        if '"total_cost_usd"' not in line:
            continue
        rec = _json(line)
        if rec and rec.get("type") == "result":
            value = rec.get("total_cost_usd")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def parse_log_steps(path: str | Path) -> LogSteps:
    """Parse a worker log. Unknown/prose formats yield no derived activity."""
    try:
        log_path = Path(path)
        if not stat.S_ISREG(log_path.lstat().st_mode):
            return LogSteps([], None, None, [])
        with log_path.open("rb") as stream:
            content = stream.read(_READ_CAP)
        lines = content.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return LogSteps([], None, None, [])
    lines = [line[:MAX_LINE_CHARS] for line in lines]
    records = [_json(line) for line in lines]
    opencode = any(
        r and r.get("type") in {"step_start", "tool_use", "text", "step_finish"}
        for r in records
    )
    codex = any(line.startswith("OpenAI Codex") for line in lines[:40])
    steps: list[StepEvent] = []
    events: list[WorkerEvent] = []
    tokens: int | None = None
    source: str | None = None
    cost: float | None = None

    if lines and lines[0].strip() == "Reading additional input from stdin..." and not codex:
        events.append(WorkerEvent("stdin_hang", None, lines[0]))

    if codex:
        seq = 0
        for index, line in enumerate(lines):
            if line.strip() == "exec":
                command = (
                    lines[index + 1].strip()[:MAX_LINE_CHARS]
                    if index + 1 < len(lines)
                    else ""
                )
                match = _SUCCESS.match(lines[index + 2]) if index + 2 < len(lines) else None
                if match:
                    seq += 1
                    steps.append(
                        StepEvent(
                            seq,
                            "exec",
                            command,
                            int(match.group(1).replace(",", "")),
                            True,
                            None,
                        )
                    )
            if _TOKENS.match(line) and index + 1 < len(lines):
                match = _COUNT.match(lines[index + 1])
                if match:
                    tokens = int(match.group(1).replace(",", ""))
                    source = "codex_exit"

    if opencode:
        total = 0
        have_tokens = False
        for line, rec in zip(lines, records, strict=False):
            stamp = _TIMESTAMP.match(line)
            at: int | None = None
            if rec:
                raw_at = rec.get("timestamp")
                if isinstance(raw_at, (int, float)):
                    at = int(raw_at)
            elif stamp:
                # ISO timestamps are retained as epoch milliseconds.
                from datetime import datetime
                at = int(
                    datetime.fromisoformat(
                        stamp.group(1).replace("Z", "+00:00")
                    ).timestamp()
                    * 1000
                )
            if _PERMISSION.search(line):
                events.append(WorkerEvent("permission_reject", at, line))
            if not rec:
                continue
            typ = rec.get("type")
            part = rec.get("part") if isinstance(rec.get("part"), dict) else {}
            if typ == "tool_use":
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                inp = state.get("input") if isinstance(state.get("input"), dict) else {}
                seq = len(steps) + 1
                status = state.get("status")
                steps.append(
                    StepEvent(
                        seq,
                        str(part.get("tool") or "tool"),
                        _summary(inp),
                        None,
                        status == "completed"
                        if status in ("completed", "error")
                        else None,
                        at,
                    )
                )
            elif typ == "step_finish":
                part_cost = part.get("cost")
                if isinstance(part_cost, (int, float)) and not isinstance(part_cost, bool):
                    cost = (cost or 0.0) + float(part_cost)
                usage = rec.get("tokens")
                if isinstance(usage, dict):
                    subtotal = sum(
                        v
                        for v in usage.values()
                        if isinstance(v, int) and not isinstance(v, bool)
                    )
                    total += subtotal
                    have_tokens = True
        if have_tokens:
            tokens, source = total, "opencode_step_finish"
    if cost is None:
        cost = _claude_cost(log_path)
    return LogSteps(steps, tokens, source, events, cost)
