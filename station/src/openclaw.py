"""OpenClaw (openclaw) per-turn data and session vitals, read from the agent database.

Read-only: every connection is ``file:...?mode=ro`` and is closed by the caller. A turn is one
``run_id`` of ``trajectory_runtime_events``; the last ``model.completed`` of a run is its outcome
and the earlier ones are retries. Turn history is unioned over every ``session_windows`` row of the
session key, not only its current window. A missing or unreadable database gives no turns and no
vitals; nothing here guesses a value.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Self

from model import TOOL_RING_S, Turn, TurnTool, Vitals

ASK_MAX = 120        # characters of the user message kept as the turn's ASK
ARGS_MAX = 80        # characters of a tool call's arguments kept as the gist
CALLS_MAX = 40       # tool calls kept per turn
TURNS_MAX = 200      # turns kept per session key
_ERROR_MAX = 200     # characters of an attempt's error kept

_EVENT_TYPES = ("prompt.submitted", "model.completed", "tool.call", "tool.result")
_LOCAL_PROVIDERS = frozenset({"llamacpp", "llama.cpp", "ollama", "lmstudio", "local"})
_VITALS_STATUS = {"running": "running", "done": "done", "failed": "failed", "killed": "killed",
                  "timeout": "timeout", "timed_out": "timeout", "error": "failed", "errored": "failed"}
# an argument that looks like a credential or an environment assignment is dropped, never shown
_SECRET_RE = re.compile(r"(?:token|secret|passw|api[_-]?key|authori[sz]ation)\w*\s*[:=]|bearer\s|"
                        r"sk-[A-Za-z0-9]{8}|xox[bp]-|ghp_|-----BEGIN", re.IGNORECASE)
_ENV_RE = re.compile(r"(?:^|[\s,{])(?:[A-Z][A-Z0-9_]{2,})=")


def _stamp(path: str) -> tuple[int, int] | None:
    """(mtime, size) of a file; None when it does not exist."""
    try:
        found = os.stat(path)
    except OSError:
        return None
    return (found.st_mtime_ns, found.st_size)


def _db_stamp(db_path: str) -> tuple | None:
    """The database's stamp together with its ``-wal``, so a write that only reached the WAL is seen."""
    main = _stamp(db_path)
    if main is None:
        return None
    return (main, _stamp(db_path + "-wal"))


def _float(value: object) -> float | None:
    """A number as a float, ignoring bools and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int(value: object) -> int | None:
    found = _float(value)
    return int(found) if found is not None else None


def _seconds(value: object) -> float | None:
    """Epoch seconds from a stored stamp in seconds or milliseconds; None when absent or not a number."""
    found = _float(value)
    if found is None or found <= 0:
        return None
    return found / 1000.0 if found > 100_000_000_000 else found


def _cost(provider: str, value: object) -> float | None:
    """USD when the provider is metered; None for a local model and for a missing or zero cost."""
    found = _float(value)
    if found is None or found <= 0 or provider.strip().lower() in _LOCAL_PROVIDERS:
        return None
    return found


def _line(text: object, limit: int) -> str:
    """One line of a possibly multi-line text, collapsed and cut to ``limit`` characters."""
    if not isinstance(text, str):
        return ""
    line = text.strip().split("\n", 1)[0].strip()
    line = " ".join(line.split())
    return line if len(line) <= limit else line[: max(1, limit - 1)].rstrip() + "…"


def _gist(args: object) -> str:
    """A one-line argument gist; "" for arguments that look like a credential or an env assignment."""
    if args is None:
        return ""
    if isinstance(args, str):
        text = args
    else:
        try:
            text = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(args)
    text = " ".join(text.split())
    if _SECRET_RE.search(text) or _ENV_RE.search(text):
        return ""
    return _line(text, ARGS_MAX)


def _message_text(content: object) -> str:
    """The text of one message's content (a string, or a list of typed parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content
                        if isinstance(part, dict) and part.get("type") == "text")
    return ""


def _ask(data: dict, prompt: object) -> str:
    """The turn's user message: the final prompt, else the last user message of the snapshot, else the prompt sent."""
    final = data.get("finalPromptText")
    if isinstance(final, str) and final.strip():
        return _line(final, ASK_MAX)
    snapshot = data.get("messagesSnapshot")
    if isinstance(snapshot, list):
        for message in reversed(snapshot):
            if isinstance(message, dict) and message.get("role") == "user":
                text = _message_text(message.get("content"))
                if text.strip():
                    return _line(text, ASK_MAX)
    return _line(prompt, ASK_MAX)


def _outcome(data: dict) -> str:
    """A finished attempt's outcome: aborted, error or ok (running when the attempt is not there)."""
    if not data:
        return "running"
    if data.get("aborted") is True or data.get("stopReason") == "aborted":
        return "aborted"
    if data.get("stopReason") == "error" or data.get("promptError"):
        return "error"
    return "ok"


