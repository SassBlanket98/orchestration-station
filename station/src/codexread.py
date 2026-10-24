"""Control center: Codex rollout reader. Reads one `rollout-*.jsonl` incrementally (offset cache like native.py) for what the
metrics reader does not give: the real first prompt, a readable name, tool calls with duration and errors, the system
prompt and the resume command. Imports model (and native helpers) only; collect calls it, never the reverse."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from model import CCCall
from native import _nv_codex_counts, _nv_codex_window, _nv_lines, _nv_text, _nv_time

# first user messages codex injects before the real prompt; skipped when looking for it (matched on the stripped start)
INJECTED_PREFIXES = ("# AGENTS.md instructions", "<environment_context>", "<user_instructions>", "<permissions instructions>")


@dataclass
class CodexSession:
    session_id: str = ""                 # session_meta payload.id
    path: str = ""
    name: str = ""                       # brief "Name:" line, else the first prompt line cut to 40 chars; never the UUID
    prompt: list[str] = field(default_factory=list)          # the real first user message, one entry per line
    system_prompt: list[str] = field(default_factory=list)   # session_meta base_instructions.text, one entry per line
    model: str = ""
    effort: str = ""
    cwd: str = ""
    started: float = 0.0                 # epoch seconds
    last_ts: float = 0.0                 # newest item time
    calls: list[CCCall] = field(default_factory=list)        # newest LAST, at most 200
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cache: int | None = None
    ctx_tokens: int | None = None
    ctx_window: int | None = None
    resume_cmd: str = ""                 # "codex resume <session_id>"; "" when the id is unknown


_CX_KEEP = 64                     # cached files per reader; the oldest is dropped past this
_CX_LINE_MAX = 16 * 1024 * 1024   # a rollout line longer than this is skipped, never parsed as a partial object
_CX_CALL_MAX = 200                # newest tool calls kept
_CX_PENDING_MAX = 500             # calls still waiting for their output
_CX_LINES_MAX = 400               # prompt and system-prompt lines kept
_CX_NAME_MAX = 40                 # a derived name is cut to this many chars
_CX_ARG_MAX = 80                  # the arguments gist is cut to this
_CX_ERR_MAX = 200                 # a failure's first line is cut to this
_CX_CALLS = frozenset({"function_call", "custom_tool_call", "local_shell_call"})
_CX_OUTPUTS = frozenset({"function_call_output", "custom_tool_call_output"})
# json keys that hold the thing a tool worked on, in the order a gist should prefer them
_CX_ARG_KEYS = ("cmd", "command", "cmdline", "path", "file_path", "file", "query", "message",
                "prompt", "target", "task_name", "name")
_CX_EXIT_RE = re.compile(r"(?im)^[ \t]*(?:exit code:[ \t]*|process exited with code[ \t]*)(-?\d+)")
_CX_FAIL_RE = re.compile(r"(?i)^(?:(?:script|command) failed\b|(?:failed|error|traceback|fatal|exception)\b[ \t]*:)")
_CX_HEADER_RE = re.compile(r"(?i)^(?:script (?:completed|failed)|wall time\b|exit code:|process exited with code|output:?$)")
# codex exec tools take javascript, e.g. `await tools.exec_command({cmd:"ls -la"})`; the cmd/path key comes out
_CX_KV_RE = re.compile(r"""(?i)\b(?:cmd|command|path|file_path)\b["']?[ \t]*[:=][ \t]*(?:"([^"\n]*)"|'([^'\n]*)'|`([^`\n]*)`)""")
_CX_NAME_RE = re.compile(r"(?i)^[ \t]*name:[ \t]*(\S.*?)[ \t]*$")
_CX_UUID_RE = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CX_HEADING_RE = re.compile(r"^#{1,6}[ \t]*")

_crd_cache: dict[str, dict] = {}   # path -> {"offset", "ino", "size", "mtime", "sess", "first", "valid", ...}


def read_codex_session(path: str, now: float | None = None) -> CodexSession | None:
    """Parse the rollout at `path` (incrementally on repeat calls). None when the file is missing or not a rollout."""
    # `now` belongs to the reader signature; this parse depends on the file's bytes only.
    key = str(path)
    target = Path(key)
    state = _crd_cache.get(key)
    if state is None:
        state = _crd_state(key)
    result = _crd_read(state, target)
    if result == "reset":
        state = _crd_state(key)
        result = _crd_read(state, target)
    if result != "ok":
        return None
    _crd_remember(key, state)
    if not state["first"] and state["valid"]:
        sess: CodexSession = state["sess"]
        # a copy: the cached parse must survive anything a caller does to what it was handed
        return replace(sess, prompt=list(sess.prompt), system_prompt=list(sess.system_prompt),
                       calls=[replace(call) for call in sess.calls])
    return None


def _crd_state(path: str) -> dict:
    """A fresh incremental parse of one rollout file."""
    return {"offset": 0, "ino": 0, "size": 0, "mtime": 0, "sess": CodexSession(path=path),
            "first": True, "valid": True, "prompt_seen": False, "pending": {}}


def _crd_remember(key: str, state: dict) -> None:
    """Cache one parse, dropping the file cached longest ago past _CX_KEEP (native.py read_codex_metrics does the same)."""
    _crd_cache[key] = state
    while len(_crd_cache) > _CX_KEEP:
        _crd_cache.pop(next(iter(_crd_cache)))


def _crd_read(state: dict, target: Path) -> str:
    """Read the new bytes of ``target`` into ``state``: "ok", "reset" (rewritten or shortened) or "gone"."""
    try:
        stat = target.stat()
    except OSError:
        return "gone"
    if state["size"] and (state["ino"] != stat.st_ino or stat.st_size < state["offset"]
                          or (stat.st_size == state["size"] and stat.st_mtime_ns != state["mtime"])):
        return "reset"
    try:
        with target.open("rb") as stream:
            stream.seek(state["offset"])
            for begin, raw, complete in _nv_lines(stream, _CX_LINE_MAX):
                if raw is None or not raw.strip():
                    continue
                try:
                    obj = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    if complete:
                        continue            # malformed line: skip it, keep parsing
                    stream.seek(begin)      # still being appended: retry the whole line next read
                    break
                if not isinstance(obj, dict):
                    if complete:
                        continue
                    stream.seek(begin)
                    break
                _crd_item(state, obj)
                if not state["valid"]:      # not a rollout: no line after the first one can matter
                    stream.seek(stat.st_size)
                    break
            state["offset"] = stream.tell()
    except OSError:
        return "gone"
    state.update(ino=stat.st_ino, size=stat.st_size, mtime=stat.st_mtime_ns)
    return "ok"


def _crd_kind(value: object) -> str:
    """A record's `type` when it is a string: a malformed value (list, dict, number, null) never reaches a set lookup."""
    return value if isinstance(value, str) else ""


def _crd_content(value: object) -> object:
    """A message body with any part whose `type` is not a string dropped: _nv_text's set lookup needs hashable values."""
    if not isinstance(value, list):
        return value
    return [part for part in value if not isinstance(part, dict) or isinstance(part.get("type"), str)]


def _crd_item(state: dict, obj: dict) -> None:
    """One parsed rollout line into the partial state."""
    if not state["valid"]:
        return
    typ = _crd_kind(obj.get("type"))
    payload = obj.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    sess: CodexSession = state["sess"]
    ts = _nv_time(obj.get("timestamp"))
    sess.last_ts = max(sess.last_ts, ts)
    if state["first"]:
        state["first"] = False
        if typ != "session_meta" or not isinstance(obj.get("payload"), dict):
            state["valid"] = False       # this file is not a rollout
            return
        _crd_meta(sess, payload, ts)
        return
    if typ == "turn_context":
        _crd_turn(sess, payload)
    elif typ == "response_item":
        _crd_response(state, payload, ts)
    elif typ == "event_msg":
        if _crd_kind(payload.get("type")) == "token_count":
            _crd_tokens(sess, payload)
    elif typ == "token_usage_record":     # the same counters, written by newer builds beside token_count
        counts = _nv_codex_counts(payload.get("thread_token_usage") or payload.get("usage"))
        if counts is not None:
            sess.tokens_in, sess.tokens_out, sess.tokens_cache = counts


def _crd_meta(sess: CodexSession, payload: dict, ts: float) -> None:
    """session_meta: the id, working directory, start time, system prompt and resume command."""
    sid = payload.get("id") or payload.get("session_id")
    sess.session_id = sid.strip() if isinstance(sid, str) else ""
    sess.name = _crd_fallback_name(sess.session_id)
    cwd = payload.get("cwd")
    sess.cwd = cwd.strip() if isinstance(cwd, str) else ""
    sess.started = _nv_time(payload.get("timestamp")) or ts
    base = payload.get("base_instructions")
    sess.system_prompt = _crd_lines(base.get("text") if isinstance(base, dict) else base)
    sess.resume_cmd = f"codex resume {sess.session_id}" if sess.session_id else ""


def _crd_lines(value: object) -> list[str]:
    """A text field as lines, at most _CX_LINES_MAX of them."""
    return value.splitlines()[:_CX_LINES_MAX] if isinstance(value, str) else []


def _crd_turn(sess: CodexSession, payload: dict) -> None:
    """turn_context: the model and its reasoning effort (older builds keep the effort under collaboration_mode)."""
    model = payload.get("model")
    if isinstance(model, str) and model.strip():
        sess.model = model.strip()
    effort = payload.get("effort", payload.get("reasoning_effort"))
    if not (isinstance(effort, str) and effort.strip()):
        mode = payload.get("collaboration_mode")
        settings = mode.get("settings") if isinstance(mode, dict) else None
        effort = settings.get("reasoning_effort") if isinstance(settings, dict) else None
    if isinstance(effort, str) and effort.strip():
        sess.effort = effort.strip()


def _crd_tokens(sess: CodexSession, payload: dict) -> None:
    """event_msg token_count: the cumulative thread counters, the context in use and the model's window."""
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    counts = _nv_codex_counts(info.get("total_token_usage"))
    if counts is not None:
        sess.tokens_in, sess.tokens_out, sess.tokens_cache = counts
    last = _nv_codex_counts(info.get("last_token_usage"))
    if last is not None and last[0] > 0:
        sess.ctx_tokens = last[0]
    window = _nv_codex_window(payload)
    if window is not None:
        sess.ctx_window = window


def _crd_response(state: dict, payload: dict, ts: float) -> None:
    """One response_item: a user message, a tool call or the output that closes a call."""
    ptype = _crd_kind(payload.get("type"))
    if ptype == "message":
        _crd_prompt(state, payload)
    elif ptype in _CX_CALLS:
        _crd_call(state, payload, ts)
    elif ptype in _CX_OUTPUTS:
        _crd_output(state, payload, ts)


def _crd_prompt(state: dict, payload: dict) -> None:
    """Keep the first user message that is not an injected instruction block: the session's real prompt."""
    sess: CodexSession = state["sess"]
    if state["prompt_seen"] or payload.get("role") != "user":
        return
    text = _nv_text(_crd_content(payload.get("content")))
    stripped = text.strip()
    if not stripped or stripped.startswith(INJECTED_PREFIXES):
        return
    sess.prompt = _crd_lines(text)
    sess.name = _crd_name(sess.prompt, sess.session_id)
    state["prompt_seen"] = True


def _crd_name(prompt: list[str], session_id: str) -> str:
    """The brief's ``Name:`` line, else the first prompt line without heading marks; the id is never the name."""
    for line in prompt:
        match = _CX_NAME_RE.match(line)
        if match is not None:
            value = " ".join(match.group(1).split())
            if value and not _CX_UUID_RE.match(value):
                return value[:_CX_NAME_MAX]
    first = next((line.strip() for line in prompt if line.strip()), "")
    if not first:
        return ""
    value = " ".join(_CX_HEADING_RE.sub("", first).split())[:_CX_NAME_MAX]
    return _crd_fallback_name(session_id) if not value or value == session_id or _CX_UUID_RE.match(value) else value


def _crd_fallback_name(session_id: str) -> str:
    """A Codex row's short, stable name until its real prompt is available."""
    return f"codex {session_id[:8]}" if session_id else "codex"


def _crd_call(state: dict, payload: dict, ts: float) -> None:
    """A tool call: its name, a one-line gist of the arguments, and a slot for the output to fill in."""
    sess: CodexSession = state["sess"]
    raw_tool = payload.get("name")
    tool = raw_tool.strip() if isinstance(raw_tool, str) and raw_tool.strip() else "shell"
    raw = payload.get("input")
    if not isinstance(raw, str):
        raw = payload.get("arguments")
    call = CCCall(ts=ts, tool=tool, arg=_crd_gist(raw if isinstance(raw, str) else ""))
    sess.calls.append(call)
    if len(sess.calls) > _CX_CALL_MAX:
        del sess.calls[:-_CX_CALL_MAX]
    call_id = payload.get("call_id")
    if isinstance(call_id, str) and call_id:
        pending = state["pending"]
        pending[call_id] = call
        while len(pending) > _CX_PENDING_MAX:
            pending.pop(next(iter(pending)))


def _crd_output(state: dict, payload: dict, ts: float) -> None:
    """The output of a call: pair it by call_id, then its duration, outcome and error line."""
    call_id = payload.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return
    call = state["pending"].pop(call_id, None)
    if call is None:
        return
    call.outcome, call.err = _crd_verdict(_nv_text(_crd_content(payload.get("output"))))
    if ts and call.ts and ts >= call.ts:
        call.dur = ts - call.ts


def _crd_verdict(text: str) -> tuple[str, str]:
    """(outcome, err) of one tool output: a non-zero exit code or an error marker fails it, ok otherwise."""
    stripped = text.strip()
    if not stripped:
        return "ok", ""
    data = _crd_json(stripped)
    if isinstance(data, dict):
        code = data.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool):
            if code != 0:
                return "failed", _crd_err(data.get("output", stripped))
            return "ok", ""
        if data.get("timed_out") is True or data.get("isError") is True:
            return "failed", _crd_err(data.get("output", stripped))
        return "ok", ""
    for value in _CX_EXIT_RE.findall(stripped):
        if int(value) != 0:
            return "failed", _crd_err(stripped)
    if _CX_FAIL_RE.match(stripped):
        return "failed", _crd_err(stripped)
    return "ok", ""


