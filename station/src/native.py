"""Native execution evidence. Never assign a transcript to a process by cwd alone.

Supported stores (all read-only):
- Codex / T3 Code: ~/.codex/sessions/**/*.jsonl. T3 is split out only when session_meta originator names T3.
- Claude personal / work: ~/.claude*/projects/**/*.jsonl; subagent files link to the parent sessionId.
- OpenCode: ~/.local/share/opencode/opencode.db (session_v2 + session_message).
- OpenClaw (openclaw agent only): ~/.openclaw/agents/openclaw/agent/openclaw-agent.sqlite session_nodes.entry_json.
- Cursor: ~/.cursor/projects/*/agent-transcripts/*/*.jsonl, with cwd/times from only the
  schemaVersion, createdAtMs, updatedAtMs and cwd fields of ~/.cursor/chats/*/*/meta.json.
  store.db is opaque and never read. Task children match an exact <user_query> prompt after
  CRLF normalization and outer whitespace trimming; ambiguous matches remain unlinked.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from model import (
    ACTIVE_S,
    QUIET_S,
    RESCAN_S,
    TOOL_RING_MAX,
    TOOL_RING_S,
    ExecutionEvidence,
    ExecutionRecord,
)
from registry import MAX_ARTIFACT_BYTES, execution_key

__all__ = ["execution_key", "read_native_executions"]
_nv_cache: dict[str, dict] = {}
_NV_HEAD = 4096
_NV_TAIL = 256
_NV_HOPS = 32
_NV_SCAN_LIMIT = 400
_NV_OVERSIZE_NOTE = "(native event over 16 MiB not loaded)"
_NV_TEXT_PARTS = ("input_text", "output_text", "text")
_NV_STORED_CAP = 1024 * 1024          # max UTF-8 bytes kept for a stored brief or result
_NV_TRUNC_MARK = "\n[truncated]"
_NV_CURSOR_HEAD = 64 * 1024
_NV_CURSOR_TAIL = 64 * 1024
_nv_cursor_cache: dict[str, dict] = {}
# Metrics parsed by the native scan, readable by the collector without touching the record: (harness, native id) -> dict.
_nv_metrics: dict[tuple[str, str], dict] = {}
_nv_metric_paths: dict[tuple[str, str], str] = {}


def _nv_time(value: object) -> float:
    if type(value) in (int, float):
        return float(value) / 1000 if value > 100_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            pass
    return 0.0


def _nv_int(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _nv_sum(*values: int | None) -> int | None:
    """The sum of the counters that were read; None when every one is an absent column or SQL NULL.

    A measured 0 therefore stays 0, while a store that does not carry the counter at all stays unknown.
    """
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _nv_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(part.get("text", "")) for part in value
                         if isinstance(part, dict) and part.get("type") in _NV_TEXT_PARTS)
    return ""


def _nv_cap(text: str) -> str:
    """Bound a stored brief/result to _NV_STORED_CAP UTF-8 bytes, ending in a '[truncated]' marker line."""
    if len(text) <= _NV_STORED_CAP // 4:
        return text                      # every char is at most 4 UTF-8 bytes: already within the cap
    raw = text.encode("utf-8", "replace")
    if len(raw) <= _NV_STORED_CAP:
        return text
    keep = _NV_STORED_CAP - len(_NV_TRUNC_MARK.encode("utf-8"))
    return raw[:keep].decode("utf-8", "ignore") + _NV_TRUNC_MARK


def _nv_goal(brief: str) -> tuple[str, str]:
    if not brief:
        return "", ""
    match = re.search(r"^Goal:\s*(.+)$", brief, re.MULTILINE | re.IGNORECASE)
    if match:
        return match.group(1).strip(), "native task"
    return " ".join(brief.split())[:240], "native excerpt"


def _nv_sig(path: Path, start: int, length: int) -> str:
    with path.open("rb") as stream:
        stream.seek(start)
        return hashlib.sha256(stream.read(length)).hexdigest()


def _nv_lines(stream, limit: int):
    """Yield (begin, raw, complete). Oversized lines yield raw=None and are consumed only once terminated."""
    while True:
        begin = stream.tell()
        raw = stream.readline(limit + 1)
        if not raw:
            return
        if len(raw) > limit:
            while not raw.endswith(b"\n"):
                chunk = stream.readline(limit + 1)
                if not chunk:
                    stream.seek(begin)   # still being appended: retry the whole line later
                    return
                raw = chunk
            yield begin, None, True
            continue
        yield begin, raw, raw.endswith(b"\n")


def _nv_start(obj: dict, body: dict, family: str, account: str, path: Path, stamp: float) -> ExecutionRecord | None:
    typ = obj.get("type")
    if family == "codex":
        if typ != "session_meta":
            return None
        sid = str(body.get("id", body.get("session_id", "")))
        if not sid:
            return None
        source = body.get("source")
        sub = source.get("subagent") if isinstance(source, dict) else None
        spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
        parent = str(spawn.get("parent_thread_id", "")) if isinstance(spawn, dict) else ""
        tokens = re.findall(r"[a-z0-9]+", str(body.get("originator", "")).lower())
        actual = "t3" if "t3" in tokens else "codex"
        return ExecutionRecord(execution_key(actual, account, sid), harness=actual, account=account, native_id=sid,
                               role="worker" if parent or source == "exec" else "lead", cwd=str(body.get("cwd", "")),
                               parent_id=execution_key(actual, account, parent) if parent else "",
                               linkage="native" if parent else "unknown",
                               started=stamp or _nv_time(body.get("timestamp")), source=str(path))
    if family == "claude" and obj.get("sessionId"):
        parent = str(obj["sessionId"])
        child = str(obj.get("agentId", "")) if "subagents" in path.parts[:-1] else ""
        headless = obj.get("entrypoint") in ("sdk-cli", "sdk-ts") or obj.get("promptSource") == "sdk"
        return ExecutionRecord(execution_key("claude", account, child or parent), harness="claude", account=account,
                               native_id=child or parent, role="worker" if child or headless else "lead", cwd=str(obj.get("cwd", "")),
                               title=_nv_subagent_description(path) if child else "",
                               parent_id=execution_key("claude", account, parent) if child else "",
                               linkage="native" if child else "unknown", started=stamp, source=str(path))
    return None


def _nv_subagent_description(path: Path) -> str:
    """The task a Claude subagent was given: the ``description`` in the ``.meta.json`` beside its transcript."""
    try:
        with open(path.with_suffix(".meta.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return ""
    text = data.get("description") if isinstance(data, dict) else ""
    return text.strip()[:80] if isinstance(text, str) else ""


def _nv_codex_counts(usage: object) -> tuple[int, int, int] | None:
    """(input, output incl. reasoning, cache) counters of one codex usage object."""
    if not isinstance(usage, dict):
        return None
    return (_nv_int(usage.get("input_tokens")) or 0,
            (_nv_int(usage.get("output_tokens")) or 0) + (_nv_int(usage.get("reasoning_output_tokens")) or 0),
            (_nv_int(usage.get("cached_input_tokens")) or 0) + (_nv_int(usage.get("cache_write_input_tokens")) or 0))


def _nv_codex_window(payload: dict) -> int | None:
    """A codex event's ``model_context_window``, at the payload root or under ``info``."""
    for row in (payload, payload.get("info")):
        if isinstance(row, dict):
            value = row.get("model_context_window")
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
    return None


