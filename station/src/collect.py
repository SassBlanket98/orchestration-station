from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import threading
import time
import weakref
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import NamedTuple

from codexread import CodexSession, read_codex_session
from gitstate import attach_work
from logparse import LogState, activity, parse_log, prune_tool_ring
from model import (
    ACTIVE_S,
    CLOSED_LIST_S,
    DONE,
    FAILED,
    IDLE_KEEP_S,
    IDLE_LIST_S,
    LIVE_STATES,
    NOREPORT,
    OPENROUTER_ACCOUNT,
    OPENROUTER_BALANCE_STALE_S,
    OPENROUTER_MAX_ROWS,
    QUIET,
    QUIET_S,
    RESCAN_S,
    RUNNING,
    SCAN_DEPTH,
    SESSION_GAP_S,
    STAGE_NAMES,
    STALL_CPU,
    STALL_S,
    STALLED,
    ExecutionEvidence,
    ExecutionRecord,
    Finding,
    GlobalSnapshot,
    Group,
    LeadInfo,
    Milestone,
    QuotaAccount,
    QuotaCredit,
    QuotaExtra,
    QuotaWindow,
    RegistrySnapshot,
    Review,
    Snapshot,
    SpendDay,
    SpendModel,
    SpendSnapshot,
    Stage,
    Worker,
    home_path,
    nest_workers,
    optional_cmd,
)
from native import (
    native_metrics,
    read_claude_metrics,
    read_codex_metrics,
    read_codex_sessions,
    read_native_executions,
    read_opencode_lead_tools,
    read_opencode_sessions,
)
from openclaw import Reader, tool_pulses
from procs import (
    _pr_argv,
    _pr_boot_id,
    _pr_boot_time,
    _pr_clk_tck,
    _pr_cwd,
    _pr_environ,
    _pr_resident,
    _pr_scan,
)
from registry import (
    default_registry_root,
    execution_key,
    read_execution_artifact,
    read_registry,
)

_CO_SPEND_PROVIDERS: dict[str, tuple[str, tuple[str, ...]]] = {   # provider id -> (override env var, default quota args)
    "openrouter": ("STATUS_OPENROUTER_CMD", ("openrouter", "--json")),
    "openai": ("STATUS_OPENAI_CMD", ("openai", "--json")),
}
_co_quota_timeout = 20
_co_quota_interval = 60
_co_usage_sample_s = 300          # one usage sample per account every 5 minutes
_co_usage_keep_s = 24 * 3600      # history kept for the usage screen's sparklines
_co_usage_buckets = 48            # points per sparkline (30 minutes each)
_co_default_usage_history = home_path("usage-history.jsonl")
_co_WINDOW_SECS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400, "thirty_day": 30 * 86400}


def _co_window_secs(win: QuotaWindow) -> int:
    """Length of a quota window in seconds; unknown ids count as a week."""
    return _co_WINDOW_SECS.get(win.window, 7 * 86400)


def _co_command(env_name: str, default_args: tuple[str, ...]) -> list[str]:
    """Resolve a configured shell-style command or build Station's default argv without splitting its path."""
    configured = os.environ.get(env_name)
    try:
        argv = (shlex.split(os.path.expanduser(configured)) if configured
                else [home_path("bin/quota"), *default_args])
    except ValueError:
        return []
    executable = optional_cmd(argv[0]) if argv else None
    return [executable, *argv[1:]] if executable else []


_co_pid_reuse_window = 120
_co_milestone_rollback = 300  # seconds after the file mtime before rolling a day back
_co_max_detail = 2000
_co_review_tail = 2 * 1024 * 1024  # bytes of a review log reread for findings
_co_service_depth = 3              # how deep into a pid's tree we look for an agent
_co_service_agents = ("claude", "codex", "opencode", "cursor-agent", "cursor")
_co_discovered_max = 200           # most unregistered headless workers listed for one stage
_co_grace_s = 600                  # a headless worker that has just exited stays listed (as done) this long, seconds
_co_loose_keep_s = 14 * 86400      # only a hard cost bound here; _co_ops_windows makes the real (24 h) age decision, seconds


def openclaw_agent() -> str:
    """Configured OpenClaw agent; empty disables the optional integration."""
    return os.environ.get("STATION_OPENCLAW_AGENT", "").strip()


OPENCLAW_AGENT = openclaw_agent()


def _co_agent_label() -> str:
    """The human-facing label for the configured OpenClaw agent."""
    return "Bot" if OPENCLAW_AGENT.casefold() == "openclaw" else OPENCLAW_AGENT[:1].upper() + OPENCLAW_AGENT[1:]


_co_execution_tags = {
    ("claude", "personal"): "CC·P",
    ("claude", "work"): "CC·W",
    ("codex", "personal"): "CDX",
    ("t3", "personal"): "T3",
    ("opencode", "personal"): "OCode",
    ("openclaw", "openclaw"): _co_agent_label(),
    ("cursor", "personal"): "CUR",
}
_co_child_live_s = 900             # an OpenCode subagent session updated this recently and not idle is running, seconds
_co_child_max = 40                 # most subagent sessions listed under one OpenCode session
_co_terminal_peer_s = 300          # in a folder with a terminal OpenCode, other sessions show only when this fresh, seconds
_co_default_ledger = home_path("ledger/runs.jsonl")
_co_native_cache: dict[str, tuple[float, tuple[ExecutionEvidence, ...]]] = {}
_co_ledger_tail = 64 * 1024                      # bytes of the run ledger read for leads
_co_status_suffixes = (".pid", ".log", ".txt")   # top-level stage files that show activity
_co_status_names = ("PROGRESS.md", "lead.json")
_co_skip_dirs = ("node_modules", "__pycache__", "target", "dist", "build")
_co_spare_root = "/tmp/cc-daemon-"                # idle claude spares run here: never leads
_co_claude_worker_flags = ("-p", "--print", "bg-pty-host")
_co_headless_flags = ("-p", "--print")     # a headless run; "bg-pty-host" is a helper, not work
_co_claude_subcommands = ("daemon", "mcp", "doctor", "update", "install", "setup-token")
_co_background_kinds = ("claude bg", "t3 code")  # leads that run without a terminal in front of them
_co_dismissed_file = home_path("status-dismissed.json")
_co_overrides_file = home_path("status-overrides.json")    # manual idle/active pins: {"<stage path>": {"state", "set"}}
_co_headless_max = 2000
# control center: codex rollout wiring
_co_cc_codex_cache_max = 256           # rollouts whose parse result is remembered by mtime
_co_cc_injected_goal = "# AGENTS.md instructions"
_co_cc_injected_names = ("# AGENTS.md instructions", "<environment_context>", "<user_instructions>",
                         "<permissions instructions>")
_co_cc_uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_co_cc_tag_harness = {"CC·W": "claude", "CC·P": "claude", "CDX": "codex",
                      "OCode": "opencode", "OClaw": "openclaw", _co_agent_label(): "openclaw", "CUR": "cursor"}
_co_cc_codex_cache: dict[str, tuple[float, CodexSession | None]] = {}


def _co_safe_pid(raw: str) -> int:
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit():
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0

_co_finding_re = re.compile(r"^- \[P(\d)\]\s*(.+)$")
_co_ms_re = re.compile(r"^(\d{2}):(\d{2})(.*)$")
_co_dispatch_re = re.compile(r"^dispatch: launched (.+)$")   # a PROGRESS.md milestone: the names launched together
_co_job_re = re.compile(r"^job:(.*)$", re.IGNORECASE)
_co_group_gap_s = 60                                        # starts this close with no dispatch line share a group
_co_effort_re = re.compile(r"\b(low|medium|med|high|xhigh|max)\b", re.IGNORECASE)
_co_session_cache: dict[tuple[str, float, int, int], object] = {}
_co_brief_cwd_cache: dict[str, tuple[float, str]] = {}


def _co_effort(text: str) -> str:
    match = _co_effort_re.search(text)
    if not match:
        return ""
    value = match.group(1).lower()
    return "medium" if value == "med" else value


def _co_worker_session(stage: str, name: str) -> str:
    try:
        with open(os.path.join(stage, name + ".session"), encoding="utf-8") as fh:
            data = json.load(fh)
        value = data.get("lead_session", "") if isinstance(data, dict) else ""
        return value if isinstance(value, str) else ""
    except (OSError, ValueError, TypeError):
        return ""


def _co_worker_lineage(stage: str, name: str) -> tuple[str, str, str, int, tuple[int, ...], float]:
    """(launching session, parent session, root session, launch depth, launcher's ancestor pids, launch time) from
    <name>.session (schema 2 fields; older files give only the launching session). With no launching id of its own the
    nearest known ancestor stands in."""
    none = ("", "", "", 0, (), 0.0)
    try:
        with open(os.path.join(stage, name + ".session"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return none
    if not isinstance(data, dict):
        return none

    def text(key: str) -> str:
        value = data.get(key, "")
        return value if isinstance(value, str) else ""
    depth = data.get("depth")
    depth = depth + 1 if isinstance(depth, int) and not isinstance(depth, bool) and 0 <= depth < 64 else 0
    pids = data.get("ancestors")
    pids = tuple(p for p in pids[:32] if isinstance(p, int) and not isinstance(p, bool) and p > 1) if isinstance(pids, list) else ()
    when = data.get("launched_at")
    when = float(when) if isinstance(when, (int, float)) and not isinstance(when, bool) else 0.0
    parent = text("parent_session")
    return text("lead_session") or parent, parent, text("root_session"), depth, pids, when


def _co_short_name(value: str) -> str:
    """At most 5 words and 36 characters, cut only between words; one over-long word is kept whole."""
    words = value.split()[:5]
    out = ""
    for word in words:
        cand = f"{out} {word}" if out else word
        if len(cand) > 36:
            break
        out = cand
    return out or (words[0] if words else "")


def _co_session_label(lead_name: str, session_id: str, fallback: str) -> str:
    """A session row's name from that row alone, so it never changes with its siblings: the lead's title, else the last
    8 characters of its session id, else the fallback (never empty)."""
    return lead_name or session_id[-8:] or fallback


def _co_iso_epoch(value: object) -> float:
    """Epoch seconds of an ISO-8601 timestamp (``...Z`` allowed); 0.0 when absent or unparsable."""
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _co_claude_sidecar(config: str, pid: int, child_session: str) -> dict[str, object]:
    """A Claude lead's <config>/sessions/<pid>.json; when the lead has none, its headless child's, found by session id."""
    folder = os.path.join(config, "sessions")
    try:
        with open(os.path.join(folder, f"{pid}.json"), encoding="utf-8") as fh:
            info = json.load(fh)
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        pass
    if not child_session:
        return {}
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    for filename in names:
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, filename), encoding="utf-8") as fh:
                info = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and info.get("sessionId") == child_session:
            return info
    return {}


def _co_first_prompt_title(transcript: str) -> str:
    """What a session with no title of its own was asked to do: the opening of its first real user message."""
    try:
        with open(transcript, "rb") as fh:
            head = fh.read(65536).decode("utf-8", "replace")
    except OSError:
        return ""
    for raw in head.splitlines()[:60]:
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "user" or not isinstance(obj.get("message"), dict):
            continue
        text = _co_prompt_text(obj["message"].get("content"))
        if text and not text.startswith(("<", "Caveat:", "[")):
            return text
    return ""


def _co_lead_json_is_mine(data: object, lead: _co_Lead, mtime: float) -> bool:
    """Whether a stage's lead.json describes this lead: its pid when it names one, else written after the lead started by the same harness."""
    if not isinstance(data, dict):
        return False
    pid = data.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool) and pid:
        return pid == lead.pid
    harness = str(data.get("harness", "")).lower()
    if harness and harness not in lead.kind.lower():
        return False
    return mtime >= lead.since


def _co_is_launcher_name(name: str, cwd: str) -> bool:
    """A name that is only the project folder with a short slot suffix (``sample-project-a6``): it says where, not what."""
    base = os.path.basename(os.path.normpath(cwd)).lower() if cwd else ""
    return bool(name) and bool(base) and re.fullmatch(rf"{re.escape(base)}(-[a-z0-9]{{1,4}})?", name.lower()) is not None


def _co_lead_session(stage: str, lead: _co_Lead | None, child_session: str = "") -> dict[str, object]:
    """Best effort, read-only metadata for the selected interactive lead."""
    result: dict[str, object] = {"session_id": "", "session_name": "", "tag": "", "lead_model": "", "effort": "", "activity": 0.0}
    result.update(parent="", root_session="", launch_depth=0, app=False, app_id="", tmux="")
    lead_json_mtime = 0.0
    lead_json: object = {}
    try:
        lead_json_path = os.path.join(stage, "lead.json")
        lead_json = json.loads(_co_read_text(lead_json_path))
        explicit_name = lead_json.get("name", "") if isinstance(lead_json, dict) else ""
        lead_json_mtime = os.stat(lead_json_path).st_mtime
    except (OSError, ValueError, TypeError):
        explicit_name = ""
    if not lead:
        return result
    if explicit_name and not _co_lead_json_is_mine(lead_json, lead, lead_json_mtime):
        explicit_name = ""   # another lead's name left in the stage folder: it is not this session's name
    pid, kind = lead.pid, lead.kind
    env = _co_environ(pid)
    result["parent"] = env.get("STATUS_PARENT_SESSION", "")
    result["root_session"] = env.get("STATUS_ROOT_SESSION", "")
    try:
        result["launch_depth"] = max(0, int(env.get("STATUS_DEPTH", "0")))
    except ValueError:
        pass
    if kind.startswith("claude"):
        config = env.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        result["tag"] = "CC·W" if "work" in os.path.basename(config).lower() else "CC·P"
        try:
            info = _co_claude_sidecar(config, pid, child_session)
            bridge = info.get("bridgeSessionId", "")
            locator = info.get("tmux", "")
            result["app_id"] = bridge if isinstance(bridge, str) else ""
            result["app"] = bool(result["app_id"])
            result["tmux"] = locator.split(":", 1)[0] if isinstance(locator, str) else ""
            sid, cwd = info.get("sessionId", ""), info.get("cwd", "")
            result["session_id"] = sid if isinstance(sid, str) else ""
            # The name the lead was started with (-n) beats the sidecar name; both beat the transcript title.
            # A "derived" sidecar name is auto-generated, so it does not count; a lead.json written before this
            # process started is stale, so the -n name wins over it.
            argv_name = _co_argv_name(pid)
            if argv_name and lead_json_mtime < lead.since:
                explicit_name = argv_name
            sidecar_name = info.get("name") if info.get("nameSource") != "derived" else ""
            named = argv_name or (sidecar_name if isinstance(sidecar_name, str) else "")
            if _co_is_launcher_name(named, cwd if isinstance(cwd, str) else ""):
                named = ""   # "<project>-a6" names the launcher slot, not the task: the transcript's own title says what it is doing
            if _co_is_launcher_name(explicit_name, cwd if isinstance(cwd, str) else ""):
                explicit_name = ""
            result["harness_title"] = named
            # sidecar updatedAt is in ms; it stands in for activity when the transcript is missing
            updated = info.get("updatedAt")
            if isinstance(updated, (int, float)) and not isinstance(updated, bool) and updated > 0:
                result["activity"] = updated / 1000.0
            transcript = os.path.join(config, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{sid}.jsonl")
            st = os.stat(transcript)
            result["activity"] = st.st_mtime
            key = (transcript, st.st_mtime, st.st_size, st.st_ino)
            parsed = _co_session_cache.get(key)
            if not isinstance(parsed, dict):
                custom_title = agent_title = ai_title = model = effort = ""
                last_entry = 0.0
                for raw in _co_read_tail(transcript, 64 * 1024):
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    typ = obj.get("type")
                    if typ in ("user", "assistant"):
                        last_entry = max(last_entry, _co_iso_epoch(obj.get("timestamp")))
                    if typ == "custom-title" and obj.get("customTitle"):
                        custom_title = str(obj["customTitle"])
                    elif typ == "agent-name" and obj.get("agentName"):
                        agent_title = str(obj["agentName"])
                    elif typ == "ai-title" and obj.get("aiTitle"):
                        ai_title = str(obj["aiTitle"])
                    elif typ == "assistant" and isinstance(obj.get("message"), dict):
                        msg = obj["message"]
                        if msg.get("model") != "<synthetic>":   # Claude Code's placeholder for error and rate-limit messages: no model
                            model = str(msg.get("model", ""))
                        effort = str(obj.get("effort", ""))
                parsed = {"title": custom_title or agent_title or ai_title or _co_first_prompt_title(transcript),
                          "model": model, "effort": effort, "last": last_entry}
                _co_session_cache[key] = parsed
            # The file mtime moves with housekeeping; the last message is the real activity.
            if parsed.get("last"):
                result["activity"] = parsed["last"]
            result["harness_title"] = named or parsed["title"]
            raw_model = parsed["model"]
            short = re.sub(r"^claude-(sonnet|opus|haiku)-(\d+)-(\d+)$", r"\1-\2.\3", raw_model)
            result["lead_model"] = short
            result["effort"] = _co_effort(parsed["effort"])
        except (OSError, ValueError, TypeError, KeyError):
            pass
        if not result["session_id"] and child_session:
            result["session_id"] = child_session
    elif kind in ("codex", "t3 code"):
        # T3 Code drives Codex underneath: its rollout is the same file, so it reads the same activity
        result["tag"] = "T3" if kind == "t3 code" else "CDX"
        try:
            codex_home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
            index_path = os.path.join(codex_home, "session_index.jsonl")
            index = []
            for raw in _co_read_lines(index_path):
                try:
                    item = json.loads(raw)
                except ValueError:
                    continue
                if item.get("thread_id"):
                    index.append(item)
            rollouts = []
            sessions_dir = os.path.join(codex_home, "sessions")
            for root, _, files in os.walk(sessions_dir):
                for filename in files:
                    if not filename.endswith(".jsonl"):
                        continue
                    full = os.path.join(root, filename)
                    try:
                        st = os.stat(full)
                        with open(full, encoding="utf-8", errors="replace") as fh:
                            first = json.loads(fh.readline(1 << 20))   # dynamic_tools can push the first line past 100 KB
                    except (OSError, ValueError):
                        continue
                    payload = first.get("payload", {}) if isinstance(first, dict) and first.get("type") == "session_meta" else {}
                    if not isinstance(payload, dict):
                        continue
                    source = payload.get("source")
                    if isinstance(source, dict) and source.get("subagent"):
                        continue
                    thread_id = payload.get("id", payload.get("thread_id"))
                    cwd = payload.get("cwd")
                    if (not isinstance(thread_id, str) or not thread_id or not isinstance(cwd, str)
                            or os.path.normpath(cwd) != os.path.normpath(lead.cwd)):
                        continue
                    rollouts.append((st.st_mtime, full, thread_id, _co_iso_epoch(first.get("timestamp"))))
            if kind == "t3 code":
                # T3 can serve a new session from an older host process.
                rollouts.sort(reverse=True)
                selected = rollouts[0] if rollouts and (len(rollouts) == 1 or rollouts[0][0] != rollouts[1][0]) else None
            else:
                # A cwd can have several Codex sessions. Corroborate the rollout's creation
                # time with the lead start; a lone legacy rollout without a timestamp is unambiguous.
                # With several candidates the closest one wins, but only when clearly closest (workers a lead
                # spawns in its own cwd start within a second of it).
                near = sorted((item for item in rollouts if item[3] and abs(item[3] - lead.since) <= _co_pid_reuse_window),
                              key=lambda item: abs(item[3] - lead.since))
                clear = len(near) == 1 or (len(near) > 1 and abs(near[1][3] - lead.since) - abs(near[0][3] - lead.since) >= 1.0)
                selected = near[0] if clear else rollouts[0] if len(rollouts) == 1 and not rollouts[0][3] else None
            if selected is not None:
                result["activity"] = selected[0]
                thread_id = selected[2]
                matches = [item for item in index if item.get("thread_id") == thread_id] if thread_id else []
                result["session_id"] = thread_id
                if matches:
                    result["harness_title"] = str(matches[-1].get("thread_name", ""))
                model = effort = ""
                with open(selected[1], encoding="utf-8", errors="replace") as fh:
                    for _ in range(32):
                        raw = fh.readline(64 * 1024)
                        if not raw:
                            break
                        try:
                            obj = json.loads(raw)
                        except ValueError:
                            continue
                        payload = obj.get("payload", {})
                        if obj.get("type") == "turn_context" and isinstance(payload, dict):
                            model, effort = str(payload.get("model", model)), str(payload.get("effort", effort))
                for raw in _co_read_tail(selected[1], 64 * 1024):
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        continue
                    payload = obj.get("payload", {})
                    if obj.get("type") == "turn_context" and isinstance(payload, dict):
                        model, effort = str(payload.get("model", model)), str(payload.get("effort", effort))
                result["lead_model"], result["effort"] = model, _co_effort(effort)
        except (OSError, ValueError, TypeError):
            pass
    elif kind == "t3 code":
        result["tag"] = "T3"   # T3 drives Codex underneath; it is its own harness, not a Codex session
    elif kind == "opencode":
        result["tag"] = "OCode"
        try:
            db = os.path.join(os.path.expanduser("~"), ".local/share/opencode/opencode.db")
            conn = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('session_v2','session')")}
                if "session_v2" in tables:   # newer OpenCode keeps the model on the session row, as JSON
                    # a terminal lead stands for a top-level session, never a subagent's child row
                    top = " AND parent_id IS NULL" if any(r[1] == "parent_id" for r in conn.execute("PRAGMA table_info(session_v2)")) else ""
                    where, arg = ("id=?", lead.sid) if lead.sid else (f"directory=?{top}", lead.cwd)
                    row = conn.execute(f"SELECT id,title,time_updated,model FROM session_v2 WHERE {where} ORDER BY time_updated DESC LIMIT 1", (arg,)).fetchone()
                    if row:
                        try:
                            result["lead_model"] = str(json.loads(row[3] or "{}").get("id", ""))
                        except (ValueError, AttributeError):
                            pass
                elif "session" in tables:
                    where, arg = ("id=?", lead.sid) if lead.sid else ("directory=?", lead.cwd)
                    row = conn.execute(f"SELECT id,title,time_updated FROM session WHERE {where} ORDER BY time_updated DESC LIMIT 1", (arg,)).fetchone()
                    if row:
                        msg = conn.execute("SELECT data FROM message WHERE session_id=? AND data LIKE '%modelID%' ORDER BY time_created DESC LIMIT 1", (row[0],)).fetchone()
                        if msg:
                            result["lead_model"] = str(json.loads(msg[0]).get("modelID", ""))
                else:
                    row = None
                if row:
                    result["session_id"], result["harness_title"] = str(row[0]), str(row[1] or "")
                    updated = float(row[2] or 0.0)
                    result["activity"] = updated / 1000.0 if updated > 100_000_000_000 else updated
            finally:
                conn.close()
        except (OSError, sqlite3.Error, ValueError, TypeError):
            pass
    elif kind == "cursor":
        result["tag"] = "CUR"
    harness_title = result.pop("harness_title", "")
    result["session_name"] = _co_short_name(str(explicit_name or harness_title))
    return result


def family_of(model: str) -> str:
    """Map a model label to a coarse family (lowercased, first match wins)."""
    m = model.lower()
    if "grok" in m:
        return "grok"
    if "deepseek" in m or "opencode" in m:
        return "deepseek"
    if "composer" in m or "cursor" in m:
        return "cursor"
    if "claude" in m or "haiku" in m or "sonnet" in m or "opus" in m:
        return "claude"
    if "gpt" in m or "luna" in m or "sol" in m or "codex" in m:
        return "codex"
    return "other"


def _co_stat(path: str) -> os.stat_result | None:
    try:
        return os.stat(path)
    except OSError:
        return None


def _co_read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (OSError, ValueError):
        return ""


def _co_read_lines(path: str) -> list[str]:
    return _co_read_text(path).splitlines()


def _co_read_tail(path: str, limit: int) -> list[str]:
    """Read at most the last ``limit`` bytes of ``path`` and split them into lines."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = size - limit if size > limit else 0
            fh.seek(start)
            data = fh.read()
    except (OSError, ValueError):
        return []
    text = data.decode("utf-8", "replace")
    if start > 0:
        cut = text.find("\n")
        text = text[cut + 1:] if cut >= 0 else ""
    return text.splitlines()


def _co_log_rewritten(path: str, st: os.stat_result, old: tuple[int, float, int, bytes]) -> bool:
    """True when a cached log was replaced or rewritten, so parsing must restart."""
    old_ino, old_mtime, old_size, old_head = old
    if st.st_ino != old_ino:
        return True
    if st.st_size <= old_size and st.st_mtime != old_mtime:
        return True
    try:
        with open(path, "rb") as handle:
            head = handle.read(min(4096, old_size))
    except OSError:
        return True
    return hashlib.sha1(head).digest() != old_head


def _co_guard(func: Callable[..., object], *args: object, **kwargs: object) -> object | None:
    """Call func, returning None instead of letting a filesystem error escape."""
    try:
        return func(*args, **kwargs)
    except Exception:  # noqa: BLE001 - the dashboard must survive any bad file
        return None


def _co_digits(path: str) -> int | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except (OSError, ValueError):
        return None
    digits = "".join(ch for ch in data if "0" <= ch <= "9")
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _co_first_line(path: str) -> str:
    lines = _co_read_lines(path)
    return lines[0].strip() if lines else ""


def _co_pick_log(stage_dir: str, name: str) -> str | None:
    for ext in (".log", ".txt"):
        path = os.path.join(stage_dir, name + ext)
        try:
            if os.path.isfile(path):
                return path
        except OSError:
            continue
    return None


def _co_brief_path(stage_dir: str, name: str) -> str | None:
    base = os.path.join(stage_dir, "briefs")
    direct = os.path.join(base, name + ".md")
    try:
        if os.path.isfile(direct):
            return direct
    except OSError:
        pass
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    prefix = name + "-"
    for entry in entries:
        if entry.startswith(prefix) and entry.endswith(".md"):
            candidate = os.path.join(base, entry)
            try:
                if os.path.isfile(candidate):
                    return candidate
            except OSError:
                continue
    return None


def _co_brief_title(path: str | None) -> str:
    if not path:
        return ""
    for line in _co_read_lines(path):
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _co_brief_goal(path: str | None) -> str:
    if not path:
        return ""
    for line in _co_read_lines(path):
        if line.startswith("Goal:"):
            return line[len("Goal:"):].strip()
    return ""


def _co_brief_job(path: str | None) -> str:
    """The brief's "Job:" value, first word lowercased ("Job: Build" -> "build"); "" if absent."""
    if not path:
        return ""
    for line in _co_read_lines(path):
        match = _co_job_re.match(line.strip())
        if match:
            words = match.group(1).split()
            return words[0].lower() if words else ""
    return ""


_co_stdout_output_re = re.compile(r"^Output:\s*stdout\b", re.IGNORECASE)


def _co_brief_stdout_only(path: str | None) -> bool:
    """True when the brief says its output is stdout ("Output: stdout only ..."): the lead saves it, so no REPORT.md is expected."""
    return bool(path) and any(_co_stdout_output_re.match(line.strip()) for line in _co_read_lines(path))


def _co_report_summary(lines: list[str]) -> list[str]:
    out: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            if line.startswith("## Summary"):
                in_section = True
            continue
        if not in_section:
            continue
        text = line.strip()
        if not text:
            continue
        if text.startswith(("- ", "* ")):
            text = text[2:]
        out.append(text)
        if len(out) >= 6:
            break
    return out


def _co_findings(lines: list[str]) -> list[Finding]:
    out: list[Finding] = []
    seen: set[str] = set()
    for line in lines:
        match = _co_finding_re.match(line)
        if not match:
            continue
        try:
            prio = int(match.group(1))
        except ValueError:
            continue
        text = match.group(2)
        cut = text.find(" \u2014 ")
        if cut >= 0:
            text = text[:cut]
        if text in seen:
            continue
        seen.add(text)
        out.append(Finding(prio=prio, text=text))
    return out


def _co_log_model_header(path: str) -> str:
    for line in _co_read_lines(path):
        if line.startswith("model:"):
            return line[len("model:"):].strip()
    return ""


def _co_clk_tck() -> int:
    return _pr_clk_tck()


def _co_page_size() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError, AttributeError):
        return 4096


def _co_boot_time() -> float:
    return _pr_boot_time()


def _co_scan_procs() -> dict[int, tuple[int, int, int, int, int]]:
    """pid -> (ppid, utime, stime, starttime_ticks, tty_nr) from the process provider."""
    return _pr_scan()


def _co_tree(root: int, children: dict[int, list[int]]) -> list[int]:
    seen: list[int] = []
    visited: set[int] = set()
    stack = [root]
    while stack:
        pid = stack.pop()
        if pid in visited:
            continue
        visited.add(pid)
        seen.append(pid)
        stack.extend(children.get(pid, ()))
    return seen


def _co_resident(pid: int, page: int) -> int | None:
    return _pr_resident(pid, page)


def _co_argv(pid: int) -> list[str]:
    """A process's argv from the process provider; empty on any error."""
    return _pr_argv(pid)


def _co_argv_name(pid: int) -> str:
    """The session name a process was started with (-n NAME, --name NAME or --name=NAME); empty when absent."""
    prev = ""
    for tok in _co_argv(pid):
        if prev in ("-n", "--name"):
            return tok
        if tok.startswith("--name="):
            return tok[len("--name="):]
        prev = tok
    return ""


def _co_cmd_model(pid: int) -> str:
    tokens = _co_argv(pid)
    model = ""
    effort = ""
    prev = ""
    for tok in tokens:
        if prev in ("-m", "--model"):
            model = tok
        elif prev == "--effort":
            effort = tok
        if tok.startswith("--model="):
            model = tok[len("--model="):]
        elif tok.startswith("--effort="):
            effort = tok[len("--effort="):]
        elif tok.startswith("model_reasoning_effort="):
            effort = tok[len("model_reasoning_effort="):]
        prev = tok
    if not model:
        return ""
    return f"{model} ({effort})" if effort else model


def _co_proc_model(pid: int, children: dict[int, list[int]]) -> str:
    queue: list[tuple[int, int]] = [(pid, 0)]
    seen: set[int] = set()
    while queue:
        current, depth = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        found = _co_cmd_model(current)
        if found:
            return found
        if depth < 3:
            for child in children.get(current, ()):
                queue.append((child, depth + 1))
    return ""


def _co_argv_is_agent(pid: int) -> bool:
    """True when the process's first two argv basenames look like an agent CLI."""
    argv = _co_argv(pid)
    for arg in argv[:2]:
        base = os.path.basename(arg).lower()
        if any(token in base for token in _co_service_agents):
            return True
    return False


