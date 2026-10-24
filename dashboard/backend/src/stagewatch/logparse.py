"""Log format detection, line parsing, the now-line and model lookup.

Reads are bounded: a 64 KB head for detection and model lookup, and a 32 KB
tail for the now-line. Nothing here reads a whole log.
"""

import json
import re
import stat
from pathlib import Path
from typing import Any

from stagewatch.models import LogFormat, LogLine, NowLine
from stagewatch.tail import read_tail, split_lines

HEAD_BYTES = 64 * 1024
NOW_TAIL_BYTES = 32 * 1024
MAX_LINE_CHARS = 2000
MAX_NOW_CHARS = 500
OPENCODE_TYPES = frozenset({"step_start", "tool_use", "text", "step_finish"})
_MODEL_JSON = re.compile(r'"(?:modelID|model)"\s*:\s*"([^"\\]+)"')
# CSI, OSC and single-character escapes: the sequences terminal UIs paint with.
_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-Z\\-_])"
)
_CLAUDE_TOOL_INPUT_KEYS = (
    "command",
    "file_path",
    "filePath",
    "path",
    "pattern",
    "query",
    "url",
    "description",
    "prompt",
)
_CURSOR_TOOL_ARGS_KEYS = (
    "command",
    "path",
    "fileText",
    "query",
    "pattern",
    "url",
    "description",
)


def _head(path: Path, nbytes: int = HEAD_BYTES) -> list[str]:
    """Lines from the first `nbytes`. A line cut by the read edge is dropped."""
    try:
        with path.open("rb") as fh:
            data = fh.read(nbytes)
    except OSError:
        return []
    if len(data) == nbytes:
        cut = data.rfind(b"\n")
        data = data[: cut + 1] if cut != -1 else b""
    return split_lines(data)