def _nv_opencode_window(model_id: object, reported: object = None) -> int | None:
    """Known OpenCode model windows, unless the database reports one directly."""
    if type(reported) is int and reported > 0:
        return reported
    if not isinstance(model_id, str):
        return None
    model = model_id.lower()
    if "claude" in model and re.search(r"(?:haiku|sonnet|opus)[- .]?5", model):
        return 1_000_000 if "1m" in model else 200_000
    if "deepseek" in model and "v4" in model:
        return 128_000
    return None


def _nv_codex_metrics(record: ExecutionRecord, st: dict, typ: object, payload: dict) -> None:
    """Codex rollout usage (cumulative thread counters) and the model's context window."""
    total = last = None
    if typ == "token_usage_record":
        total, last = payload.get("thread_token_usage") or payload.get("usage"), payload.get("usage")
    elif typ == "event_msg":
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        if payload.get("type") == "token_count":
            total, last = info.get("total_token_usage"), info.get("last_token_usage")
        window = _nv_codex_window(payload)
        if window is not None:
            st["ctx_window"] = window
    counts = _nv_codex_counts(total)
    if counts is not None:
        record.tokens_in, record.tokens_out, record.tokens_cache = counts
    last_counts = _nv_codex_counts(last)
    if last_counts is not None and last_counts[0] > 0:
        record.context_tokens = last_counts[0]   # the last request's input is the context in use


def _nv_model_window(model_usage: object) -> int | None:
    """The largest ``contextWindow`` in a claude ``modelUsage`` block; None when it has none."""
    if not isinstance(model_usage, dict):
        return None
    windows = [row.get("contextWindow") for row in model_usage.values() if isinstance(row, dict)]
    windows = [value for value in windows if isinstance(value, int) and not isinstance(value, bool) and value > 0]
    return max(windows) if windows else None


def _nv_claude_metrics(record: ExecutionRecord, st: dict, obj: dict) -> None:
    """Cumulative Claude usage (one count per message id) and the model's context window."""
    typ = obj.get("type")
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
    msg_id = message.get("id")
    if typ == "assistant" and usage is not None and isinstance(msg_id, str) and msg_id:
        row = ((_nv_int(usage.get("input_tokens")) or 0) + (_nv_int(usage.get("cache_creation_input_tokens")) or 0),
               _nv_int(usage.get("output_tokens")) or 0,
               _nv_int(usage.get("cache_read_input_tokens")) or 0)
        ids = st.setdefault("usage_ids", {})
        totals = st.setdefault("usage_totals", [0, 0, 0])
        previous = ids.get(msg_id)
        if previous is not None:
            for index in range(3):
                totals[index] -= previous[index]
        ids[msg_id] = row
        for index, value in enumerate(row):
            totals[index] += value
        record.tokens_in, record.tokens_out, record.tokens_cache = totals
        context = row[0] + row[2]
        if context > 0:      # zero usage = a synthetic message: keep the last real context
            record.context_tokens = context
    window = _nv_model_window(obj.get("modelUsage"))
    if window is not None:
        st["ctx_window"] = window
    result_usage = obj.get("usage") if typ == "result" and isinstance(obj.get("usage"), dict) else None
    if result_usage is not None:
        record.tokens_in = (_nv_int(result_usage.get("input_tokens")) or 0) + (
            _nv_int(result_usage.get("cache_creation_input_tokens")) or 0)
        record.tokens_out = _nv_int(result_usage.get("output_tokens")) or 0
        record.tokens_cache = _nv_int(result_usage.get("cache_read_input_tokens")) or 0


def _nv_apply(st: dict, obj: dict, family: str, account: str, path: Path) -> None:
    stamp = _nv_time(obj.get("timestamp"))
    typ = obj.get("type")
    body = obj.get("payload", {}) if family == "codex" else obj
    if not isinstance(body, dict):
        return
    if st["record"] is None:
        st["record"] = _nv_start(obj, body, family, account, path, stamp)
        if st["record"] is None:
            return
    record = st["record"]
    record.activity = max(record.activity, stamp)
    if (family == "claude" and record.role == "lead" and
            (obj.get("entrypoint") in ("sdk-cli", "sdk-ts") or obj.get("promptSource") == "sdk")):
        record.role = "worker"
    if family == "claude" and not record.cwd and isinstance(obj.get("cwd"), str):
        record.cwd = obj["cwd"]   # first line may lack cwd (SDK/RC transcripts); take it from the first later line that has one
    if family == "claude" and record.role == "worker":
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        pending = st.setdefault("pending_tools", set())
        if typ == "assistant":
            pending.update(block["id"] for block in blocks if isinstance(block, dict)
                           and block.get("type") == "tool_use" and isinstance(block.get("id"), str))
            st["claude_tail"] = "end_turn" if message.get("stop_reason") == "end_turn" else "other"
        elif typ == "user":
            has_tool_result = False
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    has_tool_result = True
                    tool_id = block.get("tool_use_id")
                    if isinstance(tool_id, str):
                        pending.discard(tool_id)
            if has_tool_result or obj.get("isMeta") is not True:
                st["claude_tail"] = "other"
        elif typ in ("result", "system"):
            subtype = obj.get("subtype")
            if obj.get("is_error") is True or subtype in ("error", "failed", "errored"):
                st["claude_tail"] = "failed"
            elif subtype in ("stopped", "cancelled", "aborted"):
                st["claude_tail"] = "cancelled"
            elif typ == "result" and subtype == "success" and not pending:
                st["claude_tail"] = "success"
    if typ == "turn_context":
        record.model = str(body.get("model", record.model))
        record.effort = str(body.get("effort", record.effort))
    if family == "codex":
        _nv_codex_metrics(record, st, typ, body)
    elif family == "claude":
        _nv_claude_metrics(record, st, obj)
    message = obj.get("message", {}) if family == "claude" else body
    if isinstance(message, dict):
        role = message.get("role", typ)
        text = _nv_text(message.get("content", ""))
        if role == "user" and text and not st["brief"] and obj.get("isMeta") is not True:
            st["brief"] = _nv_cap(text)
        if role == "user" and family == "claude":
            st["result"] = ""
            record.output_available = False
        if role == "assistant" and text:
            st["result"] = _nv_cap(text)
            record.output_available = True
        if message.get("model") and message["model"] != "<synthetic>":   # placeholder for error messages: no model
            record.model = str(message["model"])
    kind = body.get("type")
    if typ == "event_msg" and kind in ("task_complete", "turn_complete", "turn_aborted", "task_failed"):
        ended = stamp or st.get("mtime", 0) / 1e9
        record.outcome = {"task_complete": "success", "turn_complete": "success",
                          "turn_aborted": "cancelled", "task_failed": "failed"}[kind]
        if record.role == "worker":
            record.state, record.ended = "finished", ended
        else:
            record.state = "idle"
        st["result"] = _nv_cap(str(body.get("last_agent_message", st["result"]))) or st["result"]


