"""Control center: the event model. Builds `CCEvent` lines from data already in the model (steps, calls, turns, doing),
at event time, newest LAST. Reads files only in `log_tail`. Imports model only."""
from __future__ import annotations

import os
import re

from model import CCCall, CCEvent, GlobalSnapshot, Stage, Step, Turn, Worker

ROW_LIMIT = 200      # events kept per row
FLEET_LIMIT = 500    # events kept in the merged tail

_CX_TAIL_BYTES = 256 * 1024   # log_tail reads at most this much of the file's end
_CX_DUP_S = 2.0               # a step and a call this close are the same event
_STEP_KINDS = {"tool": "tool", "text": "msg", "error": "err"}
_CX_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"              # CSI, e.g. colours
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"     # OSC, e.g. window titles
    r"|\x1b[@-Z\\-_]"                         # other two-byte escapes
)


def row_key(stage: Stage, worker: Worker | None = None) -> str:
    """The key an event carries: "<stage path>|<worker name>"; the lead row's worker part is empty."""
    return f"{stage.path}|{worker.name if worker is not None else ''}"


def row_events(stage: Stage, worker: Worker | None = None, limit: int = ROW_LIMIT) -> list[CCEvent]:
    """Events of one row, oldest first, at most `limit` (the newest ones). A worker row reads worker.log.steps and
    worker.calls; the lead row reads stage.calls, stage.doing and stage.turns."""
    events = _cev_lead_events(stage) if worker is None else _cev_worker_events(stage, worker)
    return _cev_cap(events, limit)


def fleet_events(gs: GlobalSnapshot, limit: int = FLEET_LIMIT) -> list[CCEvent]:
    """The merged tail over every stage and worker in `gs`, oldest first, at most `limit` (the newest ones)."""
    out: list[CCEvent] = []
    for stage in gs.stages:
        out.extend(row_events(stage))
        for worker in stage.snap.workers:
            out.extend(row_events(stage, worker))
    return _cev_cap(out, limit)


def log_tail(path: str, n: int = ROW_LIMIT) -> list[str]:
    """The last `n` lines of the file at `path`, ANSI stripped, long lines kept whole; [] when unreadable. Reads only the file's tail."""
    if n <= 0:
        return []
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - _CX_TAIL_BYTES)
            handle.seek(start)
            data = handle.read()
    except (OSError, ValueError):
        return []
    if not data:
        return []
    text = data.decode("utf-8", "replace")
    if start > 0:
        cut = text.find("\n")
        if cut < 0:              # the tail holds no complete line
            return []
        text = text[cut + 1:]    # the first line may be a fragment: drop it
    return [_CX_ANSI_RE.sub("", line) for line in text.splitlines()][-n:]


def _cev_one_line(text: str) -> str:
    """``text`` with every run of whitespace (newlines included) folded to one space."""
    return " ".join(text.split())


def _cev_cap(events: list[CCEvent], limit: int) -> list[CCEvent]:
    """``events`` sorted by time (stable), cut to the newest ``limit``."""
    if limit <= 0:
        return []
    events.sort(key=lambda ev: ev.ts)
    return events[-limit:]


def _cev_step_event(step: Step, key: str) -> CCEvent:
    """One log step: a tool call, a text line or an error. Tool lines carry their tool name in front."""
    kind = _STEP_KINDS.get(step.kind, "msg")
    text = f"{step.tool} {step.text}" if kind == "tool" else step.text
    return CCEvent(step.ts, key, kind, _cev_one_line(text))


def _cev_call_event(call: CCCall, key: str) -> CCEvent:
    """One transcript call: "tool <arg>"; a failed call is an err line carrying its error."""
    text = _cev_one_line(f"{call.tool} {call.arg}")
    if call.outcome == "failed":
        return CCEvent(call.ts, key, "err", _cev_one_line(call.err) or text)
    return CCEvent(call.ts, key, "tool", text)


def _cev_step_in_call(step: Step, call: CCCall) -> bool:
    """True when ``step`` and ``call`` report the same tool call (same tool, at most 2 s apart)."""
    return (step.kind == "tool" and abs(step.ts - call.ts) <= _CX_DUP_S
            and _cev_one_line(step.tool).lower() == _cev_one_line(call.tool).lower())


def _cev_worker_events(stage: Stage, worker: Worker) -> list[CCEvent]:
    """A worker row: its log steps and calls, then a state line when it has ended."""
    key = row_key(stage, worker)
    calls = list(worker.calls)
    timed = [step for step in worker.log.steps if step.ts]
    untimed = [step for step in worker.log.steps if not step.ts]
    out: list[CCEvent] = []
    used: set[int] = set()
    for step in timed:
        match = next((i for i, call in enumerate(calls) if i not in used and _cev_step_in_call(step, call)), None)
        if match is not None:
            used.add(match)      # the call says the same thing: keep the call only
            continue
        out.append(_cev_step_event(step, key))
    out.extend(_cev_call_event(call, key) for call in calls)
    if worker.end is not None:
        text = worker.state.upper()
        if worker.failure:
            text = f"{text} · {worker.failure}"
        out.append(CCEvent(worker.end, key, "state", _cev_one_line(text)))
    if untimed and not timed and not any(call.ts for call in calls):
        out = [_cev_step_event(step, key) for step in untimed] + out   # a log with no times at all: show them first
    return out


def _cev_turn_event(turn: Turn, key: str) -> list[CCEvent]:
    """A turn's msg line (ask and model) and, when it failed, its err line."""
    when = turn.end if turn.end is not None else turn.start
    ask = _cev_one_line(turn.ask)
    text = " · ".join(part for part in (ask, turn.model.strip()) if part) or "turn"
    out = [CCEvent(when, key, "msg", _cev_one_line(text))]
    if turn.outcome in ("error", "aborted"):
        out.append(CCEvent(when, key, "err", _cev_one_line(turn.error) or turn.outcome))
    return out


def _cev_doing_is_call(ts: float, text: str, calls: list[CCCall]) -> bool:
    """True when a doing entry repeats a call within 2 s with the same last word ("Edit render.py" / "edit render.py")."""
    words = _cev_one_line(text).split()
    if not words:
        return False
    tail = words[-1].lower()
    for call in calls:
        if abs(call.ts - ts) <= _CX_DUP_S and _cev_one_line(f"{call.tool} {call.arg}").lower().split()[-1:] == [tail]:
            return True
    return False


def _cev_lead_events(stage: Stage) -> list[CCEvent]:
    """The lead row: its calls, its latest doing actions and its turns."""
    key = row_key(stage)
    out = [_cev_call_event(call, key) for call in stage.calls]
    for ts, text in stage.doing:
        if not _cev_doing_is_call(ts, text, stage.calls):
            out.append(CCEvent(ts, key, "tool", _cev_one_line(text)))
    for turn in stage.turns:
        out.extend(_cev_turn_event(turn, key))
    return out