def _crd_err(value: object) -> str:
    """The first line of a failed call's own output, past the ``Script completed``/``Wall time``/``Output:`` header."""
    lines = [line.strip() for line in _nv_text(value).splitlines()]
    for index, line in enumerate(lines):
        if line.rstrip(":").strip().lower() == "output":
            lines = lines[index + 1:]
            break
    for line in lines:
        if line and not _CX_HEADER_RE.match(line):
            return line[:_CX_ERR_MAX]
    return ""


def _crd_gist(raw: str) -> str:
    """One line for the tools table: the shell command or path when the arguments name one, else their head."""
    text = raw.strip()
    if not text:
        return ""
    return " ".join((_crd_arg_value(text) or text).split())[:_CX_ARG_MAX]


def _crd_arg_value(text: str) -> str:
    """The command or path inside a call's arguments (json or codex's javascript), "" when they name none."""
    data = _crd_json(text)
    if isinstance(data, dict):
        for key in _CX_ARG_KEYS:
            value = data.get(key)
            if isinstance(value, (list, tuple)):
                value = " ".join(str(part) for part in value)
            if isinstance(value, str) and value.strip():
                return value
        return ""
    match = _CX_KV_RE.search(text)
    if match is None:
        return ""
    return next((part for part in match.groups() if part is not None), "")


def _crd_json(text: str) -> object:
    """A tool payload that is one json object; None for text, javascript or a malformed body."""
    if not text.startswith("{"):
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None