def _nv_claude_finish(st: dict, mtime: float, now: float) -> None:
    record = st["record"]
    if record is None or record.role != "worker":
        return
    tail = st.get("claude_tail")
    if tail == "failed":
        record.outcome = "failed"
    elif tail == "cancelled":
        record.outcome = "cancelled"
    elif tail == "success" or (tail == "end_turn" and not st.get("pending_tools") and now - mtime >= QUIET_S):
        record.outcome = "success"
    else:
        record.outcome = "unknown"
    record.state = "finished" if record.outcome != "unknown" else "running"
    record.ended = (record.activity or mtime) if record.state == "finished" else None


def _nv_jsonl(path: Path, family: str, account: str, now: float) -> ExecutionEvidence | None:
    try:
        stat = path.stat()
        st = _nv_cache.get(str(path))
        if st is not None:
            valid = (st["ino"] == stat.st_ino and stat.st_size >= st["offset"]
                     and _nv_sig(path, 0, st["head_len"]) == st["head"]
                     and _nv_sig(path, st["offset"] - st["tail_len"], st["tail_len"]) == st["tail"])
            if valid and stat.st_size == st["size"]:
                if stat.st_mtime_ns == st["mtime"]:
                    if family == "claude":
                        _nv_claude_finish(st, stat.st_mtime, now)
                    return st["evidence"]
                valid = False           # same-size rewrite: mtime moved while size did not
            if not valid:
                st = None
        if st is None:
            st = {"ino": stat.st_ino, "offset": 0, "record": None, "brief": "", "result": "", "oversized": False}
        start = st["offset"]
        with path.open("rb") as stream:
            stream.seek(start)
            for begin, raw, complete in _nv_lines(stream, MAX_ARTIFACT_BYTES):
                if raw is None:
                    st["oversized"] = True   # diagnosed, never truncated into partial JSON
                    continue
                if not raw.strip():
                    continue
                try:
                    obj = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    if complete:
                        continue             # malformed complete line: skip, keep parsing
                    stream.seek(begin)       # incomplete append: retry this line later
                    break
                if not isinstance(obj, dict):
                    if not complete:
                        stream.seek(begin)
                        break
                    continue
                _nv_apply(st, obj, family, account, path)
            st["offset"] = stream.tell()
        st.update(size=stat.st_size, mtime=stat.st_mtime_ns, head_len=min(_NV_HEAD, st["offset"]),
                  tail_len=min(_NV_TAIL, st["offset"]))
        st["head"] = _nv_sig(path, 0, st["head_len"])
        st["tail"] = _nv_sig(path, st["offset"] - st["tail_len"], st["tail_len"])
        record = st["record"]
        if record is None:
            st["evidence"] = None
            _nv_cache[str(path)] = st
            return None
        if family == "claude":
            _nv_claude_finish(st, stat.st_mtime, now)
        record.goal, record.purpose_source = _nv_goal(st["brief"])
        if st["oversized"]:
            record.goal = f"{record.goal} {_NV_OVERSIZE_NOTE}".strip()
        _nv_metrics_put(record.harness, record.native_id, _nv_record_metrics(record, st), path)
        st["evidence"] = ExecutionEvidence(record, brief=st["brief"], result=st["result"])
        _nv_cache[str(path)] = st
        return st["evidence"]
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None


def _nv_db_text(conn: sqlite3.Connection, sid: str, kind: str) -> tuple[str, bool]:
    order = "ASC" if kind == "user" else "DESC"
    oversize = False
    rows = conn.execute(f"SELECT data FROM session_message WHERE session_id=? AND type=? ORDER BY seq {order} LIMIT 5",
                        (sid, kind)).fetchall()
    for (raw,) in rows:
        if raw is None or len(raw) > MAX_ARTIFACT_BYTES:
            oversize = True
            continue
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if not isinstance(data, dict):
            continue
        text = _nv_text(data.get("text", data.get("content", data.get("parts", "")))) if kind == "user" \
            else _nv_text(data.get("content", data.get("parts", "")))
        if text:
            return _nv_cap(text), oversize
    return "", oversize