def _co_proc_is_agent(pid: int, children: dict[int, list[int]]) -> bool:
    """True when pid or a descendant (depth <= ``_co_service_depth``) is an agent."""
    queue: list[tuple[int, int]] = [(pid, 0)]
    seen: set[int] = set()
    while queue:
        current, depth = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        if _co_argv_is_agent(current):
            return True
        if depth < _co_service_depth:
            for child in children.get(current, ()):
                queue.append((child, depth + 1))
    return False


def _co_is_service(
    stage: str,
    name: str,
    pid: int | None,
    procs: dict[int, tuple[int, int, int, int, int]],
    children: dict[int, list[int]],
) -> bool:
    """True when ``<name>.pid`` names a live non-agent process (a service, not a worker).

    Liveness here ignores the pid file's mtime on purpose: a server restarts under
    the same pid file, so the mtime no longer tracks the running process.
    """
    if pid is None or pid not in procs:
        return False
    if _co_brief_path(stage, name) is not None:
        return False
    if _co_stat(os.path.join(stage, name + ".model")) is not None:
        return False
    if os.path.isdir(os.path.join(stage, name)):
        return False
    return not _co_proc_is_agent(pid, children)


def _co_pid_live(
    pid: int | None,
    pid_mtime: float,
    procs: dict[int, tuple[int, int, int, int, int]],
    clk: int,
    boot: float,
) -> bool:
    if pid is None:
        return False
    info = procs.get(pid)
    if info is None:
        return False
    start_epoch = boot + info[3] / clk
    return abs(start_epoch - pid_mtime) <= _co_pid_reuse_window


class _co_Lead(NamedTuple):
    pid: int
    kind: str      # "claude", "claude work", "claude bg", "codex", "t3 code", "cursor", "opencode", "lead.json"
    state: str     # "live" (a terminal in front of it) or "background"
    cwd: str
    since: float   # process start, epoch seconds
    sid: str = ""  # the OpenCode session a daemon-hosted lead stands for; "" for a process-backed lead


def _co_cwd(pid: int) -> str:
    return _pr_cwd(pid)


_co_environ_keys = ("CLAUDE_CONFIG_DIR", "CODEX_MANAGED_BY_NPM", "CODEX_HOME", "OPENCODE_SESSION_ID",
                    "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "STATUS_PARENT_SESSION", "STATUS_PARENT_HARNESS",
                    "STATUS_ROOT_SESSION", "STATUS_DEPTH")


def _co_environ(pid: int) -> dict[str, str]:
    """The variables in ``_co_environ_keys`` from the process provider. The rest is never kept: it holds secrets."""
    return _pr_environ(pid, _co_environ_keys)


_co_ctx_cache: dict[str, tuple[float, int, int, int, int | None]] = {}   # transcript -> mtime, size, inode, ctime, window
_co_ctx_tail = 1_048_576     # bytes read from the end of a transcript


def _co_lead_ctx(pid: int, kind: str) -> int | None:
    """A claude lead's context window now: input + cache tokens of its latest assistant message.

    <account dir>/sessions/<pid>.json (~/.claude unless CLAUDE_CONFIG_DIR is set) names the session; its transcript is the newest usage record, so the
    figure drops by itself after a compact: right away from the compact_boundary entry's postTokens, then from the next reply's usage.
    None for other leads or when nothing is readable."""
    if not kind.startswith("claude"):
        return None
    # a "claude work" lead keeps its sessions under the account dir its environment names
    base = _co_environ(pid).get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        info = json.loads(_co_read_text(os.path.join(base, "sessions", f"{pid}.json")))
        session, cwd = info["sessionId"], info["cwd"]
        if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session):
            return None
        path = os.path.join(base, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{session}.jsonl")
        st = os.stat(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    cached = _co_ctx_cache.get(path)
    if cached and cached[:4] == (st.st_mtime, st.st_size, st.st_ino, st.st_ctime_ns):
        return cached[4]
    window: int | None = None
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, st.st_size - _co_ctx_tail))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        if '"usage"' not in raw and '"compact_boundary"' not in raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("subtype") == "compact_boundary":
            # a /compact or auto-compact newer than any reply: the window is what the compaction left, until the next reply measures it
            meta = obj.get("compactMetadata")
            post = meta.get("postTokens") if isinstance(meta, dict) else None
            if isinstance(post, int) and not isinstance(post, bool) and post > 0:
                window = post
                break
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if obj.get("type") != "assistant" or obj.get("isSidechain") or not isinstance(usage, dict):
            continue
        total = sum(int(usage.get(k) or 0) for k in
                    ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        if total <= 0:      # synthetic messages (errors, rate limits) carry zero usage: not a window
            continue
        window = total
        break
    _co_ctx_cache[path] = (st.st_mtime, st.st_size, st.st_ino, st.st_ctime_ns, window)
    while len(_co_ctx_cache) > 64:
        _co_ctx_cache.pop(next(iter(_co_ctx_cache)))
    return window


_co_doing_cache: dict[str, tuple[float, int, int, list[tuple[float, str]]]] = {}   # transcript -> mtime, size, inode, actions
_co_DOING_N = 6


def _co_tool_label(name: str, args: object) -> str:
    """`Edit render.py`, `Bash: git status`: one tool call as a short line."""
    a = args if isinstance(args, dict) else {}
    path = a.get("file_path") or a.get("notebook_path") or a.get("filePath")
    if isinstance(path, str) and path:
        return f"{name} {os.path.basename(path)}"
    for key in ("description", "command", "pattern", "query", "url", "prompt"):
        v = a.get(key)
        if isinstance(v, str) and v.strip():
            return f"{name}: {v.strip().splitlines()[0][:100]}"
    return name


def _co_opencode_doing(session_id: str) -> list[tuple[float, str]]:
    """An OpenCode session's latest actions (prompts, tool calls and the words around them), oldest first, from its database."""
    if not session_id:
        return []
    db = os.path.join(os.path.expanduser("~"), ".local/share/opencode/opencode.db")
    try:
        conn = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=1.0)
        try:
            rows = conn.execute("SELECT type, time_created, data FROM session_message WHERE session_id=? "
                                "AND type IN ('user','assistant') ORDER BY seq DESC LIMIT 12", (session_id,)).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []
    events: list[tuple[float, str]] = []
    for kind, created, raw in reversed(rows):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        ts = float(created or 0) / 1000.0
        if kind == "user":
            text = str(data.get("text", "")).strip()
            if text:
                events.append((ts, "you: " + text.splitlines()[0][:100]))
            continue
        for part in data.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool":
                state = part.get("state")
                events.append((ts, _co_tool_label(str(part.get("name", "tool")), state.get("input") if isinstance(state, dict) else None)))
            elif part.get("type") == "text" and str(part.get("text", "")).strip():
                events.append((ts, "says: " + str(part["text"]).strip().splitlines()[0][:100]))
    return events[-_co_DOING_N:]


_co_OC_DOING_EVENTS = 60   # the newest transcript events an OpenClaw session's actions are read from
_co_OC_PATH_ARGS = ("file_path", "filePath", "path", "notebook_path")


def _co_openclaw_doing(db_path: str, session_key: str, limit: int = 5) -> list[tuple[float, str]]:
    """An OpenClaw session's newest `limit` actions, oldest first: (epoch, short text). An assistant tool call reads
    `Read render.py` or `exec ls -la`, an assistant text its first line, a user message `asked: <first line>`.
    Reads the newest transcript events of the session's current transcript, read-only; any failure gives []."""
    try:
        conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True, timeout=1.0)
        try:
            found = conn.execute("SELECT current_session_id FROM session_nodes WHERE session_key = ?", (session_key,)).fetchone()
            if not found or not isinstance(found[0], str) or not found[0]:
                return []
            rows = conn.execute("SELECT event_json FROM transcript_events WHERE session_id = ? ORDER BY seq DESC LIMIT ?",
                                (found[0], _co_OC_DOING_EVENTS)).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
        return []
    newest: list[tuple[float, str]] = []   # newest first; each event's own actions are added in reverse, then flipped at the end
    for (event_json,) in rows:
        if len(newest) >= limit:
            break
        if not isinstance(event_json, str):   # old events kept only compressed: skipped, not decompressed
            continue
        try:
            obj = json.loads(event_json)
        except (ValueError, TypeError, RecursionError):
            continue
        if not isinstance(obj, dict) or obj.get("type") != "message" or not isinstance(obj.get("message"), dict):
            continue
        msg = obj["message"]
        ts = _co_parse_iso(obj.get("timestamp"))
        if ts is None and isinstance(msg.get("timestamp"), (int, float)) and not isinstance(msg.get("timestamp"), bool):
            ts = float(msg["timestamp"]) / 1000.0 if msg["timestamp"] > 100_000_000_000 else float(msg["timestamp"])
        if ts is None:
            continue
        role, content = msg.get("role"), msg.get("content")
        items: list[str] = []
        if role == "user":
            if isinstance(content, list):
                content = "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text")
            line = _co_text_first_line(content) if isinstance(content, str) else ""
            if line:
                items.append(_co_clip70("asked: " + line))
        elif role == "assistant" and isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "toolCall":
                    items.append(_co_clip70(_co_oc_tool_text(part)))
                elif part.get("type") == "text" and _co_text_first_line(str(part.get("text", ""))):
                    items.append(_co_clip70(_co_text_first_line(str(part.get("text", "")))))
        for text in reversed(items):
            newest.append((ts, text))
    return list(reversed(newest[:limit]))


def _co_text_first_line(text: str) -> str:
    """The first non-empty line of ``text``, stripped; "" when there is none."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _co_clip70(text: str) -> str:
    return text if len(text) <= 70 else text[:69] + "…"


def _co_oc_tool_text(part: dict) -> str:
    """A tool call as `Name arg`: the first short argument value (a path shows as its file name), the name alone without one."""
    name = str(part.get("name") or "tool")
    args = part.get("arguments")
    args = args if isinstance(args, dict) else {}
    for key in _co_OC_PATH_ARGS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return f"{name} {os.path.basename(value.strip().rstrip('/'))}"
    for value in args.values():
        if isinstance(value, str) and _co_text_first_line(value):
            return f"{name} {_co_text_first_line(value)}"
    return name


def _co_prompt_text(content: object) -> str:
    """The first line of a user message's text (its string, or its text blocks; tool results have none), at most 100 characters."""
    if isinstance(content, list):
        content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text")
    if not isinstance(content, str) or not content.strip():
        return ""
    return content.strip().splitlines()[0][:100]


def _co_lead_doing_note(pid: int, kind: str, session_id: str = "") -> tuple[list[tuple[float, str]], str]:
    """A lead's doing (see _co_lead_doing) and, when it is empty, why: "no session file" when the lead's session file names
    no usable transcript, "no transcript" when that transcript is missing or has nothing readable; "" otherwise."""
    if kind == "opencode":
        return _co_opencode_doing(session_id), ""
    if not kind.startswith("claude"):
        return [], ""
    base = _co_environ(pid).get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        info = json.loads(_co_read_text(os.path.join(base, "sessions", f"{pid}.json")))
        session, cwd = info["sessionId"], info["cwd"]
        if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session):
            return [], "no session file"
        path = os.path.join(base, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{session}.jsonl")
    except (OSError, ValueError, KeyError, TypeError):
        return [], "no session file"
    try:
        st = os.stat(path)
    except OSError:
        return [], "no transcript"
    events = _co_transcript_doing(path, st)
    return events, "" if events else "no transcript"


def _co_lead_doing(pid: int, kind: str, session_id: str = "") -> list[tuple[float, str]]:
    """A claude or OpenCode lead's latest actions (tool calls and the words around them), oldest first: a claude lead's
    from the tail of its transcript, an OpenCode session's from its database."""
    return _co_lead_doing_note(pid, kind, session_id)[0]


def _co_transcript_doing(path: str, st: os.stat_result) -> list[tuple[float, str]]:
    """A claude transcript's latest actions from its tail, oldest first; with none, its last user prompt."""
    cached = _co_doing_cache.get(path)
    if cached and cached[:3] == (st.st_mtime, st.st_size, st.st_ino):
        return cached[3]
    events: list[tuple[float, str]] = []
    prompt: list[tuple[float, str]] = []
    for raw in _co_read_tail(path, 256 * 1024):
        if '"assistant"' not in raw and '"user"' not in raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(obj, dict) or obj.get("isSidechain"):
            continue
        ts = _co_parse_iso(obj.get("timestamp")) or st.st_mtime
        if obj.get("type") == "user":
            text = _co_prompt_text(content)
            if text and not obj.get("isMeta"):
                prompt = [(ts, "asks: " + text)]
            continue
        if obj.get("type") != "assistant" or not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                events.append((ts, _co_tool_label(str(part.get("name", "tool")), part.get("input"))))
            elif part.get("type") == "text" and str(part.get("text", "")).strip():
                events.append((ts, "says: " + str(part["text"]).strip().splitlines()[0][:100]))
    events = events[-_co_DOING_N:] or prompt
    _co_doing_cache[path] = (st.st_mtime, st.st_size, st.st_ino, events)
    while len(_co_doing_cache) > 64:
        _co_doing_cache.pop(next(iter(_co_doing_cache)))
    return events


def _co_words(argv: list[str]) -> list[str]:
    """argv with a rewritten process title split into words: claude sets argv[0] to "claude bg-spare".
    A first word that is an existing path (with spaces in it) is kept whole."""
    if not argv or " " not in argv[0] or os.path.exists(argv[0]):
        return argv
    return argv[0].split() + argv[1:]


def _co_under(path: str, parent: str) -> bool:
    path, parent = os.path.normpath(path), os.path.normpath(parent)
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def _co_is_claude_binary(argv0: str) -> bool:
    """True for a claude binary: named claude, or run from the daemon's versioned install (.../claude/versions/2.1.296),
    whose basename is the version number."""
    return os.path.basename(argv0).lower().endswith("claude") or "/claude/versions/" in argv0


def _co_lead_kind(argv: list[str], cwd: str, home: str, *, tty: bool, environ: dict[str, str]) -> str:
    """The interactive lead this argv, cwd, terminal and environment name, or "" for anything else
    (workers, spares, servers, headless runs). A session someone is prompting always has a terminal."""
    argv = _co_words(argv)
    if not argv:
        return ""
    name = os.path.basename(argv[0]).lower()
    rest = argv[1:]
    if _co_is_claude_binary(argv[0]):
        if rest[:1] == ["bg-spare"]:
            return "" if cwd.startswith(_co_spare_root) else "claude bg"
        if rest and rest[0] in _co_claude_subcommands:
            return ""
        if any(tok in _co_claude_worker_flags or "stream-json" in tok for tok in rest):
            return ""
        if not tty:
            return ""
        config = environ.get("CLAUDE_CONFIG_DIR", "")
        # Personal testing uses an isolated dir; all work and other custom dirs stay in the work account.
        config_path = os.path.normpath(os.path.expanduser(config)) if config else os.path.join(home, ".claude")
        if config_path not in (os.path.join(home, ".claude"), os.path.join(home, ".claude-testing")):
            return "claude work"
        return "claude"
    if name == "codex":
        # codex run by npm or by OpenClaw is a tool's own helper, never the user's session
        codex_home = environ.get("CODEX_HOME", "")
        if environ.get("CODEX_MANAGED_BY_NPM") == "1" or (
                codex_home and _co_under(os.path.expanduser(codex_home), os.path.join(home, ".openclaw"))):
            return ""
        if "app-server" in rest:
            return "t3 code" if any("mcp_servers.t3-code" in tok for tok in rest) else ""
        if rest[:1] == ["exec"] or not tty:
            return ""
        return "codex"
    if name == "cursor-agent" or (name == "agent" and any("cursor-agent" in tok for tok in rest[:2])):
        return "" if "-p" in rest or "--print" in rest or not tty else "cursor"
    if "opencode" in name:
        if rest[:1] in (["run"], ["serve"]) or not tty:
            return ""
        return "opencode"
    return ""


def _co_headless_kind(argv: list[str], *, environ: dict[str, str], home: str) -> str:
    """The harness of a headless (non-interactive) agent run, or "" for anything else.

    These are the runs ``_co_lead_kind`` turns away: a lead has a terminal and no headless flag. A worker
    launched by ``dispatch`` writes ``<name>.pid`` and is found the normal way; this catches the rest (an
    eval harness, a one-off ``claude -p``) from the process table, so it is only ever seen while it runs."""
    argv = _co_words(argv)
    if not argv:
        return ""
    name = os.path.basename(argv[0]).lower()
    rest = argv[1:]
    if _co_is_claude_binary(argv[0]):
        if rest[:1] == ["bg-spare"] or (rest and rest[0] in _co_claude_subcommands):
            return ""
        if not any(tok in _co_headless_flags or "stream-json" in tok for tok in rest):
            return ""
        return "claude"
    if name == "codex":
        codex_home = environ.get("CODEX_HOME", "")
        if environ.get("CODEX_MANAGED_BY_NPM") == "1" or (
                codex_home and _co_under(os.path.expanduser(codex_home), os.path.join(home, ".openclaw"))):
            return ""
        if "app-server" in rest:
            return ""
        return "codex" if "exec" in rest[:3] else ""
    if name == "cursor-agent" or (name == "agent" and any("cursor-agent" in tok for tok in rest[:2])):
        return "cursor" if any(tok in ("-p", "--print") for tok in rest) else ""
    if "opencode" in name:
        return "opencode" if rest[:1] == ["run"] else ""
    return ""


def _co_proc_model_effort(pid: int, children: dict[int, list[int]]) -> tuple[str, str]:
    """A live process's model label and reasoning effort, from its own argv or a descendant's."""
    raw = _co_cmd_model(pid) or _co_proc_model(pid, children)
    match = re.match(r"^(.*?)\s*\((\w+)\)$", raw)
    if match:
        return match.group(1), match.group(2)
    return raw, _co_effort(raw)


_co_hu_memo: list = [None, {}]   # [the proc table it belongs to, pid -> (argv, cwd)]: one table is scanned once per project


def _co_hu_read(procs: dict[int, tuple[int, int, int, int, int]], pid: int) -> tuple[list[str], str]:
    if _co_hu_memo[0] is not procs:
        _co_hu_memo[0] = procs
        _co_hu_memo[1] = {}
    cache = _co_hu_memo[1]
    if pid not in cache:
        argv = _co_argv(pid)
        cache[pid] = (argv, _co_cwd(pid) if argv else "")
    return cache[pid]


def _co_headless_under(
    procs: dict[int, tuple[int, int, int, int, int]], project: str, home: str
) -> list[tuple[int, str, str]]:
    """(pid, cwd, harness) for each headless agent run under ``project``, deepest project only.

    A process the lead classifier already claims (e.g. ``codex --search exec`` typed in a terminal) is left to
    it, so one process is never counted as both a lead and a worker."""
    found: list[tuple[int, str, str]] = []
    for pid in procs:
        argv, cwd = _co_hu_read(procs, pid)
        if not argv:
            continue
        if not cwd or not _co_under(cwd, project) or not _co_owns_cwd(cwd, project):
            continue
        environ = _co_environ(pid)
        if _co_lead_kind(argv, cwd, home, tty=procs[pid][4] != 0, environ=environ):
            continue
        harness = _co_headless_kind(argv, environ=environ, home=home)
        if harness:
            found.append((pid, cwd, harness))
    return found


def _co_lead_scan(procs: dict[int, tuple[int, int, int, int, int]], argv_of: Callable[[int], list[str]],
                  cwd_of: Callable[[int], str], home: str, clk: int, boot: float,
                  environ_of: Callable[[int], dict[str, str]] | None = None) -> list[_co_Lead]:
    """The interactive leads among procs (the stat table), read through argv_of, cwd_of and environ_of."""
    leads: list[_co_Lead] = []
    for pid in procs:
        argv = argv_of(pid)
        if not argv:
            continue
        cwd = cwd_of(pid)
        environ = environ_of(pid) if environ_of is not None else {}
        kind = _co_lead_kind(argv, cwd, home, tty=procs[pid][4] != 0, environ=environ)
        if kind:
            state = "background" if kind in _co_background_kinds else "live"
            leads.append(_co_Lead(pid, kind, state, cwd, boot + procs[pid][3] / clk))
    return leads


_co_service_window = ACTIVE_S   # a daemon-hosted session is listed this long after its last update (or while a subagent runs), seconds


_co_run_session_cache: dict[tuple[str, float, int], str] = {}
_co_run_record_cache: dict[str, tuple[int, dict[str, tuple[int, int, dict]]]] = {}
_co_run_session_re = re.compile(r'"sessionID"\s*:\s*"(ses_[A-Za-z0-9]+)"')


_co_run_subdir_cache: dict[str, tuple[int, list[str]]] = {}


def _co_run_record_dirs(stage: str) -> list[str]:
    """``stage`` plus its immediate subdirectories: dispatch writes ``<name>.run.json`` in each run's own stage
    directory, which sits one level below the stage the collector found. The listing is cached by the mtime."""
    stamp = _co_stat(stage)
    if stamp is None:
        _co_run_subdir_cache.pop(stage, None)
        return []
    cached = _co_run_subdir_cache.get(stage)
    if cached is None or cached[0] != stamp.st_mtime_ns:
        try:
            with os.scandir(stage) as entries:
                subdirs = sorted(entry.path for entry in entries if entry.is_dir(follow_symlinks=False))
        except OSError:
            subdirs = []
        cached = _co_run_subdir_cache[stage] = (stamp.st_mtime_ns, subdirs)
    return [stage, *cached[1]]


def _co_load_run_records(stage: str) -> dict[str, dict]:
    """Dispatch identities directly in a known stage, cached by directory and file mtimes."""
    stamp = _co_stat(stage)
    if stamp is None:
        _co_run_record_cache.pop(stage, None)
        return {}
    cached_stamp, cached = _co_run_record_cache.get(stage, (-1, {}))
    if stamp.st_mtime_ns != cached_stamp:
        try:
            with os.scandir(stage) as entries:
                paths = sorted(entry.path for entry in entries if entry.name.endswith(".run.json"))
        except OSError:
            return {}
    else:
        paths = list(cached)
    current: dict[str, tuple[int, int, dict]] = {}
    found: dict[str, dict] = {}
    for path in paths:
        st = _co_stat(path)
        if st is None:
            continue
        prior = cached.get(path)
        if prior is not None and prior[:2] == (st.st_mtime_ns, st.st_size):
            data = prior[2]
        else:
            try:
                with open(path, encoding="utf-8") as stream:
                    data = json.load(stream)
            except OSError:
                continue
            except ValueError:
                data = {}
            if (not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1
                    or not isinstance(data.get("native_id"), str) or not data["native_id"]
                    or not isinstance(data.get("harness"), str) or not data["harness"]):
                data = {}
        current[path] = (st.st_mtime_ns, st.st_size, data)
        if data:
            found[data["native_id"]] = data
    _co_run_record_cache[stage] = (stamp.st_mtime_ns, current)
    return found


def _co_stage_run_sessions(directory: str) -> set[str]:
    """OpenCode session ids of the ``opencode run`` workers logged under ``directory/.stage``.

    Each run is a top-level session in the database but a worker, not a lead; its ``--format json`` log names it
    in the first events."""
    stage = os.path.join(directory, ".stage")
    found = set(_co_load_run_records(stage))
    for path in glob.iglob(os.path.join(glob.escape(stage), "**", "*.log"), recursive=True):
        st = _co_stat(path)
        if st is None:
            continue
        key = (path, st.st_mtime, st.st_size)
        sid = _co_run_session_cache.get(key)
        if sid is None:
            try:
                with open(path, "rb") as fh:
                    match = _co_run_session_re.search(fh.read(4096).decode("utf-8", "replace"))
            except OSError:
                continue
            sid = match.group(1) if match else ""
            _co_run_session_cache[key] = sid
        if sid:
            found.add(sid)
    return found


def _co_service_leads(procs: dict[int, tuple[int, int, int, int, int]], argv_of: Callable[[int], list[str]],
                      taken_cwds: Iterable[str], clk: int, boot: float,
                      run_ids: Iterable[str] = ()) -> list[_co_Lead]:
    """Sessions hosted by an ``opencode serve`` daemon (started from a phone or the web UI), which have no terminal.

    Each recently updated top-level session is one background lead with a zero pid (never a real
    process, so the end-session action cannot signal the daemon) and its own session id, so several sessions in
    one folder each get a row. A session stays listed past the usual window while one of its subagent sessions is
    still running. Worker runs (under a .stage folder, or logged in a folder's .stage), subagents and archived
    sessions are left out. In a folder
    a terminal OpenCode covers, the newest sessions (one per terminal) are presumed to be the terminals' own, and
    any others show only while fresh, since someone switching sessions inside a terminal leaves old ones behind."""
    daemon = 0.0
    for pid in procs:
        argv = argv_of(pid)
        if argv and "opencode" in os.path.basename(argv[0]) and "serve" in argv[1:3]:
            daemon = max(daemon, boot + procs[pid][3] / clk)
    if not daemon:
        return []
    db = os.path.join(os.path.expanduser("~"), ".local/share/opencode/opencode.db")
    now = time.time()
    live_cutoff = (now - _co_child_live_s) * 1000
    running = ("EXISTS (SELECT 1 FROM session_v2 c WHERE c.parent_id = s.id AND c.time_archived IS NULL"
               " AND c.time_updated >= ? AND (c.time_idle IS NULL OR c.time_idle < c.time_updated))")
    seen = "MAX(s.time_updated, COALESCE(s.time_idle, 0))"   # time_updated stays at the first prompt while the session works; its idle stamp moves
    try:
        conn = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
        try:
            try:
                rows = conn.execute(
                    f"SELECT s.id, s.directory, s.time_created, {seen}, {running} FROM session_v2 s"
                    f" WHERE s.parent_id IS NULL AND s.time_archived IS NULL AND ({seen} >= ? OR {running})"
                    f" ORDER BY {seen} DESC",
                    (live_cutoff, (now - _co_service_window) * 1000, live_cutoff)).fetchall()
            except sqlite3.OperationalError:   # a schema without time_idle: no running-child test, the plain window only
                rows = conn.execute(
                    "SELECT id, directory, time_created, time_updated, 0 FROM session_v2"
                    " WHERE parent_id IS NULL AND time_archived IS NULL AND time_updated >= ? ORDER BY time_updated DESC",
                    ((now - _co_service_window) * 1000,)).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []
    terminals = Counter(os.path.normpath(cwd) for cwd in taken_cwds)
    skipped: Counter[str] = Counter()
    leads: list[_co_Lead] = []
    runs: dict[str, set[str]] = {}
    excluded = set(run_ids)
    for sid, directory, created, updated, has_running in rows:
        directory = os.path.normpath(str(directory or ""))
        if not directory or os.sep + ".stage" + os.sep in directory + os.sep:
            continue
        if directory not in runs:
            runs[directory] = _co_stage_run_sessions(directory)
        if sid in excluded or sid in runs[directory]:
            continue
        if terminals[directory]:
            if skipped[directory] < terminals[directory]:
                skipped[directory] += 1
                continue
            if not has_running and float(updated or 0.0) < (now - _co_terminal_peer_s) * 1000:
                continue
        since = float(created or 0.0) / 1000.0
        leads.append(_co_Lead(0, "opencode", "background", directory,
                              since, str(sid)))
    return leads


def _co_stage_pid_names(stage: str) -> dict[int, str]:
    """pid -> worker name for every ``<name>.pid`` file in ``stage``."""
    out: dict[int, str] = {}
    try:
        names = os.listdir(stage)
    except OSError:
        return out
    for filename in names:
        if filename.endswith(".pid"):
            pid = _co_digits(os.path.join(stage, filename))
            if pid:
                out[pid] = filename[:-4]
    return out


def _co_order_workers(workers: list[Worker]) -> list[Worker]:
    """Live first (oldest first), then finished (newest first), as a Snapshot lists them."""
    live = sorted((w for w in workers if w.state in LIVE_STATES), key=lambda w: w.start)
    done = sorted((w for w in workers if w.state not in LIVE_STATES), key=lambda w: w.end or w.start, reverse=True)
    return live + done


def _co_opencode_children(sid: str, now: float) -> list[Worker]:
    """The subagent sessions OpenCode keeps for session ``sid`` (child rows in ``session_v2``), as workers.

    Running means not idle (``time_idle`` unset or older than the last update) and updated within
    ``_co_child_live_s``; a finished one is done, failed, or no-report when it was interrupted."""
    if not sid:
        return []
    db = os.path.join(os.path.expanduser("~"), ".local/share/opencode/opencode.db")
    try:
        conn = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT id, title, agent, model, time_created, time_updated, time_idle, idle_outcome FROM session_v2"
                " WHERE parent_id = ? AND time_archived IS NULL ORDER BY time_created DESC LIMIT ?",
                (sid, _co_child_max)).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []
    out: list[Worker] = []
    used: set[str] = set()
    for cid, title, agent, model_json, created, updated, idle, outcome in reversed(rows):
        try:
            model = str((json.loads(model_json or "{}") or {}).get("id", ""))
        except (ValueError, AttributeError, TypeError):
            model = ""
        created_s = float(created or 0.0) / 1000.0
        updated_s = float(updated or 0.0) / 1000.0
        idle_s = float(idle or 0.0) / 1000.0
        running = (not idle or idle_s < updated_s) and now - updated_s <= _co_child_live_s
        if running:
            state = RUNNING
        elif outcome in ("failed", "error", "errored"):
            state = FAILED
        elif outcome == "succeeded":
            state = DONE
        else:
            state = NOREPORT
        base = _co_short_name(str(title or "")) or str(agent or "subagent")
        name = base if base not in used else f"{base} {str(cid)[-4:]}"
        used.add(name)
        out.append(Worker(
            name=name, state=state, title=str(title or ""), model=model, family=family_of(model),
            start=created_s, end=None if running else max(idle_s, updated_s, created_s),
            quiet_s=max(0.0, now - updated_s), session=sid, linkage="native",
        ))
    return out


def _co_headless_by_session(procs: dict[int, tuple[int, int, int, int, int]], home: str) -> dict[str, list[tuple[int, str, str]]]:
    """OpenCode session id -> (pid, cwd, harness) for the headless agent runs it started, wherever their cwd is.

    OpenCode puts ``OPENCODE_SESSION_ID`` in the environment of every command its bash tool runs, so a worker a
    session launched in /tmp is tied to that exact session, even when the shell that started it has exited and
    the run was adopted by init. A subagent's id is mapped to its top-level session by ``_co_opencode_root``."""
    out: dict[str, list[tuple[int, str, str]]] = {}
    for pid in procs:
        argv = _co_argv(pid)
        if not argv or not _co_headless_kind(argv, environ={}, home=home):
            continue
        environ = _co_environ(pid)
        sid = environ.get("OPENCODE_SESSION_ID", "")
        cwd = _co_cwd(pid)
        if not sid or not cwd or _co_lead_kind(argv, cwd, home, tty=procs[pid][4] != 0, environ=environ):
            continue
        harness = _co_headless_kind(argv, environ=environ, home=home)
        if harness:
            out.setdefault(sid, []).append((pid, cwd, harness))
    return out


