"""Incremental parser for a single worker's log file.

Turns one worker's log into a :class:`LogState`, reading only the bytes added
since the previous call. Four shapes are handled: opencode ``--format json``
logs (one JSON object per line), claude ``-p --output-format stream-json`` and
cursor-agent ``-p --output-format stream-json`` logs (the same shape), codex
``exec``/``review`` text logs, and any other plain-text log.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime

from model import (
    ACT_BUCKET_S,
    ACT_BUCKETS,
    MAX_STEPS,
    TOOL_RING_MAX,
    TOOL_RING_S,
    LogState,
    Step,
)

STEP_OK, STEP_FAILED = "ok", "failed"   # Step.outcome values; "" means the log does not say

_LP_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])"
)
_LP_MODEL_RE = re.compile(r'"(?:modelID|model)"\s*:\s*"([^"]*)"')
_LP_ERR_RE = re.compile(r"^(?:Error|error:|ERROR)")
_LP_CODEX_RESULT_RE = re.compile(r"^ ?(?:succeeded|exited (-?\d+)) in ([^:]+):\s*$")
_LP_CODEX_DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s)$")   # codex writes "0ms", "812ms" or "1.2s"
_LP_CODEX_CWD_RE = re.compile(r" in /\S*$")
_LP_CODEX_STREAM_RE = re.compile(r"^(?:stream (?:error|disconnected)|unexpected status \d{3})", re.IGNORECASE)
_LP_OPENCODE_TYPES = frozenset({"tool_use", "text", "step_finish", "step_start", "error"})
_LP_DETECT_LINES = 40
_LP_MAX_CARRY = 65536                   # an unterminated plain-text line longer than this is parsed as it stands
_LP_MAX_JSON_CARRY = 8 * 1024 * 1024    # an unterminated JSON record is kept up to this, then skipped whole
_LP_CODEX_MARKERS = frozenset({"exec", "tokens used"})
_LP_STREAM_TOOL_KEYS = ("command", "file_path", "notebook_path", "filePath", "path", "pattern", "query", "url")
_LP_ROLLOUT_TYPES = frozenset({"session_meta", "response_item", "event_msg", "turn_context", "world_state"})
_LP_CODEX_CALLS = frozenset({"function_call", "custom_tool_call", "local_shell_call"})
_LP_CODEX_OUTPUTS = frozenset({"function_call_output", "custom_tool_call_output", "local_shell_call_output"})
_LP_ROLLOUT_HEAD_RE = re.compile(
    r'^\{"timestamp"\s*:\s*"[^"]+"\s*,\s*"ordinal"\s*:\s*\d+\s*,\s*"type"\s*:\s*'
    r'"(?:session_meta|response_item|event_msg|turn_context|world_state)"'
)
_LP_CLAUDE_FRESH = ("input_tokens", "cache_creation_input_tokens")
_LP_CURSOR_FRESH = ("inputTokens", "cacheWriteTokens")

_LP_READ = frozenset({"read", "view", "cat", "list", "ls"})
_LP_EDIT = frozenset({"edit", "write", "patch", "apply_patch", "multiedit", "notebookedit"})
_LP_SHELL = frozenset({"bash", "shell", "exec", "command"})
_LP_SEARCH = frozenset({"grep", "glob", "search", "find", "websearch", "webfetch"})


def tool_category(tool: str) -> str:
    """Map a raw tool name to one of ``TOOL_CATS`` (case-insensitive)."""
    name = (tool or "").strip().lower()
    if name in _LP_READ:
        return "read"
    if name in _LP_EDIT:
        return "edit"
    if name in _LP_SHELL:
        return "shell"
    if name in _LP_SEARCH:
        return "search"
    return "other"


def activity(state: LogState, now: float) -> list[int]:
    """Return ``ACT_BUCKETS`` event totals, oldest bucket first."""
    out = [0] * ACT_BUCKETS
    for ts, count in state.pulses:
        delta = now - ts
        delta = max(delta, 0.0)
        index = ACT_BUCKETS - math.ceil(delta / ACT_BUCKET_S)
        if index < 0:
            continue
        if index >= ACT_BUCKETS:
            index = ACT_BUCKETS - 1
        out[index] += count
    return out


def tool_rate(state: LogState, now: float, cells: int = 15, span: float = TOOL_RING_S) -> list[int]:
    """Tool calls per cell over the last ``span`` seconds, oldest cell first."""
    out = [0] * max(cells, 0)
    if cells <= 0 or span <= 0:
        return out
    width = span / cells
    for ts, count in state.tool_pulses:
        age = max(now - ts, 0.0)
        index = cells - 1 - int(age / width)
        if 0 <= index < cells:
            out[index] += count
    return out


def parse_log(path: str, prev: LogState | None = None, now: float | None = None,
              detect_head: bytes | None = None) -> LogState:
    """Parse ``path`` into a :class:`LogState`, appending to ``prev`` if given."""
    if now is None:
        now = time.time()
    try:
        info = os.stat(path)
    except OSError:
        return prev if prev is not None else LogState(path=path)
    size = info.st_size
    if prev is None or size < prev.offset:
        state = LogState(path=path)
        start = 0
    else:
        state = prev
        start = prev.offset
    try:
        with open(path, "rb") as handle:
            if start:
                handle.seek(start)
            data = handle.read()
    except OSError:
        return prev if prev is not None else LogState(path=path)

    state.offset = start + len(data)
    text = _lp_clean(state.carry + data.decode("utf-8", "replace"))
    private = _lp_private(state)
    if private.pop("skip_record", False):
        # the rest of an oversized record: drop it up to its terminating newline
        cut = text.find("\n")
        if cut < 0:
            private["skip_record"] = True
            text = ""
        else:
            text = text[cut + 1:]
    if text.endswith("\n"):
        state.carry = ""
        body = text[:-1]
    else:
        cut = text.rfind("\n")
        if cut == -1:
            state.carry = text
            body = ""
        else:
            state.carry = text[cut + 1:]
            body = text[:cut]
    lines = body.split("\n") if body else []

    if state.carry:
        carry_bytes = len(state.carry.encode("utf-8", "replace"))
        if state.carry.lstrip().startswith("{"):
            if carry_bytes > _LP_MAX_JSON_CARRY:
                # too big for a record: skip its tail and count the truncation instead of losing the next records
                private["truncated"] = private.get("truncated", 0) + 1
                private["skip_record"] = True
                state.carry = ""
        elif carry_bytes > _LP_MAX_CARRY:
            lines.append(state.carry)
            state.carry = ""

    if state.harness == "unknown":
        try:
            if detect_head is None:
                with open(path, "rb") as handle:
                    detect_head = handle.read(16 * 1024)
            head = detect_head.decode("utf-8", "replace")
            _lp_detect(state, _lp_clean(head).splitlines()[:_LP_DETECT_LINES])
        except OSError:
            _lp_detect(state, lines)
    if state.harness == "codex":
        lines = _lp_codex_holdback(state, lines)

    new_ts: list[float] = []
    plain_ts = info.st_mtime if prev is None else now
    timed = state.harness == "opencode"
    if state.harness == "opencode":
        _lp_process_opencode(state, lines, new_ts, now)
    elif state.harness == "codex":
        _lp_process_codex(state, lines, new_ts, plain_ts, start == 0)
    elif state.harness == "claude":
        _lp_process_claude(state, lines, new_ts, plain_ts)
    elif state.harness == "cursor":
        _lp_process_cursor(state, lines, new_ts, plain_ts)
    else:
        _lp_process_plain(state, lines, new_ts, plain_ts)

    if len(state.steps) > MAX_STEPS:
        del state.steps[: len(state.steps) - MAX_STEPS]
    _lp_add_pulses(state, new_ts, plain_ts, timed, now)
    keep = now - ACT_BUCKETS * ACT_BUCKET_S
    state.pulses = [pulse for pulse in state.pulses if pulse[0] >= keep]
    prune_tool_ring(state, now)
    return state


def _lp_clean(text: str) -> str:
    if not text:
        return ""
    return _LP_ANSI_RE.sub("", text).replace("\r", "")


def _lp_try_json(line: str) -> dict | None:
    if not line or line[0] != "{":
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _lp_num(value: object, default: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def _lp_add(cur: float | None, value: float) -> float:
    return (cur or 0) + value


def _lp_ms_to_s(value: object) -> float | None:
    """Milliseconds since the epoch to seconds; ``None`` if not numeric.

    A numeric but absurd value (non-finite, negative or too big for a float)
    is reported as ``0.0`` so callers never trip over it later.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value) / 1000.0
    except (OverflowError, ValueError):
        return 0.0
    if not math.isfinite(seconds) or seconds < 0:
        return 0.0
    return seconds