def _nv_opencode(home: str) -> list[ExecutionEvidence]:
    path = Path(home) / ".local/share/opencode/opencode.db"
    if not path.is_file():
        return []
    items = []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(session_v2)")}
            extra = [name for name in ("cost", "tokens_input", "tokens_output", "tokens_reasoning",
                                       "tokens_cache_read", "tokens_cache_write") if name in columns]
            rows = conn.execute("SELECT id,parent_id,directory,title,model,time_created,time_updated,time_idle,idle_outcome"
                                + "".join(", " + name for name in extra)
                                + " FROM session_v2 WHERE time_archived IS NULL ORDER BY time_updated DESC LIMIT ?",
                                (_NV_SCAN_LIMIT,)).fetchall()
            for row in rows:
                sid, parent, cwd, title, model, created, updated, idle, outcome = row[:9]
                counts = dict(zip(extra, row[9:]))
                record = ExecutionRecord(execution_key("opencode", "personal", sid), harness="opencode", account="personal", native_id=sid,
                                         role="worker" if parent else "lead", parent_id=execution_key("opencode", "personal", parent) if parent else "",
                                         linkage="native" if parent else "unknown", cwd=cwd or "", title=title or "",
                                         started=_nv_time(created), activity=_nv_time(updated), source=str(path))
                record.tokens_in = _nv_int(counts.get("tokens_input"))
                record.tokens_out = _nv_sum(_nv_int(counts.get("tokens_output")),
                                            _nv_int(counts.get("tokens_reasoning")))
                record.tokens_cache = _nv_sum(_nv_int(counts.get("tokens_cache_read")),
                                              _nv_int(counts.get("tokens_cache_write")))
                cost = counts.get("cost")
                record.cost = float(cost) if type(cost) in (int, float) and cost >= 0 else None
                try:
                    record.model = str(json.loads(model or "{}").get("id", ""))
                except (ValueError, AttributeError):
                    pass
                brief, over_user = _nv_db_text(conn, sid, "user")
                result, over_asst = _nv_db_text(conn, sid, "assistant")
                if idle and _nv_time(idle) >= record.activity:
                    record.state = "finished" if parent else "idle"
                    if parent:
                        record.ended = _nv_time(idle)
                        record.outcome = {"succeeded": "success", "failed": "failed", "error": "failed",
                                          "interrupted": "cancelled"}.get(outcome or "", "unknown")
                record.goal, record.purpose_source = _nv_goal(brief)
                if over_user or over_asst:
                    record.goal = f"{record.goal} {_NV_OVERSIZE_NOTE}".strip()
                record.output_available = bool(result)
                _nv_metrics_put(record.harness, record.native_id,
                                {"tokens_in": record.tokens_in, "tokens_out": record.tokens_out,
                                 "tokens_cache": record.tokens_cache, "cost": record.cost,
                                 "ctx_tokens": None, "ctx_window": None,
                                 "tokens_total": _metric_tokens_total(
                                     {"tokens_in": record.tokens_in, "tokens_out": record.tokens_out})}, path)
                items.append(ExecutionEvidence(record, brief=brief, result=result))
    except (OSError, sqlite3.Error):
        return items
    return items


def _nv_openclaw(home: str) -> list[ExecutionEvidence]:
    path = Path(home) / ".openclaw/agents/openclaw/agent/openclaw-agent.sqlite"
    if not path.is_file():
        return []
    items = []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1) as conn:
            rows = conn.execute("SELECT session_key, entry_json FROM session_nodes LIMIT 5000").fetchall()
    except sqlite3.Error:
        return []
    for key, raw in rows:
        try:
            entry = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            continue
        if not isinstance(entry, dict) or not isinstance(key, str) or not key:
            continue
        parent = entry.get("parentSessionKey") if isinstance(entry.get("parentSessionKey"), str) else ""
        usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else entry
        status = str(entry.get("status") or "")
        ended = _nv_time(entry.get("endedAt")) or None
        record = ExecutionRecord(execution_key("openclaw", "openclaw", key), harness="openclaw", account="openclaw", native_id=key,
                                 role="worker" if parent else "lead", cwd=str(entry.get("spawnedWorkspaceDir", "")),
                                 parent_id=execution_key("openclaw", "openclaw", parent) if parent else "",
                                 linkage="native" if parent else "unknown",
                                 title=str(entry.get("displayName") or entry.get("label") or ""),
                                 model=str(entry.get("model", "")), started=_nv_time(entry.get("startedAt")),
                                 activity=_nv_time(entry.get("lastActivityAt") or entry.get("updatedAt") or entry.get("endedAt")
                                                   or entry.get("startedAt")),
                                 ended=ended, source=str(path))
        record.tokens_in = _nv_int(usage.get("inputTokens"))
        record.tokens_out = _nv_int(usage.get("outputTokens"))
        record.tokens_cache = _nv_sum(_nv_int(usage.get("cacheRead")), _nv_int(usage.get("cacheWrite")))
        cost = entry.get("estimatedCostUsd")
        record.cost = float(cost) if type(cost) in (int, float) and cost >= 0 else None
        if status in ("done", "failed", "killed"):
            record.state = "finished" if parent else "idle"
            if parent:
                record.outcome = {"done": "success", "failed": "failed", "killed": "cancelled"}[status]
        if not parent or status in ("running", ""):
            record.state = "running" if not ended and status in ("running", "") else record.state
        # Hosted OpenClaw sessions have no transcript brief here: the gap is reported, not guessed.
        record.purpose_source = "missing brief"
        items.append(ExecutionEvidence(record))
    return items


def _nv_cursor_query(text: str) -> str:
    """Take one Cursor user_query block; normalize CRLF and trim only its outside whitespace."""
    matches = re.findall(r"<user_query>(.*?)</user_query>", text, re.DOTALL)
    return matches[0].replace("\r\n", "\n").strip() if len(matches) == 1 else ""