def _co_opencode_roots(sids: Iterable[str]) -> dict[str, str]:
    """Session id -> the top-level session it belongs to (itself, or its parent's parent and so on), in one read."""
    wanted = set(sids)
    out = {sid: sid for sid in wanted}
    if not wanted:
        return out
    db = os.path.join(os.path.expanduser("~"), ".local/share/opencode/opencode.db")
    try:
        conn = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
        try:
            for sid in wanted:
                cur = sid
                for _ in range(6):
                    row = conn.execute("SELECT parent_id FROM session_v2 WHERE id = ?", (cur,)).fetchone()
                    if not row or not row[0]:
                        break
                    cur = str(row[0])
                out[sid] = cur
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        pass
    return out


def _co_project_root(stage: str) -> str:
    """The project dir a stage's workers run in: the stage's parent for a `.stage` dir, else the stage itself."""
    stage = os.path.normpath(stage)
    return os.path.dirname(stage) if os.path.basename(stage) in STAGE_NAMES else stage


def _co_owns_cwd(cwd: str, project: str) -> bool:
    """True when no deeper ``.stage`` sits between ``cwd`` and ``project``: the deepest project owns a worker.

    ``~/Projects/.stage`` is a stage for ``~/Projects`` itself, but a worker under ``~/Projects/foo/out`` belongs
    to ``~/Projects/foo``, whose own stage already lists it."""
    here = os.path.normpath(cwd)
    project = os.path.normpath(project)
    while here != project:
        if _co_is_stage_dir(os.path.join(here, STAGE_NAMES[0])):
            return False
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return True


def _co_station_of(cwd: str, stages: list[str]) -> str | None:
    """The stage whose project dir (the stage dir's parent) equals cwd or contains it; the deepest project wins,
    and of two stage dirs in one project the canonical ``.stage`` wins."""
    here = os.path.normpath(cwd)
    best: str | None = None
    best_key = (-1, False)
    for stage in stages:
        project = os.path.dirname(os.path.normpath(stage))
        inside = here == project or here.startswith(project.rstrip(os.sep) + os.sep)
        key = (len(project), os.path.basename(stage) in STAGE_NAMES)   # deepest project, then the canonical .stage
        if inside and key > best_key:
            best, best_key = stage, key
    return best


def _co_assign_leads(leads: list[_co_Lead], stages: list[str],
                     pinned: list[tuple[str, _co_Lead]]) -> dict[str, _co_Lead]:
    """One lead per station. A lead.json pid names its station; other leads go where their cwd is.
    A live lead beats a background one, then the newest process wins."""
    best: dict[str, _co_Lead] = {}

    def offer(stage: str, lead: _co_Lead) -> None:
        cur = best.get(stage)
        if cur is None or (lead.state == "live", lead.since) > (cur.state == "live", cur.since):
            best[stage] = lead

    for stage, lead in pinned:
        offer(stage, lead)
    for lead in leads:
        stage = _co_station_of(lead.cwd, stages)
        if stage is None:
            continue
        # a project with two stage dirs (.stage and .staging) has one lead: credit it to both
        project = os.path.dirname(stage)
        for sibling in stages:
            if os.path.dirname(sibling) == project:
                offer(sibling, lead)
    return best


def _co_git_root(cwd: str, home: str) -> str | None:
    """The nearest directory at or above cwd that holds a .git entry, never $HOME or anything above it."""
    here = os.path.normpath(cwd) if cwd else ""
    home = os.path.normpath(home)
    while here and here != os.sep and here != home and not home.startswith(here + os.sep):
        if os.path.lexists(os.path.join(here, ".git")):
            return here
        here = os.path.dirname(here)
    return None


def _co_plain_root(cwd: str, home: str) -> str | None:
    """A session in a folder with no git project above it is its own project: its cwd, $HOME itself included
    (labelled "~"); a folder above $HOME has no project."""
    here = os.path.normpath(cwd) if cwd else ""
    home = os.path.normpath(home)
    if not here or here == os.sep or (here != home and home.startswith(here + os.sep)):
        return None
    return here


def _co_cwd_project_root(cwd: str, home: str) -> str | None:
    """The project directory a worker cwd belongs to (git root, else the stage's parent)."""
    here = os.path.normpath(cwd) if cwd else ""
    if not here:
        return None
    root = _co_git_root(here, home)
    if root is not None:
        return root
    walk = here
    while walk and walk != os.sep:
        if os.path.basename(walk) in STAGE_NAMES:
            return os.path.dirname(walk)
        parent = os.path.dirname(walk)
        if parent == walk:
            break
        walk = parent
    return _co_plain_root(here, home)


def _co_unattached_root(cwd: str, home: str) -> str:
    """Bucket id for a headless run with no known lead: one global bucket, else one per project root."""
    root = _co_cwd_project_root(cwd, home)
    if root is not None:
        return f"unattached:{os.path.normpath(root)}"
    return "unattached:global"


def _co_unattached_label(root_id: str, home: str) -> str:
    if root_id == "unattached:global":
        return "unattached"
    project = root_id.split(":", 1)[-1]
    project = os.path.normpath(project)
    home = os.path.normpath(home)
    if project == home:
        return "~"
    return os.path.basename(project) or project


def _co_is_session_split_row(path: str) -> bool:
    """A stage's extra row for another session (`path#<sid>` or `#unlinked`), not a virtual `.stage#<pid>` row."""
    if "#" not in path:
        return False
    base, frag = path.rsplit("#", 1)
    if frag == "unlinked":
        return True
    if frag.isdigit() or frag.startswith("oc:"):
        return False
    return os.path.basename(base) in STAGE_NAMES


def _co_merge_stage_fields(dst: Stage, src: Stage) -> None:
    """Fill empty session metadata on ``dst`` from a duplicate row we are folding away."""
    if not dst.session_name and src.session_name:
        dst.session_name = src.session_name
    if not dst.parent and src.parent:
        dst.parent = src.parent
    if not dst.root_session and src.root_session:
        dst.root_session = src.root_session
    if not dst.launch_depth and src.launch_depth:
        dst.launch_depth = src.launch_depth
    if not dst.app and src.app:
        dst.app, dst.app_id = src.app, src.app_id
    if not dst.tmux and src.tmux:
        dst.tmux = src.tmux
    if not dst.tag and src.tag:
        dst.tag = src.tag
    if not dst.lead_model and src.lead_model:
        dst.lead_model = src.lead_model
    if not dst.effort and src.effort:
        dst.effort = src.effort
    if not dst.goal and src.goal:
        dst.goal = src.goal
    if not dst.brief_text and src.brief_text:
        dst.brief_text = list(src.brief_text)
    if not dst.cwd and src.cwd:
        dst.cwd = src.cwd
    if dst.lead_state != "live" and src.lead_state == "live":
        dst.lead_state = src.lead_state
        dst.lead_pid = src.lead_pid or dst.lead_pid
        dst.lead_since = src.lead_since or dst.lead_since
        dst.lead_kind = src.lead_kind or dst.lead_kind
        dst.lead_ctx = src.lead_ctx if src.lead_ctx is not None else dst.lead_ctx
        dst.solo = src.solo


def _co_virtual_path(root: str, pid: int) -> str:
    """The key of a session with no stage dir: one row per session, so the lead's pid is part of it."""
    return f"{root}/.stage#{pid}"


def _co_orphan_leads(leads: list[_co_Lead], assigned: dict[str, _co_Lead],
                     home: str) -> dict[str, tuple[str, _co_Lead]]:
    """Virtual station path -> (project root, lead) for every lead no real station took, so each live session
    gets its own row: one station keeps one lead and the others become stations of their own, including a lead
    that lost the one-lead contest inside a project that does have a stage dir. A lead with no git project
    above its cwd uses its cwd as the project; one sitting above $HOME is skipped."""
    taken = {(lead.pid, lead.sid) for lead in assigned.values()}
    found: dict[str, tuple[str, _co_Lead]] = {}
    for lead in leads:
        if (lead.pid, lead.sid) in taken:
            continue
        root = _co_git_root(lead.cwd, home)
        if root is None:
            root = _co_plain_root(lead.cwd, home)
        if root is not None:
            found[f"{root}/.stage#oc:{lead.sid}" if lead.sid and not lead.pid else _co_virtual_path(root, lead.pid)] = (root, lead)
    return found


def _co_virtual_labels(orphans: dict[str, tuple[str, _co_Lead]], taken: set[str], home: str = "") -> dict[str, str]:
    """A unique label per virtual station: the project folder name, then " · <harness>", then " #2", " #3"."""
    labels: dict[str, str] = {}
    for path, (root, lead) in sorted(orphans.items(), key=lambda item: item[1][1].pid):
        label = "~" if os.path.normpath(root) == os.path.normpath(home) else os.path.basename(root) or root
        if label in taken:
            label = f"{label} · {lead.kind}"
        base, n = label, 2
        while label in taken:
            label = f"{base} #{n}"
            n += 1
        taken.add(label)
        labels[path] = label
    return labels


def _co_lead_json_pid(stage: str) -> int | None:
    try:
        data = json.loads(_co_read_text(os.path.join(stage, "lead.json")))
    except ValueError:
        return None
    pid = data.get("pid") if isinstance(data, dict) else None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def load_dismissed(path: str) -> dict[str, float]:
    """Stage path -> epoch it was dismissed at. A missing or unreadable file dismisses nothing."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: float(value) for key, value in data.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)}


def dismiss_stage(path: str, stage: str, now: float) -> None:
    """Record that a station was dismissed at now. Written to a temp file, then renamed over the old one."""
    data = load_dismissed(path)
    data[stage] = now
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_overrides(path: str) -> dict[str, dict[str, object]]:
    """Stage path -> {"state": "idle" | "active", "set": epoch}. A missing or unreadable file overrides nothing;
    entries that are not a valid state and time are skipped."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    overrides: dict[str, dict[str, object]] = {}
    for stage, entry in data.items():
        if not isinstance(entry, dict):
            continue
        state, when = entry.get("state"), entry.get("set")
        if state in ("idle", "active") and isinstance(when, (int, float)) and not isinstance(when, bool):
            overrides[stage] = {"state": state, "set": float(when)}
    return overrides


def set_override(path: str, stage: str, state: str | None, now: float) -> None:
    """Pin a station to idle or active at now; None removes its pin. Written to a temp file, then renamed over the old one."""
    data: dict[str, object] = dict(load_overrides(path))
    if state is None:
        data.pop(stage, None)
    else:
        data[stage] = {"state": state, "set": now}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _co_valid_hm(hour: int, minute: int) -> bool:
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _co_local_ts(mtime: float, hour: int, minute: int) -> float | None:
    try:
        lt = time.localtime(mtime)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))
    except (OverflowError, ValueError, OSError):
        return None


def _co_milestones(path: str) -> list[Milestone]:
    st = _co_stat(path)
    if st is None:
        return []
    mtime = st.st_mtime
    out: list[Milestone] = []
    for line in _co_read_lines(path):
        if not line.strip():
            continue
        match = _co_ms_re.match(line)
        hour = int(match.group(1)) if match else -1
        minute = int(match.group(2)) if match else -1
        if match and _co_valid_hm(hour, minute):
            ts = _co_local_ts(mtime, hour, minute)
            if ts is not None:
                while ts - mtime > _co_milestone_rollback:
                    ts -= 86400
                out.append(Milestone(ts=ts, text=match.group(3).strip()))
                continue
        out.append(Milestone(ts=None, text=line))
    return out


def _co_group_label(members: list[Worker]) -> str:
    """Distinct jobs joined with "+" in start order; without jobs, the common name prefix; else "workers"."""
    jobs: list[str] = []
    for wk in members:
        if wk.job and wk.job not in jobs:
            jobs.append(wk.job)
    if jobs:
        return "+".join(jobs)
    prefix = os.path.commonprefix([wk.name for wk in members]).rstrip("-_")
    return prefix or "workers"


def _co_group_of(members: list[Worker]) -> Group:
    members = sorted(members, key=lambda wk: wk.start)
    done = sum(wk.state in (DONE, FAILED, NOREPORT) for wk in members)
    finished = done == len(members)
    ends = [wk.end for wk in members if wk.end is not None]
    return Group(
        label=_co_group_label(members),
        workers=[wk.name for wk in members],
        started=members[0].start,
        done=done,
        total=len(members),
        live=sum(wk.state in LIVE_STATES for wk in members),
        finished=finished,
        finished_at=max(ends) if finished and ends else None,
    )


def _co_groups(workers: list[Worker], milestones: list[Milestone]) -> list[Group]:
    """Workers grouped by the PROGRESS.md dispatch line that launched them (nearest start wins when a name was
    launched twice); the rest, in 60 s windows of their starts. Newest group first; the current one is flagged."""
    dispatches: list[tuple[float, list[str]]] = []
    for stone in milestones:
        match = _co_dispatch_re.match(stone.text) if stone.ts is not None else None
        if match:
            dispatches.append((stone.ts, match.group(1).split()))
    buckets: dict[int, list[Worker]] = {}
    loose: list[Worker] = []
    for wk in workers:
        best: int | None = None
        for k, (ts, names) in enumerate(dispatches):
            if wk.name in names and (best is None or abs(ts - wk.start) < abs(dispatches[best][0] - wk.start)):
                best = k
        if best is None:
            loose.append(wk)
        else:
            buckets.setdefault(best, []).append(wk)
    windows: list[list[Worker]] = []
    for wk in sorted(loose, key=lambda item: item.start):
        if windows and wk.start - min(m.start for m in windows[-1]) <= _co_group_gap_s:
            windows[-1].append(wk)
        else:
            windows.append([wk])
    clusters = list(buckets.values()) + windows
    groups = sorted((_co_group_of(members) for members in clusters), key=lambda g: g.started, reverse=True)
    if groups:
        active = [g for g in groups if g.live]
        for group in active or groups[:1]:
            group.current = True
    return groups


def _co_quiet(now: float, log: LogState, log_path: str | None, start: float) -> float:
    if log_path is not None:
        newest = log.last_ts
        st = _co_stat(log_path)
        if st is not None and st.st_mtime > newest:
            newest = st.st_mtime
        return now - newest
    return now - start


def _co_state(
    live: bool,
    log: LogState,
    is_review: bool,
    has_report: bool,
    quiet_s: float,
    cpu: float | None,
) -> str:
    if live:
        # claude -p and cursor-agent print nothing until they finish: an empty log is not silence
        if log.offset == 0 and not log.steps:
            return RUNNING
        if quiet_s >= STALL_S and cpu is not None and cpu < STALL_CPU:
            return STALLED
        if quiet_s >= QUIET_S:
            return QUIET
        return RUNNING
    if has_report:
        return DONE
    if is_review and log.steps:
        return DONE
    last = log.steps[-1] if log.steps else None
    if last is not None and last.kind == "error":
        return FAILED
    return NOREPORT


def _co_account_for(family: str, label: str) -> str | None:
    low = label.lower()
    if family == "claude":
        return "claude-work" if "work" in low else "claude"
    if family == "codex":
        return "codex"
    if family == "deepseek":
        return "opencode-go"
    if family in ("grok", "cursor"):
        return "cursor-models"
    return None


def _co_current_boot_id() -> str:
    return _pr_boot_id()


def _co_absent(value: object) -> bool:
    """A merge field is absent when it is None, an empty string, or 'unknown'; 0 and False are real values."""
    return value is None or (isinstance(value, str) and value in ("", "unknown"))


# ExecutionRecord uses 0.0 as the unknown sentinel for these timestamps, and False for output_available.
_CO_ZERO_ABSENT_FIELDS = frozenset({"started", "activity", "ended"})


def _co_merge_absent(field_name: str, value: object) -> bool:
    """Field-aware absence for merge: 0.0 is unknown for timestamp fields, False for output_available."""
    if (field_name in _CO_ZERO_ABSENT_FIELDS and isinstance(value, (int, float))
            and not isinstance(value, bool) and value == 0):
        return True
    if field_name == "output_available":
        return value is None or value is False
    return _co_absent(value)


def _co_proc_identity_matches(rec: ExecutionRecord, procs: dict[int, tuple[int, int, int, int, int]],
                              boot_id: str | None = None) -> bool:
    """``boot_id`` is the current kernel boot id; callers that check many records pass it read once."""
    if rec.pid is None or rec.pid_start is None:
        return False
    info = procs.get(rec.pid)
    if info is None or info[3] != rec.pid_start:
        return False
    if rec.boot_id:
        current = _co_current_boot_id() if boot_id is None else boot_id
        if current and rec.boot_id != current:
            return False
    return True


def reconcile_executions(
    registry: RegistrySnapshot,
    native: list[ExecutionEvidence],
    procs: dict[int, tuple[int, int, int, int, int]],
    now: float,
) -> tuple[list[ExecutionEvidence], list[str]]:
    """Merge registry, native and exact process identity evidence without inferring ownership from cwd or time."""
    merged: dict[str, ExecutionEvidence] = {}
    namespaced: dict[tuple[str, str, str], str] = {}
    aliases: dict[str, str] = {}
    diagnostics = list(registry.diagnostics)
    boot_id = _co_current_boot_id()

    def add(item: ExecutionEvidence, registered: bool) -> None:
        rec = replace(item.record)
        key = rec.execution_id or execution_key(rec.harness, rec.account, rec.native_id)
        ns = (rec.harness, rec.account, rec.native_id) if rec.native_id else None
        target = key if key in merged else namespaced.get(ns, "") if ns else ""
        if target and target in merged:
            old = merged[target]
            current = old.record
            if registered and current.source != "registry":
                base, other = rec, current
            else:
                base, other = current, rec
            # Registry values are authoritative. Native evidence fills only absent values and supplies artifacts.
            for field_name in ("execution_id", "harness", "account", "native_id", "role", "parent_id", "root_id",
                               "linkage", "cwd", "title", "goal", "purpose_source", "brief_ref", "result_ref",
                               "pid", "pid_start", "boot_id", "started", "activity", "ended", "state", "outcome",
                               "exit_code", "model", "effort", "current_action", "output_available"):
                if field_name == "root_id" and base.parent_id and base.parent_id != other.parent_id:
                    continue  # a registered parent takes precedence over native prompt inference and its root
                if _co_merge_absent(field_name, getattr(base, field_name)) and not _co_merge_absent(field_name, getattr(other, field_name)):
                    setattr(base, field_name, getattr(other, field_name))
            merged[target] = ExecutionEvidence(base, old.event if old.event != "observe" else item.event,
                                               old.brief or item.brief, old.result or item.result)
            if rec.execution_id and rec.execution_id != target:
                aliases[rec.execution_id] = target
            if ns:
                namespaced[ns] = target
            return
        merged[key] = item if rec is item.record else ExecutionEvidence(rec, item.event, item.brief, item.result)
        if ns:
            namespaced[ns] = key

    for rec in registry.records:
        add(ExecutionEvidence(rec), True)
    for item in native:
        add(item, False)

    # A PID is corroborating evidence only with the same kernel start tick; a reused PID stays historical.
    for item in merged.values():
        rec = item.record
        if _co_proc_identity_matches(rec, procs, boot_id):
            continue
        if rec.state == "running":
            rec.state = "finished"
            if rec.outcome in ("", "unknown"):
                rec.outcome = "unknown"
            rec.ended = rec.ended or (rec.activity or rec.started or now)

    # When no adapter supplied ancestry, corroborate it only through exact live process identities.
    by_pid = {item.record.pid: item.record for item in merged.values()
              if item.record.pid is not None and item.record.pid_start is not None
              and _co_proc_identity_matches(item.record, procs, boot_id)}
    for item in merged.values():
        rec = item.record
        info = procs.get(rec.pid) if rec.pid is not None else None
        if rec.parent_id or rec.linkage != "unknown" or info is None or not _co_proc_identity_matches(rec, procs, boot_id):
            continue
        parent_pid = info[0]
        for _ in range(64):
            ancestor = by_pid.get(parent_pid)
            if ancestor is not None and ancestor.execution_id != rec.execution_id:
                rec.parent_id = ancestor.execution_id
                rec.root_id = ancestor.root_id or ancestor.execution_id
                rec.linkage = "process"
                break
            parent_info = procs.get(parent_pid)
            if parent_info is None or parent_info[0] == parent_pid:
                break
            parent_pid = parent_info[0]

    # Resolve missing roots through immediate parents. Broken/cyclic chains remain visible with no owner.
    def canonical(value: str) -> str:
        seen: set[str] = set()
        while value in aliases and value not in seen:
            seen.add(value)
            value = aliases[value]
        return value

    for item in merged.values():
        item.record.parent_id = canonical(item.record.parent_id)
        item.record.root_id = canonical(item.record.root_id)
    by_id = {item.record.execution_id: item.record for item in merged.values()}
    for rec in by_id.values():
        seen = {rec.execution_id}
        parent = rec.parent_id
        cycle = False
        derived_root = ""
        while parent:
            if parent in seen:
                cycle = True
                break
            seen.add(parent)
            ancestor = by_id.get(parent)
            if ancestor is None:
                break
            if not ancestor.parent_id:
                derived_root = ancestor.execution_id
                break
            parent = ancestor.parent_id
        else:
            derived_root = ""
        if cycle:
            diagnostics.append(f"execution ancestry cycle: {rec.execution_id[:12]}")
            rec.parent_id = ""
            rec.root_id = ""
            rec.linkage = "conflict"
            continue
        if rec.root_id and rec.root_id in by_id and rec.parent_id:
            if rec.parent_id not in by_id:
                diagnostics.append(f"execution parent missing: {rec.execution_id[:12]}")
                rec.parent_id = ""
                rec.root_id = ""
                rec.linkage = "unknown"
                continue
            cur = by_id[rec.parent_id]
            while cur.parent_id and cur.parent_id in by_id:
                cur = by_id[cur.parent_id]
            if cur.execution_id != rec.root_id:
                diagnostics.append(f"execution root conflicts with ancestry: {rec.execution_id[:12]}")
                rec.parent_id = ""
                rec.root_id = ""
                rec.linkage = "conflict"
                continue
        elif not rec.root_id and rec.parent_id:
            rec.root_id = derived_root or rec.parent_id
    return list(merged.values()), diagnostics


def _co_execution_worker(item: ExecutionEvidence, now: float, live: bool) -> Worker:
    rec = item.record
    if live:
        state = RUNNING
        end = None
    else:
        state = DONE if rec.outcome == "success" else FAILED if rec.outcome in ("failed", "error") else NOREPORT
        end = rec.ended or rec.activity or rec.started
    model = rec.model
    brief_text = item.brief.splitlines()
    result_text = item.result.splitlines()
    # The record's own counters ride on the worker's log, like every other harness's.
    log = LogState()
    _co_metrics_fill(log, {"tokens_in": rec.tokens_in, "tokens_out": rec.tokens_out,
                           "tokens_cache": rec.tokens_cache, "cost": rec.cost,
                           "ctx_tokens": rec.context_tokens}, "record")
    return Worker(
        name=rec.title or rec.native_id or rec.execution_id[:12], state=state, title=rec.title,
        goal=rec.goal, model=model, family=family_of(model), pid=rec.pid, start=rec.started,
        end=end, quiet_s=max(0.0, now - rec.activity) if rec.activity else 0.0,
        execution_id=rec.execution_id, account=rec.account, native_id=rec.native_id,
        parent_id=rec.parent_id, root_id=rec.root_id, linkage=rec.linkage, outcome=rec.outcome,
        brief_text=brief_text, result_text=result_text, purpose_source=rec.purpose_source,
        output_available=rec.output_available or bool(item.result), log=log,
    )


_co_dup_window_s = 5.0   # two workers of one stage starting and ending this close may be one native run


def _co_same_run_worker(a: Worker, b: Worker, records: Iterable[dict] = ()) -> bool:
    """Whether two workers of one session row are the same native run.

    Two native session ids settle it. Otherwise the two must carry the same name and start and end
    within a few seconds, and one more identity must agree: a pid, an execution id, a native id, or
    one dispatch run record that launched both. Times and a shared harness never merge two workers.
    """
    if a is b:
        return False
    if a.native_id and b.native_id:
        return a.native_id == b.native_id
    if not a.start or not b.start or abs(a.start - b.start) > _co_dup_window_s:
        return False
    if a.end is not None and b.end is not None and abs(a.end - b.end) > _co_dup_window_s:
        return False
    unnamed = (a.native_id and a.name == a.native_id) or (b.native_id and b.name == b.native_id)   # a record row is named by its session id
    if a.name != b.name and not unnamed:
        return False
    return _co_run_identity_matches(a, b, records)


def _co_run_identity_matches(a: Worker, b: Worker, records: Iterable[dict] = ()) -> bool:
    """Corroborating identity for two same-named rows that started together.

    A pid, execution id or native id they share, or one dispatch run record that names both rows.
    Two different known identities of the same kind are a conflict, never a match.
    """
    if a.native_id and b.native_id and a.native_id != b.native_id:
        return False
    if a.pid and b.pid and a.pid != b.pid:
        return False
    if a.execution_id and b.execution_id and a.execution_id != b.execution_id:
        return False
    if a.pid and a.pid == b.pid:
        return True
    if a.execution_id and a.execution_id == b.execution_id:
        return True
    if a.native_id and a.native_id == b.native_id:
        return True
    return _co_shared_run_record(a, b, records)


def _co_run_record_owns(record: dict, worker: Worker) -> bool:
    """Whether one dispatch run record launched ``worker``: its native id names the row, or its pid and name do."""
    ident = record.get("native_id") or ""
    if ident and worker.native_id and worker.native_id == ident:
        return True
    pid = record.get("pid")
    return bool(pid) and worker.pid == pid and worker.name == record.get("name")


def _co_shared_run_record(a: Worker, b: Worker, records: Iterable[dict]) -> bool:
    """Whether one actual dispatch run record launched both rows, compared by that record, not by its linkage tag."""
    return any(_co_run_record_owns(record, a) and _co_run_record_owns(record, b) for record in records)


def _co_stage_run_records(stage_dir: str) -> list[dict]:
    """Every dispatch run record of a stage, with its native id: name, pid and native id identify one launch."""
    if not stage_dir:
        return []
    out: list[dict] = []
    for directory in _co_run_record_dirs(stage_dir):
        for ident, data in _co_load_run_records(directory).items():
            out.append({**data, "native_id": ident})
        out.extend({**data, "native_id": ""} for data in _co_unlinked_run_records(directory))
    return out


def _co_fill_identity(target: Worker, source: Worker, field_name: str) -> None:
    """Copy one identity field when the target has none; for linkage, "unknown" is no answer either."""
    value = getattr(source, field_name)
    current = getattr(target, field_name)
    if value and (not current or (field_name == "linkage" and current == "unknown")):
        setattr(target, field_name, value)


def _co_fold_worker_into(base: Worker, other: Worker) -> None:
    """Fill the base worker's empty identity and artifact fields from the duplicate, keeping its own log and times."""
    for field_name in ("goal", "title", "purpose_source", "execution_id", "account", "native_id", "parent_id",
                       "root_id", "outcome", "session", "parent_session", "root_session", "model",
                       "effort", "job", "level_tag", "failure"):
        if not getattr(base, field_name) and getattr(other, field_name):
            setattr(base, field_name, getattr(other, field_name))
    if base.linkage in ("", "unknown") and other.linkage not in ("", "unknown"):
        base.linkage = other.linkage      # a native child keeps saying so through a fold
    if not base.brief_text and other.brief_text:
        base.brief_text = list(other.brief_text)
    if not base.result_text and other.result_text:
        base.result_text = list(other.result_text)
    if not base.flags and other.flags:
        base.flags = other.flags
    base.output_available = base.output_available or other.output_available
    if base.family == "other" and other.family != "other":
        base.family = other.family
    if base.state not in LIVE_STATES and (base.end is None or (other.end is not None and other.end > base.end)):
        base.end = other.end
    if not base.log.path and other.log.path:
        base.log.path = other.log.path
    for field_name in ("tokens_in", "tokens_out", "tokens_cache", "cost", "ctx_tokens", "ctx_window"):
        value = getattr(other.log, field_name)
        if value is None:
            continue
        if (getattr(base.log, field_name) is None
                or _co_metric_rank_of(other.log, field_name) > _co_metric_rank_of(base.log, field_name)):
            setattr(base.log, field_name, value)
            _co_metric_keep_rank(base.log, other.log, field_name)


def _co_duplicate_of(kept: list[Worker], worker: Worker, records: Iterable[dict] = ()) -> Worker | None:
    """The already-kept worker that is the same run as ``worker``: the same native id first, then the same run."""
    for candidate in kept:
        if candidate.native_id and worker.native_id and candidate.native_id == worker.native_id:
            return candidate
    return next((candidate for candidate in kept if _co_same_run_worker(candidate, worker, records)), None)


def _co_fold_duplicate_workers(workers: list[Worker], records: Iterable[dict] = ()) -> list[Worker]:
    """One worker per native run: the DB row (log, steps) and the execution-registry row (goal, brief, ids) of the
    same run fold into one, keeping the richer log and the registry's identity."""
    out: list[Worker] = []
    for worker in workers:
        target = _co_duplicate_of(out, worker, records)
        if target is None:
            out.append(worker)
            continue
        if len(worker.log.steps) > len(target.log.steps):   # the DB row's steps are the ones to keep
            index = next(index for index, kept in enumerate(out) if kept is target)
            out[index] = worker                              # ... and it must be the entry that stays in `out`
            target, worker = worker, target
        _co_fold_worker_into(target, worker)
    return out


# -- harness metrics: native source > registry record > log estimate ------------------------------

_co_metric_names = ("tokens_in", "tokens_out", "tokens_cache", "cost", "ctx_tokens", "ctx_window")
_co_metric_rank = {"log": 0, "record": 1, "native": 2}   # a log estimate, a registry record, the harness's own store
_co_metric_sources: dict[int, tuple[weakref.ref, dict[str, int]]] = {}   # id(log) -> (log, field -> source rank)
_co_metric_sources_max = 4096


def _co_metric_ranks(log: LogState) -> dict[str, int]:
    """The per-field source ranks already written into this log; a fresh (or reused) log starts with none."""
    entry = _co_metric_sources.get(id(log))
    if entry is not None and entry[0]() is log:
        return entry[1]
    if len(_co_metric_sources) >= _co_metric_sources_max:
        for key in [key for key, (owner, _) in _co_metric_sources.items() if owner() is None]:
            del _co_metric_sources[key]
        while len(_co_metric_sources) >= _co_metric_sources_max:   # every owner still alive: forget the oldest
            del _co_metric_sources[next(iter(_co_metric_sources))]
    ranks: dict[str, int] = {}
    _co_metric_sources[id(log)] = (weakref.ref(log), ranks)
    return ranks