def _lp_event_ts(obj: dict) -> float | None:
    """A stream or rollout event's own ISO-8601 ``timestamp`` in epoch seconds; None when absent or unreadable."""
    value = obj.get("timestamp")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _lp_find_model(state: LogState, line: str) -> None:
    if state.model:
        return
    match = _LP_MODEL_RE.search(line)
    if match and match.group(1):
        state.model = match.group(1)


def _lp_add_step(
    state: LogState,
    new_ts: list[float],
    ts: float,
    kind: str,
    tool: str,
    text: str,
    dur: float | None = None,
    outcome: str = "",
) -> Step:
    step = _lp_push_step(state, ts, kind, tool, text, dur, outcome)
    new_ts.append(ts)
    return step


def _lp_push_step(
    state: LogState,
    ts: float,
    kind: str,
    tool: str,
    text: str,
    dur: float | None = None,
    outcome: str = "",
) -> Step:
    """Append one step; an error step is a failure whatever else the log says about it."""
    if not outcome and kind == "error":
        outcome = STEP_FAILED
    step = Step(ts=ts, kind=kind, tool=tool, text=" ".join(_lp_clean(text).split()),
                dur=dur, outcome=outcome)
    state.steps.append(step)
    state.steps_total += 1
    if ts > 0:
        if state.first_ts == 0.0 or ts < state.first_ts:
            state.first_ts = ts
        state.last_ts = max(state.last_ts, ts)
    if kind == "tool":
        _lp_tool_pulse(state, ts)
    return step