def _attempt_error(data: dict) -> str:
    """One line why an attempt failed; "" when it did not fail or the event carries no reason."""
    error = data.get("promptError")
    if isinstance(error, str) and error.strip():
        return _line(error, _ERROR_MAX)
    if data.get("stopReason") == "error":
        texts = data.get("assistantTexts")
        if isinstance(texts, list):
            for text in reversed(texts):
                if isinstance(text, str) and text.strip():
                    return _line(text, _ERROR_MAX)
    return ""


def _ok(value: object) -> bool | None:
    """A tool result's success flag; None when the event does not say."""
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    return None


def _call_ts(event: dict, created: float | None) -> float:
    """A tool event's time: the stored stamp, else the event's own timestamp."""
    return created or _seconds(event.get("ts")) or 0.0


def _turn(run: dict) -> Turn | None:
    """One run as a Turn; None when it has no usable time."""
    completions: list[tuple[float, dict, dict]] = run["completions"]
    final = completions[-1] if completions else None
    asked = run["start"]          # 0.0 when the run carries no prompt.submitted event
    start = asked or (final[0] if final else 0.0)
    if start <= 0:
        return None
    data = final[2] if final else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    provider = str((final[1].get("provider") if final else "") or run["provider"] or "")
    model = str((final[1].get("modelId") if final else "") or run["model"] or "")
    cost = _cost(provider, (usage.get("cost") or {}).get("total") if isinstance(usage.get("cost"), dict) else None)
    end = (final[0] or None) if final else None
    errors = [_attempt_error(attempt[2]) for attempt in completions]
    error = next((text for text in errors if text), "")
    calls = [TurnTool(ts=call["ts"], name=call["name"], ok=call["ok"], dur=call["dur"], args=call["args"])
             for call in run["calls"]]
    tools = Counter(call.name for call in calls if call.name)
    return Turn(
        run_id=run["id"], start=start, end=end, ask=_ask(data, run["prompt"]),
        model=model, provider=provider,
        tokens_in=_int(usage.get("input")), tokens_out=_int(usage.get("output")),
        tokens_cache=_int(usage.get("cacheRead")), cost=cost,
        latency=end - asked if end is not None and asked and end >= asked else None,
        tools=dict(tools.most_common()), calls=calls[-CALLS_MAX:],
        attempts=len(completions) or 1, outcome=_outcome(data), error=error)


