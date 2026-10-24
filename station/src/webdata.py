"""JSON payloads for the ``station --serve`` monitor.

Pure functions over the frozen model (:mod:`model`): they turn a
:class:`~model.GlobalSnapshot` or :class:`~model.Snapshot` into the plain,
JSON-serialisable envelope the web client renders, and read one worker's files
for the detail panel. No dataclass, tuple or set leaves these functions; every
``None`` maps to JSON ``null``.

The envelope and element shapes below are what the Station Dashboard reads.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from collections import OrderedDict

from labels import display_model
from logparse import parse_log
from model import (
    DONE,
    FAILED,
    LIVE_STATES,
    NOREPORT,
    TOOL_CATS,
    TOOL_RING_S,
    GlobalSnapshot,
    LeadInfo,
    LogState,
    Snapshot,
    SpendSnapshot,
    Stage,
    Turn,
    Vitals,
    Worker,
    WorkState,
)
from render import _rd_g_root, _rd_g_section

__all__ = ["contract_document", "execution_detail", "global_payload", "single_payload", "stage_payload", "worker_detail"]

_DETAIL_CACHE: OrderedDict[tuple[str, str], tuple[LogState, tuple[int, int, int], bool]] = OrderedDict()
_DETAIL_LOCK = threading.RLock()
_DETAIL_CACHE_MAX = 8
_DETAIL_LIMIT = 256 * 1024
TOOL_BIN_S = 120                        # one tool-call bin: two minutes
TOOL_BINS = TOOL_RING_S // TOOL_BIN_S   # 15 bins: the last 30 minutes
TURNS_MAX = 50                          # OpenClaw turns served per stage, newest last


# -- element builders ------------------------------------------------------------


def _lead_dict(lead: LeadInfo | None) -> dict | None:
    """A stage's lead as a plain dict, or ``None`` when unknown."""
    if lead is None:
        return None
    return {"model": display_model(lead.model), "phase": lead.phase, "stream": lead.stream}


def _text(value: str) -> str | None:
    """A text field: ``""`` is unknown, so ``None``."""
    return value or None