def _co_metric_rank_of(log: LogState, name: str) -> int:
    """The priority of the source that wrote one field; -1 for a value no favoured source has written."""
    return _co_metric_ranks(log).get(name, -1)


def _co_metric_keep_rank(target: LogState, source: LogState, name: str) -> None:
    """Carry a folded metric's source rank along, so a later lower-priority fill cannot overwrite it."""
    rank = _co_metric_ranks(source).get(name)
    if rank is not None:
        _co_metric_ranks(target)[name] = rank


def _co_metrics_fill(log: LogState, metrics: dict | None, source: str = "log") -> None:
    """Write a source's non-null metric fields into a log; only a higher or equal priority source replaces a value."""
    if not metrics:
        return
    rank = _co_metric_rank[source]
    ranks = _co_metric_ranks(log)
    for name in _co_metric_names:
        value = metrics.get(name)
        if value is None or ranks.get(name, -1) > rank:
            continue
        setattr(log, name, value)
        ranks[name] = rank


def _co_metrics_harness(worker: Worker) -> str:
    """The harness a worker's own numbers can come from."""
    found = worker.log.harness
    if found in ("codex", "claude", "cursor", "opencode"):
        return found
    if worker.family in ("codex", "claude", "cursor"):
        return worker.family
    return "opencode" if worker.native_id.startswith("ses_") else ""


def _co_metrics_opencode_index(sessions: dict[str, dict]) -> dict[tuple[str, str], dict]:
    """(parent session, short title) -> row, kept only when exactly one row carries that pair."""
    out: dict[tuple[str, str], dict | None] = {}
    for row in sessions.values():
        parent = str(row.get("parent_id") or "")
        if not parent:
            continue
        key = (parent, _co_short_name(str(row.get("title") or "")))
        out[key] = row if key not in out else None
    return {key: row for key, row in out.items() if row is not None}


def _co_cc_codex_session(path: str) -> CodexSession | None:
    """The Codex session at ``path``, rerun through the reader only when the rollout's mtime has moved."""
    st = _co_stat(path)
    if st is None:
        return None
    cached = _co_cc_codex_cache.get(path)
    if cached is not None and cached[0] == st.st_mtime:
        return cached[1]
    found = _co_guard(read_codex_session, path)
    session = found if isinstance(found, CodexSession) else None
    _co_cc_codex_cache[path] = (st.st_mtime, session)
    while len(_co_cc_codex_cache) > _co_cc_codex_cache_max:
        del _co_cc_codex_cache[next(iter(_co_cc_codex_cache))]
    return session


def _co_cc_needs_name(value: str) -> bool:
    """A row name that is missing, codex's injected header, or only a bare UUID."""
    return not value or value.lstrip().startswith(_co_cc_injected_names) or bool(_co_cc_uuid_re.match(value))


def _co_cc_first_prompt(session: CodexSession) -> str:
    return session.prompt[0].strip() if session.prompt else ""


def _co_cc_codex_stage(stage: Stage, path: str) -> None:
    """Fill a Codex session row's control center fields from its rollout at ``path``."""
    session = _co_cc_codex_session(path)
    if session is None:
        return
    stage.harness = "codex"
    stage.prompt = list(session.prompt)
    stage.system_prompt = list(session.system_prompt)
    stage.calls = list(session.calls)
    stage.transcript_path = path
    if not stage.resume_cmd:
        stage.resume_cmd = session.resume_cmd or (f"codex resume {stage.native_id}" if stage.native_id else "")
    if session.name and _co_cc_needs_name(stage.session_name):
        stage.session_name = session.name
    first = _co_cc_first_prompt(session)
    if first and (not stage.goal or stage.goal.startswith(_co_cc_injected_goal)):
        stage.goal = first


def _co_cc_rollout_path(worker: Worker) -> str:
    """A worker's own Codex rollout when its log is one: harness codex, a .jsonl file whose first line is a rollout."""
    path = worker.log.path
    if worker.log.harness != "codex" or not path:
        return ""
    if not (path.endswith(".jsonl") or os.path.realpath(path).endswith(".jsonl")):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline(1 << 20)
    except (OSError, ValueError):
        return ""
    try:
        obj = json.loads(first)
    except ValueError:
        return ""
    return path if isinstance(obj, dict) and obj.get("type") == "session_meta" else ""


def _co_cc_codex_worker(worker: Worker) -> None:
    """Fill a Codex worker's control center fields from the rollout its log points at."""
    path = _co_cc_rollout_path(worker)
    if not path:
        return
    session = _co_cc_codex_session(path)
    if session is None:
        return
    worker.harness = "codex"
    worker.prompt = list(session.prompt)
    worker.system_prompt = list(session.system_prompt)
    worker.calls = list(session.calls)
    if not worker.resume_cmd:
        worker.resume_cmd = session.resume_cmd or (f"codex resume {worker.native_id}" if worker.native_id else "")
    if session.name and _co_cc_needs_name(worker.name):
        worker.name = session.name
    if session.name and _co_cc_needs_name(worker.title):
        worker.title = session.name
    first = _co_cc_first_prompt(session)
    if first and (not worker.goal or worker.goal.startswith(_co_cc_injected_goal)):
        worker.goal = first


def _co_cc_fill_harness(stages: list[Stage]) -> None:
    """Every row's harness tag, plus the resume command and transcript the collector already knows."""
    for stage in stages:
        if not stage.harness:
            stage.harness = _co_cc_tag_harness.get(stage.tag, "")
        if stage.harness == "claude":
            sid = stage.session_id or stage.native_id
            if sid and not stage.resume_cmd:
                stage.resume_cmd = f"claude --resume {sid}"
            if not stage.transcript_path:
                stage.transcript_path = _co_metrics_lead_path(stage.lead_pid, stage.lead_kind)
        elif stage.harness == "codex" and not stage.resume_cmd and stage.native_id:
            stage.resume_cmd = f"codex resume {stage.native_id}"


def _co_metrics_workers(workers: list[Worker], now: float) -> None:
    """Fill workers' missing tokens, cost and context from the harness's own store (cached, never guessed)."""
    home = os.path.expanduser("~")
    sessions: dict[str, dict] | None = None
    children: dict[tuple[str, str], dict] | None = None
    for worker in workers:
        try:
            _co_cc_codex_worker(worker)
            if not worker.harness and worker.log.harness not in ("", "unknown"):
                worker.harness = worker.log.harness
            harness = _co_metrics_harness(worker)
            metrics = native_metrics(harness, worker.native_id)
            if metrics is None and harness == "codex":
                metrics = native_metrics("t3", worker.native_id)   # T3 drives codex: the scan tags those records t3
            if metrics is None and (worker.native_id.startswith("ses_") or (
                    worker.session.startswith("ses_") and not worker.native_id)):
                if sessions is None:
                    sessions = read_opencode_sessions(home)
                    children = _co_metrics_opencode_index(sessions)
                metrics = sessions.get(worker.native_id) or (
                    children.get((worker.session, worker.name)) if worker.session and not worker.native_id else None)
            _co_metrics_fill(worker.log, metrics, "native")
        except Exception:   # noqa: BLE001, S110 - metrics never take a row down
            pass