def _nv_cursor_prompt(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def _nv_cursor_blocks(obj: dict) -> list[dict]:
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [part for part in content if isinstance(part, dict)] if isinstance(content, list) else []


def _nv_cursor_file(path: Path) -> dict | None:
    """Cache a bounded first query, incremental Task scan, and bounded status/output tail."""
    try:
        stat = path.stat()
        key = str(path)
        st = _nv_cursor_cache.get(key)
        if st is not None:
            valid = (st["ino"] == stat.st_ino and stat.st_size >= st["offset"]
                     and _nv_sig(path, 0, st["head_len"]) == st["head"]
                     and _nv_sig(path, st["offset"] - st["tail_len"], st["tail_len"]) == st["tail"])
            if valid and stat.st_size == st["size"] and stat.st_mtime_ns == st["mtime"]:
                return st
            if not valid or (stat.st_size == st["size"] and stat.st_mtime_ns != st["mtime"]):
                st = None
        if st is None:
            st = {"ino": stat.st_ino, "offset": 0, "brief": "", "prompts": set(), "oversized": False,
                  "started": 0.0}
        with path.open("rb") as stream:
            stream.seek(st["offset"])
            for begin, raw, complete in _nv_lines(stream, MAX_ARTIFACT_BYTES):
                if raw is None:
                    st["oversized"] = True
                    continue
                if not complete:
                    stream.seek(begin)
                    break
                try:
                    obj = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if not isinstance(obj, dict):
                    continue
                if not st["started"]:
                    st["started"] = _nv_time(obj.get("timestamp"))
                blocks = _nv_cursor_blocks(obj)
                if obj.get("role") == "user" and not st["brief"] and begin < _NV_CURSOR_HEAD:
                    text = "".join(part.get("text", "") for part in blocks
                                   if part.get("type") == "text" and isinstance(part.get("text"), str))
                    st["brief"] = _nv_cap(_nv_cursor_query(text))
                if obj.get("role") == "assistant":
                    for part in blocks:
                        if part.get("type") != "tool_use":
                            continue
                        data = part.get("input")
                        if part.get("name") == "CallDynamicTool" and isinstance(data, dict) and data.get("toolName") == "Task":
                            data = data.get("arguments")
                        elif part.get("name") != "Task":
                            continue
                        prompt = data.get("prompt") if isinstance(data, dict) else None
                        if isinstance(prompt, str) and _nv_cursor_prompt(prompt):
                            st["prompts"].add(_nv_cursor_prompt(prompt))
            st["offset"] = stream.tell()
            stream.seek(max(0, stat.st_size - _NV_CURSOR_TAIL))
            if stream.tell():
                stream.readline()  # discard a partial first line
            tail = stream.read(_NV_CURSOR_TAIL)
        st["status"], st["result"] = "", ""
        for raw in tail.splitlines():
            if len(raw) > MAX_ARTIFACT_BYTES:
                st["oversized"] = True
                continue
            try:
                obj = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "turn_ended" and isinstance(obj.get("status"), str):
                st["status"] = obj["status"]
            elif obj.get("role") == "user":
                st["status"] = ""  # a new turn supersedes the prior turn_ended
                st["result"] = ""
            elif obj.get("role") == "assistant":
                text = "\n".join(part["text"] for part in _nv_cursor_blocks(obj)
                                 if part.get("type") == "text" and isinstance(part.get("text"), str))
                if text:
                    st["result"] = _nv_cap(text)
        st.update(size=stat.st_size, mtime=stat.st_mtime_ns, head_len=min(_NV_HEAD, st["offset"]),
                  tail_len=min(_NV_TAIL, st["offset"]))
        st["head"] = _nv_sig(path, 0, st["head_len"])
        st["tail"] = _nv_sig(path, st["offset"] - st["tail_len"], st["tail_len"])
        _nv_cursor_cache[key] = st
        return st
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None


def _nv_cursor(home: str, now: float) -> list[ExecutionEvidence]:
    base = Path(home) / ".cursor"
    project_root = base / "projects"
    chat_root = base / "chats"
    if not project_root.is_dir():
        return []
    metadata: dict[str, dict] = {}
    cwd_slugs: dict[str, set[str]] = {}
    try:
        meta_paths = chat_root.glob("*/*/meta.json")
        chat_root_resolved = chat_root.resolve()
        for path in meta_paths:
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(chat_root_resolved) or path.stat().st_size > _NV_CURSOR_HEAD:
                    continue
                with path.open("rb") as stream:
                    data = json.load(stream)
            except (OSError, ValueError, RecursionError):
                continue
            if not isinstance(data, dict) or not isinstance(data.get("schemaVersion"), int):
                continue
            chat_id = path.parent.name
            if chat_id in metadata:
                metadata[chat_id] = {}  # duplicate identity is not trustworthy
                continue
            meta = {name: data.get(name) for name in ("schemaVersion", "createdAtMs", "updatedAtMs", "cwd")}
            metadata[chat_id] = meta
            cwd = meta["cwd"]
            if isinstance(cwd, str) and os.path.isabs(cwd):
                for ancestor in (Path(cwd), *Path(cwd).parents):
                    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(ancestor)).strip("-")
                    cwd_slugs.setdefault(slug, set()).add(str(ancestor))
    except (OSError, ValueError, RecursionError):
        pass
    try:
        root = project_root.resolve()
        stamped = []
        for path in project_root.glob("*/agent-transcripts/*/*.jsonl"):
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root) or path.stem != path.parent.name:
                    continue
                stamped.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError:
        return []
    stamped.sort(key=lambda pair: pair[0], reverse=True)
    items = []
    prompts: dict[str, set[str]] = {}
    for mtime, path in stamped[:_NV_SCAN_LIMIT]:
        parsed = _nv_cursor_file(path)
        if parsed is None:
            continue
        sid = path.stem
        meta = metadata.get(sid)
        has_meta = bool(meta)
        cwd = meta.get("cwd") if has_meta and isinstance(meta.get("cwd"), str) else ""
        if not cwd:
            candidates = cwd_slugs.get(path.parents[2].name, set())
            cwd = next(iter(candidates)) if len(candidates) == 1 else ""
        if os.sep + ".stage" + os.sep in cwd:
            cwd = cwd.split(os.sep + ".stage" + os.sep, 1)[0]
        started = (_nv_time(meta.get("createdAtMs")) or mtime) if has_meta else (parsed["started"] or mtime)
        activity = max(_nv_time(meta.get("updatedAtMs")) if has_meta else 0.0, mtime)
        record = ExecutionRecord(execution_key("cursor", "personal", sid), harness="cursor", account="personal",
                                 native_id=sid, role="lead" if has_meta else "worker", cwd=cwd,
                                 title="" if has_meta else "Cursor task (unlinked)",
                                 started=started, activity=activity, source=str(path))
        status = parsed["status"]
        if status:
            record.outcome = {"success": "success", "error": "failed", "failed": "failed",
                              "aborted": "cancelled", "cancelled": "cancelled"}.get(status, "unknown")
            record.state = "idle" if has_meta else "finished"
            if not has_meta:
                record.ended = activity
        elif now - activity >= ACTIVE_S:
            record.state = "idle" if has_meta else "finished"
            if not has_meta:
                record.ended = activity
        record.goal, record.purpose_source = _nv_goal(parsed["brief"])
        if parsed["oversized"]:
            record.goal = f"{record.goal} {_NV_OVERSIZE_NOTE}".strip()
        record.output_available = bool(parsed["result"])
        items.append(ExecutionEvidence(record, brief=parsed["brief"], result=parsed["result"]))
        for prompt in parsed["prompts"]:
            prompts.setdefault(prompt, set()).add(sid)
    for item in items:
        record = item.record
        if record.role != "worker":
            continue
        matches = prompts.get(_nv_cursor_prompt(item.brief), set()) - {record.native_id}
        if len(matches) == 1:
            record.parent_id = execution_key("cursor", "personal", next(iter(matches)))
            record.linkage = "native"
            record.title = "Cursor task"
            if not record.cwd:
                record.cwd = next((parent.record.cwd for parent in items
                                   if parent.record.execution_id == record.parent_id), "")
        elif len(matches) > 1:
            record.title = "Cursor task (ambiguous, unlinked)"
    return items