def _lp_tool_pulse(state: LogState, ts: float) -> None:
    """Count one tool call in the 30-minute ring at its event second; untimed calls are not plotted."""
    if ts <= 0:
        return
    second = float(int(ts))
    ring = state.tool_pulses
    if ring and ring[-1][0] == second:
        ring[-1] = (second, ring[-1][1] + 1)
    elif not ring or ring[-1][0] < second:
        ring.append((second, 1))
    else:   # an event older than the newest one: merge it in place
        for index in range(len(ring) - 1, -1, -1):
            if ring[index][0] == second:
                ring[index] = (second, ring[index][1] + 1)
                break
            if ring[index][0] < second:
                ring.insert(index + 1, (second, 1))
                break
        else:
            ring.insert(0, (second, 1))
    if len(ring) > TOOL_RING_MAX:
        del ring[: len(ring) - TOOL_RING_MAX]


def prune_tool_ring(state: LogState, now: float) -> None:
    """Drop ring entries older than ``TOOL_RING_S`` before ``now``.

    Pruning here, on every parse, is what keeps the ring's 30-minute promise when a log stops
    gaining tool calls: the newest event is not the clock.
    """
    ring = state.tool_pulses
    if not ring:
        return
    cutoff = now - TOOL_RING_S
    drop = 0
    while drop < len(ring) and ring[drop][0] < cutoff:
        drop += 1
    if drop:
        del ring[:drop]
    if len(ring) > TOOL_RING_MAX:
        del ring[: len(ring) - TOOL_RING_MAX]


def _lp_stream_harness(obj: dict | None) -> str:
    """``claude`` or ``cursor`` when ``obj`` is that CLI's stream-json start event, else ``""``."""
    if obj is None or obj.get("type") != "system":
        return ""
    if "claude_code_version" in obj:
        return "claude"
    subtype = obj.get("subtype")
    if isinstance(subtype, str) and subtype.startswith("hook_") and "session_id" in obj:
        return "claude"
    if subtype == "init" and "apiKeySource" in obj:
        return "cursor"
    return ""


def _lp_rollout_item(obj: dict | None) -> bool:
    """True for one line of a codex ``rollout-*.jsonl`` session (an item with a payload and a timestamp)."""
    return (
        isinstance(obj, dict)
        and obj.get("type") in _LP_ROLLOUT_TYPES
        and isinstance(obj.get("payload"), dict)
    )


def _lp_detect(state: LogState, lines: list[str]) -> None:
    seen = 0
    stream = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Reading additional input from stdin"):
            continue
        if line.startswith("OpenAI Codex"):
            state.harness = "codex"
            return
        obj = _lp_try_json(line)
        if isinstance(obj, dict) and obj.get("type") in _LP_OPENCODE_TYPES:
            state.harness = "opencode"
            return
        if _lp_rollout_item(obj) or _LP_ROLLOUT_HEAD_RE.match(line):
            state.harness = "codex"
            return
        if not stream:
            stream = _lp_stream_harness(obj)
        seen += 1
        if seen >= _LP_DETECT_LINES:
            break
    if stream:
        state.harness = stream


def _lp_process_plain(
    state: LogState, lines: list[str], new_ts: list[float], plain_ts: float
) -> None:
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if _LP_ERR_RE.match(line):
            _lp_add_step(state, new_ts, plain_ts, "error", "", line)
            state.errors += 1
        else:
            _lp_add_step(state, new_ts, plain_ts, "text", "", line)


def _lp_codex_holdback(state: LogState, lines: list[str]) -> list[str]:
    """Return ``lines`` without a trailing codex marker line, which ``state.carry`` keeps.

    A parse call can end right after ``exec`` or ``tokens used``; the line that
    gives that marker meaning may only arrive next call. Holding the marker back
    keeps the pending state across reads.
    """
    if not lines or lines[-1].strip() not in _LP_CODEX_MARKERS:
        return lines
    state.carry = lines[-1] + "\n" + state.carry
    return lines[:-1]