def _load_turns(conn: sqlite3.Connection, session_key: str) -> list[Turn]:
    """Every turn of a session key over all of its windows; [] when it has none."""
    try:
        ids = [row[0] for row in conn.execute(
            "SELECT session_id FROM session_windows WHERE session_key = ?", (session_key,)) if row[0]]
        current = conn.execute("SELECT current_session_id FROM session_nodes WHERE session_key = ?",
                               (session_key,)).fetchone()
    except sqlite3.Error:
        return []
    if current and current[0] and current[0] not in ids:
        ids.append(current[0])
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    kinds = ",".join("?" * len(_EVENT_TYPES))
    try:
        rows = conn.execute(
            f"SELECT session_id, seq, run_id, created_at, event_json FROM trajectory_runtime_events "
            f"WHERE session_id IN ({marks}) AND json_extract(event_json,'$.type') IN ({kinds}) "
            f"ORDER BY created_at, seq", (*ids, *_EVENT_TYPES)).fetchall()
    except sqlite3.Error:
        return []
    runs: dict[str, dict] = {}
    order: list[str] = []
    for session_id, seq, run_id, created_at, event_json in rows:
        try:
            event = json.loads(event_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        key = run_id if isinstance(run_id, str) and run_id else f"{session_id}:{seq}"
        run = runs.get(key)
        if run is None:
            run = runs[key] = {"id": key, "start": 0.0, "prompt": "", "model": "", "provider": "",
                               "completions": [], "calls": []}
            order.append(key)
        created = _seconds(created_at)
        if kind == "prompt.submitted":
            run["start"] = created or _seconds(event.get("ts")) or run["start"]
            run["prompt"] = data.get("prompt") if isinstance(data.get("prompt"), str) else run["prompt"]
            run["model"] = str(event.get("modelId") or run["model"] or "")
            run["provider"] = str(event.get("provider") or run["provider"] or "")
        elif kind == "model.completed":
            stamp = created or _seconds(event.get("ts")) or 0.0
            run["completions"].append((stamp, event, data))
        elif kind == "tool.call":
            name = str(data.get("name") or "")
            run["calls"].append({"ts": _call_ts(event, created), "name": name, "id": str(data.get("toolCallId") or ""),
                                 "args": _gist(data.get("args") if data.get("args") is not None
                                               else data.get("arguments")),
                                 "ok": None, "dur": None})
        elif kind == "tool.result":
            call_id = str(data.get("toolCallId") or "")
            for call in reversed(run["calls"]):
                if call["id"] == call_id and call["ok"] is None:
                    call["ok"] = _ok(data.get("success"))
                    result = _call_ts(event, created)
                    call["dur"] = result - call["ts"] if result >= call["ts"] else None
                    break
    turns: list[Turn] = []
    for key in order:
        run = runs[key]
        run["completions"].sort(key=lambda attempt: attempt[0])
        run["calls"].sort(key=lambda call: call["ts"])
        found = _turn(run)
        if found is not None:
            turns.append(found)
    turns.sort(key=lambda turn: turn.start)
    return turns[-TURNS_MAX:]


def _load_vitals(conn: sqlite3.Connection, session_key: str) -> Vitals | None:
    """The vitals strip of one session from its ``session_nodes`` row; None when there is no usable row."""
    try:
        row = conn.execute("SELECT entry_json, status FROM session_nodes WHERE session_key = ?",
                           (session_key,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    try:
        entry = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(entry, dict):
        return None
    budget = entry.get("contextBudgetStatus") if isinstance(entry.get("contextBudgetStatus"), dict) else {}
    provider = str(entry.get("modelProvider") or "")
    status = str(entry.get("status") or row[1] or "").strip().lower()
    return Vitals(
        model=str(entry.get("model") or ""), provider=provider,
        ctx_used=_int(budget.get("estimatedPromptTokens")), ctx_budget=_int(entry.get("contextTokens")),
        compactions=max(0, _int(entry.get("compactionCount")) or 0),
        cost=_cost(provider, entry.get("estimatedCostUsd")), runtime=_seconds_runtime(entry.get("runtimeMs")),
        status=_VITALS_STATUS.get(status, ""),
        last_error=_line(entry.get("lastRunError"), _ERROR_MAX))


def _seconds_runtime(value: object) -> float | None:
    """runtimeMs as seconds; None when absent."""
    found = _float(value)
    return found / 1000.0 if found is not None and found >= 0 else None


@dataclass
class _Data:
    """One generation of a database read, kept while the database and its WAL keep their stamp."""
    stamp: tuple = ()
    turns: dict[str, list[Turn]] = field(default_factory=dict)
    vitals: dict[str, Vitals | None] = field(default_factory=dict)


_cache_lock = threading.Lock()
_cache: dict[str, tuple[tuple, _Data]] = {}
_CACHE_MAX = 4          # agent databases remembered at once


def _data(db_path: str) -> _Data:
    """The cached read for this database, or a fresh empty generation when it changed."""
    stamp = _db_stamp(db_path) or ()
    if not stamp:
        return _Data()
    with _cache_lock:
        cached = _cache.get(db_path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
    fresh = _Data(stamp)
    with _cache_lock:
        _cache[db_path] = (stamp, fresh)
        while len(_cache) > _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
    return fresh


def clear_cache() -> None:
    """Forget every cached read (tests and a database that was replaced in place)."""
    with _cache_lock:
        _cache.clear()


class Reader:
    """One read-only connection for one snapshot; turns and vitals are cached per database stamp."""

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.expanduser(db_path)
        self._conn: sqlite3.Connection | None = None
        self._data = _data(self.db_path)

    def _connect(self) -> sqlite3.Connection | None:
        if self._conn is None:
            try:
                self._conn = sqlite3.connect("file:" + self.db_path + "?mode=ro", uri=True, timeout=1.0)
            except (OSError, sqlite3.Error):
                return None
        return self._conn

    def turns(self, session_key: str, since: float = 0.0, limit: int = TURNS_MAX) -> list[Turn]:
        """Turns of one session key, oldest first: at most ``limit`` newest ones, started at ``since`` or later."""
        if session_key not in self._data.turns:
            conn = self._connect()
            self._data.turns[session_key] = _load_turns(conn, session_key) if conn is not None else []
        turns = self._data.turns[session_key]
        if since > 0:
            turns = [turn for turn in turns if turn.start >= since]
        return turns[-limit:] if limit > 0 else []

    def vitals(self, session_key: str) -> Vitals | None:
        """The vitals strip of one session key; None when its row is missing or unreadable."""
        if session_key not in self._data.vitals:
            conn = self._connect()
            self._data.vitals[session_key] = _load_vitals(conn, session_key) if conn is not None else None
        return self._data.vitals[session_key]

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def read_turns(db_path: str, session_key: str, since: float = 0.0, limit: int = TURNS_MAX) -> list[Turn]:
    """Turns of one openclaw session; one read-only connection, closed before returning."""
    with Reader(db_path) as reader:
        return reader.turns(session_key, since, limit)


def read_vitals(db_path: str, session_key: str) -> Vitals | None:
    """The vitals of one openclaw session; one read-only connection, closed before returning."""
    with Reader(db_path) as reader:
        return reader.vitals(session_key)


def tool_pulses(turns: list[Turn], now: float) -> list[tuple[float, int]]:
    """(epoch second, calls) for the turns' tool calls inside the last ``TOOL_RING_S``, oldest first."""
    cutoff = now - TOOL_RING_S
    per_second: Counter[float] = Counter(float(int(call.ts)) for turn in turns for call in turn.calls
                                         if call.ts >= cutoff)
    return sorted(per_second.items())