def _nv_scan(base: Path, account: str, family: str, now: float) -> list[ExecutionEvidence]:
    try:
        stamped = []
        root = base.resolve()
        for path in base.rglob("*.jsonl"):
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    continue             # never follow links out of the harness store
                stamped.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError:
        return []
    stamped.sort(key=lambda pair: pair[0], reverse=True)
    out = []
    for _, path in stamped[:_NV_SCAN_LIMIT]:
        item = _nv_jsonl(path, family, account, now)
        if item is not None:
            out.append(item)
    return out


def _nv_link_roots(items: list[ExecutionEvidence]) -> None:
    by_id = {item.record.execution_id: item.record for item in items}
    for item in items:
        record = item.record
        if not record.parent_id:
            record.root_id = record.execution_id
            continue
        cursor, seen, root = record, {record.execution_id}, ""
        for _ in range(_NV_HOPS):
            parent = by_id.get(cursor.parent_id)
            if parent is None or parent.execution_id in seen:
                break
            seen.add(parent.execution_id)
            if not parent.parent_id:
                root = parent.execution_id
                break
            cursor = parent
        record.root_id = root


# --- metrics: cheap readers used by the collector, one connection per file/DB per snapshot -----------------

_NV_CODEX_CALLS = frozenset({"function_call", "custom_tool_call", "local_shell_call"})
_NV_ROLLOUT_NAME = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T[\d-]+-(.+)\.jsonl$")
_NV_CODEX_STORES = (".codex/sessions", ".codex-testing/sessions")
_metric_codex_paths: dict[str, tuple[float, dict[str, str]]] = {}
_metric_codex: dict[str, dict] = {}
_metric_claude: dict[str, dict] = {}
_metric_opencode_db: dict[str, tuple[object, dict[str, dict], float]] = {}
_metric_opencode_tools: dict[str, dict[str, tuple[float, int, list[tuple[float, int]]]]] = {}
_metric_keep = 400     # cached files per reader
_metric_opencode_ttl_s = 60.0   # a WAL write can leave the main file's stamp alone: re-read at least this often


def _nv_stamp(path: Path) -> tuple[int, int] | None:
    """(mtime in ns, size) of a file; None when it does not exist."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _nv_metrics_put(harness: str, native_id: str, metrics: dict, path: object) -> None:
    if harness and native_id:
        _nv_metrics[(harness, native_id)] = metrics
        _nv_metric_paths[(harness, native_id)] = str(path)


def _nv_record_metrics(record: ExecutionRecord, st: dict) -> dict:
    window = st.get("ctx_window") if isinstance(st, dict) else None
    metrics = {"tokens_in": record.tokens_in, "tokens_out": record.tokens_out,
               "tokens_cache": record.tokens_cache, "cost": record.cost,
               "ctx_tokens": record.context_tokens,
               "ctx_window": window if isinstance(window, int) and not isinstance(window, bool) and window > 0 else None}
    metrics["tokens_total"] = _metric_tokens_total(metrics)
    return metrics


def native_metrics(harness: str, native_id: str) -> dict | None:
    """Metrics the last native scan parsed for one run; None when that run was not scanned."""
    if not harness or not native_id:
        return None
    return _nv_metrics.get((harness, native_id))


def _metric_tokens_total(metrics: dict) -> int | None:
    if metrics.get("tokens_in") is None and metrics.get("tokens_out") is None:
        return None
    return (metrics.get("tokens_in") or 0) + (metrics.get("tokens_out") or 0)


def _metric_result(state: dict, now: float) -> dict:
    """The public metrics dict of one incremental parse: tokens, ctx, tools and the pruned pulse ring."""
    pulses = [(stamp, count) for stamp, count in state["pulses"] if stamp >= now - TOOL_RING_S]
    state["pulses"] = pulses[-TOOL_RING_MAX:]
    metrics = dict(state["metrics"])
    metrics.setdefault("cost", None)
    metrics["tools"] = state["tools"]
    metrics["tool_pulses"] = list(state["pulses"])
    metrics["tokens_total"] = _metric_tokens_total(metrics)
    return metrics


def _metric_lines(state: dict, path: Path, apply) -> str:
    """Read the new bytes of ``path`` into ``state`` with ``apply(state, obj)``: "ok", "reset" or "gone"."""
    try:
        stat = path.stat()
    except OSError:
        return "gone"
    if state.get("size") is not None and (
            state.get("ino") != stat.st_ino or stat.st_size < state["offset"]
            or (stat.st_size == state["size"] and stat.st_mtime_ns != state["mtime"])):
        return "reset"
    try:
        with path.open("rb") as stream:
            stream.seek(state["offset"])
            for begin, raw, complete in _nv_lines(stream, MAX_ARTIFACT_BYTES):
                if raw is None or not raw.strip():
                    continue
                try:
                    obj = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    if complete:
                        continue
                    stream.seek(begin)
                    break
                if not isinstance(obj, dict):
                    if not complete:
                        stream.seek(begin)
                        break
                    continue
                apply(state, obj)
            state["offset"] = stream.tell()
    except OSError:
        return "gone"
    state.update(ino=stat.st_ino, size=stat.st_size, mtime=stat.st_mtime_ns)
    return "ok"


def read_codex_sessions(home: str) -> dict[str, str]:
    """Thread id -> rollout path under codex's stores, newest file wins; rebuilt at most every RESCAN_S."""
    mono = time.monotonic()
    cached = _metric_codex_paths.get(home)
    if cached is not None and mono - cached[0] < RESCAN_S:
        return cached[1]
    found: dict[str, tuple[float, str]] = {}
    for store in _NV_CODEX_STORES:
        base = Path(home) / store
        try:
            if not base.is_dir():
                continue
            for root, _dirs, files in os.walk(base):   # os.walk does not follow symlinked dirs
                for name in files:
                    if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                        continue
                    path = Path(root) / name
                    try:
                        if path.is_symlink():
                            continue
                        match = _NV_ROLLOUT_NAME.fullmatch(name)
                        if match is None:
                            continue
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    prior = found.get(match.group(1))
                    if prior is None or mtime > prior[0]:
                        found[match.group(1)] = (mtime, str(path))
        except OSError:
            continue
    paths = {key: value[1] for key, value in found.items()}
    _metric_codex_paths[home] = (mono, paths)
    return paths