def _lp_process_opencode(
    state: LogState, lines: list[str], new_ts: list[float], now: float
) -> None:
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        obj = _lp_try_json(line)
        if obj is None:
            continue
        if not state.model:
            _lp_find_model(state, line)
        ts = _lp_ms_to_s(obj.get("timestamp"))
        if ts is None:
            ts = 0.0 if "timestamp" in obj else now
        kind = obj.get("type")
        if kind == "tool_use":
            _lp_oc_tool(state, obj, ts, new_ts)
        elif kind == "text":
            part = obj.get("part")
            value = part.get("text") if isinstance(part, dict) else None
            if isinstance(value, str) and value.strip():
                _lp_add_step(state, new_ts, ts, "text", "", value)
        elif kind == "step_finish":
            _lp_oc_finish(state, obj)
        elif kind == "error":
            err = obj.get("error")
            msg = err.get("message") if isinstance(err, dict) else None
            text = msg if isinstance(msg, str) and msg else "error"
            _lp_add_step(state, new_ts, ts, "error", "", text)
            state.errors += 1


def _lp_oc_tool(state: LogState, obj: dict, ts: float, new_ts: list[float]) -> None:
    part = obj.get("part")
    if not isinstance(part, dict):
        return
    raw_tool = part.get("tool")
    tool = str(raw_tool) if raw_tool else ""
    stt = part.get("state")
    stt = stt if isinstance(stt, dict) else {}
    inp = stt.get("input")
    inp = inp if isinstance(inp, dict) else {}
    text = ""
    for key in ("command", "filePath", "path"):
        value = inp.get(key)
        if isinstance(value, str) and value:
            text = value
            break
    if not text:
        title = stt.get("title")
        if isinstance(title, str):
            text = title
    cat = tool_category(tool)
    state.tool_counts[cat] = state.tool_counts.get(cat, 0) + 1
    dur, outcome = _lp_oc_state(stt)
    _lp_add_step(state, new_ts, ts, "tool", tool.lower(), text, dur, outcome)


_LP_OC_OUTCOME = {"completed": STEP_OK, "error": STEP_FAILED}


def _lp_oc_state(stt: dict) -> tuple[float | None, str]:
    """An opencode tool part's own duration and outcome, from the timestamps and status it already carries.

    A part still running has no end time and no outcome; then both stay empty rather than being guessed.
    A part whose start and end are the same keeps 0.0, not None.
    """
    outcome = _LP_OC_OUTCOME.get(str(stt.get("status") or ""), "")
    timing = stt.get("time")
    if not isinstance(timing, dict):
        return None, outcome
    start = _lp_ms_to_s(timing.get("start"))
    end = _lp_ms_to_s(timing.get("end"))
    if start is None or end is None or end < start:
        return None, outcome
    return end - start, outcome


def _lp_oc_finish(state: LogState, obj: dict) -> None:
    part = obj.get("part")
    if not isinstance(part, dict):
        return
    cost = part.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        state.cost = (state.cost or 0.0) + float(cost)
    toks = part.get("tokens")
    if not isinstance(toks, dict):
        return
    cache = toks.get("cache")
    cache_read = cache.get("read") if isinstance(cache, dict) else 0
    tokens_in = _lp_num(toks.get("input"))
    tokens_out = _lp_num(toks.get("output")) + _lp_num(toks.get("reasoning"))
    tokens_cache = _lp_num(cache_read)
    state.tokens_in = _lp_add(state.tokens_in, tokens_in)
    state.tokens_out = _lp_add(state.tokens_out, tokens_out)
    state.tokens_cache = _lp_add(state.tokens_cache, tokens_cache)
    state.ctx_tokens = int(_lp_num(toks.get("input")) + _lp_num(cache_read))


def _lp_private(state: LogState) -> dict:
    """Parser-only memory that ``LogState`` has no field for; kept on the state object between reads."""
    return vars(state).setdefault("_lp_private", {})


def _lp_set_model(state: LogState, value: object) -> None:
    if not state.model and isinstance(value, str) and value and value != "<synthetic>":   # placeholder for error messages: no model
        state.model = value


def _lp_usage_row(usage: dict, fresh: tuple[str, ...], out_key: str, cache_key: str) -> tuple[int, int, int]:
    """(tokens in, tokens out, tokens cache) of one usage object; every ``fresh`` key counts as input."""
    tokens_in = sum(int(_lp_num(usage.get(key))) for key in fresh)
    return tokens_in, int(_lp_num(usage.get(out_key))), int(_lp_num(usage.get(cache_key)))


def _lp_result_text(content: object) -> str:
    """Text of a tool result: a string, or the text parts of a list of blocks."""
    if isinstance(content, list):
        parts = [part.get("text") for part in content if isinstance(part, dict)]
        value = " ".join(part for part in parts if isinstance(part, str))
    else:
        value = content if isinstance(content, str) else ""
    return " ".join(value.split())[:_LP_STEP_TEXT_MAX] or "error"