def _co_metrics_lead_path(pid: int | None, kind: str) -> str:
    """The transcript of an interactive Claude lead, found the same way as Stage.lead_ctx."""
    if not pid or not kind.startswith("claude"):
        return ""
    base = _co_environ(pid).get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        info = json.loads(_co_read_text(os.path.join(base, "sessions", f"{pid}.json")))
        session, cwd = info["sessionId"], info["cwd"]
        if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session):
            return ""
        return os.path.join(base, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{session}.jsonl")
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _co_metered_lead(stage: Stage, transcript: str = "") -> bool:
    """Whether a session row's own lead bills per token, from the same route evidence a worker row uses."""
    probe = Worker(name="lead", state=DONE, model=stage.lead_model, pid=stage.lead_pid,
                   log=LogState(path=transcript, harness=stage.lead_kind.split()[0] if stage.lead_kind else "",
                                model=stage.lead_model))
    return _co_metered(probe)


def _co_metrics_fill_lead(stage: Stage, metrics: dict | None) -> None:
    """Copy whatever a lead's own transcript recorded into the session row's metric columns."""
    if not metrics:
        return
    if stage.lead_tokens is None and metrics.get("tokens_total") is not None:
        stage.lead_tokens = metrics["tokens_total"]
    if stage.lead_cost is None and metrics.get("cost") is not None:
        stage.lead_cost = metrics["cost"]
    if stage.lead_ctx_window is None and metrics.get("ctx_window") is not None:
        stage.lead_ctx_window = metrics["ctx_window"]
    if stage.lead_ctx is None and metrics.get("ctx_tokens") is not None:
        stage.lead_ctx = metrics["ctx_tokens"]
    if stage.lead_tools is None and metrics.get("tools") is not None:
        stage.lead_tools = metrics["tools"]
    pulses = metrics.get("tool_pulses")
    if not stage.lead_tool_pulses and pulses:
        stage.lead_tool_pulses = list(pulses)


def _co_metrics_lead(stage: Stage, now: float, home: str = "",
                     sessions: dict[str, dict] | None = None, tools: dict[str, tuple[int, list]] | None = None) -> None:
    """Fill one session row's own metric columns from its harness transcript. Cost is kept only on a metered route."""
    kind = stage.lead_kind
    if not kind or kind.startswith("openclaw"):
        return
    home = home or os.path.expanduser("~")
    metrics: dict | None = None
    path = ""
    sid = stage.session_id or stage.native_id
    if kind.startswith("claude"):
        path = _co_metrics_lead_path(stage.lead_pid, kind)
        if path:
            metrics = read_claude_metrics(path, now)
    elif kind in ("codex", "t3 code") and sid:
        path = read_codex_sessions(home).get(sid)
        if path:
            metrics = read_codex_metrics(path, now)
            if kind == "codex":
                _co_cc_codex_stage(stage, path)
    elif kind == "opencode" and sid:
        rows = sessions if sessions is not None else read_opencode_sessions(home)
        row = rows.get(sid)
        if row:
            metrics = dict(row)
            found = (tools if tools is not None else read_opencode_lead_tools(home, {sid: row}, now)).get(sid)
            if found is not None:
                metrics["tools"], metrics["tool_pulses"] = found
    if metrics is None and stage.native_id:
        metrics = native_metrics(kind if kind in ("codex", "claude", "opencode", "cursor") else "", stage.native_id)
    if metrics is not None and metrics.get("cost") is not None and not _co_metered_lead(stage, path):
        metrics = {**metrics, "cost": None}   # one billing rule, for the lead's own row too
    _co_metrics_fill_lead(stage, metrics)


def _co_metrics_leads(stages: list[Stage], now: float) -> None:
    """Fill every session row's metric columns; one OpenCode DB read for all of them."""
    home = os.path.expanduser("~")
    sids = [stage.session_id or stage.native_id for stage in stages
            if stage.lead_kind == "opencode" and (stage.session_id or stage.native_id)]
    sessions: dict[str, dict] | None = None
    tools: dict[str, tuple[int, list]] | None = None
    if sids:
        loaded = read_opencode_sessions(home)
        wanted = {sid: loaded[sid] for sid in sids if sid in loaded}
        sessions, tools = loaded, read_opencode_lead_tools(home, wanted, now)
    for stage in stages:
        _co_metrics_lead(stage, now, home, sessions, tools)


def _co_stage_project_root(path: str) -> str:
    """``<project>/.stage/...`` -> ``<project>``; "" when the path is not inside a stage folder."""
    path = os.path.normpath(path) if path else ""
    marker = os.sep + ".stage" + os.sep
    if marker in path:
        return path.split(marker, 1)[0]
    return path.removesuffix(os.sep + ".stage") if path.endswith(os.sep + ".stage") else ""


def _co_stage_tree_root(cwds: Iterable[str]) -> str:
    """The project whose `.stage` tree holds every known cwd of a bucket; "" when they are mixed or none is in one."""
    roots = {_co_stage_project_root(cwd) for cwd in cwds if cwd}
    return next(iter(roots)) if len(roots) == 1 else ""


def _co_openclaw_project_root(cwd: str, home: str) -> str:
    """The openclaw project root for a run started in openclaw's workspace; "" for any other directory."""
    if not OPENCLAW_AGENT:
        return ""
    root = os.path.join(home, ".openclaw", "agents", OPENCLAW_AGENT)
    workspace = os.path.join(home, ".openclaw", "workspace-" + OPENCLAW_AGENT)
    cwd = os.path.normpath(cwd) if cwd else ""
    if cwd and (cwd == workspace or cwd.startswith(workspace + os.sep)):
        return root
    return ""


def _co_scope_session(
    workers: list[Worker], reviews: list[Review], now: float, all_history: bool,
    lead_since: float | None = None, lead_active: bool = False,
) -> tuple[float, int]:
    items: list[tuple[float, float]] = []
    for worker in workers:
        end = now if worker.state in LIVE_STATES else (worker.end if worker.end is not None else worker.start)
        items.append((worker.start, end))
    for review in reviews:
        end = now if review.running else review.end
        items.append((review.start, end))
    if not items:
        return now, 0
    if lead_since is not None and lead_since > 0:
        hidden = sum(1 for worker in workers if worker.start < lead_since and
                     (lead_active or worker.state not in LIVE_STATES))
        return lead_since, hidden
    items.sort(key=lambda item: item[0])
    if all_history:
        return items[0][0], 0
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    latest_end: float | None = None
    for start, end in items:
        if current and latest_end is not None and start > latest_end + SESSION_GAP_S:
            runs.append(current)
            current = []
            latest_end = None
        current.append((start, end))
        latest_end = end if latest_end is None else max(latest_end, end)
    if current:
        runs.append(current)
    session_start = runs[-1][0][0]
    hidden = sum(1 for worker in workers if worker.start < session_start)
    return session_start, hidden


def _co_filter_session(workers: list[Worker], session_start: float, all_history: bool,
                       lead_active: bool) -> list[Worker]:
    if all_history:
        return workers
    return [worker for worker in workers if worker.start >= session_start or
            (not lead_active and worker.state in LIVE_STATES)]


def _co_totals(workers: list[Worker]) -> tuple[int | None, float | None]:
    tokens = 0
    tokens_any = False
    cost = 0.0
    cost_any = False
    for worker in workers:
        log = worker.log
        if log.tokens_in is not None or log.tokens_out is not None:
            tokens_any = True
            tokens += (log.tokens_in or 0) + (log.tokens_out or 0)
        if log.cost is not None:
            cost_any = True
            cost += log.cost
    return (tokens if tokens_any else None, cost if cost_any else None)


# -- billing: per-token routes carry USD, subscriptions and free tiers do not ----------------------

_co_models_file = home_path("picker/models.json")
# Picker subscription ids that bill per token: an API key (Anthropic, Cursor), Zen credits, OpenRouter.
# opencode-go is a flat subscription, cursor-models and opencode-free are included, local is the GPU.
_co_metered_routes = frozenset({"anthropic-api", "openrouter", "openai-api", "opencode-api", "cursor-api"})
_co_route_cache: dict[str, tuple[object, dict[str, str], float]] = {}
_co_route_ttl_s = 60.0


def _co_picker_routes() -> dict[str, str]:
    """Model label -> its subscription route, from $STATION_HOME/picker/models.json; {} when it cannot be read.

    A picker id (``deepseek-or``) maps to its own route. A CLI model id (``gpt-6-luna``) maps only when every
    picker entry that runs it shares one route, so a subscription and its API twin never vote for each other.
    """
    path = os.path.expanduser(_co_models_file)
    stamp = _co_stat(path)
    key = (stamp.st_mtime_ns, stamp.st_size) if stamp is not None else None
    now = time.monotonic()
    cached = _co_route_cache.get(path)
    if cached is not None and cached[0] == key and now - cached[2] < _co_route_ttl_s:
        return cached[1]
    routes: dict[str, str] = {}
    by_cli: dict[str, set[str]] = {}
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        for model in data.get("models", []) if isinstance(data, dict) else []:
            if not isinstance(model, dict) or not isinstance(model.get("subscription"), str):
                continue
            if isinstance(model.get("id"), str):
                routes[model["id"].lower()] = model["subscription"]
            if isinstance(model.get("cliModel"), str):
                by_cli.setdefault(model["cliModel"].lower(), set()).add(model["subscription"])
    except (OSError, ValueError, TypeError):
        routes, by_cli = {}, {}
    for name, subscriptions in by_cli.items():
        if len(subscriptions) == 1 and name not in routes:
            routes[name] = next(iter(subscriptions))
    _co_route_cache[path] = (key, routes, now)
    return routes


def _co_route_label(worker: Worker) -> str:
    """The model id inside a worker's label: ``deepseek-or high``, ``luna@medium`` and ``gpt-6-luna (medium)``
    all read as their first model word."""
    label = (worker.model or "").strip()
    label = re.sub(r"\s*\([^)]*\)\s*$", "", label)   # the effort suffix an argv label carries
    return label.split()[0].split("@", 1)[0].strip().lower() if label else ""


def _co_claude_config_metered(path: str) -> bool:
    """Whether a Claude config dir, or a transcript below one, sits in the API-key config ``~/.claude-api``."""
    if not path:
        return False
    return any(part == ".claude-api" for part in os.path.normpath(path).replace("\\", "/").split("/"))


def _co_metered(worker: Worker) -> bool:
    """Whether a worker's route bills per token, from route and account evidence.

    Evidence, in order: the execution account (an OpenRouter or API-account subscription id), the model label
    matched against the picker's ``subscription``/``cliModel`` routes in models.json, the raw model id (an
    OpenRouter or Zen-credits prefix, an OpenCode Go, free or local id), an ``-api`` label suffix, then the
    Claude config dir of the worker's own process or transcript (``~/.claude-api``). Unknown is not metered.
    """
    if worker.account == OPENROUTER_ACCOUNT or (worker.account or "").lower() in _co_metered_routes:
        return True
    label = _co_route_label(worker)
    route = _co_picker_routes().get(label, "")
    if route:
        return route in _co_metered_routes
    raw = (worker.log.model or label).strip().lower()
    if raw.startswith("openrouter/") or label.endswith("-or"):
        return True
    if "-free" in raw or ":free" in raw or raw in ("local",) or raw.startswith("local/"):
        return False
    if raw.startswith("opencode-go/"):
        return False   # the flat OpenCode Go subscription, not Zen credits
    if raw.startswith("opencode/"):
        return True
    if label.endswith("-api"):
        return True
    if worker.log.path and _co_claude_config_metered(worker.log.path):
        return True
    if worker.pid:
        config = _co_environ(worker.pid).get("CLAUDE_CONFIG_DIR", "")
        if config and _co_claude_config_metered(config):
            return True
    return False


def _co_gate_costs(workers: Iterable[Worker]) -> None:
    """The one place billing is applied: only a metered route keeps a USD cost, for log and native sources alike."""
    for worker in workers:
        if worker.log.cost is not None and not _co_metered(worker):
            worker.log.cost = None


def _co_apply_billing(stages: list[Stage]) -> None:
    for stage in stages:
        _co_gate_costs(stage.snap.workers)


def _co_recompute_totals(gs: GlobalSnapshot) -> None:
    """Stage totals from their final worker rows, then the global totals from those stages."""
    tokens: list[int] = []
    costs: list[float] = []
    for stage in gs.stages:
        stage.snap.tokens_total, stage.snap.cost_total = _co_totals(stage.snap.workers)
        if stage.snap.tokens_total is not None:
            tokens.append(stage.snap.tokens_total)
        if stage.snap.cost_total is not None:
            costs.append(stage.snap.cost_total)
    gs.tokens_total = sum(tokens) if tokens else None
    gs.cost_total = sum(costs) if costs else None


class Collector:
    """Reads a stage directory plus /proc and builds a Snapshot."""

    def __init__(self, stage_dir: str, *, quota: bool = True, openrouter_only: bool = False) -> None:
        # quota=False: no quota thread; the owner (GlobalCollector) reads quota once for all stages
        # openrouter_only: with quota=False, poll spend-provider commands only (the phone monitor's feed)
        self._co_stage = stage_dir
        self._co_logs: dict[str, LogState] = {}
        self._co_log_fp: dict[str, tuple[int, float, int, bytes]] = {}
        self._co_prev_cpu: dict[str, tuple[int, int, float]] = {}
        self._co_review_cache: dict[str, tuple[tuple[int, float, int] | None, list[Finding], str]] = {}
        self._co_lock = threading.Lock()
        self._co_quota_accounts: list[QuotaAccount] = []
        self._co_quota_ever_ok = False
        self._co_quota_last_ok = False
        self._co_usage_path = os.path.expanduser(os.environ.get("STATUS_USAGE_HISTORY") or _co_default_usage_history)
        self._co_usage: list[tuple[float, dict[str, float]]] = []   # (time, account -> % used of its shortest window)
        self._co_usage_loaded = False
        self._co_spend: dict[str, SpendSnapshot] = {}
        self._co_spend_ever_ok: dict[str, bool] = {pid: False for pid in _CO_SPEND_PROVIDERS}
        self._co_spend_last_ok: dict[str, bool] = {pid: False for pid in _CO_SPEND_PROVIDERS}
        self._co_clk = _co_clk_tck()
        self._co_page = _co_page_size()
        self._co_ghosts: dict[tuple[str, int], tuple[Worker, float | None]] = {}   # recently seen headless workers
        self._co_boot = _co_boot_time()
        self._co_quota_cmd = _co_command("STATUS_QUOTA_CMD", ("status", "--json"))
        self._co_spend_cmds: dict[str, list[str]] = {
            pid: _co_command(env_key, args) for pid, (env_key, args) in _CO_SPEND_PROVIDERS.items()}
        self._co_stop = threading.Event()
        self._co_quick = False   # first paint: GlobalCollector sets it to skip cold parses of finished workers' logs
        self._co_thread: threading.Thread | None = None
        self._co_quota_on = quota
        if quota or openrouter_only:
            self._co_thread = threading.Thread(target=self._co_quota_loop, daemon=True)
            self._co_thread.start()

    # -- quota ---------------------------------------------------------------

    def _co_run_quota(self) -> list[QuotaAccount] | None:
        if not self._co_quota_cmd:
            return None
        try:
            result = subprocess.run(
                self._co_quota_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=_co_quota_timeout,
                check=False,
                text=True,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if result.returncode != 0:
            return None
        try:
            data = json.loads(result.stdout)
            subs = data["subscriptions"]
        except (ValueError, KeyError, TypeError):
            return None
        accounts: list[QuotaAccount] = []
        try:
            caps = {}
            for ep in data.get("extra") or []:
                usd = ep.get("usd")
                if not usd or usd <= 0:
                    continue   # no cost data, or nothing spent past the cap: not worth a mark
                caps[str(ep.get("account", ""))] = QuotaExtra(
                    usd=None if usd is None else float(usd), method=str(ep.get("method", "")),
                    since=float(ep.get("startAt", 0)) / 1000.0)
            for sub in subs:
                windows: list[QuotaWindow] = []
                for limit in sub.get("limits", []):
                    pace = limit.get("pacePct")
                    resets = limit.get("resetsAt")
                    projected = limit.get("projectedPct")
                    runs_out = limit.get("runsOutAt")
                    windows.append(
                        QuotaWindow(
                            name=str(limit.get("name", "")),
                            used=float(limit.get("usedPct", 0.0)),
                            pace=None if pace is None else float(pace),
                            stale=bool(limit.get("stale", False)),
                            window=str(limit.get("window", "")),
                            resets_at=None if resets is None else float(resets) / 1000.0,
                            projected_pct=None if projected is None else float(projected),
                            runs_out_at=None if runs_out is None else float(runs_out) / 1000.0,
                        )
                    )
                accounts.append(
                    QuotaAccount(
                        name=str(sub.get("id", "")),
                        windows=windows,
                        stale=bool(sub.get("stale", False)),
                        extra=caps.get(str(sub.get("account", ""))),
                    )
                )
            for cred in data.get("credits") or []:
                balance, burn, out = cred.get("balanceUsd"), cred.get("burnPerDayUsd"), cred.get("runsOutAt")
                accounts.append(
                    QuotaAccount(
                        name=str(cred.get("account", "")),
                        credit=QuotaCredit(
                            balance=None if balance is None else float(balance),
                            burn_per_day=None if burn is None else float(burn),
                            runs_out_at=None if out is None else float(out) / 1000.0,
                            low=bool(cred.get("low", False)),
                        ),
                    )
                )
        except (ValueError, TypeError, AttributeError):
            return None
        return accounts

    def _co_parse_spend(self, provider: str, data: dict, now: float) -> SpendSnapshot | None:
        try:
            floor_raw = data.get("floorUsd")
            floor_usd = None
            if floor_raw is not None:
                try:
                    floor_usd = float(floor_raw)
                except (TypeError, ValueError, OverflowError):
                    floor_usd = None
            cred = data.get("credit") or {}
            bal = cred.get("balanceUsd")
            balance_f: float | None = None
            if bal is not None:
                try:
                    balance_f = float(bal)
                except (TypeError, ValueError, OverflowError):
                    balance_f = None
            bal_block = data.get("balance") or {}
            if balance_f is None and isinstance(bal_block, dict):
                usd = bal_block.get("usd")
                if usd is not None:
                    try:
                        balance_f = float(usd)
                    except (TypeError, ValueError, OverflowError):
                        balance_f = None
            balance_at_ms = cred.get("balanceAt")
            if balance_at_ms is None and isinstance(bal_block, dict):
                balance_at_ms = bal_block.get("at")
            balance_at: float | None = None
            if balance_at_ms is not None:
                try:
                    balance_at = float(balance_at_ms) / 1000.0
                except (TypeError, ValueError, OverflowError):
                    balance_at = None
            burn = cred.get("burnPerDayUsd")
            burn_f: float | None = None
            if burn is not None:
                try:
                    burn_f = float(burn)
                except (TypeError, ValueError, OverflowError):
                    burn_f = None
            out = cred.get("runsOutAt")
            runs_out: float | None = None
            if out is not None:
                try:
                    runs_out = float(out) / 1000.0
                except (TypeError, ValueError, OverflowError):
                    runs_out = None
            stale_flag = data.get("stale")
            stale = bool(stale_flag) if stale_flag is not None else False
            if provider == OPENROUTER_ACCOUNT and balance_at is not None:
                stale = stale or (now - balance_at > OPENROUTER_BALANCE_STALE_S)
            low = bool(cred.get("low", False))
            if balance_f is not None and floor_usd is not None and balance_f < floor_usd:
                low = True
            spend = data.get("spend") or {}
            since = str(spend.get("since", "")) if spend.get("since") is not None else ""
            mtd: float | None = None
            mtd_raw = spend.get("monthToDateUsd")
            if mtd_raw is not None:
                try:
                    mtd = float(mtd_raw)
                except (TypeError, ValueError, OverflowError):
                    mtd = None
            per_day: list[SpendDay] = []
            for row in (spend.get("perDay") or [])[:OPENROUTER_MAX_ROWS]:
                if not isinstance(row, dict):
                    continue
                try:
                    per_day.append(SpendDay(
                        date=str(row.get("date", "")),
                        usd=float(row.get("usd", 0)),
                        requests=int(row.get("requests", 0)),
                    ))
                except (TypeError, ValueError, OverflowError):
                    continue
            per_model: list[SpendModel] = []
            for row in (spend.get("perModel") or [])[:OPENROUTER_MAX_ROWS]:
                if not isinstance(row, dict):
                    continue
                try:
                    per_model.append(SpendModel(
                        model=str(row.get("model", "")),
                        usd=float(row.get("usd", 0)),
                        requests=int(row.get("requests", 0)),
                        prompt_tokens=int(row.get("promptTokens", 0)),
                        completion_tokens=int(row.get("completionTokens", 0)),
                    ))
                except (TypeError, ValueError, OverflowError):
                    continue
            budget = data.get("budget") or {}
            budget_limit: float | None = None
            budget_pct: float | None = None
            if isinstance(budget, dict):
                lim = budget.get("limitUsd")
                if lim is not None:
                    try:
                        budget_limit = float(lim)
                    except (TypeError, ValueError, OverflowError):
                        budget_limit = None
                used = budget.get("usedPct")
                if used is not None:
                    try:
                        budget_pct = float(used)
                    except (TypeError, ValueError, OverflowError):
                        budget_pct = None
            src = data.get("source")
            source = str(src) if src is not None else None
            label = str(data.get("account", provider) or provider)
            if not (balance_f is not None or mtd is not None or per_day or per_model or since):
                return None
            return SpendSnapshot(
                provider=label,
                balance=balance_f,
                floor_usd=floor_usd,
                burn_per_day=burn_f,
                runs_out_at=runs_out,
                balance_at=balance_at,
                low=low,
                stale=stale,
                since=since,
                per_day=per_day,
                per_model=per_model,
                month_to_date_usd=mtd,
                budget_limit_usd=budget_limit,
                budget_used_pct=budget_pct,
                source=source,
            )
        except (TypeError, ValueError, OverflowError, AttributeError):
            return None

    def _co_parse_openrouter(self, data: dict, now: float) -> SpendSnapshot | None:
        return self._co_parse_spend(OPENROUTER_ACCOUNT, data, now)

    def _co_run_spend(self, provider: str) -> SpendSnapshot | None:
        cmd = self._co_spend_cmds.get(provider) or []
        if not cmd:
            return None
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=_co_quota_timeout,
                check=False,
                text=True,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if result.returncode != 0:
            return None
        try:
            data = json.loads(result.stdout)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        return self._co_parse_spend(provider, data, time.time())

    def _co_run_openrouter(self) -> SpendSnapshot | None:
        return self._co_run_spend(OPENROUTER_ACCOUNT)

    def _co_quota_loop(self) -> None:
        while not self._co_stop.is_set():
            accounts = self._co_run_quota() if self._co_quota_on else None
            spend_results = {pid: self._co_run_spend(pid) for pid in _CO_SPEND_PROVIDERS}
            with self._co_lock:
                if self._co_quota_on:
                    self._co_quota_last_ok = accounts is not None
                if accounts is not None:
                    self._co_quota_ever_ok = True
                    self._co_quota_accounts = accounts
            if accounts is not None:
                self._co_usage_sample(accounts, time.time())
            with self._co_lock:
                for pid, snap in spend_results.items():
                    self._co_spend_last_ok[pid] = snap is not None
                    if snap is not None:
                        self._co_spend_ever_ok[pid] = True
                        self._co_spend[pid] = snap
            if self._co_stop.wait(_co_quota_interval):
                return

    def _co_usage_load(self, now: float) -> None:
        """Read the history file once (the owner may have been restarted); a bad line is skipped."""
        if self._co_usage_loaded:
            return
        self._co_usage_loaded = True
        try:
            with open(self._co_usage_path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                        t = float(row["t"])
                        vals = {str(k): float(v) for k, v in row["a"].items()}
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
                    if now - t <= _co_usage_keep_s:
                        self._co_usage.append((t, vals))
        except OSError:
            pass

    def _co_usage_sample(self, accounts: list[QuotaAccount], now: float) -> None:
        """Append one sample (every 5 minutes at most) and keep the file to the last 24 hours."""
        with self._co_lock:
            self._co_usage_load(now)
        vals = {a.name: min(a.windows, key=lambda w: _co_window_secs(w)).used
                for a in accounts if a.windows and not a.stale}
        if not vals or (self._co_usage and now - self._co_usage[-1][0] < _co_usage_sample_s):
            return
        with self._co_lock:
            self._co_usage.append((now, vals))
            self._co_usage = [(t, v) for t, v in self._co_usage if now - t <= _co_usage_keep_s]
            rows = list(self._co_usage)
        try:
            os.makedirs(os.path.dirname(self._co_usage_path) or ".", exist_ok=True)
            tmp = self._co_usage_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps({"t": round(t, 1), "a": v}) + "\n" for t, v in rows)
            os.replace(tmp, self._co_usage_path)
        except OSError:
            pass

    def _co_usage_series(self, now: float) -> dict[str, list[float]]:
        """Per account, % used over the last 24 hours in 30-minute steps, oldest first; empty steps are skipped."""
        with self._co_lock:
            self._co_usage_load(now)
            samples = list(self._co_usage)
        step = _co_usage_keep_s / _co_usage_buckets
        out: dict[str, dict[int, float]] = {}
        for t, vals in samples:
            idx = int((t - (now - _co_usage_keep_s)) // step)
            if not 0 <= idx < _co_usage_buckets:
                continue
            for name, used in vals.items():
                out.setdefault(name, {})[idx] = used     # later samples overwrite: the step's last value
        return {name: [pts[i] for i in sorted(pts)] for name, pts in out.items()}

    def _co_quota_ok(self) -> bool:
        with self._co_lock:
            return self._co_quota_ever_ok and self._co_quota_last_ok

    def _co_spend_ok(self, provider: str) -> bool:
        with self._co_lock:
            return self._co_spend_ever_ok.get(provider, False) and self._co_spend_last_ok.get(provider, False)

    def _co_spend_snapshot(self, provider: str) -> SpendSnapshot | None:
        with self._co_lock:
            return self._co_spend.get(provider)

    def _co_spend_state(self) -> tuple[dict[str, SpendSnapshot], dict[str, bool]]:
        with self._co_lock:
            return dict(self._co_spend), {
                pid: self._co_spend_ever_ok.get(pid, False) and self._co_spend_last_ok.get(pid, False)
                for pid in _CO_SPEND_PROVIDERS
            }

    def _co_openrouter_ok(self) -> bool:
        return self._co_spend_ok(OPENROUTER_ACCOUNT)

    def _co_openrouter_snapshot(self) -> SpendSnapshot | None:
        return self._co_spend_snapshot(OPENROUTER_ACCOUNT)

    def _co_quota_snapshot(self, workers: list[Worker]) -> list[QuotaAccount]:
        with self._co_lock:
            accounts = list(self._co_quota_accounts)
        used = set()
        for worker in workers:
            account = _co_account_for(worker.family, worker.model)
            if account:
                used.add(account)
        return [
            QuotaAccount(
                name=acc.name,
                windows=list(acc.windows),
                stale=acc.stale,
                in_use=acc.name in used,
                credit=acc.credit,
                extra=acc.extra,
            )
            for acc in accounts
        ]

    def close(self) -> None:
        self._co_stop.set()
        thread = self._co_thread
        if thread is not None:
            thread.join(timeout=1.0)

    # -- title ---------------------------------------------------------------

    def _co_title(self) -> str:
        env = os.environ.get("STATUS_TITLE")
        if env:
            return env
        try:
            parent = os.path.dirname(os.path.abspath(self._co_stage))
            return os.path.basename(parent)
        except (OSError, ValueError):
            return ""

    # -- workers -------------------------------------------------------------

    def _co_log_for(self, name: str, log_path: str | None, live: bool, now: float) -> LogState:
        if log_path is None:
            self._co_logs.pop(name, None)
            self._co_log_fp.pop(name, None)
            return LogState()
        st = _co_stat(log_path)
        if st is not None:
            try:
                with open(log_path, "rb") as handle:
                    head_hash = hashlib.sha1(handle.read(min(4096, st.st_size))).digest()
            except OSError:
                head_hash = b""
            fingerprint = (st.st_ino, st.st_mtime, st.st_size, head_hash)
        else:
            fingerprint = None
        prev = self._co_logs.get(name)
        old = self._co_log_fp.get(name)
        if st is not None and prev is not None and old is not None and _co_log_rewritten(log_path, st, old):
            prev = None
        unchanged = fingerprint is not None and old == fingerprint
        if not live and prev is not None and unchanged:
            prune_tool_ring(prev, now)   # an unchanged log still ages out of the tool ring
            return prev
        if self._co_quick and not live and prev is None:
            return LogState(path=log_path)   # first paint: a finished worker's log is read by the full pass that follows
        state = _co_guard(parse_log, log_path, prev=prev, now=now)
        if not isinstance(state, LogState):
            state = prev if prev is not None else LogState(path=log_path)
        self._co_logs[name] = state
        if fingerprint is not None:
            self._co_log_fp[name] = fingerprint
        return state

    def _co_cpu(self, name: str, pid: int, ticks: int, now: float) -> float | None:
        prev = self._co_prev_cpu.get(name)
        cpu: float | None = None
        if prev is not None and prev[0] == pid and now > prev[2]:
            delta_ticks = ticks - prev[1]
            cpu = max(delta_ticks / self._co_clk / (now - prev[2]) * 100.0, 0.0)
        self._co_prev_cpu[name] = (pid, ticks, now)
        return cpu

    def _co_one_worker(
        self,
        stage: str,
        name: str,
        pid_path: str,
        pid: int | None,
        now: float,
        procs: dict[int, tuple[int, int, int, int, int]],
        children: dict[int, list[int]],
    ) -> Worker:
        st = _co_stat(pid_path)
        start = st.st_mtime if st is not None else 0.0
        live = _co_pid_live(pid, start, procs, self._co_clk, self._co_boot)
        log_path = _co_pick_log(stage, name)
        log = self._co_log_for(name, log_path, live, now)

        report_path = os.path.join(stage, name, "REPORT.md")
        has_report = os.path.isfile(report_path)
        summary = _co_report_summary(_co_read_lines(report_path)) if has_report else []

        brief = _co_brief_path(stage, name)
        title = _co_brief_title(brief)
        goal = _co_brief_goal(brief)
        job = _co_brief_job(brief)

        model = _co_first_line(os.path.join(stage, name + ".model"))
        effort = ""
        if log_path:
            for line in _co_read_tail(log_path, 16 * 1024):
                match = re.search(r"reasoning effort:\s*(\S+)", line, re.IGNORECASE)
                if match:
                    effort = _co_effort(match.group(1))
                    break
        if not effort:
            effort = _co_effort(_co_read_text(os.path.join(stage, name + ".model")))
        if not model:
            model = log.model or ""
        if not model and live and pid is not None:
            model = _co_proc_model(pid, children)
        family = family_of(model)

        cpu: float | None = None
        rss: int | None = None
        nproc: int | None = None
        if live and pid is not None:
            tree = _co_tree(pid, children)
            ticks = 0
            rss_bytes = 0
            for member in tree:
                info = procs.get(member)
                if info is not None:
                    ticks += info[1] + info[2]
                resident = _co_resident(member, self._co_page)
                if resident is not None:
                    rss_bytes += resident
            nproc = len(tree)
            rss = rss_bytes
            cpu = self._co_cpu(name, pid, ticks, now)

        if live:
            end: float | None = None
        else:
            stamps: list[float] = []
            if log_path is not None:
                log_st = _co_stat(log_path)
                if log_st is not None:
                    stamps.append(log_st.st_mtime)
            report_st = _co_stat(report_path)
            if report_st is not None:
                stamps.append(report_st.st_mtime)
            end = max(stamps) if stamps else start

        quiet_s = _co_quiet(now, log, log_path, start)
        is_review = name.startswith("review")
        state = _co_state(live, log, is_review, has_report, quiet_s, cpu)
        if state == NOREPORT and _co_brief_stdout_only(brief):
            state = DONE
        lineage = _co_worker_lineage(stage, name)
        return Worker(
            name=name,
            state=state,
            title=title,
            goal=goal,
            model=model,
            family=family,
            pid=pid,
            start=start,
            end=end,
            cpu=cpu,
            rss=rss,
            nproc=nproc,
            log=log,
            activity=activity(log, now),
            quiet_s=quiet_s,
            summary=summary,
            has_report=has_report,
            is_review=is_review,
            job=job,
            effort=effort,
            session=lineage[0], parent_session=lineage[1], root_session=lineage[2], depth=lineage[3],
            ancestors=lineage[4], launched=lineage[5],
        )

    def _co_discovered(
        self,
        now: float,
        procs: dict[int, tuple[int, int, int, int, int]],
        children: dict[int, list[int]],
        taken_pids: set[int],
        taken_names: set[str],
        project: str,
    ) -> list[Worker]:
        """Headless agent processes running under ``project`` that have no ``.pid`` file of their own.

        A ``dispatch`` worker writes ``<name>.pid`` and is found the normal way (and skipped here by pid); this
        picks up the rest straight from the process table. A worker that has just exited stays listed as done (its
        outcome is unknown, but it has no report to be missing) for ``_co_grace_s`` (a long-running monitor keeps what it saw)."""
        found = _co_headless_under(procs, project, os.path.expanduser("~"))
        return self._co_procs_as_workers(now, found, procs, children, taken_pids, taken_names, project, project)

    def _co_procs_as_workers(
        self,
        now: float,
        found: list[tuple[int, str, str]],
        procs: dict[int, tuple[int, int, int, int, int]],
        children: dict[int, list[int]],
        taken_pids: set[int],
        taken_names: set[str],
        project: str,
        scope: str,
        session: str = "",
        slices: dict[int, str] | None = None,
    ) -> list[Worker]:
        """Workers for headless processes ``found``, plus those from ``scope`` that exited within the grace period (shown as
        done: a discovered run has no report, so a finished one is not a problem). ``slices`` maps a pid to the name of
        the ``.pid`` file that holds it: a run a stage already names keeps that name instead of ``opencode-<pid>``."""
        for key in [key for key, (_, gone) in self._co_ghosts.items() if gone is not None and now - gone > _co_grace_s]:
            del self._co_ghosts[key]     # also the scopes nobody asks about any more (an archived session)
        out: list[Worker] = []
        used = set(taken_names)
        live_pids: set[int] = set()
        for pid, cwd, harness in found:
            if pid in taken_pids or _co_ancestor_in(pid, taken_pids, procs):
                continue
            live_pids.add(pid)
            base = (slices or {}).get(pid) or os.path.basename(os.path.normpath(cwd))
            if not base or os.path.normpath(cwd) == os.path.normpath(project):
                base = f"{harness}-{pid}"
            name = base if base not in used else f"{base}-{pid}"
            used.add(name)
            model, effort = _co_proc_model_effort(pid, children)
            tree = _co_tree(pid, children)
            ticks = sum(procs[member][1] + procs[member][2] for member in tree if member in procs)
            rss = sum(_co_resident(member, self._co_page) or 0 for member in tree)
            start = self._co_boot + procs[pid][3] / self._co_clk
            worker = Worker(
                name=name,
                state=RUNNING,     # no log to judge silence from: a live headless run reads as running
                model=model,
                family=family_of(model),
                pid=pid,
                start=start,
                cpu=self._co_cpu(name, pid, ticks, now),
                rss=rss,
                nproc=len(tree),
                quiet_s=now - start,
                effort=effort,
                session=session,
            )
            self._co_ghosts[(scope, pid)] = (worker, None)
            out.append(worker)
            if len(out) >= _co_discovered_max:
                break
        for key in [key for key in self._co_ghosts if key[0] == scope and key[1] not in live_pids]:
            worker, gone = self._co_ghosts[key]
            if key[1] in taken_pids or key[1] in procs and _co_pid_live(key[1], worker.start, procs, self._co_clk, self._co_boot):
                continue   # still running, just not found this time (or now someone else's): leave it
            if gone is None:
                gone = now
                self._co_ghosts[key] = (worker, gone)
            if now - gone > _co_grace_s:
                del self._co_ghosts[key]
                continue
            out.append(replace(worker, state=NOREPORT, end=gone, cpu=0.0, quiet_s=now - gone,
                               nproc=0, outcome="unknown"))
        return out

    def _co_workers(
        self,
        stage: str,
        now: float,
        procs: dict[int, tuple[int, int, int, int, int]],
        children: dict[int, list[int]],
    ) -> tuple[list[Worker], list[tuple[str, bool]]]:
        out: list[Worker] = []
        services: list[tuple[str, bool]] = []
        try:
            entries = sorted(os.listdir(stage))
        except OSError:
            return out, services
        for filename in entries:
            if not filename.endswith(".pid"):
                continue
            path = os.path.join(stage, filename)
            try:
                if not os.path.isfile(path):
                    continue
            except OSError:
                continue
            name = filename[:-4]
            pid = _co_digits(path)
            if _co_guard(_co_is_service, stage, name, pid, procs, children) is True:
                services.append((name, True))
                continue
            worker = _co_guard(self._co_one_worker, stage, name, path, pid, now, procs, children)
            if isinstance(worker, Worker):
                out.append(worker)
        services.sort(key=lambda item: item[0])
        return out, services

    # -- reviews -------------------------------------------------------------

    def _co_reviews(
        self, stage: str, procs: dict[int, tuple[int, int, int, int, int]]
    ) -> list[Review]:
        out: list[Review] = []
        try:
            entries = sorted(os.listdir(stage))
        except OSError:
            return out
        for filename in entries:
            if not (filename.startswith("review-") and filename.endswith(".log")):
                continue
            path = os.path.join(stage, filename)
            try:
                if not os.path.isfile(path):
                    continue
            except OSError:
                continue
            name = filename[:-4]
            st = _co_stat(path)
            end = st.st_mtime if st is not None else 0.0
            pid_path = os.path.join(stage, name + ".pid")
            pid_st = _co_stat(pid_path)
            pid = _co_digits(pid_path) if pid_st is not None else None
            start = pid_st.st_mtime if pid_st is not None else end
            running = _co_pid_live(pid, start, procs, self._co_clk, self._co_boot)
            findings, header = self._co_review_parsed(name, path, st)
            model = _co_first_line(os.path.join(stage, name + ".model"))
            if not model:
                model = header
            out.append(
                Review(
                    name=name,
                    model=model,
                    family=family_of(model),
                    start=start,
                    end=end,
                    running=running,
                    findings=findings,
                )
            )
        return out

    def _co_review_parsed(
        self, name: str, path: str, st: os.stat_result | None
    ) -> tuple[list[Finding], str]:
        """Findings and log model header for a review, cached by (inode, mtime, size)."""
        fingerprint = (st.st_ino, st.st_mtime, st.st_size) if st is not None else None
        cached = self._co_review_cache.get(name)
        if cached is not None and cached[0] == fingerprint:
            return cached[1], cached[2]
        findings = _co_findings(_co_read_tail(path, _co_review_tail))
        header = _co_log_model_header(path)
        self._co_review_cache[name] = (fingerprint, findings, header)
        return findings, header

    def _co_evict(self, seen: set[str]) -> None:
        """Drop cached per-worker/review state for names absent from the latest scan."""
        for name in list(self._co_logs):
            if name not in seen:
                del self._co_logs[name]
        for name in list(self._co_log_fp):
            if name not in seen:
                del self._co_log_fp[name]
        for name in list(self._co_prev_cpu):
            if name not in seen:
                del self._co_prev_cpu[name]
        for name in list(self._co_review_cache):
            if name not in seen:
                del self._co_review_cache[name]

    # -- snapshot ------------------------------------------------------------

    def snapshot(self, now: float | None = None, all_history: bool = False,
                 procs: dict[int, tuple[int, int, int, int, int]] | None = None,
                 lead_since: float | None = None, lead_active: bool = False) -> Snapshot:
        if now is None:
            now = time.time()
        result = _co_guard(self._co_build, now, all_history, procs, lead_since, lead_active)
        if isinstance(result, Snapshot):
            return result
        return Snapshot(
            title=self._co_title(),
            now=now,
            session_start=now,
            all_history=all_history,
            quota=self._co_quota_snapshot([]),
            quota_ok=self._co_quota_ok(),
        )

    def _co_build(self, now: float, all_history: bool,
                  procs: dict[int, tuple[int, int, int, int, int]] | None = None,
                  lead_since: float | None = None, lead_active: bool = False) -> Snapshot:
        stage = self._co_stage
        if procs is None:
            procs = _co_scan_procs()
        children: dict[int, list[int]] = {}
        for pid, info in procs.items():
            children.setdefault(info[0], []).append(pid)

        workers, services = self._co_workers(stage, now, procs, children)
        project = _co_project_root(stage)
        # headless workers with no .pid file of their own; a nested stage (<stage>/review) is skipped so the
        # outer stage, which already covers that project, is the only one to list them
        if project and os.path.isabs(project) and not _co_is_stage_dir(os.path.dirname(stage)):
            workers += self._co_discovered(
                now, procs, children,
                {worker.pid for worker in workers if worker.pid} | _co_nested_stage_pids(stage),
                {worker.name for worker in workers},
                project,
            )
        reviews = self._co_reviews(stage, procs)
        self._co_evict({worker.name for worker in workers} | {review.name for review in reviews})
        milestones = _co_milestones(os.path.join(stage, "PROGRESS.md"))
        all_milestones = milestones

        session_start, hidden = _co_scope_session(workers, reviews, now, all_history, lead_since, lead_active)
        if not all_history:
            workers = _co_filter_session(workers, session_start, all_history, lead_active)
            reviews = [review for review in reviews if review.start >= session_start]
            milestones = [
                stone for stone in milestones if stone.ts is None or stone.ts >= session_start
            ]
        groups = _co_groups(workers, all_milestones)

        live = sorted(
            (worker for worker in workers if worker.state in LIVE_STATES),
            key=lambda worker: worker.start,
        )
        dead = sorted(
            (worker for worker in workers if worker.state not in LIVE_STATES),
            key=lambda worker: worker.end if worker.end is not None else 0.0,
            reverse=True,
        )
        reviews.sort(key=lambda review: review.end, reverse=True)
        _co_metrics_workers(workers, now)
        _co_gate_costs(live + dead)   # one billing rule, before the row's own totals are summed
        tokens_total, cost_total = _co_totals(live + dead)

        return Snapshot(
            title=self._co_title(),
            now=now,
            session_start=session_start,
            all_history=all_history,
            workers=live + dead,
            tokens_total=tokens_total,
            cost_total=cost_total,
            hidden_older=hidden,
            reviews=reviews,
            milestones=milestones,
            quota=self._co_quota_snapshot(live + dead),
            quota_ok=self._co_quota_ok(),
            services=services,
            groups=groups,
        )

    # -- detail --------------------------------------------------------------

    def detail(self, name: str, tab: str) -> list[str]:
        result = _co_guard(self._co_detail, name, tab)
        return result if isinstance(result, list) else []

    def _co_detail(self, name: str, tab: str) -> list[str]:
        stage = self._co_stage
        if tab == "brief":
            path = _co_brief_path(stage, name)
        elif tab == "report":
            path = os.path.join(stage, name, "REPORT.md")
        else:
            return []
        if not path or not os.path.isfile(path):
            return []
        lines = _co_read_text(path).splitlines()
        return [line.replace("\t", "    ") for line in lines][:_co_max_detail]


# -- global view -----------------------------------------------------------------


def _co_skip_dir(name: str) -> bool:
    return name.startswith(".") or name in _co_skip_dirs


def _co_is_stage_dir(path: str) -> bool:
    """A directory is a stage when it contains any marker from the stage contract."""
    try:
        names = os.listdir(path)
    except OSError:
        return False
    if (os.path.isdir(os.path.join(path, "briefs"))
            or any(os.path.isfile(os.path.join(path, name)) for name in ("station", "PROGRESS.md", "lead.json"))):
        return True
    if any(name.endswith(".pid") and os.path.isfile(os.path.join(path, name)) for name in names):
        return True
    return any(os.path.isdir(os.path.join(path, name))
               and os.path.isfile(os.path.join(path, name, "REPORT.md")) for name in names)


def _co_nested_stages(stage: str) -> list[str]:
    """Stage dirs one level inside a stage: a dispatch run keeps its own `<stage>/review/` with pid files stage marker."""
    found: list[str] = []
    try:
        with os.scandir(stage) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False) and _co_is_stage_dir(entry.path):
                    found.append(entry.path)
    except OSError:
        pass
    return found


def _co_walk(root: str, depth: int) -> list[str]:
    """Absolute stage dirs under ``root``, at most ``depth`` levels down. Symlinks are not followed."""
    found: list[str] = []
    stack: list[tuple[str, int]] = [(root, 0)]
    while stack:
        current, level = stack.pop()
        if level >= depth:
            continue
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except OSError:
            continue
        for entry in children:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name in STAGE_NAMES or (entry.name.startswith(".") and _co_is_stage_dir(entry.path)):
                found.append(entry.path)
                found.extend(_co_nested_stages(entry.path))
            elif not _co_skip_dir(entry.name):
                stack.append((entry.path, level + 1))
    return found


def find_stages(roots: list[str], depth: int = SCAN_DEPTH) -> list[tuple[str, str]]:
    """(absolute stage path, label) for each stage dir found, sorted by label."""
    found: dict[str, str] = {}
    for raw in roots:
        root = os.path.abspath(os.path.expanduser(raw))
        stages = _co_walk(root, depth)
        # a root that holds a stage itself labels everything relative to its parent
        direct = any(os.path.dirname(stage) == root for stage in stages)
        base = os.path.dirname(root) if direct else root
        for stage in stages:
            if stage not in found:
                found[stage] = os.path.relpath(os.path.dirname(stage), base).replace(os.sep, "/")
    # short labels: the project folder name, with its parent path only when two names clash
    # only a project with a normal stage dir owns the projects below it (not a shared ~/Projects/.occ-stage)
    projects = {os.path.dirname(stage) for stage in found if os.path.basename(stage) in STAGE_NAMES}
    short: dict[str, str] = {}
    for stage, label in found.items():
        project = os.path.dirname(stage)
        owner = next((p for p in sorted(projects, key=len, reverse=True)
                      if p != project and project.startswith(p + os.sep)), None)
        start = os.path.dirname(owner) if owner else os.path.dirname(project)
        short[stage] = os.path.relpath(project, start).replace(os.sep, "/") or label
    counts: dict[str, int] = {}
    for name in short.values():
        counts[name] = counts.get(name, 0) + 1
    labels: dict[str, str] = {}
    for stage, name in short.items():
        if counts[name] == 1:
            labels[stage] = name
            continue
        # clash: name the second stage dir of one project by its folder, else add the parent folder
        siblings = [other for other in short if os.path.dirname(other) == os.path.dirname(stage)]
        if len(siblings) > 1 and os.path.basename(stage) not in STAGE_NAMES:
            labels[stage] = f"{name} ({os.path.basename(stage)})"
        else:
            parent = os.path.basename(os.path.dirname(os.path.dirname(stage)))
            labels[stage] = f"{name} ({parent})" if parent else name
    for stage in list(labels):   # a stage dir inside another (`<stage>/review/`) is named after its parent: "billing-api (review)"
        parent = os.path.dirname(stage)
        if parent in labels:
            labels[stage] = f"{labels[parent]} ({os.path.basename(stage)})"
    return sorted(labels.items(), key=lambda item: (item[1], item[0]))


def _co_stage_status(stage: str) -> tuple[float, list[tuple[str, float]]]:
    """Newest mtime of the stage's status files, and its ``*.pid`` files as (path, mtime)."""
    newest = 0.0
    pid_files: list[tuple[str, float]] = []
    try:
        with os.scandir(stage) as entries:
            children = list(entries)
    except OSError:
        return newest, pid_files
    for entry in children:
        if not (entry.name.endswith(_co_status_suffixes) or entry.name in _co_status_names):
            continue
        try:
            if not entry.is_file():
                continue
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        newest = max(newest, mtime)
        if entry.name.endswith(".pid"):
            pid_files.append((entry.path, mtime))
    return newest, pid_files


def _co_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _co_parse_iso(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _co_lead_file(stage: str, now: float) -> LeadInfo | None:
    path = os.path.join(stage, "lead.json")
    st = _co_stat(path)
    if st is None:
        return None
    try:
        data = json.loads(_co_read_text(path))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    stamp = _co_parse_iso(data.get("updated_at"))
    if stamp is None:
        stamp = st.st_mtime
    if abs(now - stamp) > SESSION_GAP_S:
        return None
    stream = os.path.basename(_co_text(data.get("stream"))).removesuffix(".md")
    return LeadInfo(
        model=_co_text(data.get("lead_label")) or _co_text(data.get("lead_model")),
        phase=_co_text(data.get("phase")),
        stream=stream,
    )


def _co_read_ledger(path: str) -> list[tuple[float, str, str]]:
    """(epoch, stage path, lead) for each ledger line in the last ``_co_ledger_tail`` bytes."""
    rows: list[tuple[float, str, str]] = []
    for line in _co_read_tail(path, _co_ledger_tail):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        stamp = _co_parse_iso(entry.get("ts"))
        stage = _co_text(entry.get("stage"))
        lead = _co_text(entry.get("lead"))
        if stamp is None or not stage or not lead:
            continue
        rows.append((stamp, os.path.normpath(os.path.expanduser(stage)), lead))
    return rows


def _co_ledger_lead(rows: list[tuple[float, str, str]], stage: str, now: float) -> LeadInfo | None:
    matches = [row for row in rows if row[1] == stage]
    if not matches:
        return None
    stamp, _, lead = max(matches, key=lambda row: row[0])
    if abs(now - stamp) > SESSION_GAP_S:
        return None
    return LeadInfo(model=lead)


def _co_last_activity(snap: Snapshot) -> float:
    stamps: list[float] = []
    for worker in snap.workers:
        stamps.append(worker.start)
        if worker.end is not None:
            stamps.append(worker.end)
    stamps.extend(stone.ts for stone in snap.milestones if stone.ts is not None)
    for review in snap.reviews:
        stamps.extend((review.start, review.end))
    return max(stamps, default=0.0)


def _co_filter_dismissed_stages(stages: list[Stage], dismissed: dict[str, float]) -> tuple[list[Stage], int]:
    """Drop dismissed non-live stations with no live workers until their activity advances."""
    kept: list[Stage] = []
    count = 0
    for stage in stages:
        activity_at = stage.activity or stage.last_activity
        if (stage.path in dismissed and stage.lead_state != "live" and stage.live == 0
                and activity_at <= dismissed[stage.path]):
            count += 1
        else:
            kept.append(stage)
    return kept, count


def _co_empty_idle_leads(stages: list[Stage], all_history: bool) -> None:
    """Keep finished workers visible; only an open lead with no workers is solo."""
    for stage in stages:
        stage.solo = stage.lead_state in ("live", "background") and not stage.snap.workers


def _co_nested_stage_pids(stage: str) -> set[int]:
    """The pids named by .pid files in the stage's immediate subdirectories: those are other stages' workers, which
    the outer stage must not list a second time when it finds their processes."""
    found: set[int] = set()
    try:
        with os.scandir(stage) as entries:
            subdirs = [entry.path for entry in entries if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return found
    for directory in subdirs:
        try:
            names = [name for name in os.listdir(directory) if name.endswith(".pid")]
        except OSError:
            continue
        for name in names:
            try:
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    text = fh.read(32).split()
            except OSError:
                continue
            if text and text[0].isdigit():
                found.add(int(text[0]))
    return found


def _co_ancestor_in(pid: int, pids: set[int], procs: dict[int, tuple[int, int, int, int, int]]) -> bool:
    """Whether a process above ``pid`` is one of ``pids``: a worker's .pid file holds its launcher, its agent runs below it."""
    if not pids:
        return False
    cur = procs.get(pid, (0,))[0]
    for _ in range(16):
        if cur <= 1 or cur not in procs:
            return False
        if cur in pids:
            return True
        cur = procs[cur][0]
    return False


_OWN_NATIVE_START_S = 15.0   # a worker's own session starts within this long of the worker being launched


def _co_is_own_native_row(parent: Worker | None, child: Worker | None) -> bool:
    """Whether `child` (a native execution row) is the session of the dispatch worker `parent`, not a subagent of it:
    the parent is a stage worker with a process and no native identity yet, the child is that session (a native
    execution row or a process-table row), they are the same model family, and the child started right after the
    parent was launched."""
    if parent is None or child is None or parent.execution_id or not parent.pid:
        return False
    process_row = bool(child.pid) and child.name.endswith(f"-{child.pid}")   # "claude-1062374": found from the process table
    if not (child.execution_id and child.native_id or process_row) or child.family != parent.family:
        return False
    return bool(parent.start and child.start) and 0.0 <= child.start - parent.start <= _OWN_NATIVE_START_S


def _co_nest_workers(stages: list[Stage], procs: dict[int, tuple[int, int, int, int, int]]) -> None:
    """Link each worker to the worker in the same session row that launched it (``parent``, ``level``), and list
    children right after their parent. A parent is found, in order, by: the execution parent id; the launching (or
    nearest ancestor) session id equal to a sibling's own native id; a sibling's pid among the worker's process
    ancestors. Execution rows are left alone."""
    for stage in stages:
        workers = stage.snap.workers
        if stage.execution_id or len(workers) < 2:
            continue
        by_exec = {wk.execution_id: wk for wk in workers if wk.execution_id}
        by_native = {wk.native_id: wk for wk in workers if wk.native_id}
        by_pid = {wk.pid: wk for wk in workers if wk.pid}
        parent_of: dict[str, str] = {}
        for wk in workers:
            found = by_exec.get(wk.parent_id) if wk.parent_id else None
            for sid in (wk.session, wk.parent_session):
                if found is None and sid:
                    found = by_native.get(sid)
            for pid in wk.ancestors:   # recorded at launch: still works after both processes have exited
                cand = by_pid.get(pid)
                if found is None and cand is not None and cand is not wk and (not cand.start or not wk.launched or cand.start <= wk.launched):
                    found = cand
            if found is None and wk.pid:
                pid = procs.get(wk.pid, (0,))[0]
                for _ in range(16):
                    if pid <= 1 or pid not in procs:
                        break
                    found = by_pid.get(pid)
                    if found is not None:
                        break
                    pid = procs[pid][0]
            if found is not None and found is not wk:
                parent_of[wk.name] = found.name
        if not parent_of:
            continue
        by_name = {wk.name: wk for wk in workers}
        merged_children: set[str] = set()
        for child_name, parent_name in list(parent_of.items()):
            parent, child = by_name.get(parent_name), by_name.get(child_name)
            if _co_is_own_native_row(parent, child) and not any(
                    other_parent == parent_name and other in merged_children for other, other_parent in parent_of.items()):
                # A dispatch worker's own native session, still running (its run record has no native id yet): one worker, not two.
                for field_name in ("execution_id", "account", "native_id", "parent_id", "root_id", "linkage", "outcome",
                                   "brief_text", "result_text", "purpose_source", "output_available"):
                    _co_fill_identity(parent, child, field_name)
                if child.model and parent.state in LIVE_STATES:
                    parent.model = child.model   # the session's own model name is more exact than the brief's alias
                merged_children.add(child_name)
        if merged_children:
            for name, parent_name in list(parent_of.items()):
                if parent_name in merged_children:
                    parent_of[name] = parent_of[parent_name]
            for name in merged_children:
                del parent_of[name]
            workers = [wk for wk in workers if wk.name not in merged_children]
            stage.snap.workers = workers
            if not parent_of:
                continue
        for wk in workers:   # a loop of parents is cut at the worker that closes it
            chain, cur = {wk.name}, parent_of.get(wk.name, "")
            while cur and cur not in chain:
                chain.add(cur)
                cur = parent_of.get(cur, "")
            wk.parent = parent_of.get(wk.name, "") if not cur else ""
        names = {wk.name: wk for wk in workers}
        for wk in workers:
            level, cur = 0, wk.parent
            while cur and level < 32:
                level += 1
                cur = names[cur].parent if cur in names else ""
            wk.level = level
        stage.snap.workers = nest_workers(workers)


def _co_adopt_by_session(stages: list[Stage]) -> list[Stage]:
    """A worker whose <name>.session names a live lead's session id belongs to that lead, whichever stage folder it was
    launched into. Only the exact session id matches (never cwd or time); execution rows are left alone. A row left with
    no workers and no live lead is dropped."""
    leads: dict[str, Stage] = {}
    for stage in stages:
        if not stage.execution_id and stage.session_id and stage.lead_state == "live":
            leads.setdefault(stage.session_id, stage)
    if not leads:
        return stages
    adopted: dict[str, list[Worker]] = {}   # lead session id -> workers it took in
    changed: set[int] = set()               # ids of rows that gave workers away
    for stage in stages:
        if stage.execution_id:
            continue
        kept: list[Worker] = []
        for wk in stage.snap.workers:
            lead = leads.get(wk.session) if wk.session else None
            if lead is None or lead is stage or any(o.name == wk.name and o.pid == wk.pid for o in lead.snap.workers):
                kept.append(wk)
                continue
            wk.home = wk.home or stage.dir or stage.path
            lead.snap.workers.append(wk)
            adopted.setdefault(wk.session, []).append(wk)
        if len(kept) != len(stage.snap.workers):
            stage.snap.workers = kept
            changed.add(id(stage))
    if not adopted and not changed:
        return stages
    for sid, moved in adopted.items():
        lead = leads[sid]
        newest = max(max(wk.start, wk.end or 0.0) for wk in moved)
        lead.snap.workers = _co_order_workers(lead.snap.workers)
        lead.live = sum(wk.state in LIVE_STATES for wk in lead.snap.workers)
        lead.solo = False
        lead.last_activity = max(lead.last_activity, newest)
        lead.activity = max(lead.activity, newest)
        lead.snap.groups = _co_groups(lead.snap.workers, lead.snap.milestones)
    out: list[Stage] = []
    for stage in stages:
        if id(stage) in changed:
            stage.live = sum(wk.state in LIVE_STATES for wk in stage.snap.workers)
            stage.snap.groups = _co_groups(stage.snap.workers, stage.snap.milestones)
            if not stage.snap.workers and stage.lead_state != "live":
                continue
        out.append(stage)
    return out


def _co_station_exec_key(stage: Stage) -> str:
    """Return the native execution key for a station row with a known lead session."""
    if stage.execution_id or not stage.session_id:
        return ""
    identity = {
        "claude": ("claude", "personal"),
        "claude work": ("claude", "work"),
        "codex": ("codex", "personal"),
        "t3 code": ("codex", "personal"),
        "opencode": ("opencode", "personal"),
        "cursor": ("cursor", "personal"),
        "openclaw": ("openclaw", "openclaw"),
    }.get(stage.lead_kind)
    return execution_key(*identity, stage.session_id) if identity else ""


def _co_run_record_workers(stages: list[Stage]) -> dict[str, tuple[Stage, str]]:
    """Native session id -> the first station whose <name>.run.json records it, with that run's worker name."""
    found: dict[str, tuple[Stage, str]] = {}
    for station in stages:
        if not station.dir or not station.snap.workers:
            continue
        for directory in _co_run_record_dirs(station.dir):
            for native_id, data in _co_load_run_records(directory).items():
                if native_id not in found:
                    found[native_id] = (station, str(data.get("name") or ""))
    return found


_co_unlinked_run_cache: dict[str, tuple[int, list[dict]]] = {}


def _co_unlinked_run_records(stage: str) -> list[dict]:
    """Dispatch records that name no native session yet (an OpenCode run learns its session id after it starts), cached
    by the directory mtime. Each keeps the cwd, start time and launching session they were written with."""
    stamp = _co_stat(stage)
    if stamp is None:
        _co_unlinked_run_cache.pop(stage, None)
        return []
    cached = _co_unlinked_run_cache.get(stage)
    if cached is not None and cached[0] == stamp.st_mtime_ns:
        return cached[1]
    try:
        with os.scandir(stage) as entries:
            paths = sorted(entry.path for entry in entries if entry.name.endswith(".run.json"))
    except OSError:
        paths = []
    found: list[dict] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            continue
        if (not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1
                or not isinstance(data.get("harness"), str) or not data["harness"]
                or not isinstance(data.get("cwd"), str) or not data["cwd"]
                or type(data.get("started")) not in (int, float)
                or not isinstance(data.get("lead_session"), str) or not data["lead_session"]):
            continue
        if isinstance(data.get("native_id"), str) and data["native_id"]:
            continue   # a run with a native id is linked by that id, not by time
        found.append(data)
    _co_unlinked_run_cache[stage] = (stamp.st_mtime_ns, found)
    return found


def _co_link_run_record(execution: Stage, stations: dict[str, Stage]) -> str:
    """The station execution key of the session that launched a worker run inside <project>/.stage, from a dispatch
    record that names its cwd and start but has no native id yet. "" when no record or station matches."""
    project = _co_stage_project_root(execution.cwd)
    started = execution.lead_since or min((worker.start for worker in execution.snap.workers if worker.start), default=0.0)
    if not project or not started:
        return ""
    by_session = {station.session_id: key for key, station in stations.items() if station.session_id}
    matches: set[str] = set()
    for directory in _co_run_record_dirs(os.path.join(project, ".stage")):
        for data in _co_unlinked_run_records(directory):
            if os.path.normpath(data["cwd"]) != os.path.normpath(execution.cwd):
                continue
            if abs(float(data["started"]) - started) > _co_dup_window_s:
                continue
            key = by_session.get(data["lead_session"])
            if key:
                matches.add(key)
    return next(iter(matches)) if len(matches) == 1 else ""


def _co_attach_executions(stages: list[Stage]) -> list[Stage]:
    """Attach native execution members to exact live station identities and tag leftovers."""
    stations: dict[str, Stage] = {}
    for stage in stages:
        key = _co_station_exec_key(stage)
        if key:
            stations.setdefault(key, stage)

    moved: dict[int, list[Worker]] = {}
    changed: set[int] = set()
    dropped: set[int] = set()
    # A worker run launched in <project>/.stage belongs to the session that started it, even before dispatch has
    # learned its native id: the record's cwd and start time find the launching station.
    for execution in stages:
        if not execution.execution_id or execution.parent_id or not execution.cwd:
            continue
        if execution.root_id and execution.root_id in stations:
            continue
        key = _co_link_run_record(execution, stations)
        if key and key != execution.execution_id:
            execution.parent_id = key
            execution.linkage = "run-record"
    folder_targets: Counter[str] = Counter(os.path.normpath(execution.cwd) for execution in stages
                                            if execution.execution_id and execution.cwd and len(execution.snap.workers) == 1)
    # A native run in .stage/<stage>/<worker> is the same worker the stage files show.
    # Keep the stage worker's name and process details; add its native identity and owner.
    for execution in stages:
        if not execution.execution_id or not execution.cwd or len(execution.snap.workers) != 1:
            continue
        if folder_targets[os.path.normpath(execution.cwd)] != 1:
            continue
        native_worker = execution.snap.workers[0]
        for station in stages:
            if not station.dir:
                continue
            existing = next((worker for worker in station.snap.workers
                             if os.path.normpath(os.path.join(station.dir, worker.name)) == os.path.normpath(execution.cwd)), None)
            if existing is None:
                continue
            for field_name in ("execution_id", "account", "native_id", "parent_id", "root_id", "linkage", "outcome",
                               "brief_text", "result_text", "purpose_source", "output_available"):
                value = getattr(native_worker, field_name)
                if value:
                    setattr(existing, field_name, value)
            if execution.root_id in stations and stations[execution.root_id].session_id:
                existing.session = stations[execution.root_id].session_id
            execution.snap.workers.clear()
            dropped.add(id(execution))
            break
    # Dispatch can run a worker in the repo instead of its stage folder: the stage's <name>.run.json names its
    # native session, so that native row is the same worker and not a second one.
    run_targets = _co_run_record_workers(stages)   # one pass: the per-pair scan grows with every kept stage
    for execution in stages:
        if not execution.execution_id or id(execution) in dropped:
            continue
        merged = False
        for native_worker in list(execution.snap.workers):
            if not native_worker.native_id:
                continue
            target = run_targets.get(native_worker.native_id)
            existing = (next((worker for worker in target[0].snap.workers if worker.name == target[1]), None)
                        if target is not None else None)
            if existing is not None:
                for field_name in ("execution_id", "account", "native_id", "parent_id", "root_id", "linkage", "outcome",
                                   "brief_text", "result_text", "purpose_source", "output_available"):
                    value = getattr(native_worker, field_name)
                    if value:
                        setattr(existing, field_name, value)
                execution.snap.workers.remove(native_worker)
                merged = True
                break
        if merged and not execution.snap.workers:
            dropped.add(id(execution))
    for stage in stages:
        if not stage.execution_id:
            continue
        if stage.snap.workers:
            target = stations.get(stage.root_id) if stage.root_id else None
            if target is None and stage.parent_id:
                target = stations.get(stage.parent_id)
            if target is not None:
                target.snap.workers.extend(stage.snap.workers)
                moved.setdefault(id(target), []).extend(stage.snap.workers)
                stage.snap.workers = []
                changed.add(id(stage))
        target = stations.get(stage.execution_id)
        if target is not None:
            if not target.goal:
                target.goal = stage.goal
            if not target.brief_text:
                target.brief_text = list(stage.brief_text)
            dropped.add(id(stage))

    # One worker per native run: a DB row and the execution-registry row of the same run fold into one, keeping the
    # DB row's log and steps and the registry row's goal, brief and identity.
    for stage in stages:
        if stage.execution_id or len(stage.snap.workers) < 2:
            continue
        folded = _co_fold_duplicate_workers(stage.snap.workers, _co_stage_run_records(stage.dir))
        if len(folded) != len(stage.snap.workers):
            stage.snap.workers = _co_order_workers(folded)
            stage.live = sum(worker.state in LIVE_STATES for worker in stage.snap.workers)
            stage.snap.groups = _co_groups(stage.snap.workers, stage.snap.milestones)
            changed.add(id(stage))

    for stage in stages:
        if id(stage) in moved:
            newest = max((max(worker.start, worker.end or 0.0) for worker in moved[id(stage)]), default=0.0)
            stage.snap.workers = _co_order_workers(stage.snap.workers)
            stage.live = sum(worker.state in LIVE_STATES for worker in stage.snap.workers)
            stage.solo = False
            stage.last_activity = max(stage.last_activity, newest)
            stage.activity = max(stage.activity, newest)
            stage.snap.groups = _co_groups(stage.snap.workers, stage.snap.milestones)
        elif id(stage) in changed:
            stage.live = sum(worker.state in LIVE_STATES for worker in stage.snap.workers)
            stage.snap.groups = _co_groups(stage.snap.workers, stage.snap.milestones)

    out: list[Stage] = []
    for stage in stages:
        if id(stage) in dropped:
            continue
        if id(stage) in changed and not stage.snap.workers and stage.lead_state != "live":
            continue
        if stage.execution_id:
            harness = stage.lead_kind
            tag = _co_execution_tags.get((harness, stage.account))
            if not tag and not harness and stage.native_id:
                identity = next((pair for pair in _co_execution_tags
                                 if pair[1] == stage.account
                                 and execution_key(pair[0], pair[1], stage.native_id) == stage.execution_id), None)
                if identity:
                    harness = stage.lead_kind = identity[0]
                    tag = _co_execution_tags[identity]
            if tag:
                stage.tag = _co_agent_label() if harness == "openclaw" else tag
        out.append(stage)
    return out


def _co_dedupe_sessions(stages: list[Stage]) -> list[Stage]:
    """One row for each known session id. The row that has the live lead wins (else a real stage row, then one with workers),
    and the workers of the session's other rows (launched from another project's stage dir) move into it, each keeping
    `home`, the stage dir it really lives in."""
    groups: dict[str, list[Stage]] = {}
    empty: list[Stage] = []
    for stage in stages:
        (groups.setdefault(stage.session_id, []) if stage.session_id else empty).append(stage)
    out: list[Stage] = []
    for rows in groups.values():
        win = max(rows, key=lambda r: (r.lead_kind == "opencode" and r.lead_state == "background",
                                     bool(r.dir),
                                     r.lead_pid is not None,
                                     bool(r.snap.workers),
                                     not _co_is_session_split_row(r.path),
                                     not r.path.startswith("execution:")))
        # two live leads that resolve to one session id (OpenCode: two terminals in one folder) are two sessions, not one
        own = {r.lead_pid: r for r in rows if r.lead_pid is not None and r is not win}
        for pid, extra in own.items():
            if pid != win.lead_pid:
                out.append(extra)
        rows = [r for r in rows if r is win or r.lead_pid is None]
        for other in rows:
            if other is win:
                continue
            _co_merge_stage_fields(win, other)
            if win.lead_pid is None and not (win.lead_kind == "opencode" and win.lead_state == "background"):
                continue
            have = {w.name for w in win.snap.workers}
            for wk in other.snap.workers:
                if wk.name in have:      # the same worker seen from two stage dirs counts once
                    continue
                wk.home = wk.home or other.dir or other.path
                win.snap.workers.append(wk)
                win.live += wk.state in LIVE_STATES
            win.snap.reviews.extend(other.snap.reviews)
            if not win.snap.milestones:
                win.snap.milestones = list(other.snap.milestones)
            win.last_activity = max(win.last_activity, other.last_activity)
            win.activity = max(win.activity, other.activity)
            win.solo = win.solo and not other.snap.workers
        if win.lead_pid is not None and win.live and win.state == "idle":
            win.state = "active"
        if win.snap.workers:
            win.snap.groups = _co_groups(win.snap.workers, win.snap.milestones)
            win.solo = False
        out.append(win)   # with no live lead anywhere the best row stands alone, as before
    return out + empty


def _co_closed_aged_out(stage: Stage, now: float) -> bool:
    """A closed session with nothing running and no activity for _co_loose_keep_s. Unknown activity (0) counts as
    old. Only a hard cost bound: _co_ops_windows makes the real age decision after work state is attached."""
    if stage.state != "closed" or stage.live:
        return False
    return now - max(stage.activity, stage.last_activity) > _co_loose_keep_s


def _co_order_stages(stages: list[Stage]) -> None:
    section = {"active": 0, "idle": 1, "closed": 2}
    stages.sort(key=lambda stage: (section.get(stage.state, 2), -stage.activity, stage.label, stage.path))


def openclaw_peers() -> tuple[str, ...]:
    """Configured private Telegram peers, empty by default."""
    return tuple(peer.strip() for peer in os.environ.get("STATION_OPENCLAW_PEERS", "").split(",") if peer.strip())


OPENCLAW_OWN_PEERS = openclaw_peers()


OPENCLAW_KEEP_S = 24 * 3600    # an OpenClaw session quiet this long is hidden, as the older sessions of any station are
_co_openclaw_turns = 50            # turns kept on a Stage row (model.Stage.turns); turns before 2026-10-10 do not exist in the DB


def _co_openclaw_worker(key: str, label: str, entry: dict, created_ms: float, last_ms: float, now: float,
                        parent: str = "") -> Worker:
    """An openclaw subagent session as a worker of the session that spawned it."""
    def seconds(value: object, default: float) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value) / 1000.0 if value > 100_000_000_000 else float(value)
        return default
    start = seconds(entry.get("startedAt") or entry.get("createdAt"), seconds(created_ms, 0.0))
    ended = seconds(entry.get("endedAt"), 0.0)
    last = seconds(entry.get("lastActivityAt") or last_ms, start)
    status = str(entry.get("status", "")).lower()
    if status in ("failed", "error", "errored", "killed", "timeout", "timed_out"):
        state = FAILED
    elif status == "done" or ended:
        state = DONE
    elif now - last <= _co_child_live_s:
        state = RUNNING
    else:
        state = NOREPORT
    model = str(entry.get("model", ""))
    live = state == RUNNING
    owner = parent or str(entry.get("parentSessionKey", "") or entry.get("spawnedBy", ""))
    return Worker(name=_co_short_name(label) or key.rsplit(":", 1)[-1][:8], state=state, title=label, model=model,
                  family=family_of(model), start=start, end=None if live else (ended or last), quiet_s=max(0.0, now - last),
                  session=owner, native_id=key, linkage="native")


def _co_openclaw_jobs(now: float, all_history: bool, home: str) -> dict[str, list[Worker]]:
    """Background exec jobs with an explicit OpenClaw requester session."""
    db = os.path.join(home, ".openclaw", "state", "openclaw.sqlite")
    if not os.path.exists(db):
        return {}
    try:
        conn = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=1.0)
        try:
            rows = conn.execute(
                "SELECT task_id, requester_session_key, parent_task_id, status, created_at, started_at, ended_at, last_event_at "
                "FROM task_runs WHERE agent_id = ? AND runtime = 'cli' AND task_kind = 'exec' "
                "AND requester_session_key LIKE ?",
                (OPENCLAW_AGENT, f"agent:{OPENCLAW_AGENT}:%")).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return {}

    def stamp(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return 0.0
        return float(value) / 1000.0 if value > 100_000_000_000 else float(value)

    jobs: dict[str, list[Worker]] = {}
    for task_id, requester, parent_task, status, created, started, ended, last_event in rows:
        if not isinstance(task_id, str) or not task_id or not isinstance(requester, str) or not requester:
            continue
        start = stamp(started) or stamp(created)
        last = max(stamp(last_event), stamp(ended), start)
        if not last or (not all_history and now - last > OPENCLAW_KEEP_S):
            continue
        status = str(status or "").lower()
        if status in ("failed", "cancelled", "canceled", "error", "timed_out"):
            state = FAILED
        elif status in ("succeeded", "completed", "done"):
            state = DONE
        elif status in ("running", "active") and now - last <= _co_child_live_s:
            state = RUNNING
        else:
            state = NOREPORT
        name = f"job {task_id[:12]}"
        jobs.setdefault(requester, []).append(Worker(
            name=name, state=state, title=name, start=start, end=None if state == RUNNING else (stamp(ended) or last),
            quiet_s=max(0.0, now - last), native_id=task_id,
            session=parent_task if isinstance(parent_task, str) and parent_task else requester,
            parent_session=requester))
    return jobs


_co_openclaw_cron_cache: dict[str, tuple[object, dict[str, str], float]] = {}
_co_openclaw_cron_ttl_s = 60.0   # a WAL write can leave the main file's stamp alone: re-read at least this often


def _co_file_stamp(path: str) -> tuple[float, int] | None:
    """(mtime, size) of a file; None when it does not exist."""
    stamp = _co_stat(path)
    return (stamp.st_mtime, stamp.st_size) if stamp is not None else None


def _co_openclaw_cron_names(home: str) -> dict[str, str]:
    """Cron job id -> job name from OpenClaw's state database (read-only); {} when it cannot be read.

    Cached while the database and its ``-wal`` keep their mtime and size, and for 60 s at most, so a
    rename that only reaches the WAL still shows up.
    """
    db = os.path.join(home, ".openclaw", "state", "openclaw.sqlite")
    key = (_co_file_stamp(db), _co_file_stamp(db + "-wal"))
    if key[0] is None:
        return {}
    now = time.monotonic()
    cached = _co_openclaw_cron_cache.get(db)
    if cached is not None and cached[0] == key and now - cached[2] < _co_openclaw_cron_ttl_s:
        return cached[1]
    names: dict[str, str] = {}
    try:
        conn = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=1.0)
        try:
            for job_id, name in conn.execute("SELECT job_id, name FROM cron_jobs"):
                if isinstance(job_id, str) and job_id and isinstance(name, str) and name:
                    names[job_id] = name
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return {}
    _co_openclaw_cron_cache[db] = (key, names, now)
    return names


def _co_openclaw_display(key: str, label: str, kind: str, cron_names: dict[str, str]) -> str:
    """The session row's name from its key: `telegram · direct`, `telegram · openclaw direct`, `dashboard · <label>`,
    `main`, the cron job's name, else the label a subagent-origin row keeps."""
    if ":telegram:" in key:
        return "telegram · openclaw direct" if ":telegram:openclaw:direct:" in key else "telegram · direct"
    if key.endswith(":main"):
        return "main"
    if ":dashboard:" in key:
        name = label.removeprefix("dashboard:").strip()
        return f"dashboard · {name}" if name else "dashboard"
    if kind == "cron" or ":cron:" in key:
        return cron_names.get(key.rsplit(":", 1)[-1]) or label.removeprefix("Automation:").strip() or "cron"
    return label


def _co_openclaw_reader(db: str) -> Reader | None:
    """The turn/vitals reader for one openclaw snapshot; None when the reader cannot be made."""
    try:
        return Reader(db)
    except (OSError, sqlite3.Error, ValueError):
        return None


def _co_openclaw_fill(stage: Stage, reader: Reader | None, now: float) -> None:
    """Turns, vitals and the lead metric columns of one openclaw session row, from its turn data.

    Tokens, cost and tool counts cover the turns read (the newest 200), which is all the database keeps;
    a value the turns do not carry stays None, never 0.
    """
    if reader is None:
        return
    turns = reader.turns(stage.session_id, 0.0)
    vitals = reader.vitals(stage.session_id)
    stage.vitals = vitals
    stage.turns = turns[-_co_openclaw_turns:]
    if stage.lead_ctx_window is None and vitals is not None:
        stage.lead_ctx_window = vitals.ctx_budget
    if not turns:
        return
    spent = [(turn.tokens_in or 0) + (turn.tokens_out or 0) for turn in turns
             if turn.tokens_in is not None or turn.tokens_out is not None]
    stage.lead_tokens = sum(spent) if spent else None
    costs = [turn.cost for turn in turns if turn.cost is not None]
    stage.lead_cost = (vitals.cost if vitals is not None and vitals.cost is not None
                       else sum(costs) if costs else None)
    stage.lead_tools = sum(len(turn.calls) for turn in turns)
    stage.lead_tool_pulses = tool_pulses(turns, now)


def _co_openclaw_stages(now: float, all_history: bool, home: str) -> list[Stage]:
    """your own OpenClaw sessions as rows: the Telegram sessions (`agent:openclaw:main`, and the direct chat of a peer in
    OPENCLAW_OWN_PEERS), the dashboard chats (`agent:openclaw:dashboard:*`) and the cron automations (`cluster`). A subagent is
    a worker of the session that spawned it, never a row of its own; a session that spawned one shows even if it is a
    run/test session. Read-only; any failure gives no rows."""
    db = os.path.join(home, ".openclaw", "agents", OPENCLAW_AGENT, "agent", "openclaw-agent.sqlite")
    if not os.path.exists(db):
        return []
    jobs = _co_openclaw_jobs(now, all_history, home)
    rows: list[tuple] = []
    try:
        conn = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=1.0)
        try:
            rows = conn.execute(
                "SELECT session_key, display_name, label, COALESCE(last_activity_at, updated_at), created_at, entry_json FROM session_nodes "
                "WHERE session_key = ? OR session_key LIKE ? OR session_key LIKE ? OR session_key LIKE ? OR session_key LIKE ?",
                (f"agent:{OPENCLAW_AGENT}:main", f"agent:{OPENCLAW_AGENT}:dashboard:%", f"agent:{OPENCLAW_AGENT}:subagent:%",
                 f"agent:{OPENCLAW_AGENT}:cron:%", f"agent:{OPENCLAW_AGENT}:telegram:%direct:%")).fetchall()
            try:   # a subagent's parent is also a column; an older database may not have it
                parents = {key: parent for key, parent in conn.execute(
                    "SELECT session_key, parent_session_key FROM session_nodes WHERE session_key LIKE ?",
                    (f"agent:{OPENCLAW_AGENT}:subagent:%",)) if parent}
            except sqlite3.Error:
                parents = {}
            have = {row[0] for row in rows}
            wanted: set[str] = set(jobs) - have
            for row in rows:
                try:
                    parent = str((json.loads(row[5]) or {}).get("parentSessionKey", "")) if ":subagent:" in row[0] and row[5] else ""
                except (ValueError, AttributeError):
                    parent = ""
                parent = parent or parents.get(row[0], "")
                if parent and parent not in have:
                    wanted.add(parent)
            for key in sorted(wanted):   # the session a subagent came from, even a one-off run
                rows += conn.execute(
                    "SELECT session_key, display_name, label, COALESCE(last_activity_at, updated_at), created_at, entry_json FROM session_nodes "
                    "WHERE session_key = ?", (key,)).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []
    root = os.path.join(home, ".openclaw", "agents", OPENCLAW_AGENT)
    entries: dict[str, dict] = {}
    for row in rows:
        try:
            parsed = json.loads(row[5]) if row[5] else {}
        except ValueError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        if not parsed.get("parentSessionKey") and parents.get(row[0]):
            parsed = {**parsed, "parentSessionKey": parents[row[0]]}
        entries[row[0]] = parsed

    def owner(key: str) -> str:
        """The session a subagent finally belongs to: its parent, or that parent's parent when it is a subagent too."""
        for _ in range(8):
            parent = str(entries.get(key, {}).get("parentSessionKey", ""))
            if not parent or parent not in entries:
                return parent
            if ":subagent:" not in parent:
                return parent
            key = parent
        return ""

    def stamp_s(value: object) -> float:
        """Epoch seconds from a stored stamp (seconds or milliseconds); 0.0 when absent or not a number."""
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return 0.0
        return float(value) / 1000.0 if value > 100_000_000_000 else float(value)

    kids: dict[str, list[Worker]] = {}
    for key, display, label, last_ms, created_ms, _ in rows:
        if ":subagent:" in key:
            worker = _co_openclaw_worker(key, str(label or display or ""), entries[key], created_ms or 0.0, last_ms or 0.0, now)
            mates = kids.setdefault(owner(key), [])
            if any(w.name == worker.name for w in mates):   # two subagents with one label stay two rows
                worker = replace(worker, name=f"{worker.name} {key[-4:]}")
            mates.append(worker)
    for requester, workers in jobs.items():
        if requester not in entries:
            continue
        parent = owner(requester) if ":subagent:" in requester else requester
        if parent in entries:
            kids.setdefault(parent, []).extend(workers)
    stages: list[Stage] = []
    cron_names = _co_openclaw_cron_names(home)
    reader = _co_openclaw_reader(db)

    def add(key: str, name: str, kind: str, entry: dict, created_s: float, stamp_s_: float, mine: list[Worker], direct: bool) -> None:
        activity = stamp_s_
        for worker in mine:
            activity = max(activity, worker.start, worker.end or 0.0)
        if activity <= 0 or (not all_history and now - activity > OPENCLAW_KEEP_S):
            return
        live = sum(1 for worker in mine if worker.state in LIVE_STATES)
        age = now - activity
        # the same 15-min idle and 2 h closed windows as every other session (see _co_ops_windows)
        state = "active" if live or age <= ACTIVE_S else "idle" if age <= IDLE_LIST_S else "closed"
        if kind == "cron":
            name = name.removeprefix("Automation:").strip()
        elif key.endswith(":main") or direct:
            name = "telegram"
        ctx = entry.get("totalTokens")
        ordered = _co_order_workers(mine)
        stage = Stage(
            path=f"{root}/.stage#{key.split(':', 2)[-1].replace(':', '-')}", label=_co_agent_label(), dir=root,
            snap=Snapshot(title=_co_agent_label(), now=now, session_start=created_s, all_history=all_history,
                          workers=ordered, groups=_co_groups(ordered, [])),
            live=live, last_activity=activity, lead_state="gone" if state == "closed" else "live", lead_kind="openclaw",
            lead_ctx=int(ctx) if isinstance(ctx, (int, float)) and not isinstance(ctx, bool) else None,
            solo=not mine, session_id=key, session_name=_co_short_name(name),
            display_name=_co_openclaw_display(key, name, kind, cron_names),
            tag=_co_agent_label(), lead_model=str(entry.get("model", "")),
            state=state, activity=activity, cwd=root,
            cluster="cron" if kind == "cron" else "",
            doing=[] if mine else _co_openclaw_doing(db, key),
        )
        _co_openclaw_fill(stage, reader, now)
        stages.append(stage)

    try:
        placed: set[str] = set()
        for key, display, label, last_ms, created_ms, _ in rows:
            if ":subagent:" in key:
                continue
            direct = ":direct:" in key and ":telegram:" in key
            mine = kids.get(key, [])
            if direct and key.rsplit(":", 1)[-1] not in OPENCLAW_OWN_PEERS and not mine:
                continue
            placed.add(key)
            add(key, str(label or display or ""), key.split(":")[2] if key.count(":") >= 2 else "", entries[key],
                stamp_s(created_ms), stamp_s(last_ms) or stamp_s(created_ms), mine, direct)
        for owner_key, mine in kids.items():   # subagents whose spawning session has no row (pruned, or a loop): still shown
            if owner_key in placed:
                continue
            first = min(mine, key=lambda w: w.start)
            add(f"agent:{OPENCLAW_AGENT}:orphans:{owner_key.rsplit(':', 1)[-1] or 'none'}", "subagents", "orphans", {},
                first.start, 0.0, mine, False)
    finally:
        if reader is not None:
            reader.close()
    return stages


def _co_openclaw_launches(procs: dict[int, tuple[int, int, int, int, int]]) -> dict[str, tuple[str, bool]]:
    """openclaw sessions a Claude session started with ``openclaw agent --session-key K``: session key -> (Claude session id, run alive).

    The run's environment carries CLAUDE_CODE_SESSION_ID (Claude Code's bash tool sets it), so the launcher is known
    while the run lives; the caller remembers it after the run exits."""
    out: dict[str, tuple[str, bool]] = {}
    for pid in procs:
        argv = _co_argv(pid)
        if "agent" not in argv[1:6] or "--session-key" not in " ".join(argv) or not any("openclaw" in tok for tok in argv[:3]):
            continue
        key = agent = ""
        for index, tok in enumerate(argv):
            if tok == "--session-key" and index + 1 < len(argv):
                key = argv[index + 1]
            elif tok.startswith("--session-key="):
                key = tok.split("=", 1)[1]
            elif tok == "--agent" and index + 1 < len(argv):
                agent = argv[index + 1]
        sid = _co_environ(pid).get("CLAUDE_CODE_SESSION_ID", "")
        if not key or not sid:
            continue
        if not key.startswith("agent:"):
            key = f"agent:{agent or OPENCLAW_AGENT}:{key}"
        out[key] = (sid, True)
    return out


class GlobalCollector:
    """Every stage dir under the roots: one Collector per stage, and one quota reader for all."""

    def __init__(self, roots: list[str], *, ledger: str | None = None, quota: bool = True,
                 openrouter_only: bool = False, dismissed: str | None = None, openclaw_home: str | None = None,
                 registry_root: str | None = None, overrides: str | None = None,
                 headless_cache: str | None = None) -> None:
        self._co_roots = list(roots)
        self._co_openclaw_home = openclaw_home if OPENCLAW_AGENT else None   # explicit config enables this integration
        self._co_registry_explicit = registry_root is not None or bool(os.environ.get("STATUS_REGISTRY_ROOT"))
        self._co_registry_root = os.path.expanduser(registry_root or default_registry_root())
        self._co_quick = False   # set for one snapshot: skip the slow native transcript scan so the first paint is fast
        self._co_execution_scanned_at: float | None = None
        self._co_execution_evidence: dict[str, ExecutionEvidence] = {}
        self._co_execution_diagnostics: list[str] = []
        self._co_headless_links: dict[str, tuple[str, str]] = {}  # native execution -> Claude session and account
        self._co_child_sessions: dict[int, str] = {}
        self._co_stage_lead_sessions: dict[int, tuple[str, int | None, float]] = {}
        self._co_headless_ids: set[str] = set()   # runs once seen as headless: still workers after the process exits
        cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        self._co_headless_path = headless_cache or os.path.join(cache_home, "station", "headless.json")
        self._co_headless_order: list[str] = []
        self._co_load_headless()
        self._co_ledger = os.path.expanduser(ledger or home_path("ledger/runs.jsonl"))
        self._co_dismissed_path = os.path.expanduser(dismissed or _co_dismissed_file)
        self._co_overrides_path = os.path.expanduser(overrides or _co_overrides_file)
        self._co_found: list[tuple[str, str]] = []
        self._co_collectors: dict[str, Collector] = {}
        self._co_last_lead_since: dict[str, float] = {}
        self._co_last_stages: list[Stage] = []
        self._co_last_global: GlobalSnapshot | None = None
        self._co_scanned_at: float | None = None
        self._co_clk = _co_clk_tck()
        self._co_boot = _co_boot_time()
        # per snapshot: the /proc scan and the ledger parse each run at most once, and only if needed
        self._co_procs: dict[int, tuple[int, int, int, int, int]] | None = None
        self._co_rows: list[tuple[float, str, str]] | None = None
        self._co_oc_runs: dict[str, list[tuple[int, str, str]]] | None = None
        self._co_oc_children: dict[int, list[int]] = {}
        self._co_orphans: dict[str, tuple[str, _co_Lead]] = {}
        self._co_oc_launchers: dict[str, tuple[str, float]] = {}
        self._co_quota = Collector("", quota=quota, openrouter_only=openrouter_only)

    def _co_load_headless(self) -> None:
        try:
            with open(self._co_headless_path, encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        ids = data.get("ids", [])
        links = data.get("links", {})
        if not isinstance(ids, list) or not isinstance(links, dict):
            return
        for key in ids[-_co_headless_max:]:
            if isinstance(key, str) and key:
                self._co_headless_ids.add(key)
                self._co_headless_order.append(key)
                link = links.get(key)
                if (isinstance(link, list) and len(link) == 2 and
                        all(isinstance(value, str) for value in link)):
                    self._co_headless_links[key] = (link[0], link[1])

    def _co_save_headless(self) -> None:
        path = self._co_headless_path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            temp = f"{path}.{os.getpid()}.tmp"
            with open(temp, "w", encoding="utf-8") as stream:
                json.dump({"ids": self._co_headless_order, "links": self._co_headless_links}, stream)
            os.replace(temp, path)
        except OSError:
            pass

    def _co_remember_headless(self, key: str, link: tuple[str, str] | None) -> None:
        changed = key not in self._co_headless_ids or self._co_headless_links.get(key) != link
        self._co_headless_ids.add(key)
        if key in self._co_headless_order:
            self._co_headless_order.remove(key)
        self._co_headless_order.append(key)
        if link is not None:
            self._co_headless_links[key] = link
        else:
            self._co_headless_links.pop(key, None)
        while len(self._co_headless_order) > _co_headless_max:
            oldest = self._co_headless_order.pop(0)
            self._co_headless_ids.discard(oldest)
            self._co_headless_links.pop(oldest, None)
            changed = True
        if changed:
            self._co_save_headless()

    def _co_executions(self, now: float) -> list[ExecutionEvidence]:
        mono = time.monotonic()
        if self._co_execution_scanned_at is not None and mono - self._co_execution_scanned_at < RESCAN_S:
            return list(self._co_execution_evidence.values())
        quick = self._co_quick and mono - (_co_native_cache.get(os.path.expanduser("~")) or (-1e9,))[0] >= RESCAN_S
        if not quick:
            self._co_execution_scanned_at = mono
        home_root = os.path.normpath(os.path.expanduser("~"))
        roots_touch_home = any(
            os.path.normpath(os.path.expanduser(root)) == home_root
            or os.path.normpath(os.path.expanduser(root)).startswith(home_root + os.sep)
            or home_root.startswith(os.path.normpath(os.path.expanduser(root)).rstrip(os.sep) + os.sep)
            for root in self._co_roots
        )
        registry = (_co_guard(read_registry, self._co_registry_root)
                    if self._co_registry_explicit or roots_touch_home else RegistrySnapshot())
        if not isinstance(registry, RegistrySnapshot):
            registry = RegistrySnapshot()
        home = os.path.expanduser("~")
        cached_native = _co_native_cache.get(home)
        if cached_native is not None and mono - cached_native[0] < RESCAN_S:
            native = list(cached_native[1])
        elif quick:
            native = []   # first paint: the full scan follows at once and fills these in
        else:
            native = []
            for harness in ("codex", "t3", "claude", "opencode", "cursor"):
                found = _co_guard(read_native_executions, harness, home, now)
                if isinstance(found, list):
                    native.extend(item for item in found if isinstance(item, ExecutionEvidence))
            _co_native_cache[home] = (mono, tuple(native))
        # Roots constrain discovery only. Ownership is still resolved exclusively from execution IDs,
        # explicit parent links, or exact PID/start identity.
        # both sides resolved: roots arrive resolved, and a stored cwd may still hold a symlink
        allowed = [os.path.realpath(os.path.expanduser(root)) for root in self._co_roots]

        def in_scope(rec: ExecutionRecord) -> bool:
            raw = os.path.expanduser(rec.cwd) if rec.cwd else ""
            cwd = os.path.realpath(raw) if os.path.isabs(raw) else ""   # relative: never under a root
            return bool(cwd and any(cwd == root or cwd.startswith(root.rstrip(os.sep) + os.sep) for root in allowed))

        # Registry records get the same roots scope as native ones; a record with no cwd, or a non-absolute cwd,
        # cannot be placed under a root, so it stays.
        registry = replace(registry, records=[
            rec for rec in registry.records
            if not rec.cwd or not os.path.isabs(os.path.expanduser(rec.cwd)) or in_scope(rec)])
        accepted = {item.record.execution_id for item in native if in_scope(item.record)}
        changed = True
        while changed:
            changed = False
            for item in native:
                if item.record.execution_id not in accepted and item.record.parent_id in accepted:
                    accepted.add(item.record.execution_id)
                    changed = True
        native = [item for item in native if item.record.execution_id in accepted]
        items, diagnostics = reconcile_executions(registry, native, self._co_proc_table(), now)
        self._co_link_headless(items)
        self._co_execution_evidence = {item.record.execution_id: item for item in items}
        self._co_execution_diagnostics = diagnostics
        return items

    def _co_load_run_records(self) -> None:
        self._co_run_records: dict[str, dict] = {}
        stages = {path for path, _ in self._co_found}
        for stage in sorted(stages):
            for directory in _co_run_record_dirs(stage):
                self._co_run_records.update(_co_load_run_records(directory))

    def _co_link_headless(self, items: list[ExecutionEvidence]) -> None:
        """Join a live headless process to one native run by exact cwd and close start time.

        Only an unambiguous process/run pair is used; environment or live ancestry supplies
        ownership. Dispatch records also recover runs never seen live by this collector.
        """
        procs = self._co_proc_table()
        self._co_stage_lead_sessions = {
            pid: value for pid, value in self._co_stage_lead_sessions.items()
            if pid in procs and (value[1] == procs[pid][3] if value[1] is not None
                                 else value[2] >= self._co_boot + procs[pid][3] / self._co_clk)
        }
        home = os.path.expanduser("~")
        runs: list[tuple[int, str, str, float, dict[str, str]]] = []
        leads = {lead.pid: lead for lead in _co_lead_scan(
            procs, _co_argv, _co_cwd, home, self._co_clk, self._co_boot, _co_environ)
                 if lead.state == "live"}
        parents: dict[tuple[str, str], set[str]] = {}
        for item in items:
            rec = item.record
            if rec.native_id:
                parents.setdefault((rec.harness, rec.native_id), set()).add(rec.execution_id)
        for stage in self._co_last_stages:
            key = _co_station_exec_key(stage)
            if key:
                parents.setdefault((stage.lead_kind.split()[0], stage.session_id), set()).add(key)

        def parent_key(harness: str, sid: str, lead_pid: int | None = None) -> str:
            known = parents.get((harness, sid), set())
            if len(known) == 1:
                return next(iter(known))
            config = _co_environ(lead_pid).get("CLAUDE_CONFIG_DIR", "") if lead_pid in procs else ""
            account = ("work" if harness == "claude" and
                       "work" in os.path.basename(config).lower() else "personal")
            return execution_key(harness, account, sid)

        ancestry: dict[int, tuple[str, str]] = {}
        self._co_child_sessions = {}
        for pid, info in procs.items():
            argv = _co_argv(pid)
            env = _co_environ(pid) if argv else {}
            kind = _co_headless_kind(argv, environ=env, home=home)
            if not kind:
                continue
            sid = env.get("CLAUDE_CODE_SESSION_ID", "")
            lead_pid = _co_safe_pid(env.get("CLAUDE_PID", ""))
            if sid and lead_pid in procs:
                prior = self._co_child_sessions.get(lead_pid)
                if prior is None or prior == sid:
                    self._co_child_sessions[lead_pid] = sid
                else:
                    self._co_child_sessions.pop(lead_pid, None)
            if not sid and not env.get("STATUS_PARENT_SESSION"):
                child, parent, seen = pid, info[0], {pid}
                while parent in procs and parent not in seen:
                    seen.add(parent)
                    lead = leads.get(parent)
                    if lead is not None:
                        config = _co_environ(parent).get("CLAUDE_CONFIG_DIR", "")
                        account = "work" if "work" in os.path.basename(config).lower() else "personal"
                        child_sid = ""
                        if lead.kind.startswith("claude"):
                            sidecar = _co_claude_sidecar(config or os.path.join(home, ".claude"), child, "")
                            child_sid = sidecar.get("sessionId", "")
                            if isinstance(child_sid, str) and child_sid:
                                self._co_child_sessions[parent] = child_sid
                        meta = _co_lead_session("", lead, self._co_child_session(parent))
                        parent_sid = meta.get("session_id")
                        if isinstance(parent_sid, str) and parent_sid:
                            ancestry[pid] = (execution_key(lead.kind.split()[0], account, parent_sid), "")
                        break
                    child, parent = parent, procs[parent][0]
            runs.append((pid, _co_cwd(pid), kind, self._co_boot + info[3] / self._co_clk, env))
        candidates: dict[int, list[tuple[float, ExecutionRecord]]] = {}
        for pid, cwd, kind, started, _ in runs:
            matches = [(abs(rec.started - started), rec) for item in items if (rec := item.record).native_id
                       and rec.harness == kind and rec.cwd and os.path.normpath(rec.cwd) == os.path.normpath(cwd)
                       and rec.started and abs(rec.started - started) <= 3
                       and rec.ended is None and rec.state != "finished"
                       and (not rec.activity or rec.activity >= started - 2)
                       and (rec.pid is None or rec.pid == pid)
                       and (rec.pid_start is None or rec.pid_start == procs[pid][3])
                       and (not rec.boot_id or rec.boot_id == (_co_current_boot_id() or ""))]
            if matches:
                candidates[pid] = sorted(matches, key=lambda pair: pair[0])
        selected: dict[str, list[int]] = {}
        for pid, matches in candidates.items():
            if len(matches) == 1 or matches[0][0] + 1.0 < matches[1][0]:
                selected.setdefault(matches[0][1].execution_id, []).append(pid)
        by_pid = {pid: (cwd, kind, env) for pid, cwd, kind, _, env in runs}
        for item in items:
            rec = item.record
            matched = selected.get(rec.execution_id, [])
            pid = matched[0] if len(matched) == 1 else None
            if pid is not None:
                rec.pid = pid
                rec.pid_start = procs[pid][3]
                rec.boot_id = _co_current_boot_id() or ""
                rec.role = "worker"
                env = by_pid[pid][2]
                sid = env.get("CLAUDE_CODE_SESSION_ID", "")
                lead_pid = _co_safe_pid(env.get("CLAUDE_PID", ""))
                link = None
                if sid and lead_pid in procs and self._co_child_sessions.get(lead_pid) == sid:
                    config = _co_environ(lead_pid).get("CLAUDE_CONFIG_DIR", "")
                    account = "work" if "work" in os.path.basename(config).lower() else "personal"
                    if execution_key("claude", account, sid) != rec.execution_id:
                        link = (sid, account)
                if link is None:
                    # An empty account stores an already-qualified parent key in the existing link cache.
                    sid = env.get("STATUS_PARENT_SESSION", "")
                    harness = env.get("STATUS_PARENT_HARNESS", "")
                    link = (parent_key(harness, sid, lead_pid), "") if sid and harness else ancestry.get(pid)
                self._co_remember_headless(rec.execution_id, link)
            data = getattr(self, "_co_run_records", {}).get(rec.native_id, {})
            recorded = data.get("harness") == rec.harness
            if recorded:
                rec.role = "worker"
                sid, harness = data.get("lead_session"), data.get("lead_harness")
                if isinstance(sid, str) and sid and isinstance(harness, str) and harness:
                    key = parent_key(harness, sid, _co_safe_pid(str(data.get("lead_pid", ""))))
                    if key != rec.execution_id and not rec.parent_id:
                        rec.parent_id = rec.root_id = key
                        rec.linkage = "run-record"
            elif (rec.harness in ("opencode", "cursor") and os.sep + ".stage" + os.sep in rec.cwd
                  and (rec.ended is not None or rec.state == "finished" or not self._co_execution_live(rec))
                  and not any(lead.kind.split()[0] == rec.harness and lead.cwd and
                              os.path.normpath(lead.cwd) == os.path.normpath(rec.cwd) for lead in leads.values())):
                rec.role = "worker"
            if rec.execution_id in self._co_headless_ids or rec.execution_id in self._co_headless_links:
                rec.role = "worker"
            link = self._co_headless_links.get(rec.execution_id)
            if link and (execution_key("claude", link[1], link[0]) if link[1] else link[0]) == rec.execution_id:
                self._co_headless_links.pop(rec.execution_id, None)
                link = None
            if link and rec.role == "worker" and not rec.parent_id:
                sid, account = link
                key = execution_key("claude", account, sid) if account else sid
                if key != rec.execution_id:
                    rec.parent_id = rec.root_id = key
                    rec.linkage = "env"
            elif rec.role == "worker" and not rec.parent_id and rec.root_id in ("", rec.execution_id):
                rec.root_id = _co_unattached_root(rec.cwd, os.path.expanduser("~"))

    def _co_stage_sessions(self, stage: str, workers: list[Worker],
                           procs: dict[int, tuple[int, int, int, int, int]]) -> None:
        """Learn live lead session ids from the stage workers' lineage files."""
        self._co_stage_lead_sessions = {
            pid: value for pid, value in self._co_stage_lead_sessions.items()
            if pid in procs and (value[1] == procs[pid][3] if value[1] is not None
                                 else value[2] >= self._co_boot + procs[pid][3] / self._co_clk)
        }
        names = [worker.name for worker in workers] if workers else [
            os.path.basename(f)[:-len(".session")] for f in sorted(glob.glob(os.path.join(glob.escape(stage), "*.session")))[-50:]]
        for name in names:
            try:
                with open(os.path.join(stage, name + ".session"), encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            pid = data.get("lead_pid")
            sid = data.get("lead_session", "")
            launched = data.get("launched_at", 0)
            start_tick = data.get("lead_pid_start", data.get("lead_start_tick"))
            if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1 or pid not in procs
                    or not isinstance(sid, str) or not sid
                    or not isinstance(launched, (int, float)) or isinstance(launched, bool)):
                continue
            process_started = self._co_boot + procs[pid][3] / self._co_clk
            if start_tick is not None:
                if not isinstance(start_tick, int) or isinstance(start_tick, bool) or start_tick != procs[pid][3]:
                    continue
            elif launched < process_started:
                continue
            self._co_stage_lead_sessions[pid] = (sid, start_tick, float(launched))

    def _co_child_session(self, pid: int) -> str:
        return self._co_child_sessions.get(pid, "") or self._co_stage_lead_sessions.get(pid, ("", None, 0.0))[0]

    def _co_execution_live(self, rec: ExecutionRecord, boot_id: str | None = None) -> bool:
        return _co_proc_identity_matches(rec, self._co_proc_table(), boot_id)

    def _co_execution_stages(self, items: list[ExecutionEvidence], now: float,
                             all_history: bool) -> list[Stage]:
        boot_id = _co_current_boot_id()
        known = {item.record.execution_id: item for item in items}
        roots: dict[str, ExecutionEvidence | None] = {}
        for item in items:
            rec = item.record
            if rec.role == "worker" and not rec.parent_id:
                roots.setdefault(rec.root_id or rec.execution_id, None)
            elif rec.role == "lead" or not rec.parent_id:
                roots.setdefault(rec.root_id or rec.execution_id, item)
            elif rec.parent_id not in known:
                roots.setdefault(rec.root_id or rec.parent_id, None)
        stages: list[Stage] = []
        home = os.path.expanduser("~")
        openclaw_home = self._co_openclaw_home or home   # openclaw's workspace is only openclaw's when we know its home
        for root_id, root_item in roots.items():
            root = root_item.record if root_item is not None else None
            members = [item for item in items if (root is None or item.record.execution_id != root.execution_id)
                       and (item.record.root_id == root_id or item.record.parent_id == root_id
                            or item.record.execution_id == root_id)]
            workers = [_co_execution_worker(item, now, self._co_execution_live(item.record, boot_id)) for item in members]
            _co_metrics_workers(workers, now)
            root = root or ExecutionRecord(root_id, role="lead", linkage="unknown")
            unattached = root_id.startswith("unattached:")
            global_unattached = root_id == "unattached:global"
            if root_item is None and members and not unattached:
                identities = {(item.record.harness, item.record.account) for item in members
                              if item.record.harness and item.record.account}
                if len(identities) == 1:
                    root.harness, root.account = next(iter(identities))
            if root_item is None and len(members) == 1:
                root.cwd = members[0].record.cwd
            label = (_co_unattached_label(root_id, home) if unattached else
                     root.title or (os.path.basename(os.path.normpath(root.cwd)) if root.cwd else "execution"))
            activity_at = max([root.activity, *(item.record.activity for item in members)], default=0.0)
            live = self._co_execution_live(root, boot_id)
            worker_live = sum(1 for worker in workers if worker.state in LIVE_STATES)
            state = "active" if worker_live or (live and (not activity_at or now - activity_at <= ACTIVE_S)) else "idle"
            if live and not activity_at:
                activity_at = root.started
            project_dir = ""
            if unattached and not global_unattached:
                project_dir = os.path.normpath(root_id.split(":", 1)[-1])
            # A record with nothing to show -- no workers, no directory of its own and no activity time -- is not a
            # row. The directory must come from the record, not from the bucket path an unattached run is filed under.
            has_dir = bool(root.cwd) or any(item.record.cwd for item in members)
            if not workers and not live and not has_dir and not activity_at:
                continue
            # A worker run launched inside <project>/.stage/<worktree> belongs to the project, not to the worktree.
            # The renderer folds it as one unattached run of that project unless a link finds its launching session.
            worktree_root = _co_stage_tree_root(
                [root.cwd, *(item.record.cwd for item in members)]) or _co_stage_project_root(project_dir)
            stage_dir, stage_label, folded = project_dir, label, False
            if not stage_dir:   # a headless run with a directory belongs to that project, not to a project named after its task
                stage_dir = os.path.normpath(root.cwd or next((item.record.cwd for item in members if item.record.cwd), ""))
                stage_dir = "" if stage_dir == "." else stage_dir
                folded = bool(stage_dir) and state != "active"   # a quiet headless run is counted as unattached, not listed as a LEAD
            if worktree_root:
                stage_dir, stage_label, folded = worktree_root, os.path.basename(worktree_root) or label, True
            # A run started in openclaw's own workspace is openclaw's, never a project of its own.
            openclaw_root = _co_openclaw_project_root(root.cwd or project_dir, openclaw_home)
            if openclaw_root:
                stage_dir, stage_label, folded = openclaw_root, _co_agent_label(), True
            stage = Stage(
                path=f"execution:{root.execution_id}", dir=stage_dir, label=stage_label,
                snap=Snapshot(title=stage_label, now=now, workers=_co_order_workers(workers),
                              groups=_co_groups(workers, []), session_start=root.started,
                              all_history=all_history),
                live=worker_live,
                last_activity=activity_at, lead_state="live" if live else "gone",
                lead_pid=root.pid, lead_since=root.started, lead_kind=root.harness,
                solo=live and not workers, state=state,
                activity=activity_at, cwd=root.cwd, execution_id=root.execution_id,
                account=root.account, parent_id=root.parent_id, root_id=root_id,
                goal=root.goal, brief_text=root_item.brief.splitlines() if root_item is not None else [], linkage=root.linkage,
                native_id=root.native_id, outcome=root.outcome, purpose_source=root.purpose_source,
                unlinked=global_unattached, unattached=global_unattached or folded,
            )
            recency = max(activity_at, root.ended or 0.0)  # a recent end keeps a stage even when activity is old
            # A hard cost bound only: _co_ops_windows makes the real age decision once work state is known.
            if not all_history and not live and not worker_live and (
                    recency <= 0 or now - recency >= _co_loose_keep_s):
                continue
            stages.append(stage)
        return stages

    def _co_refresh(self) -> None:
        mono = time.monotonic()
        if self._co_scanned_at is not None and mono - self._co_scanned_at < RESCAN_S:
            return
        self._co_scanned_at = mono
        found = _co_guard(find_stages, self._co_roots)
        if not isinstance(found, list):
            return
        self._co_found = found
        wanted = {path for path, _ in found}
        for path in [path for path in self._co_collectors if path not in wanted]:
            self._co_collectors.pop(path).close()
        for path, _ in found:
            if path not in self._co_collectors:
                self._co_collectors[path] = Collector(path, quota=False)

    def _co_attach_openclaw(self, stages: list[Stage], oc_stages: list[Stage], now: float) -> list[Stage]:
        """openclaw rows to add. One a Claude session launched (``openclaw agent``) becomes workers of that Claude session's
        row (the run itself, then its subagents) instead of a row of its own."""
        live = _co_guard(_co_openclaw_launches, self._co_proc_table()) if oc_stages else {}
        for key, (sid, _) in (live if isinstance(live, dict) else {}).items():
            self._co_oc_launchers[key] = (sid, now)
        for key in [k for k, (_, seen) in self._co_oc_launchers.items() if now - seen > OPENCLAW_KEEP_S]:
            del self._co_oc_launchers[key]
        alive = set(live) if isinstance(live, dict) else set()
        keep: list[Stage] = []
        for oc in oc_stages:
            launcher = self._co_oc_launchers.get(oc.session_id)
            host = next((s for s in stages if launcher and s.session_id == launcher[0] and s.lead_kind.startswith("claude")), None)
            if host is None:
                keep.append(oc)
                continue
            running = oc.session_id in alive
            run = Worker(
                name="openclaw " + _co_short_name(oc.session_name or oc.session_id.rsplit(":", 1)[-1]),
                state=RUNNING if running else DONE, title=oc.session_name, model=oc.lead_model, family=family_of(oc.lead_model),
                start=oc.snap.session_start, end=None if running else (oc.activity or oc.snap.session_start),
                quiet_s=max(0.0, now - oc.activity), session=host.session_id)
            workers = _co_order_workers(host.snap.workers + [run] + [replace(w, session=host.session_id) for w in oc.snap.workers])
            host.snap = Snapshot(**{**host.snap.__dict__, "workers": workers, "groups": _co_groups(workers, host.snap.milestones)})
            host.live = sum(1 for worker in workers if worker.state in LIVE_STATES)
            host.solo = False
            host.last_activity = max(host.last_activity, oc.activity)
            host.activity = max(host.activity, oc.activity)
        return keep

    def _co_proc_table(self) -> dict[int, tuple[int, int, int, int, int]]:
        """The /proc stat scan, made at most once per snapshot."""
        if self._co_procs is None:
            self._co_procs = _co_scan_procs()
        return self._co_procs

    def _co_idle(self, stage: str, now: float) -> bool:
        """True when no status file moved within IDLE_KEEP_S and no pid file names a live process."""
        newest, pid_files = _co_stage_status(stage)
        if newest >= now - IDLE_KEEP_S:
            return False
        procs = self._co_proc_table()
        for path, mtime in pid_files:
            if _co_pid_live(_co_digits(path), mtime, procs, self._co_clk, self._co_boot):
                return False
        # a headless worker with no pid file of its own still keeps its stage on the dashboard
        return not _co_headless_under(procs, _co_project_root(stage), os.path.expanduser("~"))

    def _co_lead_map(self) -> dict[str, _co_Lead]:
        """Station path -> its interactive lead, from the process table and each lead.json pid."""
        procs = self._co_proc_table()
        stages = [path for path, _ in self._co_found]
        home = os.path.expanduser("~")
        leads = _co_lead_scan(procs, _co_argv, _co_cwd, home, self._co_clk, self._co_boot, _co_environ)
        hosted = _co_service_leads(procs, _co_argv, [lead.cwd for lead in leads if lead.kind == "opencode"],
                                   self._co_clk, self._co_boot, getattr(self, "_co_run_records", {}))
        leads += hosted
        self._co_live_leads_by_stage: dict[str, list[_co_Lead]] = {stage: [] for stage in stages}
        for candidate in leads:
            if candidate.state not in ("live", "background"):
                continue
            assigned_stage = _co_station_of(candidate.cwd, stages)
            if assigned_stage is not None:
                project = os.path.dirname(assigned_stage)
                for sibling in stages:
                    if os.path.dirname(sibling) == project:
                        self._co_live_leads_by_stage.setdefault(sibling, []).append(candidate)
        pinned: list[tuple[str, _co_Lead]] = []
        for stage in stages:
            pid = _co_lead_json_pid(stage)
            if pid is None or pid not in procs:
                continue
            known = next((lead for lead in leads if lead.pid == pid), None)
            since = self._co_boot + procs[pid][3] / self._co_clk
            pinned_lead = known or _co_Lead(pid, "lead.json", "live", "", since)
            pinned.append((stage, pinned_lead))
            bucket = self._co_live_leads_by_stage.setdefault(stage, [])
            if pinned_lead.state in ("live", "background") and all(item.pid != pid for item in bucket):
                bucket.append(pinned_lead)
        assigned = _co_assign_leads(leads, stages, pinned)
        # a lead with no stage dir is a station of its own, in the same dict so current_lead() finds it
        self._co_orphans = _co_orphan_leads(leads, assigned, home)
        for path, (_, lead) in self._co_orphans.items():
            assigned[path] = lead
        return assigned

    def _co_opencode_extras(self, lead: _co_Lead, sid: str, now: float, taken_pids: set[int],
                            taken_names: set[str], slices: dict[int, str] | None = None) -> list[Worker]:
        """Workers an OpenCode session owns that no stage lists: its subagent sessions, plus headless runs its
        bash tool started anywhere (found by the session id in the run's environment)."""
        if not sid:
            return []
        procs = self._co_proc_table()
        if self._co_oc_runs is None:
            by_session = _co_headless_by_session(procs, os.path.expanduser("~"))
            roots = _co_opencode_roots(by_session)
            self._co_oc_runs = {}
            for run_sid, runs in by_session.items():
                self._co_oc_runs.setdefault(roots[run_sid], []).extend(runs)
            self._co_oc_children = {}
            for pid, info in procs.items():
                self._co_oc_children.setdefault(info[0], []).append(pid)
        out: list[Worker] = []
        used = set(taken_names)
        for worker in _co_opencode_children(sid, now):
            if worker.name in used:   # a subagent titled like a stage worker keeps its own row
                n = 2
                while f"{worker.name} #{n}" in used:
                    n += 1
                worker = replace(worker, name=f"{worker.name} #{n}")
            used.add(worker.name)
            out.append(worker)
        out += self._co_quota._co_procs_as_workers(
            now, self._co_oc_runs.get(sid, []), procs, self._co_oc_children, taken_pids, used, lead.cwd, f"oc:{sid}", sid, slices)
        return _co_order_workers(out)

    def current_lead(self, stage_path: str) -> _co_Lead | None:
        """The station's lead from a fresh /proc read. Call this right before any signal is sent."""
        self._co_procs = None
        leads = _co_guard(self._co_lead_map)
        return leads.get(stage_path) if isinstance(leads, dict) else None

    def dismiss(self, stage_path: str, now: float | None = None) -> bool:
        """Hide a station until its status files or its lead change. False when the file cannot be written."""
        if not stage_path.startswith("execution:"):   # an execution row is filtered by its own path; its dir would hide nothing
            stage_path = next((s.dir for s in self._co_last_stages if s.path == stage_path and s.dir), stage_path)
        # by dir: a station split into session rows (`#<sid>`, `#unlinked`) has no row whose path is the bare dir
        known_station = any(s.dir == stage_path or (s.path == stage_path and s.dir) for s in self._co_last_stages)
        openclaw_station = any(s.path == stage_path and s.lead_kind == "openclaw" for s in self._co_last_stages)
        if not known_station and not openclaw_station and not stage_path.startswith("execution:"):
            return False
        try:
            dismiss_stage(self._co_dismissed_path, stage_path, time.time() if now is None else now)
        except OSError:
            return False
        return True

    def toggle_idle(self, stage_path: str, now: float | None = None) -> str:
        """Pin a station idle or active by hand, or clear its pin. Returns the state now pinned, "" when cleared.
        Without a pin the station flips from its shown state (active -> idle, anything else -> active)."""
        key = next(((s.dir or s.path) for s in self._co_last_stages if s.path == stage_path), stage_path)
        existing = load_overrides(self._co_overrides_path).get(key)
        current = next((s.state for s in self._co_last_stages if (s.dir or s.path) == key), "")
        stale = existing is not None and any(
            (s.dir or s.path) == key and s.activity > float(existing["set"]) for s in self._co_last_stages)
        if existing is not None and not stale:
            new = None   # the pin is in effect: pressing again clears it; a stale pin (activity since) is replaced
        else:
            new = "idle" if current == "active" else "active"
        try:
            set_override(self._co_overrides_path, key, new, time.time() if now is None else now)
        except OSError:
            return str(existing["state"]) if existing is not None else ""   # nothing was written: report what is pinned
        return new or ""

    def _co_lead_for(self, stage: str, now: float) -> LeadInfo | None:
        lead = _co_guard(_co_lead_file, stage, now)
        if isinstance(lead, LeadInfo):
            return lead
        if self._co_rows is None:
            rows = _co_guard(_co_read_ledger, self._co_ledger)
            self._co_rows = rows if isinstance(rows, list) else []
        return _co_ledger_lead(self._co_rows, stage, now)

    def _co_build(self, now: float, all_history: bool) -> GlobalSnapshot:
        self._co_refresh()
        self._co_procs = None
        self._co_rows = None
        self._co_oc_runs = None
        self._co_load_run_records()
        for path, _ in self._co_found:
            self._co_stage_sessions(path, [], self._co_proc_table())
        executions = self._co_executions(now)
        execution_pids = {item.record.pid: item.record.cwd for item in executions
                          if item.record.pid is not None and self._co_execution_live(item.record)}
        leads = self._co_lead_map()
        dismissed = load_dismissed(self._co_dismissed_path)
        overrides = load_overrides(self._co_overrides_path)
        stages: list[Stage] = []
        hidden = 0
        dismissed_count = 0
        virtual = _co_virtual_labels(self._co_orphans, {label for _, label in self._co_found}, os.path.expanduser("~"))
        for path, label in self._co_found + sorted(virtual.items()):
            lead = leads.get(path)
            pin_set = float(overrides.get(path, {}).get("set", 0.0) or 0.0)   # when the pin was set; activity after it clears it
            pin_state = str(overrides.get(path, {}).get("state", ""))   # a manual idle/active pin, "" when automatic
            if path in virtual:
                # a session with no stage dir: its lead is present by construction, so never idle or dismissed
                if lead is not None:
                    meta = _co_lead_session(path, lead, self._co_child_session(lead.pid))
                    lead_activity = float(meta.get("activity", 0.0) or 0.0)
                    extras = (self._co_opencode_extras(lead, str(meta["session_id"]), now, set(), set())
                              if lead.kind == "opencode" else [])
                    extra_live = sum(1 for worker in extras if worker.state in LIVE_STATES)
                    for worker in extras:
                        lead_activity = max(lead_activity, worker.start, worker.end or 0.0)
                    row_state = "active" if extra_live or (lead.state == "live" and not lead_activity) or (
                        lead.state in ("live", "background") and lead_activity and now - lead_activity <= ACTIVE_S) else "idle"
                    if lead.state == "live" and not lead_activity:
                        lead_activity = lead.since
                    doing_list, doing_reason = ([], "") if extras else _co_lead_doing_note(lead.pid, lead.kind, meta["session_id"])
                    stages.append(
                        Stage(
                            path=path,
                            label=label,
                            snap=Snapshot(title=label, now=now, workers=extras, groups=_co_groups(extras, []),
                                          session_start=lead.since, all_history=all_history),
                            live=extra_live,
                            last_activity=0.0,
                            lead_state=lead.state,
                            lead_pid=lead.pid or None,
                            lead_since=lead.since,
                            lead_kind=lead.kind,
                            lead_ctx=_co_lead_ctx(lead.pid, lead.kind),
                            solo=lead.state in ("live", "background") and not extras,
                            doing=doing_list, doing_note=doing_reason,
                            session_id=meta["session_id"],
                            session_name=_co_session_label(meta["session_name"], meta["session_id"], label),
                            parent=meta.get("parent", ""), root_session=meta.get("root_session", ""),
                            launch_depth=meta.get("launch_depth", 0), app=meta.get("app", False),
                            app_id=meta.get("app_id", ""), tmux=meta.get("tmux", ""),
                            tag=meta["tag"],
                            lead_model=meta["lead_model"],
                            effort=meta["effort"],
                            state=pin_state or row_state,
                            pinned=pin_state,
                            activity=lead_activity,
                            cwd=lead.cwd,
                        )
                    )
                continue
            collector = self._co_collectors.get(path)
            if collector is None:
                continue
            # a dismissed station stays hidden until its status files move on, a lead shows up, or a
            # headless worker (which writes no status file) starts under it
            if (path in dismissed and lead is None and _co_stage_status(path)[0] <= dismissed[path]
                    and not _co_headless_under(self._co_proc_table(), _co_project_root(path), os.path.expanduser("~"))):
                dismissed_count += 1
                continue
            # a station pinned idle is hidden like any idle one; a station pinned active is never hidden as idle
            if pin_state and _co_stage_status(path)[0] > pin_set:
                pin_state = ""   # new status-file activity since the pin: back to automatic
            if not all_history and lead is None and (pin_state == "idle" or (
                    pin_state != "active" and self._co_idle(path, now))):
                hidden += 1
                continue
            if lead is not None:
                self._co_last_lead_since[path] = lead.since
            session_since = self._co_last_lead_since.get(path)
            collector._co_quick = self._co_quick
            snap = collector.snapshot(now, all_history, self._co_proc_table(), session_since, lead is not None)
            self._co_stage_sessions(path, snap.workers, self._co_proc_table())
            if execution_pids:
                # The execution row below replaces a stage sighting only after exact PID/start corroboration.
                snap.workers = [worker for worker in snap.workers
                                if worker.pid not in execution_pids or worker.state not in LIVE_STATES
                                or os.path.normpath(os.path.join(path, worker.name)) == os.path.normpath(execution_pids[worker.pid])]
            for worker in snap.workers:
                if worker.session or not worker.pid or worker.pid not in self._co_proc_table():
                    continue
                env = _co_environ(worker.pid)
                sid = env.get("CLAUDE_CODE_SESSION_ID", "")
                lead_pid = _co_safe_pid(env.get("CLAUDE_PID", ""))
                if sid and self._co_child_sessions.get(lead_pid) == sid:
                    worker.session = sid
            live = sum(1 for worker in snap.workers if worker.state in LIVE_STATES)
            last = _co_last_activity(snap)
            if lead is None and live == 0 and last < now - SESSION_GAP_S:   # the snapshot only holds the latest session
                last = max(last, _co_stage_status(path)[0])
            meta = _co_lead_session(path, lead, self._co_child_session(lead.pid) if lead else "")
            live_sid = meta["session_id"]
            if lead is not None and lead.kind == "opencode":
                extras = self._co_opencode_extras(lead, str(live_sid), now,
                                                  {w.pid for w in snap.workers if w.pid}, {w.name for w in snap.workers},
                                                  _co_stage_pid_names(path))
                if extras:
                    snap = Snapshot(**{**snap.__dict__, "workers": _co_order_workers(snap.workers + extras)})
                    live = sum(1 for worker in snap.workers if worker.state in LIVE_STATES)
                    last = max(last, *(max(w.start, w.end or 0.0) for w in extras))
            _co_metrics_workers(snap.workers, now)
            owned = {live_sid} if live_sid else set()
            buckets: list[tuple[str, list[Worker], bool]] = []
            if lead is not None:
                buckets.append((live_sid, [w for w in snap.workers if w.session == live_sid] if live_sid else [], False))
            closed_ids = sorted({w.session for w in snap.workers if w.session and w.session not in owned})
            buckets.extend((sid, [w for w in snap.workers if w.session == sid], False) for sid in closed_ids)
            unlinked = [w for w in snap.workers if not w.session]
            if unlinked:
                buckets.append(("", unlinked, True))
            split_rows = bool(lead is not None or closed_ids or unlinked)
            if not buckets:
                buckets.append(("", snap.workers, False))
            for index, (sid, members, is_unlinked) in enumerate(buckets):
                row_milestones = snap.milestones if index == 0 else []
                row_snap = Snapshot(**{**snap.__dict__, "workers": members, "milestones": row_milestones,
                                       "reviews": snap.reviews if index == 0 else [],
                                       "groups": _co_groups(members, row_milestones)})   # this row's tasks only
                row_live = sum(1 for worker in members if worker.state in LIVE_STATES)
                log_stamps = []
                if collector is not None:
                    for worker in members:
                        fp = collector._co_log_fp.get(worker.name)
                        if fp is not None:
                            log_stamps.append(fp[1])
                row_last = max([max(w.start, w.end or 0.0) for w in members] + log_stamps, default=0.0)
                if lead is not None and index == 0:
                    row_last = max(row_last, float(meta.get("activity", 0.0) or 0.0))
                workdirs: set[str] = set()
                project_root = os.path.normpath(os.path.dirname(path))
                for worker in members:
                    brief_path = _co_brief_path(path, worker.name)
                    if brief_path:
                        try:
                            mtime = os.stat(brief_path).st_mtime
                        except OSError:
                            continue
                        cached = _co_brief_cwd_cache.get(brief_path)
                        if cached is None or cached[0] != mtime:
                            _co_brief_cwd_cache[brief_path] = (mtime, _co_brief_cwd(brief_path))
                        value = _co_brief_cwd_cache[brief_path][1]
                        if value:
                            value = os.path.normpath(os.path.expanduser(value))
                            if value != project_root:
                                home = os.path.expanduser("~")
                                if value == home or value.startswith(home + os.sep):
                                    value = "~" + value[len(home):]
                                workdirs.add(value)
                row_activity = row_last or last or 0.0
                row_live = sum(1 for worker in members if worker.state in LIVE_STATES)
                row_lead = lead if index == 0 else None
                row_state = "active" if row_live or (row_lead is not None and (
                    (row_lead.state == "live" and not row_activity) or (
                        row_lead.state in ("live", "background") and row_activity and now - row_activity <= ACTIVE_S))) else "idle"
                if row_lead is not None and row_lead.state == "live" and not row_activity:
                    row_activity = row_lead.since
                row_pin = "" if row_activity > pin_set else pin_state   # activity after the pin clears it
                row_path = path if not split_rows or (lead is not None and index == 0) else (path + "#unlinked" if is_unlinked else path + "#" + (sid or "unlinked"))
                row_doing, row_reason = (_co_lead_doing_note(lead.pid, lead.kind, meta["session_id"])
                                         if lead is not None and index == 0 and not members else ([], ""))
                row = Stage(
                    path=row_path, dir=path, label=label, snap=row_snap, live=row_live,
                    last_activity=last if index == 0 else row_last,
                    lead=self._co_lead_for(path, now) if index == 0 else None,
                    lead_state=lead.state if lead is not None and index == 0 else "gone",
                    lead_pid=lead.pid or None if lead is not None and index == 0 else None,
                    lead_since=lead.since if lead is not None and index == 0 else 0.0,
                    lead_kind=lead.kind if lead is not None and index == 0 else "",
                    lead_ctx=_co_lead_ctx(lead.pid, lead.kind) if lead is not None and index == 0 else None,
                    solo=row_lead is not None and row_lead.state in ("live", "background") and not members,
                    doing=row_doing, doing_note=row_reason,
                    session_id=sid, session_name=_co_session_label(meta["session_name"] if lead is not None and index == 0 else "", sid, label),
                    tag=meta["tag"] if lead is not None and index == 0 else "",
                    lead_model=meta["lead_model"] if lead is not None and index == 0 else "",
                    effort=meta["effort"] if lead is not None and index == 0 else "",
                    parent=meta.get("parent", "") if row_lead else "",
                    root_session=meta.get("root_session", "") if row_lead else "",
                    launch_depth=meta.get("launch_depth", 0) if row_lead else 0,
                    app=meta.get("app", False) if row_lead else False,
                    app_id=meta.get("app_id", "") if row_lead else "",
                    tmux=meta.get("tmux", "") if row_lead else "",
                    unlinked=is_unlinked,
                    state=row_pin or row_state,
                    pinned=row_pin,
                    activity=row_activity,
                    cwd=lead.cwd if lead is not None and index == 0 else "",
                    workdirs=sorted(workdirs)[:4],
                )
                stages.append(row)
        if self._co_openclaw_home:
            stages.extend(self._co_attach_openclaw(stages, _co_openclaw_stages(now, all_history, self._co_openclaw_home), now))
        stages.extend(self._co_execution_stages(executions, now, all_history))
        # Real stage folders were already checked against status-file mtimes above. Exclude their
        # dismissal keys here so row activity cannot override that more precise check.
        appended_dismissals = {path: stamp for path, stamp in dismissed.items()
                               if not any(path == real_path for real_path, _ in self._co_found)}
        stages, newly_dismissed = _co_filter_dismissed_stages(stages, appended_dismissals)
        dismissed_count += newly_dismissed
        stages = _co_attach_executions(stages)
        stages = _co_adopt_by_session(stages)
        # A real stage row wins duplicate session ids over a virtual row; workers break ties.
        stages = _co_dedupe_sessions(stages)
        _co_nest_workers(stages, self._co_proc_table())
        _co_empty_idle_leads(stages, all_history)
        if not all_history:
            kept = [stage for stage in stages if not _co_closed_aged_out(stage, now)]
            hidden += len(stages) - len(kept)
            stages = kept
        _co_order_stages(stages)
        _co_metrics_leads(stages, now)
        _co_cc_fill_harness(stages)
        workers = [worker for stage in stages for worker in stage.snap.workers]
        spend, spend_ok = self._co_quota._co_spend_state()
        return GlobalSnapshot(
            now=now,
            all_history=all_history,
            stages=stages,
            hidden_idle=hidden,
            dismissed=dismissed_count,
            unattached=sum(len(stage.snap.workers) for stage in stages if stage.unattached),
            quota=self._co_quota._co_quota_snapshot(workers),
            quota_ok=self._co_quota._co_quota_ok(),
            spend=spend,
            spend_ok=spend_ok,
            openrouter=self._co_quota._co_openrouter_snapshot(),
            openrouter_ok=self._co_quota._co_openrouter_ok(),
            usage_history=self._co_quota._co_usage_series(now),
            errors=list(self._co_execution_diagnostics),
        )

    def snapshot(self, now: float | None = None, all_history: bool = False, quick: bool = False) -> GlobalSnapshot:
        """quick: skip the native transcript scan (the slow part of a cold start); the next full snapshot adds it."""
        if now is None:
            now = time.time()
        self._co_quick = quick
        try:
            result = self._co_build(now, all_history)
        except Exception as exc:
            previous = self._co_last_global
            return GlobalSnapshot(
                now=previous.now if previous else now, all_history=all_history,
                stages=list(previous.stages if previous else getattr(self, "_co_last_stages", [])),
                hidden_idle=previous.hidden_idle if previous else 0,
                dismissed=previous.dismissed if previous else 0,
                unattached=previous.unattached if previous else 0,
                errors=[f"collector: {type(exc).__name__}: {exc}"],
            )
        finally:
            self._co_quick = False
            for sub in self._co_collectors.values():
                sub._co_quick = False
        _co_level_tags(result.stages)
        attach_work(result.stages, now)
        dropped = _co_ops_windows(result.stages, now, all_history)  # after attach_work: the 24 h rule reads stage.work
        result.hidden_idle += dropped
        _co_apply_billing(result.stages)      # one place, before any total is summed
        _co_recompute_totals(result)          # from the final worker membership
        self._co_last_stages = list(result.stages)
        self._co_last_global = result
        return result


    def collector_for(self, stage_path: str) -> Collector | None:
        return self._co_collectors.get(stage_path) or self._co_collectors.get(next((s.dir for s in self._co_last_stages if s.path == stage_path and s.dir), ""))

    def detail(self, stage_path: str, name: str, tab: str) -> list[str]:
        collector = self.collector_for(stage_path)
        stage = next((s for s in self._co_last_stages if s.path == stage_path), None)
        wk = next((w for w in stage.snap.workers if w.name == name), None) if stage is not None else None
        if wk is not None and wk.home:     # a worker launched from another project's stage dir
            collector = self._co_collectors.get(wk.home) or collector
        execution_id = wk.execution_id if wk is not None else stage.execution_id if stage is not None else ""
        evidence = self._co_execution_evidence.get(execution_id)
        kind = "brief" if tab == "brief" else "result" if tab == "report" else ""
        if kind and execution_id:
            artifact = read_execution_artifact(self._co_registry_root, execution_id, kind)
            if artifact:
                return artifact[:_co_max_detail]
            if evidence is not None:
                text = evidence.brief if kind == "brief" else evidence.result
                if text:
                    return text.replace("\t", "    ").splitlines()[:_co_max_detail]
        return collector.detail(name, tab) if collector is not None else []

    def close(self) -> None:
        collectors, self._co_collectors = list(self._co_collectors.values()), {}
        for collector in collectors:
            collector.close()
        self._co_quota.close()


def _co_ops_windows(stages: list[Stage], now: float, all_history: bool = False) -> int:
    """Apply the ops console's 2 h idle and 24 h closed windows in place. History keeps every closed session.
    A stage kept only for dirty work is never dropped. Returns how many stages it dropped."""
    kept = []
    for stage in stages:
        live = any(worker.state in LIVE_STATES for worker in stage.snap.workers) or stage.state == "active"
        if live:
            kept.append(stage)
            continue
        activity_at = stage.activity or stage.last_activity
        age = max(0.0, now - activity_at) if activity_at else 0.0
        if stage.state == "idle" and age > IDLE_LIST_S:
            stage.state = "closed"
        if stage.state == "closed" and not all_history and age > CLOSED_LIST_S:
            badge = stage.work.badge() if stage.work is not None else ""
            if badge not in ("uncommitted", "unmerged") and not badge.endswith(" unpushed"):
                continue
        kept.append(stage)
    dropped = len(stages) - len(kept)
    stages[:] = kept
    return dropped


# --- station pages: read on open and on r, never per frame ---------------------------------------
_co_page_lead_json = 40        # lead.json lines on the lead page
_co_page_stream = 30           # stream file lines on the lead page
_co_page_picks = 10            # newest "pick" lines of PROGRESS.md on the lead page
_co_page_files = 200           # files listed for a worker with no git dir
_co_page_git_timeout = 2       # seconds git may take on the files page
_co_codex_scan = 400           # newest rollout files searched for a station's codex session
_co_cwd_re = re.compile(r"^(?:Cwd|Repo/worktree|Worktree)\s*:\s*(\S+)")


def _co_git(cwd: str, *args: str) -> tuple[int | None, str]:
    """git -C cwd args. (None, "") when git cannot run or does not answer within the page timeout."""
    try:
        done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=_co_page_git_timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return done.returncode, done.stdout


def _co_age(sec: float) -> str:
    sec = int(max(sec, 0))
    if sec < 3600:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m"


def lead_page(stage_dir: str, stage: Stage | None, now: float) -> list[str]:
    """Lead page: the station's lead, its lead.json, the stream file of the project's branch, and the newest pick lines."""
    out: list[str] = []
    if stage is None:
        out.append("lead state is only known in the all-orchestrators view")
    else:
        since = _co_age(now - stage.lead_since) if stage.lead_since else "–"
        out += [f"kind   {stage.lead_kind or 'none'}",
                f"state  {stage.lead_state}",
                f"pid    {stage.lead_pid or '–'}",
                f"age    {since}"]
    out += ["", "lead.json"]
    try:
        data = json.loads(_co_read_text(os.path.join(stage_dir, "lead.json")))
        out += ["  " + ln for ln in json.dumps(data, indent=2, sort_keys=True).splitlines()[:_co_page_lead_json]]
    except ValueError:
        out.append("  none")
    project = os.path.dirname(os.path.normpath(stage_dir))
    rc, branch = _co_git(project, "branch", "--show-current")
    branch = branch.strip() if rc == 0 else ""
    slug = branch.replace("/", "-")
    out += ["", f"stream  {slug}.md" if slug else "stream"]
    if not slug:
        out.append("  no git branch here")
    else:
        text = _co_read_lines(os.path.join(project, "docs", "streams", slug + ".md"))[:_co_page_stream]
        out += ["  " + ln for ln in text] if text else [f"  no docs/streams/{slug}.md for branch {branch}"]
    picks = [ln.rstrip() for ln in _co_read_lines(os.path.join(stage_dir, "PROGRESS.md")) if "pick" in ln.lower()]
    out += ["", "quota picks (PROGRESS.md)"]
    out += ["  " + ln for ln in picks[-_co_page_picks:]] or ["  none"]
    return out


def _co_brief_cwd(path: str | None) -> str:
    """The brief's Cwd: (or Repo/worktree:) path; "" if there is none."""
    if not path:
        return ""
    for line in _co_read_lines(path):
        match = _co_cwd_re.match(line.strip())
        if match:
            return match.group(1)
    return ""


def _co_numstat(cwd: str) -> dict[str, tuple[int, int]] | None:
    """path -> (added, removed) from git diff --numstat; binary files count 0. None when git did not answer."""
    rc, text = _co_git(cwd, "diff", "HEAD", "--numstat", "--no-renames")   # staged and unstaged together
    if rc not in (0, None):
        rc, text = _co_git(cwd, "diff", "--numstat", "--no-renames")   # a repo with no commit yet has no HEAD
    if rc != 0:
        return None
    counts: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            added, removed, path = parts
            counts[path] = (int(added) if added.isdigit() else 0, int(removed) if removed.isdigit() else 0)
    return counts


def _co_ago(sec: float) -> str:
    sec = int(max(sec, 0))
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


def _co_file_rows(cwd: str, status: str, counts: dict[str, tuple[int, int]]) -> list[str]:
    """One line per changed file from git status --porcelain, newest edit first."""
    now = time.time()
    rows: list[tuple[float, str]] = []
    for line in status.splitlines():
        code, path = line[:2].strip() or "?", line[3:]
        if not path:
            continue
        try:
            mtime: float | None = os.path.getmtime(os.path.join(cwd, path))
        except OSError:
            mtime = None
        if code == "??":
            stat = "untracked"
        elif path in counts:
            stat = f"+{counts[path][0]} −{counts[path][1]}"
        else:
            stat = ""
        when = _co_ago(now - mtime) if mtime is not None else "–"
        rows.append((mtime or 0.0, f"  {code:<2}  {path:<44} {stat:<12} {when}"))
    rows.sort(key=lambda row: -row[0])
    return [text for _, text in rows]


def _co_git_files(cwd: str, read_only: bool = False) -> list[str]:
    out = [cwd, ""]
    if read_only:
        out += ["read-only worker: no file changes expected", ""]
    out.append("changed files, newest edit first")
    rc, status = _co_git(cwd, "status", "--porcelain", "--untracked-files=all")
    if rc != 0:
        out += _co_git_block(rc, status)
    else:
        rows = _co_file_rows(cwd, status, _co_numstat(cwd) or {})
        out += ["  " + ln for ln in rows[:_co_page_files]] or ["  clean"]
        if len(rows) > _co_page_files:
            out.append(f"  … more than {_co_page_files} files")
    out += ["", "git diff --stat"]
    rc, stat = _co_git(cwd, "diff", "--stat")
    out += _co_git_block(rc, stat)
    return out


def _co_git_block(rc: int | None, text: str) -> list[str]:
    if rc is None:
        return [f"  git did not answer within {_co_page_git_timeout} s"]
    if rc != 0:
        return [f"  git exited {rc}"]
    lines = text.rstrip().splitlines()
    return ["  " + ln for ln in lines] if lines else ["  clean"]


def _co_folder_files(folder: str) -> list[str]:
    out = [folder]
    if not os.path.isdir(folder):
        return out + ["no folder here"]
    found: list[tuple[str, int, float]] = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            found.append((os.path.relpath(path, folder), st.st_size, st.st_mtime))
            if len(found) > _co_page_files:
                break
        if len(found) > _co_page_files:
            break
    if not found:
        return out + ["  no files"]
    for rel, size, mtime in found[:_co_page_files]:
        mem = f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        out.append(f"  {rel:<44} {mem:>10}  {when}")
    if len(found) > _co_page_files:
        out.append(f"  … more than {_co_page_files} files")
    return out


_co_read_only_jobs = ("review", "read", "triage", "investigate", "plan")   # jobs that are not expected to change files
_co_changes_ttl = 5.0                                      # seconds a worker's change summary is reused


@dataclass(frozen=True)
class WorkerChanges:
    files: int
    added: int
    removed: int
    last_edit: float | None      # newest mtime among the changed files, epoch seconds
    scope: str                   # "git" (the brief's Cwd repo) or "folder" (the worker's stage folder)
    read_only: bool


_co_changes_cache: dict[tuple[str, str], tuple[float, WorkerChanges | None]] = {}   # (stage dir, worker) -> (monotonic, value)


def _co_git_changes(cwd: str) -> WorkerChanges | None:
    counts = _co_numstat(cwd)
    rc, text = _co_git(cwd, "ls-files", "--others", "--exclude-standard")
    if counts is None or rc != 0:
        return None
    untracked = [ln for ln in text.splitlines() if ln]
    paths = set(counts) | set(untracked)
    mtimes = []
    for path in paths:
        try:
            mtimes.append(os.path.getmtime(os.path.join(cwd, path)))
        except OSError:
            continue
    return WorkerChanges(files=len(paths), added=sum(a for a, _ in counts.values()),
                         removed=sum(r for _, r in counts.values()),
                         last_edit=max(mtimes) if mtimes else None, scope="git", read_only=False)


def _co_folder_changes(folder: str) -> WorkerChanges:
    mtimes = []
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                mtimes.append(os.path.getmtime(os.path.join(root, name)))
            except OSError:
                continue
    return WorkerChanges(files=len(mtimes), added=0, removed=0, last_edit=max(mtimes) if mtimes else None,
                         scope="folder", read_only=False)


def worker_changes(stage_dir: str, worker: Worker, now: float) -> WorkerChanges | None:
    """What this worker has changed: git numstat and untracked files of its brief's Cwd when that is a git dir,
    else the files under its stage folder. Read-only jobs run no git. Cached 5 s per worker so redraws never
    spawn git; None on any error. now is the frame's clock; the cache keys on the monotonic clock."""
    try:
        if worker.is_review or worker.job in _co_read_only_jobs:
            return WorkerChanges(files=0, added=0, removed=0, last_edit=None, scope="folder", read_only=True)
        key = (stage_dir, worker.name)
        mono = time.monotonic()
        hit = _co_changes_cache.get(key)
        if hit is not None and mono - hit[0] < _co_changes_ttl:
            return hit[1]
        cwd = _co_brief_cwd(_co_brief_path(stage_dir, worker.name))
        if cwd and os.path.exists(os.path.join(cwd, ".git")):
            value = _co_git_changes(cwd)
        else:
            value = _co_folder_changes(os.path.join(stage_dir, worker.name))
        _co_changes_cache[key] = (mono, value)
        return value
    except Exception:
        return None


def files_page(stage_dir: str, worker: Worker | None) -> list[str]:
    """Files page for one worker: its brief's Cwd as a changed-file list with diff --stat when that is a git dir,
    else the files under its stage folder. A read-only worker gets one line saying no changes are expected."""
    if worker is None:
        return ["no worker selected: move the cursor onto a worker row"]
    read_only = worker.is_review or worker.job in _co_read_only_jobs
    cwd = _co_brief_cwd(_co_brief_path(stage_dir, worker.name))
    if cwd and os.path.exists(os.path.join(cwd, ".git")):
        return _co_git_files(cwd, read_only)
    folder = _co_folder_files(os.path.join(stage_dir, worker.name))
    return (["read-only worker: no file changes expected", ""] + folder) if read_only else folder


def codex_session_id(project: str) -> str | None:
    """Session id of the newest codex rollout whose session_meta cwd is project; None when there is none."""
    names: list[str] = []
    for sessions_dir in ("~/.codex/sessions", "~/.codex-testing/sessions"):
        for root, _, files in os.walk(os.path.expanduser(sessions_dir)):
            names += [os.path.join(root, f) for f in files if f.startswith("rollout-") and f.endswith(".jsonl")]
    want = os.path.normpath(project)
    for path in sorted(names, key=os.path.basename, reverse=True)[:_co_codex_scan]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                payload = json.loads(fh.readline()).get("payload")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(payload, dict) and _co_text(payload.get("cwd")) and _co_text(payload.get("id")) \
                and os.path.normpath(payload["cwd"]) == want:
            return payload["id"]
    return None


def _co_level_tags(stages: list[Stage]) -> None:
    """LEVEL column tags. A session row is CRON, SPAWN or LEAD; a worker is SUB (harness-native child) or WORKER.
    Never raises: a missing or odd field keeps the plain tag."""
    for stage in stages:
        stage.level_tag = "LEAD"
        try:
            if stage.cluster == "cron":
                stage.level_tag = "CRON"
            elif stage.parent or (stage.launch_depth or 0) > 0:
                stage.level_tag = "SPAWN"
            workers = list(stage.snap.workers)
        except (AttributeError, TypeError):
            continue
        for worker in workers:
            worker.level_tag = "SUB" if worker.linkage == "native" else "WORKER"
