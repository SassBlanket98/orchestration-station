"""Read a short, privacy-filtered last action from live lead transcripts."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

TAIL_BYTES = 256 * 1024
MAX_STAGES = 40
_ROLLOUT = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T[\d-]+-(.+)\.jsonl$")


# Words, prefixes and shapes that suggest a credential. A match drops the line entirely.
_SECRETISH = re.compile(
    r"(?i)(api[_-]?key|secret|token|passw|bearer\s|authorization|private[_-]?key|"
    r"\bsk-[a-z0-9]|ghp_|gho_|xox[bpas]-|AKIA[0-9A-Z]{12}|"
    r"[A-Za-z0-9+/_-]{32,}={0,2})"
)
_PROGRAM = re.compile(r"[A-Za-z0-9_.~/][A-Za-z0-9_.~/+-]*")
_ENV_ASSIGN = re.compile(r"^[A-Z_][A-Z0-9_]*=\S*$")


def _command_words(command: str) -> str:
    """`cd x && git commit -m "..."` -> `git commit`: program and subcommand, never arguments."""
    line = command.strip().splitlines()[0]
    for part in re.split(r"&&|\|\||;|\|", line):
        words = [w for w in part.split() if not _ENV_ASSIGN.match(w)]
        if not words or words[0] in ("cd", "export", "set", "source", "."):
            continue
        if not _PROGRAM.fullmatch(words[0]):
            continue
        program = os.path.basename(words[0])[:24]
        sub = words[1] if len(words) > 1 and re.fullmatch(r"[a-z][a-z0-9-]{0,20}", words[1]) else ""
        return f"{program} {sub}".strip()
    return "command"


def _num(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


class ActivityFeed:
    """Small in-memory caches around transcript discovery and tail parsing."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = (home or Path.home()).resolve()
        self._path_cache: dict[Path, tuple[int, int, dict[str, Any] | None]] = {}
        self._codex_index: tuple[float, dict[str, Path]] = (0.0, {})
        self._response_cache: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})

    def get(self, stages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        if now - self._response_cache[0] < 3.0:
            return self._response_cache[1]
        result: dict[str, dict[str, Any]] = {}
        live = [
            stage
            for stage in stages
            if isinstance(stage, dict) and stage.get("lead_state") == "live"
        ]
        # The most recently active sessions get a line when there are more than the cap.
        live.sort(key=lambda stage: _num(stage.get("last_activity")), reverse=True)
        live = live[:MAX_STAGES]
        for stage in live:
            try:
                stage_path = stage.get("path")
                sid = stage.get("session_id") or stage.get("native_id")
                cwd = stage.get("cwd")
                kind = str(stage.get("lead_kind") or "").lower()
                if not isinstance(stage_path, str) or not isinstance(sid, str) or not sid:
                    continue
                if not isinstance(cwd, str):
                    cwd = ""
                transcript = (
                    self._find_claude(sid, cwd)
                    if kind.startswith("claude")
                    else (self._find_codex(sid) if kind.startswith("codex") else None)
                )
                if transcript is None:
                    continue
                item = self._read_activity(transcript)
                if item is not None:
                    result[stage_path] = item
            except Exception:  # noqa: BLE001 - malformed or unreadable stages are skipped
                continue
        self._response_cache = (now, result)
        return result

    def _safe(self, path: Path, roots: tuple[Path, ...]) -> Path | None:
        try:
            resolved = path.resolve(strict=True)
            if resolved.is_file() and any(
                resolved.is_relative_to(root.resolve()) for root in roots
            ):
                return resolved
        except (OSError, RuntimeError):
            return None
        return None

    def _find_claude(self, sid: str, cwd: str) -> Path | None:
        config_dirs = [
            part.strip()
            for part in os.environ.get("STATION_CLAUDE_CONFIG_DIRS", ".claude").split(":")
            if part.strip()
        ] or [".claude"]
        roots = tuple(self.home / name / "projects" for name in config_dirs)
        # Claude Code names the folder after the cwd with every non-alphanumeric character as "-".
        encoded = re.sub(r"[^A-Za-z0-9]", "-", cwd)
        for root in roots:
            candidate = self._safe(root / encoded / f"{sid}.jsonl", roots)
            if candidate is not None:
                return candidate
        return None

    def _find_codex(self, sid: str) -> Path | None:
        now = time.monotonic()
        if now - self._codex_index[0] >= 60.0:
            found: dict[str, tuple[float, Path]] = {}
            base = self.home / ".codex" / "sessions"
            try:
                for root, dirs, files in os.walk(base, followlinks=False):
                    dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
                    for name in files:
                        match = _ROLLOUT.fullmatch(name)
                        if match is None:
                            continue
                        path = Path(root) / name
                        if path.is_symlink():
                            continue
                        try:
                            mtime = path.stat().st_mtime
                        except OSError:
                            continue
                        prior = found.get(match.group(1))
                        if prior is None or mtime > prior[0]:
                            found[match.group(1)] = (mtime, path)
            except OSError:
                pass
            self._codex_index = (now, {key: value[1] for key, value in found.items()})
        path = self._codex_index[1].get(sid)
        return self._safe(path, (self.home / ".codex",)) if path is not None else None

    def _read_activity(self, path: Path) -> dict[str, Any] | None:
        stat = path.stat()
        cached = self._path_cache.get(path)
        if cached is not None and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            return cached[2]
        with path.open("rb") as handle:
            handle.seek(max(0, stat.st_size - TAIL_BYTES))
            raw = handle.read(TAIL_BYTES)
        text = raw.decode("utf-8", "replace")
        if stat.st_size > TAIL_BYTES:
            text = text.split("\n", 1)[1] if "\n" in text else ""
        result = None
        for line in reversed(text.splitlines()):
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            event = self._event(obj)
            if event is not None:
                label, kind = event
                label = label.replace("\r", " ").replace("\n", " ").strip()[:120]
                if self._secret(label):
                    continue
                timestamp = obj.get("timestamp") or obj.get("created_at")
                result = {
                    "text": label,
                    "ts": self._timestamp(timestamp, stat.st_mtime),
                    "kind": kind,
                }
                break
        self._path_cache[path] = (stat.st_size, stat.st_mtime_ns, result)
        if len(self._path_cache) > 256:
            self._path_cache.pop(next(iter(self._path_cache)))
        return result

    @classmethod
    def _event(cls, obj: dict[str, Any]) -> tuple[str, str] | None:
        if obj.get("type") == "user":
            return None
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(message, dict) and message.get("role") not in (None, "assistant"):
            return None
        # Claude stores blocks under message.content; Codex stores each event in payload.
        payload = obj.get("payload")
        if isinstance(payload, dict):
            if payload.get("type") == "function_call":
                name = str(payload.get("name") or "tool")
                if name == "AskUserQuestion":
                    return "asked the user a question", "ask"
                return cls._tool(name, payload.get("arguments"))
            if payload.get("type") == "message" and payload.get("role") == "assistant":
                content = payload.get("content")
            elif payload.get("type") == "message":
                return None
        if isinstance(content, list):
            for part in reversed(content):
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("tool_use", "function_call"):
                    name = str(part.get("name") or part.get("tool_name") or "tool")
                    args = part.get("input", part.get("arguments"))
                    if name == "AskUserQuestion":
                        return "asked the user a question", "ask"
                    return cls._tool(name, args)
                if part.get("type") in ("text", "output_text"):
                    value = part.get("text")
                    if isinstance(value, str):
                        line = value.strip().splitlines()[0] if value.strip() else ""
                        if line and not cls._secret(line):
                            return line[:120], "say"
        return None

    @staticmethod
    def _tool(name: str, args: Any) -> tuple[str, str]:
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"command": args}
        args = args if isinstance(args, dict) else {}
        path = args.get("file_path") or args.get("notebook_path") or args.get("filePath")
        if name in ("Edit", "Write", "Read") and isinstance(path, str) and path:
            short = os.path.basename(path.rstrip("/\\"))
            return f"{name} {short}", "tool"
        command = args.get("command") or args.get("cmd")
        if name == "Bash" and isinstance(command, str) and command.strip():
            return f"Bash {_command_words(command)}", "tool"
        return name[:120], "tool"

    @staticmethod
    def _secret(value: str) -> bool:
        return _SECRETISH.search(value) is not None

    @staticmethod
    def _timestamp(value: Any, fallback: float) -> float:
        if not isinstance(value, str):
            return fallback
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return fallback