def _lp_stream_content(state: LogState, obj: dict, new_ts: list[float], plain_ts: float) -> None:
    """Steps from one claude or cursor ``assistant`` or ``user`` event.

    Tool calls and text come from assistant events only (a user prompt is not a step);
    a tool result flagged ``is_error`` is an error step. A result also gives the tool step it
    answers its outcome, and its duration from ``tool_use_result.durationMs`` or the event times.
    """
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    assistant = obj.get("type") == "assistant"
    event_ts = _lp_event_ts(obj)
    ts = plain_ts if event_ts is None else event_ts
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if assistant and kind == "tool_use":
            step = _lp_stream_tool(state, block, new_ts, ts)
            call_id = block.get("id")
            if isinstance(call_id, str) and call_id:
                _lp_stream_pending(state)[call_id] = (step, event_ts)
        elif assistant and kind == "text":
            value = block.get("text")
            if isinstance(value, str) and value.strip():
                _lp_add_step(state, new_ts, ts, "text", "", value[:_LP_STEP_TEXT_MAX])
        elif kind == "tool_result":
            _lp_stream_result(state, obj, block, new_ts, ts, event_ts)


_LP_PENDING_MAX = 256   # tool calls kept while waiting for their result; a long run must not grow this for ever


def _lp_stream_pending(state: LogState) -> dict[str, tuple[Step, float | None]]:
    """Tool steps waiting for the result that closes them: call id -> (step, event time)."""
    pending = _lp_private(state).setdefault("stream", {}).setdefault("tools", {})
    while len(pending) > _LP_PENDING_MAX:
        pending.pop(next(iter(pending)))
    return pending


def _lp_stream_result(
    state: LogState,
    obj: dict,
    block: dict,
    new_ts: list[float],
    ts: float,
    event_ts: float | None,
) -> None:
    """Close the tool step a result answers, and keep the error step an error result already made."""
    call_id = block.get("tool_use_id")
    failed = block.get("is_error") is True
    if isinstance(call_id, str) and call_id:
        entry = _lp_stream_pending(state).pop(call_id, None)
        if entry is not None:
            step, use_ts = entry
            step.outcome = STEP_FAILED if failed else STEP_OK
            dur = _lp_result_dur(obj)
            if dur is None and use_ts is not None and event_ts is not None and event_ts >= use_ts:
                dur = event_ts - use_ts
            if dur is not None:
                step.dur = dur
    if failed:
        _lp_add_step(state, new_ts, ts, "error", "", _lp_result_text(block.get("content")))
        state.errors += 1


def _lp_result_dur(obj: dict) -> float | None:
    """A claude ``user`` event's own ``tool_use_result.durationMs`` in seconds; 0.0 is a real 0 ms."""
    result = obj.get("tool_use_result")
    if not isinstance(result, dict):
        return None
    value = result.get("durationMs")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value) / 1000.0


def _lp_stream_tool(state: LogState, block: dict, new_ts: list[float], ts: float) -> Step:
    raw_tool = block.get("name")
    tool = raw_tool if isinstance(raw_tool, str) else ""
    inp = block.get("input")
    inp = inp if isinstance(inp, dict) else {}
    text = ""
    for key in _LP_STREAM_TOOL_KEYS:
        value = inp.get(key)
        if isinstance(value, str) and value:
            text = value
            break
    cat = tool_category(tool)
    state.tool_counts[cat] = state.tool_counts.get(cat, 0) + 1
    return _lp_add_step(state, new_ts, ts, "tool", tool.lower(), text[:_LP_STEP_TEXT_MAX])


def _lp_claude_store(state: LogState) -> dict:
    return _lp_private(state).setdefault("claude", {"ids": {}, "final": None, "totals": [0, 0, 0]})


def _lp_claude_totals(state: LogState) -> None:
    """Token totals of a claude stream: the final ``result`` usage once seen, else the sum over messages."""
    store = _lp_claude_store(state)
    if store["final"] is not None:
        rows = (store["final"],)
        state.tokens_in, state.tokens_out, state.tokens_cache = (sum(row[i] for row in rows) for i in range(3))
        return
    totals = store.setdefault("totals", [0, 0, 0])
    state.tokens_in, state.tokens_out, state.tokens_cache = totals


def _lp_claude_usage(state: LogState, obj: dict) -> None:
    """Keep one message's usage under its id: events that share an id count once, the last one wins."""
    message = obj.get("message")
    if not isinstance(message, dict):
        return
    msg_id = message.get("id")
    usage = message.get("usage")
    if isinstance(msg_id, str) and msg_id and isinstance(usage, dict):
        row = _lp_usage_row(usage, _LP_CLAUDE_FRESH, "output_tokens", "cache_read_input_tokens")
        store = _lp_claude_store(state)
        previous = store["ids"].get(msg_id)
        if previous is not None:
            for i in range(3):
                store["totals"][i] -= previous[i]
        store["ids"][msg_id] = row
        totals = store.setdefault("totals", [0, 0, 0])
        for i in range(3):
            totals[i] += row[i]
        window = row[0] + int(_lp_num(usage.get("cache_read_input_tokens")))
        if window > 0:      # zero usage = synthetic message, keep the last real window
            state.ctx_tokens = window
        _lp_claude_totals(state)