def _json_obj(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_str(*values: Any) -> str | None:
    """First value that is a string. Mirrors `??`, so an empty string still wins."""
    for value in values:
        if isinstance(value, str):
            return value
    return None


def _cap(text: str, limit: int = MAX_LINE_CHARS) -> str:
    return text[:limit]


def _parse_record(line: str) -> LogLine | None:
    """Map one OpenCode JSONL record to a LogLine, or None if it is not a known type."""
    rec = _json_obj(line)
    if rec is None:
        return None
    rtype = rec.get("type")
    part = _as_dict(rec.get("part"))
    if rtype == "text":
        return LogLine(kind="text", text=_cap(_first_str(part.get("text")) or ""), tool=None)
    if rtype == "tool_use":
        state = _as_dict(part.get("state"))
        inp = _as_dict(state.get("input")) or _as_dict(part.get("input"))
        text = _first_str(
            inp.get("command"),
            inp.get("filePath"),
            inp.get("path"),
            state.get("title"),
            part.get("title"),
        )
        return LogLine(
            kind="tool",
            text=_cap(text or ""),
            tool=_first_str(part.get("tool")),
        )
    if rtype in ("step_start", "step_finish"):
        return LogLine(kind="step", text=rtype, tool=None)
    return None


def _strip_ansi(text: str) -> str:
    """Remove ANSI escapes and remaining C0/C1 control characters (keep tabs)."""
    text = _ANSI_RE.sub("", text)
    return "".join(ch for ch in text if ch == "\t" or ch.isprintable())


def _content_blocks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [block for block in value if isinstance(block, dict)]


def _first_text_block(content: Any) -> str | None:
    for block in _content_blocks(content):
        if block.get("type") == "text":
            return _first_str(block.get("text"))
    return None


def _input_summary(inp: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = inp.get(key)
        if isinstance(value, str) and value:
            return value
    for value in inp.values():
        if isinstance(value, str) and value:
            return value
    return ""


def _parse_claude_record(line: str) -> LogLine | None:
    """Map one Claude Code `stream-json` record to a LogLine, or None if unknown.

    Content blocks are collapsed to one line per record: a tool_use block wins
    over text, so a record that both thinks and calls a tool reads as the call.
    """
    rec = _json_obj(line)
    if rec is None:
        return None
    rtype = rec.get("type")
    if rtype == "assistant":
        msg = _as_dict(rec.get("message"))
        content = msg.get("content")
        for block in _content_blocks(content):
            if block.get("type") == "tool_use":
                inp = _as_dict(block.get("input"))
                return LogLine(
                    kind="tool",
                    text=_cap(_input_summary(inp, _CLAUDE_TOOL_INPUT_KEYS)),
                    tool=_first_str(block.get("name")),
                )
        text = _first_text_block(content)
        if text is not None:
            return LogLine(kind="text", text=_cap(text), tool=None)
        if isinstance(content, str):
            return LogLine(kind="text", text=_cap(content), tool=None)
        return LogLine(kind="step", text="assistant", tool=None)
    if rtype == "user":
        msg = _as_dict(rec.get("message"))
        content = msg.get("content")
        if isinstance(content, str):
            return LogLine(kind="text", text=_cap(content), tool=None)
        text = _first_text_block(content)
        if text is not None:
            return LogLine(kind="text", text=_cap(text), tool=None)
        return None
    if rtype == "system":
        return LogLine(kind="step", text="init", tool=None)
    if rtype == "result":
        return LogLine(kind="step", text=_first_str(rec.get("subtype")) or "done", tool=None)
    return None


def _cursor_tool(rec: dict[str, Any]) -> tuple[str | None, str]:
    """`(tool name, concise args)` from a Cursor `tool_call.tool_call` object."""
    calls = _as_dict(rec.get("tool_call"))
    for name, value in calls.items():
        call = _as_dict(value)
        if name == "function":
            tool = _first_str(call.get("name"))
            raw_args = call.get("arguments")
            if isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    return tool, _cap(_input_summary(parsed, _CURSOR_TOOL_ARGS_KEYS))
                return tool, _cap(raw_args)
            return tool, _cap(_input_summary(_as_dict(raw_args), _CURSOR_TOOL_ARGS_KEYS))
        if name.endswith("ToolCall"):
            args = _as_dict(call.get("args"))
            return name[: -len("ToolCall")], _cap(_input_summary(args, _CURSOR_TOOL_ARGS_KEYS))
    return None, ""


def _parse_cursor_record(line: str) -> LogLine | None:
    """Map one Cursor `stream-json` record to a LogLine, or None if unknown."""
    rec = _json_obj(line)
    if rec is None:
        return None
    rtype = rec.get("type")
    if rtype == "assistant":
        text = _first_text_block(_as_dict(rec.get("message")).get("content"))
        if text is not None:
            return LogLine(kind="text", text=_cap(text), tool=None)
        return LogLine(kind="step", text="assistant", tool=None)
    if rtype == "user":
        msg = _as_dict(rec.get("message"))
        content = msg.get("content")
        if isinstance(content, str):
            return LogLine(kind="text", text=_cap(content), tool=None)
        text = _first_text_block(content)
        if text is not None:
            return LogLine(kind="text", text=_cap(text), tool=None)
        return None
    if rtype == "tool_call":
        tool, text = _cursor_tool(rec)
        return LogLine(kind="tool", text=text, tool=tool)
    if rtype == "system":
        return LogLine(kind="step", text="init", tool=None)
    if rtype == "result":
        return LogLine(kind="step", text=_first_str(rec.get("subtype")) or "done", tool=None)
    return None


def _json_family(lines: list[str]) -> str | None:
    """`"claude"`, `"cursor"` or None from Claude/Cursor stream-JSON markers."""
    claude = cursor = False
    for line in lines:
        rec = _json_obj(line)
        if rec is None:
            continue
        rtype = rec.get("type")
        if rtype == "tool_call" and isinstance(rec.get("tool_call"), dict):
            cursor = True
        elif rtype == "system":
            if "tools" in rec or "mcp_servers" in rec:
                claude = True
            elif "apiKeySource" in rec:
                cursor = True
        elif rtype == "assistant":
            msg = _as_dict(rec.get("message"))
            if (
                "parentUuid" in rec
                or "uuid" in rec
                or any(key in msg for key in ("id", "model", "usage", "stop_reason"))
            ):
                claude = True
            elif "message" in rec:
                cursor = True
        elif rtype == "result" and ("num_turns" in rec or "total_cost_usd" in rec):
            claude = True
    if claude:
        return "claude"
    if cursor:
        return "cursor"
    return None


def _is_cursor_text(lines: list[str]) -> bool:
    """Cursor CLI print output carries a `Cursor Agent` banner on its first lines."""
    for line in lines[:15]:
        if re.match(r"^\s*Cursor Agent\b", _strip_ansi(line)):
            return True
    return False


def _is_opencode(lines: list[str]) -> bool:
    for line in lines:
        rec = _json_obj(line)
        if rec is None:
            continue
        if rec.get("type") in OPENCODE_TYPES:
            return True
        session = rec.get("sessionID")
        if isinstance(session, str) and session.startswith("ses_"):
            return True
    return False


def detect_format(path: Path) -> LogFormat:
    """Classify a log by its first lines. Returns "missing" when there is no file."""
    try:
        st = path.stat()
    except OSError:
        return "missing"
    if not stat.S_ISREG(st.st_mode):
        return "missing"
    if st.st_size == 0:
        return "empty"
    head = [line for line in _head(path) if line.strip()]
    if _is_opencode(head[:20]):
        return "opencode-jsonl"
    family = _json_family(head[:40])
    if family == "claude":
        return "claude-stream-json"
    if family == "cursor":
        return "cursor-jsonl"
    if any(line.startswith(("OpenAI Codex", "model:")) for line in head[:40]):
        return "codex-text"
    if _is_cursor_text(head[:15]):
        return "cursor-text"
    return "text"


_RECORD_PARSERS: dict[str, Any] = {
    "opencode-jsonl": _parse_record,
    "claude-stream-json": _parse_claude_record,
    "cursor-jsonl": _parse_cursor_record,
}


def parse_lines(lines: list[str], fmt: LogFormat) -> list[LogLine]:
    """Map raw lines to LogLines.

    Known JSONL formats use their record parser with a raw fallback per line.
    `cursor-text` is cleaned of terminal control sequences and read as text.
    Every other format maps each line to "raw".
    """
    if fmt == "cursor-text":
        return [LogLine(kind="text", text=_cap(_strip_ansi(line)), tool=None) for line in lines]
    parser = _RECORD_PARSERS.get(fmt)
    if parser is None:
        return [LogLine(kind="raw", text=_cap(_strip_ansi(line)), tool=None) for line in lines]
    parsed: list[LogLine] = []
    for line in lines:
        item = parser(line)
        parsed.append(item or LogLine(kind="raw", text=_cap(_strip_ansi(line)), tool=None))
    return parsed


def _flatten(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").strip()[:MAX_NOW_CHARS]


def now_line(path: Path) -> NowLine | None:
    """The latest activity: last text or tool record, else the last non-blank line."""
    fmt = detect_format(path)
    if fmt in ("missing", "empty"):
        return None
    try:
        lines = [line for line in read_tail(path, NOW_TAIL_BYTES).lines if line.strip()]
    except OSError:
        return None
    if not lines:
        return None
    if fmt == "cursor-text":
        cleaned = [line for line in lines if _strip_ansi(line).strip()]
        if not cleaned:
            return None
        return NowLine(kind="text", text=_flatten(_strip_ansi(cleaned[-1])), tool=None)
    parser = _RECORD_PARSERS.get(fmt)
    if parser is not None:
        for line in reversed(lines):
            item = parser(line)
            if item is not None and item.kind in ("text", "tool"):
                return NowLine(kind=item.kind, text=_flatten(item.text), tool=item.tool)
    cleaned = [line for line in lines if _strip_ansi(line).strip()]
    if not cleaned:
        return None
    return NowLine(kind="raw", text=_flatten(_strip_ansi(cleaned[-1])), tool=None)


def model_from_log(path: Path) -> str | None:
    """A `model:` header in the first 40 lines, else the first model JSON value in 64 KB."""
    head = _head(path)
    for line in head[:40]:
        if line.startswith("model:"):
            value = line[len("model:") :].strip()
            if value:
                return value
    match = _MODEL_JSON.search("\n".join(head))
    return match.group(1) if match else None