def _metric_codex_item(state: dict, obj: dict) -> None:
    typ, payload = obj.get("type"), obj.get("payload")
    if not isinstance(payload, dict):
        return
    metrics = state["metrics"]
    stamp = _nv_time(obj.get("timestamp"))
    if typ == "token_usage_record":
        counts = _nv_codex_counts(payload.get("thread_token_usage") or payload.get("usage"))
        if counts is not None:
            metrics["tokens_in"], metrics["tokens_out"], metrics["tokens_cache"] = counts
        last = _nv_codex_counts(payload.get("usage"))
        if last is not None and last[0] > 0:
            metrics["ctx_tokens"] = last[0]
    elif typ == "event_msg":
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        if payload.get("type") == "token_count":
            counts = _nv_codex_counts(info.get("total_token_usage"))
            if counts is not None:
                metrics["tokens_in"], metrics["tokens_out"], metrics["tokens_cache"] = counts
            last = _nv_codex_counts(info.get("last_token_usage"))
            if last is not None and last[0] > 0:
                metrics["ctx_tokens"] = last[0]
        window = _nv_codex_window(payload)
        if window is not None:
            metrics["ctx_window"] = window
    elif typ == "response_item" and payload.get("type") in _NV_CODEX_CALLS:
        state["tools"] += 1
        if stamp:
            state["pulses"].append((stamp, 1))
    if len(state["pulses"]) > TOOL_RING_MAX:
        state["pulses"] = state["pulses"][-TOOL_RING_MAX:]


def read_codex_metrics(path: str, now: float | None = None) -> dict | None:
    """Tokens, context window/used and tool pulses of one codex rollout, parsed incrementally."""
    if now is None:
        now = time.time()
    target = Path(path)
    state = _metric_codex.get(str(target))
    if state is None:
        state = {"offset": 0, "metrics": {}, "tools": 0, "pulses": []}
    ok = _metric_lines(state, target, _metric_codex_item)
    if ok == "reset":
        state = {"offset": 0, "metrics": {}, "tools": 0, "pulses": []}
        ok = _metric_lines(state, target, _metric_codex_item)
    if ok != "ok":
        return None
    _metric_codex[str(target)] = state
    while len(_metric_codex) > _metric_keep:
        _metric_codex.pop(next(iter(_metric_codex)))
    return _metric_result(state, now)


def _metric_claude_item(state: dict, obj: dict) -> None:
    typ = obj.get("type")
    metrics = state["metrics"]
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
    message_id = message.get("id")
    stamp = _nv_time(obj.get("timestamp"))
    if typ == "assistant" and usage is not None and isinstance(message_id, str) and message_id:
        row = ((_nv_int(usage.get("input_tokens")) or 0) + (_nv_int(usage.get("cache_creation_input_tokens")) or 0),
               _nv_int(usage.get("output_tokens")) or 0,
               _nv_int(usage.get("cache_read_input_tokens")) or 0)
        ids, totals = state.setdefault("ids", {}), state.setdefault("totals", [0, 0, 0])
        previous = ids.get(message_id)
        if previous is not None:
            for index in range(3):
                totals[index] -= previous[index]
        ids[message_id] = row
        for index, value in enumerate(row):
            totals[index] += value
        metrics["tokens_in"], metrics["tokens_out"], metrics["tokens_cache"] = totals
        context = row[0] + row[2]
        if context > 0:
            metrics["ctx_tokens"] = context
        content = message.get("content")
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_id = block.get("id")
                seen = state.setdefault("tool_ids", set())
                if isinstance(tool_id, str) and tool_id:
                    if tool_id in seen:
                        continue
                    seen.add(tool_id)
                state["tools"] += 1
                if stamp:
                    state["pulses"].append((stamp, 1))
    window = _nv_model_window(obj.get("modelUsage"))
    if window is not None:
        metrics["ctx_window"] = window
    if typ == "result" and isinstance(obj.get("usage"), dict):
        result = obj["usage"]
        metrics["tokens_in"] = ((_nv_int(result.get("input_tokens")) or 0)
                                + (_nv_int(result.get("cache_creation_input_tokens")) or 0))
        metrics["tokens_out"] = _nv_int(result.get("output_tokens")) or 0
        metrics["tokens_cache"] = _nv_int(result.get("cache_read_input_tokens")) or 0
        cost = obj.get("total_cost_usd")
        if type(cost) in (int, float) and not isinstance(cost, bool) and cost >= 0:
            metrics["cost"] = float(cost)   # collect keeps it only on a metered route (a subscription drops it)
    if len(state["pulses"]) > TOOL_RING_MAX:
        state["pulses"] = state["pulses"][-TOOL_RING_MAX:]


def read_claude_metrics(path: str, now: float | None = None) -> dict | None:
    """Tokens, context window/used and tool pulses of one Claude transcript, parsed incrementally."""
    if now is None:
        now = time.time()
    target = Path(path)
    state = _metric_claude.get(str(target))
    if state is None:
        state = {"offset": 0, "metrics": {}, "tools": 0, "pulses": []}
    ok = _metric_lines(state, target, _metric_claude_item)
    if ok == "reset":
        state = {"offset": 0, "metrics": {}, "tools": 0, "pulses": []}
        ok = _metric_lines(state, target, _metric_claude_item)
    if ok != "ok":
        return None
    _metric_claude[str(target)] = state
    while len(_metric_claude) > _metric_keep:
        _metric_claude.pop(next(iter(_metric_claude)))
    return _metric_result(state, now)