def _lp_claude_result(state: LogState, obj: dict) -> None:
    """The closing ``result`` event: the run's cost, final usage and the model's context window."""
    cost = obj.get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        state.cost = float(cost)
    usage = obj.get("usage")
    if isinstance(usage, dict):
        row = _lp_usage_row(usage, _LP_CLAUDE_FRESH, "output_tokens", "cache_read_input_tokens")
        _lp_claude_store(state)["final"] = row
        _lp_claude_totals(state)
    window = _lp_model_window(obj.get("modelUsage"))
    if window is not None:
        state.ctx_window = window


def _lp_model_window(model_usage: object) -> int | None:
    """The largest ``contextWindow`` in a claude ``result.modelUsage`` block; None when it has none."""
    if not isinstance(model_usage, dict):
        return None
    windows = [
        row.get("contextWindow") for row in model_usage.values() if isinstance(row, dict)
    ]
    windows = [w for w in windows if isinstance(w, int) and not isinstance(w, bool) and w > 0]
    return max(windows) if windows else None


def _lp_process_claude(
    state: LogState, lines: list[str], new_ts: list[float], plain_ts: float
) -> None:
    """Claude Code ``-p --output-format stream-json`` events, one JSON object per line."""
    for raw in lines:
        obj = _lp_try_json(raw.strip())
        if obj is None:
            continue
        kind = obj.get("type")
        if kind == "system":
            if obj.get("subtype") == "init":
                _lp_set_model(state, obj.get("model"))
        elif kind == "assistant":
            _lp_stream_content(state, obj, new_ts, plain_ts)
            _lp_claude_usage(state, obj)
        elif kind == "user":
            _lp_stream_content(state, obj, new_ts, plain_ts)
        elif kind == "result":
            _lp_claude_result(state, obj)


def _lp_process_cursor(
    state: LogState, lines: list[str], new_ts: list[float], plain_ts: float
) -> None:
    """cursor-agent ``-p --output-format stream-json`` events; its ``result`` has usage but no cost."""
    for raw in lines:
        obj = _lp_try_json(raw.strip())
        if obj is None:
            continue
        kind = obj.get("type")
        if kind == "system":
            if obj.get("subtype") == "init":
                _lp_set_model(state, obj.get("model"))
        elif kind in ("assistant", "user"):
            _lp_stream_content(state, obj, new_ts, plain_ts)
        elif kind == "result":
            usage = obj.get("usage")
            if isinstance(usage, dict):
                row = _lp_usage_row(usage, _LP_CURSOR_FRESH, "outputTokens", "cacheReadTokens")
                state.tokens_in, state.tokens_out, state.tokens_cache = row


def _lp_codex_json(lines: list[str]) -> bool:
    """True when these lines are codex ``rollout-*.jsonl`` items rather than the exec text log."""
    for raw in lines:
        line = raw.strip()
        if line:
            return _lp_rollout_item(_lp_try_json(line))
    return False


def _lp_process_codex_json(
    state: LogState, lines: list[str], new_ts: list[float], plain_ts: float
) -> None:
    """One codex rollout session: each item carries its own ISO ``timestamp``."""
    for raw in lines:
        obj = _lp_try_json(raw.strip())
        if obj is None:
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        ts = _lp_event_ts(obj)
        if ts is None:
            ts = plain_ts
        kind = obj.get("type")
        if kind == "event_msg":
            window = _lp_codex_window(payload)
            if window is not None:
                state.ctx_window = window
        elif kind == "turn_context":
            _lp_set_model(state, payload.get("model"))
        elif kind == "response_item":
            _lp_codex_item(state, payload, ts, new_ts)