def _tool_bins(pulses: list[tuple[float, int]], now: float) -> list[int]:
    """Tool calls per two-minute bin over the last ``TOOL_RING_S``, oldest first; the newest bin ends at ``now``."""
    start = now - TOOL_RING_S
    bins = [0] * TOOL_BINS
    for ts, count in pulses:
        if start <= ts <= now:
            bins[min(int((ts - start) // TOOL_BIN_S), TOOL_BINS - 1)] += count
    return bins


def _log_known(log) -> bool:
    """True when the log was read at all; an unread log's bins are unknown, not zero."""
    return log.harness != "unknown" or bool(log.steps_total or log.tool_pulses)


def _step_dict(step) -> dict:
    return {"ts": step.ts, "kind": step.kind, "tool": step.tool, "text": step.text,
            "dur": step.dur, "outcome": _text(step.outcome)}


def _turn_dict(turn: Turn, *, calls: bool) -> dict:
    """One OpenClaw turn; ``calls`` adds the tool calls, for the stage detail only."""
    out = {
        "run_id": turn.run_id, "start": turn.start, "end": turn.end,
        "ask": _text(turn.ask), "model": _text(turn.model), "provider": _text(turn.provider),
        "tokens_in": turn.tokens_in, "tokens_out": turn.tokens_out, "tokens_cache": turn.tokens_cache,
        "cost": turn.cost, "latency": turn.latency, "tools": dict(turn.tools),
        "attempts": turn.attempts, "outcome": _text(turn.outcome), "error": _text(turn.error),
    }
    if calls:
        out["calls"] = [{"ts": call.ts, "name": call.name, "ok": call.ok, "dur": call.dur,
                         "args": _text(call.args)} for call in turn.calls]
    return out


def _vitals_dict(vitals: Vitals | None) -> dict | None:
    if vitals is None:
        return None
    return {
        "model": _text(vitals.model), "provider": _text(vitals.provider),
        "ctx_used": vitals.ctx_used, "ctx_budget": vitals.ctx_budget, "compactions": vitals.compactions,
        "cost": vitals.cost, "runtime": vitals.runtime,
        "status": _text(vitals.status), "last_error": _text(vitals.last_error),
    }


def _work_dict(work: WorkState | None) -> dict | None:
    """Git state of a session's directory; ``badge`` is ``""`` when clean, ``None`` when nothing is known."""
    if work is None:
        return None
    unknown = work.dirty is None and work.ahead is None and work.unmerged is None
    return {
        "branch": _text(work.branch), "dirty": work.dirty, "ahead": work.ahead,
        "unmerged": work.unmerged, "checked": work.checked or None,
        "badge": None if unknown else work.badge(),
    }


def _worker_dict(worker: Worker, now: float | None = None) -> dict:
    """One ``<worker>`` element; the same key set for every state."""
    log = worker.log
    return {
        "name": worker.name,
        "state": worker.state,
        "title": worker.title,
        "goal": worker.goal,
        "model": display_model(worker.model),
        "family": worker.family,
        "pid": worker.pid,
        "start": worker.start,
        "end": worker.end,
        "cpu": worker.cpu,
        "rss": worker.rss,
        "nproc": worker.nproc,
        "job": worker.job,
        "is_review": worker.is_review,
        "has_report": worker.has_report,
        "quiet_s": worker.quiet_s,
        "harness": log.harness,
        "log_model": log.model,
        "errors": log.errors,
        "tokens": {
            "in": log.tokens_in,
            "out": log.tokens_out,
            "cache": log.tokens_cache,
            "ctx": log.ctx_tokens,
        },
        "cost": log.cost,
        "tool_counts": {cat: log.tool_counts.get(cat, 0) for cat in TOOL_CATS},
        "tool_bins": _tool_bins(log.tool_pulses, now) if now is not None and _log_known(log) else None,
        "ctx_window": log.ctx_window,
        "level_tag": _text(worker.level_tag),
        "flags": sorted(worker.flags),
        "failure": _text(worker.failure),
        "activity": list(worker.activity),
        "summary": list(worker.summary),
        **_identity_dict(worker),
        **{key: getattr(worker, key) for key in _TREE_KEYS},
        "brief_text": list(worker.brief_text),
        "result_text": list(worker.result_text),
    }


# normalized execution identity, additive to the v2 worker element (None-free strings/bools)
_IDENTITY_KEYS = ("execution_id", "account", "native_id", "parent_id", "root_id", "linkage",
                  "outcome", "purpose_source", "output_available")


# nesting: the launching worker's name in the same stage ("" = the lead), its level, and the lineage ids from <name>.session
_TREE_KEYS = ("parent", "level", "parent_session", "root_session", "depth")


def _identity_dict(item) -> dict:
    """The identity keys of a Worker or Stage; missing hydration gives "" / False."""
    out = {}
    for key in _IDENTITY_KEYS:
        value = getattr(item, key, "")
        if key == "output_available":
            out[key] = bool(value)
        else:
            out[key] = value or ("unknown" if key in ("linkage", "outcome") else "")
    return out


def _spend_entry_dict(snap: SpendSnapshot) -> dict:
    out = {
        "balance": snap.balance,
        "floorUsd": snap.floor_usd,
        "burnPerDayUsd": snap.burn_per_day,
        "runsOutAt": snap.runs_out_at,
        "low": snap.low,
        "stale": snap.stale,
        "since": snap.since,
        "perDay": [{"date": row.date, "usd": row.usd, "requests": row.requests} for row in snap.per_day],
        "perModel": [
            {
                "model": row.model,
                "usd": row.usd,
                "requests": row.requests,
                "promptTokens": row.prompt_tokens,
                "completionTokens": row.completion_tokens,
            }
            for row in snap.per_model
        ],
    }
    if snap.month_to_date_usd is not None:
        out["monthToDateUsd"] = snap.month_to_date_usd
    if snap.budget_limit_usd is not None or snap.budget_used_pct is not None:
        out["budget"] = {"limitUsd": snap.budget_limit_usd, "usedPct": snap.budget_used_pct}
    if snap.source is not None:
        out["source"] = snap.source
    return out


def _openrouter_dict(snap: SpendSnapshot | None, *, ok: bool) -> dict | None:
    if not ok or snap is None:
        return None
    return _spend_entry_dict(snap)


def _spend_dict(spend: dict[str, SpendSnapshot], spend_ok: dict[str, bool]) -> dict[str, dict]:
    return {pid: _spend_entry_dict(snap) for pid, snap in spend.items() if spend_ok.get(pid)}


def _counts(workers: list[Worker]) -> dict:
    """Worker tally: live (running/quiet/stalled), failed, noreport, done, total."""
    return {
        "live": sum(1 for worker in workers if worker.state in LIVE_STATES),
        "failed": sum(1 for worker in workers if worker.state == FAILED),
        "noreport": sum(1 for worker in workers if worker.state == NOREPORT),
        "done": sum(1 for worker in workers if worker.state == DONE),
        "total": len(workers),
    }


def _last_activity(snap: Snapshot) -> float:
    """Newest worker start/end, milestone or review time; ``0.0`` when there is none."""
    stamps: list[float] = []
    for worker in snap.workers:
        stamps.append(worker.start)
        if worker.end is not None:
            stamps.append(worker.end)
    stamps.extend(stone.ts for stone in snap.milestones if stone.ts is not None)
    for review in snap.reviews:
        stamps.extend((review.start, review.end))
    return max(stamps, default=0.0)


def _reviews(snap: Snapshot) -> list[dict]:
    return [
        {
            "name": review.name,
            "model": display_model(review.model),
            "family": review.family,
            "running": review.running,
            "start": review.start,
            "end": review.end,
            "findings": [{"prio": finding.prio, "text": finding.text} for finding in review.findings],
        }
        for review in snap.reviews
    ]


def _milestones(snap: Snapshot) -> list[dict]:
    return [{"ts": stone.ts, "text": stone.text} for stone in snap.milestones]


def _groups(snap: Snapshot) -> list[dict]:
    return [
        {
            "label": group.label,
            "workers": list(group.workers),
            "started": group.started,
            "done": group.done,
            "total": group.total,
            "live": group.live,
            "finished": group.finished,
            "current": group.current,
        }
        for group in snap.groups
    ]


def _stage_dict(
    *,
    path: str,
    label: str,
    snap: Snapshot,
    live: int,
    last_activity: float,
    lead: LeadInfo | None,
    lead_state: str,
    lead_kind: str,
    lead_pid: int | None,
    lead_since: float,
    lead_ctx: int | None = None,
    solo: bool = False,
    tag: str = "",
    session_id: str = "",
    session_name: str = "",
    lead_model: str = "",
    effort: str = "",
    section: str = "closed",
    cwd: str = "",
    now: float,
) -> dict:
    """One stage-shaped element of the envelope, shared by both modes."""
    return {
        "path": path,
        "label": label,
        "live": live,
        "last_activity": last_activity,
        "lead": _lead_dict(lead),
        "lead_state": lead_state,
        "lead_kind": lead_kind,
        "lead_pid": lead_pid,
        "lead_since": lead_since,
        "lead_ctx": lead_ctx,
        "solo": solo,
        "tag": tag,
        "session_id": session_id,
        "session_name": session_name,
        "lead_model": display_model(lead_model),
        "effort": effort,
        "section": section,
        "cwd": cwd,
        "counts": _counts(snap.workers),
        "tokens_total": snap.tokens_total,
        "cost_total": snap.cost_total,
        "milestones": _milestones(snap),
        "reviews": _reviews(snap),
        "groups": _groups(snap),
        "workers": [_worker_dict(worker, now) for worker in snap.workers],
    }


def _stage_element(stage: Stage, now: float | None = None, *, calls: bool = False) -> dict:
    # an unlinked or gone stage has no live session to name, so its name, model and effort stay blank
    blank = stage.unlinked or stage.lead_state == "gone"
    element = _stage_dict(
        now=now,
        path=stage.path,
        label=stage.label,
        snap=stage.snap,
        live=stage.live,
        last_activity=stage.last_activity,
        lead=stage.lead,
        lead_state=stage.lead_state,
        lead_kind=stage.lead_kind,
        lead_pid=stage.lead_pid,
        lead_since=stage.lead_since,
        lead_ctx=stage.lead_ctx,
        solo=stage.solo,
        tag=stage.tag,
        session_id=stage.session_id or "",
        session_name="" if blank else stage.session_name or "",
        lead_model="" if blank else stage.lead_model or "",
        effort="" if blank else stage.effort or "",
        section=_rd_g_section(stage),
        cwd=stage.cwd or "",
    )
    element.update(parent_session=stage.parent, root_session=stage.root_session,
                   launch_depth=stage.launch_depth, app={"on": stage.app, "id": stage.app_id},
                   tmux=stage.tmux, project=_rd_g_root(stage))
    element["goal"] = stage.goal or ""
    element.update(_identity_dict(stage))
    element.update(
        level_tag=_text(stage.level_tag), display_name=_text(stage.display_name),
        lead_ctx_window=stage.lead_ctx_window, lead_tokens=stage.lead_tokens,
        lead_cost=stage.lead_cost, lead_tools=stage.lead_tools,
        lead_tool_bins=(_tool_bins(stage.lead_tool_pulses, now)
                        if now is not None and (stage.lead_tools is not None or stage.lead_tool_pulses)
                        else None),
        work=_work_dict(stage.work), vitals=_vitals_dict(stage.vitals),
        turns=[_turn_dict(turn, calls=calls) for turn in stage.turns[-TURNS_MAX:]],
    )
    return element


def _summary_stage(stage: Stage, now: float) -> dict:
    full = _stage_element(stage, now)
    turns = full.pop("turns")   # full turns go out on the stage detail only; the summary keeps counts
    full["turns_summary"] = {"count": len(stage.turns), "retries": sum(1 for t in turns if (t.get("attempts") or 1) > 1),
                             "errors": sum(1 for t in turns if t.get("outcome") == "error" or t.get("error")),
                             "last": turns[-1] if turns else None}
    milestones = full["milestones"]
    reviews = full.pop("reviews")
    full.pop("milestones")
    workers = full["workers"]
    eligible = [w for w in workers if w["state"] in LIVE_STATES or w["state"] in (FAILED, NOREPORT)]
    done = [w for w in workers if w["state"] == DONE]
    selected = eligible + sorted(done, key=lambda w: w["end"] or 0, reverse=True)[:3]
    if any(w["parent"] for w in workers):   # keep a nested worker right after the one that launched it
        chosen = {id(w) for w in selected}
        selected = [w for w in workers if id(w) in chosen]
    rows = []
    for worker in selected:
        tokens = worker["tokens"]
        total = tokens["ctx"] if tokens["ctx"] is not None else (
            None if any(tokens[k] is None for k in ("in", "out", "cache")) else sum(tokens[k] for k in ("in", "out", "cache")))
        rows.append({key: worker[key] for key in ("name", "state", "model", "family", "harness", "job", "is_review", "has_report", "start", "end", "quiet_s", "errors", "cost", "goal", "tool_bins", "ctx_window", "level_tag", "flags", "failure", *_IDENTITY_KEYS, *_TREE_KEYS)} | {
            "title": (worker["title"] or "")[:120], "tokens_total": total,
        })
    full["workers"] = rows
    full["more_done"] = max(0, len(done) - 3)
    full["latest_milestone"] = max(milestones, key=lambda m: m["ts"] or 0, default=None)
    full["reviews"] = {"total": len(reviews), "running": sum(r["running"] for r in reviews),
                       "p1": sum(f["prio"] == 1 for r in reviews for f in r["findings"]),
                       "p2": sum(f["prio"] == 2 for r in reviews for f in r["findings"]),
                       "p3": sum(f["prio"] == 3 for r in reviews for f in r["findings"]),
                       "clean": sum(not r["running"] and not r["findings"] for r in reviews)}
    return full


# -- public API ------------------------------------------------------------------


def global_payload(gs: GlobalSnapshot, *, title: str = "all orchestrators",
                   poll_ms: int = 3000) -> dict:
    """The envelope for every stage, one element per ``gs.stages`` in order."""
    return {
        "v": 2, "ops": 1, "mode": "global", "now": gs.now, "generated": gs.now,
        "title": title, "poll_ms": poll_ms, "tokens_total": gs.tokens_total,
        "cost_total": gs.cost_total, "hidden_idle": gs.hidden_idle,
        "dismissed": gs.dismissed, "errors": list(gs.errors),
        "openrouter": _openrouter_dict(gs.openrouter, ok=gs.openrouter_ok),
        "spend": _spend_dict(gs.spend, gs.spend_ok),
        "stages": [_summary_stage(stage, gs.now) for stage in gs.stages],
    }


def stage_payload(gs: GlobalSnapshot, path: str) -> dict | None:
    """Return one full cached stage, or None when it is not in this snapshot."""
    for stage in gs.stages:
        if stage.path == path:
            item = _stage_element(stage, gs.now, calls=True)
            item["ops"] = 1
            item["brief_text"] = list(stage.brief_text)   # full brief, never cut
            item["reviews_full"] = item.pop("reviews")
            item["workers_full"] = item.pop("workers")
            item["latest_milestone"] = max(item["milestones"], key=lambda m: m["ts"] or 0, default=None)
            item["reviews"] = {"total": len(item["reviews_full"]), "running": sum(r["running"] for r in item["reviews_full"]),
                               "p1": sum(f["prio"] == 1 for r in item["reviews_full"] for f in r["findings"]),
                               "p2": sum(f["prio"] == 2 for r in item["reviews_full"] for f in r["findings"]),
                               "p3": sum(f["prio"] == 3 for r in item["reviews_full"] for f in r["findings"]),
                               "clean": sum(not r["running"] and not r["findings"] for r in item["reviews_full"])}
            return item
    return None


def single_payload(snap: Snapshot, *, stage_dir: str, label: str,
                   poll_ms: int = 3000) -> dict:
    """The same envelope for one stage: its snapshot as a one-element ``stages`` list.

    ``lead`` is unknown in single mode, so it is ``null`` / ``gone`` and no idle
    stages are hidden.
    """
    live = sum(1 for worker in snap.workers if worker.state in LIVE_STATES)
    raw_stage = _stage_dict(
        path=stage_dir,
        label=label,
        snap=snap,
        live=live,
        last_activity=_last_activity(snap),
        lead=None,
        lead_state="gone",
        lead_kind="",
        lead_pid=None,
        lead_since=0.0,
        solo=False,
        tag="",
        now=snap.now,
    )
    stage = _summary_stage(Stage(path=stage_dir, label=label, snap=snap, live=live,
                                 last_activity=raw_stage["last_activity"]), snap.now)
    return {
        "v": 2, "ops": 1, "generated": snap.now, "dismissed": 0, "errors": [], "mode": "stage",
        "now": snap.now,
        "title": snap.title or label,
        "poll_ms": poll_ms,
        "tokens_total": snap.tokens_total,
        "cost_total": snap.cost_total,
        "hidden_idle": 0,
        "stages": [stage],
    }


SCHEMA = "status.visibility"
SCHEMA_VERSION = 1


def contract_document(payload: dict, *, diagnostics: list[str]) -> dict:
    """The one-shot envelope: the unchanged v2 payload plus schema, version and diagnostics."""
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "diagnostics": list(diagnostics), **payload}


def execution_detail(workers: list[Worker], execution_id: str, *, stage_dir: str | None = None) -> dict | None:
    """Full detail of one normalized worker, looked up only among ``workers`` (the snapshot's allowlist).

    An id that matches no worker gives ``None``, so a client cannot reach anything outside the
    snapshot. Brief and result come from the snapshot's full text; a worker with none (no
    hydration) falls back to its stage files when ``stage_dir`` is given. Metrics stay ``None``
    when the log never reported them.
    """
    if not isinstance(execution_id, str) or not execution_id:
        return None
    worker = next((w for w in workers if w.execution_id == execution_id), None)
    if worker is None:
        return None
    brief = list(worker.brief_text)
    result = list(worker.result_text)
    name = worker.name or ""
    if stage_dir and name and "/" not in name and "\\" not in name and ".." not in name:
        brief_path, report_path, _log_path = _worker_files(stage_dir, name)
        if not brief:
            brief = _read_lines(brief_path)
        if not result:
            result = _read_lines(report_path)
    log = worker.log
    return {
        **_identity_dict(worker),
        "name": name, "title": worker.title, "goal": worker.goal, "model": display_model(worker.model),
        "state": worker.state, "start": worker.start, "end": worker.end,
        "brief": brief, "result": result, "report": list(result),
        "steps": [_step_dict(s) for s in log.steps],
        "next": log.steps_total, "truncated": False,
        "tokens": {"in": log.tokens_in, "out": log.tokens_out, "cache": log.tokens_cache, "ctx": log.ctx_tokens},
        "cost": log.cost,
    }


# -- worker detail ---------------------------------------------------------------


def _read_lines(path: str | None) -> list[str]:
    """Lines of ``path``; an unreadable or missing file gives ``[]``."""
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()
    except (OSError, ValueError):
        return []


def _first_file(*paths: str) -> str | None:
    """The first path that is an existing file, else ``None``."""
    for path in paths:
        try:
            if os.path.isfile(path):
                return path
        except OSError:
            continue
    return None


def _worker_files(stage_dir: str, name: str) -> tuple[str | None, str | None, str | None]:
    """(brief path, report path, log path) for a worker; each ``None`` when absent."""
    brief = _first_file(
        os.path.join(stage_dir, "briefs", name + ".md"),
        os.path.join(stage_dir, "briefs", name + ".txt"),
    )
    report = _first_file(
        os.path.join(stage_dir, name, "REPORT.md"),
        os.path.join(stage_dir, name, "report.md"),
    )
    log = _first_file(
        os.path.join(stage_dir, name + ".log"),
        os.path.join(stage_dir, name + ".txt"),
    )
    return brief, report, log


def _known_state(report: str | None, steps: list[dict]) -> str:
    """The state from files alone (``""`` when it cannot be known).

    A report makes a worker done; otherwise a log whose last step is an error is
    failed. Liveness needs ``/proc`` and the collector's snapshot, which this
    read-only helper does not have, so anything else is unknown.
    """
    if report is not None:
        return DONE
    if steps and steps[-1]["kind"] == "error":
        return FAILED
    return ""


def worker_detail(stage_dir: str, name: str, since: str | int | None = None) -> dict:
    """Brief, report and live steps for one worker, read from ``stage_dir``.

    A ``name`` containing ``/``, ``\\`` or ``..`` and a ``stage_dir`` that is not
    an existing directory both raise :class:`ValueError`. Missing files give
    ``[]``; the function never reads outside ``stage_dir``.
    """
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"invalid worker name: {name!r}")
    if not os.path.isdir(stage_dir):
        raise ValueError(f"not a stage directory: {stage_dir!r}")

    brief_path, report_path, log_path = _worker_files(stage_dir, name)
    try:
        index = int(since) if since is not None else None
        if index is not None and (isinstance(since, bool) or index < 0 or str(index) != str(since)):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("invalid since") from None
    key = (os.path.abspath(stage_dir), name)
    reset = False
    truncated = False
    log = None
    with _DETAIL_LOCK:
        try:
            stat = os.stat(log_path) if log_path else None
        except OSError:
            stat = None
        if stat and log_path:
            try:
                with open(log_path, "rb") as handle:
                    head_hash = hashlib.sha1(handle.read(min(4096, stat.st_size))).digest()
            except OSError:
                head_hash = b""
            fingerprint = (stat.st_ino, stat.st_mtime_ns, stat.st_size, head_hash)
        else:
            fingerprint = (0, 0, 0, b"")
        entry = _DETAIL_CACHE.pop(key, None)
        if entry is not None:
            prior, old_fp, old_truncated = entry
            try:
                with open(log_path, "rb") as handle:
                    prefix = hashlib.sha1(handle.read(min(4096, old_fp[2]))).digest()
            except OSError:
                prefix = b""
            rewritten = (fingerprint[0] != old_fp[0] or fingerprint[2] < prior.offset or
                         (fingerprint[2] == old_fp[2] and fingerprint[1] != old_fp[1]) or
                         prefix != old_fp[3])
            if rewritten:
                entry = None
            else:
                log = parse_log(log_path, prev=prior) if log_path and stat else prior
                truncated = old_truncated
        if entry is None:
            reset = True
            if stat:
                if stat.st_size > _DETAIL_LIMIT:
                    truncated = True
                    try:
                        with open(log_path, "rb") as handle:
                            head = handle.read(16 * 1024)
                        with open(log_path, "rb") as handle:
                            handle.seek(stat.st_size - _DETAIL_LIMIT)
                            data = handle.read(_DETAIL_LIMIT)
                        data = data[data.find(b"\n") + 1:] if b"\n" in data else data
                        with tempfile.NamedTemporaryFile(mode="wb") as handle:
                            handle.write(data); handle.flush()
                            log = parse_log(handle.name, detect_head=head)
                        log.path = log_path
                        log.offset = stat.st_size
                    except OSError:
                        log = LogState(path=log_path)
                else:
                    log = parse_log(log_path)
            else:
                log = LogState(path=log_path or "")
        if log is None:
            log = LogState(path=log_path or "")
        _DETAIL_CACHE[key] = (log, fingerprint, truncated)
        while len(_DETAIL_CACHE) > _DETAIL_CACHE_MAX:
            _DETAIL_CACHE.popitem(last=False)
        all_steps = [_step_dict(step) for step in log.steps]
        next_index = log.steps_total
        first_index = next_index - len(all_steps)   # the log keeps only the newest MAX_STEPS steps
        if index is not None and (index > next_index or index < first_index):
            reset = True
        steps = all_steps[index - first_index:] if index is not None and not reset else all_steps
        state = _known_state(report_path, all_steps)
    result = {
        "name": name,
        "state": state,
        "now": time.time(), "truncated": truncated, "next": next_index, "reset": reset,
        "steps": steps,
    }
    if index is None:
        result["brief"] = _read_lines(brief_path)
        result["report"] = _read_lines(report_path)
    return result