def read_opencode_sessions(home: str) -> dict[str, dict]:
    """Metrics of every open OpenCode session, keyed by session id; one connection.

    Cached while the database and its ``-wal`` keep their mtime and size, and for 60 s at most, so a
    write that only reaches the WAL still shows up in this snapshot.
    """
    path = Path(home) / ".local/share/opencode/opencode.db"
    stamp = (_nv_stamp(path), _nv_stamp(Path(str(path) + "-wal")))
    if stamp[0] is None:
        return {}
    key = str(path)
    now = time.monotonic()
    cached = _metric_opencode_db.get(key)
    if cached is not None and cached[0] == stamp and now - cached[2] < _metric_opencode_ttl_s:
        return cached[1]
    rows: dict[str, dict] = {}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(session_v2)")}
            model_column = "model" if "model" in columns else "NULL AS model"
            extra = [name for name in ("cost", "tokens_input", "tokens_output", "tokens_reasoning",
                                       "tokens_cache_read", "tokens_cache_write", "time_updated") if name in columns]
            query = ("SELECT id,parent_id,directory,title," + model_column + "".join(", " + name for name in extra)
                     + " FROM session_v2 WHERE time_archived IS NULL")
            for row in conn.execute(query):
                sid, parent, directory, title, model_data = row[:5]
                counts = dict(zip(extra, row[5:]))
                try:
                    model_data = json.loads(model_data) if isinstance(model_data, str) else {}
                except ValueError:
                    model_data = {}
                model_id = model_data.get("id") if isinstance(model_data, dict) else None
                reported_window = None
                if isinstance(model_data, dict):
                    reported_window = model_data.get("contextWindow", model_data.get("context_window"))
                ctx_tokens = None
                try:
                    cursor = conn.execute(
                        "SELECT json_extract(data,'$.tokens.input') FROM session_message "
                        "WHERE session_id=? AND type='assistant' ORDER BY seq DESC LIMIT 1", (sid,)
                    )
                    try:
                        latest = cursor.fetchone()
                    finally:
                        cursor.close()
                    ctx_tokens = _nv_int(latest[0]) if latest else None
                except sqlite3.Error:
                    pass
                metrics = {
                    "parent_id": parent or "", "directory": directory or "", "title": title or "",
                    "tokens_in": _nv_int(counts.get("tokens_input")),
                    "tokens_out": _nv_sum(_nv_int(counts.get("tokens_output")),
                                          _nv_int(counts.get("tokens_reasoning"))),
                    "tokens_cache": _nv_sum(_nv_int(counts.get("tokens_cache_read")),
                                            _nv_int(counts.get("tokens_cache_write"))),
                    "cost": (float(counts["cost"]) if type(counts.get("cost")) in (int, float)
                             and counts["cost"] >= 0 else None),
                    "ctx_tokens": ctx_tokens,
                    "ctx_window": _nv_opencode_window(model_id, reported_window), "tools": None,
                    "updated": _nv_time(counts.get("time_updated")),
                }
                metrics["tokens_total"] = _metric_tokens_total(metrics)
                rows[str(sid)] = metrics
    except (OSError, sqlite3.Error):
        return cached[1] if cached is not None else {}
    _metric_opencode_db[key] = (stamp, rows, now)
    return rows


def read_opencode_lead_tools(home: str, sessions: dict[str, dict], now: float) -> dict[str, tuple[int, list[tuple[float, int]]]]:
    """Tool calls and event-time pulses per session, from the DB's assistant messages; only moved sessions are read."""
    path = Path(home) / ".local/share/opencode/opencode.db"
    store = _metric_opencode_tools.setdefault(str(path), {})
    out = {sid: (store[sid][1], [pulse for pulse in store[sid][2] if pulse[0] >= now - TOOL_RING_S])
           for sid in sessions if sid in store}
    stale = [sid for sid, row in sessions.items() if store.get(sid, (None,))[0] != row.get("updated")]
    if not stale:
        return out
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1) as conn:
            for sid in stale:
                tools, pulses = 0, []
                for created, raw in conn.execute(
                        "SELECT time_created,data FROM session_message WHERE session_id=? AND type='assistant'", (sid,)):
                    try:
                        data = json.loads(raw)
                    except (ValueError, TypeError, RecursionError):
                        continue
                    count = sum(1 for part in (data.get("content") if isinstance(data, dict) else None) or ()
                                if isinstance(part, dict) and part.get("type") == "tool")
                    if count:
                        tools += count
                        stamp = _nv_time(created)
                        if stamp:
                            pulses.append((stamp, count))
                store[sid] = (sessions[sid].get("updated"), tools, pulses[-TOOL_RING_MAX:])
                out[sid] = (tools, [pulse for pulse in store[sid][2] if pulse[0] >= now - TOOL_RING_S])
    except (OSError, sqlite3.Error):
        return out
    return out


def _nv_evict() -> None:
    """Drop cached transcript state for paths that have been deleted."""
    for key in [key for key in _nv_cache if not os.path.lexists(key)]:
        del _nv_cache[key]
    for key in [key for key in _nv_cursor_cache if not os.path.lexists(key)]:
        del _nv_cursor_cache[key]
    for key in [key for key in _nv_metric_paths if not os.path.lexists(_nv_metric_paths[key])]:
        _nv_metrics.pop(key, None)
        del _nv_metric_paths[key]
    for key in [key for key in _metric_codex if not os.path.lexists(key)]:
        del _metric_codex[key]
    for key in [key for key in _metric_claude if not os.path.lexists(key)]:
        del _metric_claude[key]
    for key in [key for key in _metric_opencode_db if not os.path.lexists(key)]:
        del _metric_opencode_db[key]
        _metric_opencode_tools.pop(key, None)


def read_native_executions(harness: str, home: str, now: float) -> list[ExecutionEvidence]:
    _nv_evict()
    if harness == "opencode":
        items = _nv_opencode(home)
    elif harness == "openclaw":
        items = _nv_openclaw(home)
    elif harness in ("codex", "t3"):
        items = (_nv_scan(Path(home) / ".codex/sessions", "personal", "codex", now)
                 + _nv_scan(Path(home) / ".codex-testing/sessions", "personal", "codex", now))
    elif harness == "claude":
        config_dirs = [name for name in os.environ.get("STATION_CLAUDE_CONFIG_DIRS", ".claude").split(":") if name]
        items = []
        for name in config_dirs:
            account = "work" if "work" in name.lower() else "personal"
            items.extend(_nv_scan(Path(home) / name / "projects", account, "claude", now))
    elif harness == "cursor":
        items = _nv_cursor(home, now)
    else:
        return []
    items = [item for item in items if item.record.harness == harness]
    _nv_link_roots(items)
    return items