def _lp_codex_window(payload: dict) -> int | None:
    """A codex ``event_msg``'s ``model_context_window``, at the payload root or under ``info``."""
    for row in (payload, payload.get("info")):
        if not isinstance(row, dict):
            continue
        value = row.get("model_context_window")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _lp_codex_item(state: LogState, payload: dict, ts: float, new_ts: list[float]) -> None:
    """One rollout ``response_item``: a tool call, the output that closes it, or assistant text."""
    ptype = payload.get("type")
    if ptype in _LP_CODEX_CALLS:
        raw_tool = payload.get("name")
        tool = raw_tool if isinstance(raw_tool, str) and raw_tool else "shell"
        text = payload.get("input")
        if not isinstance(text, str):
            text = payload.get("arguments")
        text = text if isinstance(text, str) else ""
        cat = tool_category(tool)
        state.tool_counts[cat] = state.tool_counts.get(cat, 0) + 1
        step = _lp_add_step(state, new_ts, ts, "tool", tool.lower(), text[:_LP_STEP_TEXT_MAX])
        call_id = payload.get("call_id")
        if isinstance(call_id, str) and call_id:
            _lp_stream_pending(state)[call_id] = (step, ts)
    elif ptype in _LP_CODEX_OUTPUTS:
        call_id = payload.get("call_id")
        if isinstance(call_id, str) and call_id:
            entry = _lp_stream_pending(state).pop(call_id, None)
            if entry is not None:
                step, call_ts = entry
                if ts >= call_ts:
                    step.dur = ts - call_ts
    elif ptype == "message" and payload.get("role") == "assistant":
        text = _lp_content_text(payload.get("content"))
        if text.strip():
            _lp_add_step(state, new_ts, ts, "text", "", text)


def _lp_content_text(content: object) -> str:
    """The text of a message body: a string, or the text of a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [block.get("text") for block in content if isinstance(block, dict)]
        return " ".join(part for part in parts if isinstance(part, str))
    return ""


def _lp_process_codex(
    state: LogState,
    lines: list[str],
    new_ts: list[float],
    plain_ts: float,
    first: bool,
) -> None:
    """Turn codex log lines into steps; see ``_lp_codex_marker`` for the block layout.

    ``state.codex_mode`` carries the block across reads, so a read that stops in
    the middle of a command's output resumes there, not as text. A ``rollout-*.jsonl``
    session is JSON items instead, and goes to ``_lp_process_codex_json``.
    """
    if _lp_codex_json(lines):
        _lp_process_codex_json(state, lines, new_ts, plain_ts)
        return
    in_header = first
    delims = 0
    model_id = ""
    effort = ""
    for raw in lines:
        line = raw.strip()
        if in_header:
            if line == "--------":
                delims += 1
                if delims >= 2:
                    in_header = False
                    if model_id:
                        state.model = model_id + (f" ({effort})" if effort else "")
                continue
            if line.startswith("model:"):
                model_id = line.split(":", 1)[1].strip()
                if model_id and effort:
                    state.model = f"{model_id} ({effort})"
                continue
            if line.startswith("reasoning effort:"):
                effort = line.split(":", 1)[1].strip()
                if model_id and effort:
                    state.model = f"{model_id} ({effort})"
                continue
            continue
        marker = raw.rstrip()
        mode = state.codex_mode
        if mode == "tokens":
            digits = line.replace(",", "").replace(" ", "")
            if digits.isdigit():
                _lp_codex_tokens(state, int(digits))
            state.codex_mode = ""
            continue
        # a banner between blocks starts a new run; inside command output it is just output
        if mode in ("", "message") and line.startswith("OpenAI Codex"):
            _lp_codex_new_run(state)
            state.codex_mode = ""
            continue
        if mode == "user" and marker not in ("codex", "exec"):
            continue
        if _lp_codex_marker(state, marker, new_ts, plain_ts):
            continue
        if not line:
            continue
        new_ts.append(plain_ts)
        if mode == "agent":
            _lp_push_step(state, plain_ts, "text", "", line)
            state.codex_mode = "message"
        elif mode == "message":
            _lp_codex_extend(state, line)
        elif mode == "exec":
            _lp_push_step(state, plain_ts, "tool", "shell", line)
            state.tool_counts["shell"] = state.tool_counts.get("shell", 0) + 1
            state.codex_mode = "command"
        elif mode == "command":
            result = _LP_CODEX_RESULT_RE.match(raw)
            if result:
                _lp_codex_finish_command(state, plain_ts, result.group(1), result.group(2))
            else:
                _lp_codex_extend(state, line)
        elif mode == "patch":
            if raw.startswith("/"):
                _lp_push_step(state, plain_ts, "tool", "edit", line)
                state.tool_counts["edit"] = state.tool_counts.get("edit", 0) + 1
        elif mode == "":   # between blocks: only a column-0 error line counts
            if _LP_ERR_RE.match(raw) or _LP_CODEX_STREAM_RE.match(raw):
                _lp_push_step(state, plain_ts, "error", "", line)
                state.errors += 1
        # mode "output": command output, never a step


def _lp_codex_marker(
    state: LogState, marker: str, new_ts: list[float], plain_ts: float
) -> bool:
    """Handle a codex block marker; return False for any other line.

    A marker closes the block before it. Command output runs until the next
    marker or the result line that ends its command, so echoed file contents
    and command output never become steps.
    """
    if state.codex_mode == "command":
        _lp_codex_strip_cwd(state)
    if marker == "codex":
        state.codex_mode = "agent"
    elif marker == "exec":
        state.codex_mode = "exec"
    elif marker == "user":
        state.codex_mode = "user"
    elif marker == "tokens used":
        state.codex_mode = "tokens"
    elif marker == "apply patch":
        state.codex_mode = "patch"
    elif marker.startswith("hook:"):
        state.codex_mode = ""
    elif marker.startswith("web search:"):
        state.codex_mode = ""
        query = " ".join(marker[len("web search:"):].split())
        if query:
            _lp_push_step(state, plain_ts, "tool", "websearch", query)
            new_ts.append(plain_ts)
            state.tool_counts["search"] = state.tool_counts.get("search", 0) + 1
    else:
        return False
    return True


_LP_STEP_TEXT_MAX = 400  # steps render as one line; longer text is never shown


def _lp_codex_extend(state: LogState, line: str) -> None:
    """Append a continuation line to the step just opened (a multi-line message or command)."""
    last = state.steps[-1]
    if len(last.text) >= _LP_STEP_TEXT_MAX:
        return
    last.text = " ".join(f"{last.text} {line[:_LP_STEP_TEXT_MAX]}".split())[:_LP_STEP_TEXT_MAX]


def _lp_codex_strip_cwd(state: LogState) -> None:
    """Drop the trailing ` in <cwd>` codex appends to a command's last line."""
    last = state.steps[-1]
    last.text = _LP_CODEX_CWD_RE.sub("", last.text)


# exit 1 from these means "no match" or "differs", not a failure
_LP_CODEX_EXIT1_OK_RE = re.compile(r"^(?:\S*bash -lc\s+)?[\"']?\s*(?:grep|egrep|fgrep|rg|diff|cmp|test)\b")


def _lp_codex_dur(value: str | None) -> float | None:
    """The duration codex prints in its result line, in seconds; None when it is not one it knows.

    Codex has no start time for a command, so the result line's own figure is the only duration there is.
    """
    if not value:
        return None
    match = _LP_CODEX_DUR_RE.match(value.strip())
    if not match:
        return None
    amount = float(match.group(1))
    if match.group(2) == "ms":
        return amount / 1000.0
    return amount


def _lp_codex_finish_command(
    state: LogState, plain_ts: float, exit_code: str | None, dur_text: str | None = None
) -> None:
    """Close the command step; a non-zero exit is an error step (except a search finding nothing)."""
    _lp_codex_strip_cwd(state)
    cmd = state.steps[-1].text
    if exit_code == "1" and _LP_CODEX_EXIT1_OK_RE.match(cmd) and not re.search(r"[;&|]", cmd):
        exit_code = None
    state.steps[-1].outcome = STEP_OK if exit_code in (None, "0") else STEP_FAILED
    state.steps[-1].dur = _lp_codex_dur(dur_text)
    if exit_code not in (None, "0"):
        _lp_push_step(state, plain_ts, "error", "", f"command exited {exit_code}")
        state.errors += 1
    state.codex_mode = "output"


def _lp_codex_tokens(state: LogState, value: int) -> None:
    """Record one ``tokens used`` value: a run counts its last value, and the runs add up.

    A value lower than the one before it starts a new run, the same as a banner does.
    """
    store = _lp_private(state).setdefault("codex", {"done": 0, "last": None})
    if store["last"] is not None and value < store["last"]:
        _lp_codex_new_run(state)
    store["last"] = value
    state.tokens_in = store["done"] + value


def _lp_codex_new_run(state: LogState) -> None:
    """Close the current codex run: its last ``tokens used`` value joins the total."""
    store = _lp_private(state).setdefault("codex", {"done": 0, "last": None})
    if store["last"] is not None:
        store["done"] += store["last"]
        store["last"] = None


def _lp_clamp(ts: float, now: float) -> float:
    """Clamp an event time into ``[0, now]``; bad values become ``0.0``."""
    if not math.isfinite(ts) or ts < 0:
        return 0.0
    return min(ts, now)


def _lp_add_pulses(
    state: LogState,
    new_ts: list[float],
    plain_ts: float,
    timed: bool,
    now: float,
) -> None:
    if not new_ts:
        return
    if not timed:
        state.pulses.append((_lp_clamp(plain_ts, now), len(new_ts)))
    else:
        counts: dict[int, int] = {}
        for ts in new_ts:
            key = int(_lp_clamp(ts, now))
            counts[key] = counts.get(key, 0) + 1
        for key, count in counts.items():
            merged = False
            for index, (pts, pcount) in enumerate(state.pulses):
                if int(pts) == key:
                    state.pulses[index] = (pts, pcount + count)
                    merged = True
                    break
            if not merged:
                state.pulses.append((float(key), count))
    limit = ACT_BUCKETS * 4
    if len(state.pulses) > limit:
        state.pulses.sort(key=lambda pulse: pulse[0])
        del state.pulses[: len(state.pulses) - limit]
