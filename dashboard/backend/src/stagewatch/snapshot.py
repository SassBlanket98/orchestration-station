"""SnapshotStore: mtime-gated cache over Monitor-listed stage sessions (§3).

`discover()` refreshes the authoritative stage list from Monitor; `refresh()`
only re-scans paths accepted from the latest good state. `refresh()` is
synchronous. A session whose signature (its file stats plus the set of live pids)
is unchanged is reused as-is and no file is opened. Only files whose
`(mtime_ns, size)` changed are re-read; the rest come from a per-file cache.

Readers and `refresh()` may run on different threads. `refresh()` builds the next
maps privately and publishes them with one assignment, so a reader that captured
the previous `_Published` sees one whole snapshot and never a half-updated one.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from stagewatch import discovery
from stagewatch.activity import ActivitySeries, session_series
from stagewatch.config import Config
from stagewatch.models import (
    LatFacts,
    LogSizeSample,
    SessionDetail,
    SessionSummary,
    WorkerDetail,
)
from stagewatch.monitor import fetch_state_sync
from stagewatch.stage import (
    REVIEW_PREFIX,
    WORKER_STEM_RE,
    StageParser,
    StageScan,
    read_text_capped,
    resolve_brief,
    summary_of,
    worker_log_sizes,
)

Signature = tuple[tuple[tuple[str, int, int], ...], frozenset[int]]
# Each worker's series holds one key: its own log size.
LOG_KEY = "log"
# The newest samples published per worker. The window in ActivitySeries bounds age;
# this bounds the payload when a scan runs often.
LOG_SERIES_LIMIT = 120


class _Published(NamedTuple):
    """One consistent set of maps. Never mutated after `refresh()` publishes it."""

    details: dict[str, SessionDetail]
    signatures: dict[str, Signature]
    parsers: dict[str, StageParser]


def _project_and_run(stage: Path) -> tuple[str, str | None]:
    """`(project, run)` for a discovered path; run is None for a top-level stage."""
    if discovery.is_run_stage(stage):
        return stage.parent.parent.name, stage.name
    return stage.parent.name, None


def _with_log_series(
    detail: SessionDetail, series: dict[str, list[LogSizeSample]]
) -> SessionDetail:
    """A copy of `detail` whose workers carry this scan's log series.

    The revision is not touched: it counts content changes, and a sample alone is not one.
    """
    workers = [
        worker.model_copy(update={"log_series": series.get(worker.name, [])})
        for worker in detail.workers
    ]
    return detail.model_copy(update={"workers": workers})


def _inside_stage(stage: Path, path: Path) -> bool:
    """Only allow a file whose resolved target remains within the named stage."""
    try:
        return path.resolve(strict=True).is_relative_to(stage.resolve(strict=True))
    except (OSError, RuntimeError):
        return False


class SnapshotStore:
    """Implements `contracts.StoreProtocol` (project plan §3)."""

    def __init__(
        self,
        cfg: Config,
        clock: Callable[[], float] = time.time,
        state_fetcher: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self.cfg = cfg
        self._published = _Published({}, {}, {})
        self._stages: dict[str, Path] = {}
        self._clock = clock
        self._state_fetcher = (
            state_fetcher
            if state_fetcher is not None
            else lambda: fetch_state_sync(cfg.monitor_url)
        )
        self._stage_facts: dict[str, LatFacts | None] = {}
        self.monitor_online = False
        # One series per (session id, worker stem). Writes happen only in refresh().
        self._activity: dict[tuple[str, str], ActivitySeries] = {}

    def discover(self) -> None:
        """Fetch the authoritative stage list, retaining the last good list on failure."""
        if self.cfg.monitor_url is None:
            self._stages = {}
            self._stage_facts = {}
            self.monitor_online = False
            return
        try:
            state = self._state_fetcher()
        except Exception:
            self.monitor_online = False
            return
        if not isinstance(state, dict) or not isinstance(state.get("stages"), list):
            self.monitor_online = False
            return

        valid: list[tuple[Path, LatFacts | None]] = []
        for item in state["stages"]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            # Several sessions can share one stage dir (`<dir>#<session id>`); runs with no
            # stage dir are `execution:<id>` and have no files to read.
            raw = item["path"]
            if raw.startswith("execution:"):
                continue
            head, sep, tail = raw.rpartition("#")
            if sep and tail and "/" not in tail:
                raw = head
            stage = Path(raw)
            if not _allowed_stage(stage):
                continue
            try:
                if not stage.is_dir():
                    continue
            except OSError:
                continue
            valid.append((stage, _lat_facts(item)))

        # One session per stage dir. When sessions share a dir, the working one's facts win.
        facts_by_path: dict[Path, LatFacts | None] = {}
        for stage, facts in valid:
            known = facts_by_path.get(stage)
            if stage not in facts_by_path or (_working(facts) and not _working(known)):
                facts_by_path[stage] = facts
        self._stages = discovery.assign_ids(list(facts_by_path))
        self._stage_facts = {
            sid: facts_by_path[stage] for sid, stage in self._stages.items()
        }
        self.monitor_online = True

    # -- StoreProtocol ----------------------------------------------------- #
    def refresh(self) -> set[str]:
        """Re-scan the known stages and return the ids of sessions that changed.

        Nothing is locked while files are scanned or parsed. Only the final
        assignment publishes the new maps.
        """
        stages = self._stages
        current = self._published
        details = dict(current.details)
        signatures = dict(current.signatures)
        parsers = dict(current.parsers)
        changed: set[str] = set()

        for sid, stage in stages.items():
            if not stage.is_dir():
                # A vanished stage is a removal, which only `discover()` acts on.
                continue
            parser = parsers.get(sid)
            if parser is None or parser.stage != stage:
                parser = StageParser(stage)
                parsers[sid] = parser
                signatures.pop(sid, None)

            scan = parser.scan()
            # Observed on every scan, so an unchanged signature still adds a sample.
            series = self._observe(sid, scan)
            signature = parser.signature(scan)
            if signatures.get(sid) == signature:
                previous_detail = details.get(sid)
                if previous_detail is not None:
                    details[sid] = _with_log_series(previous_detail, series)
                continue

            project, run = _project_and_run(stage)
            detail = parser.build(sid, project, scan, run).model_copy(
                update={"lat": self._stage_facts.get(sid)}
            )
            previous = current.details.get(sid)
            detail = detail.model_copy(update={"rev": previous.rev + 1 if previous else 1})
            details[sid] = _with_log_series(detail, series)
            signatures[sid] = signature
            changed.add(sid)

        for sid in list(details):
            if sid in stages:
                continue
            del details[sid]
            signatures.pop(sid, None)
            parsers.pop(sid, None)
            changed.add(sid)
        for key in [key for key in self._activity if key[0] not in stages]:
            del self._activity[key]

        for sid, detail in list(details.items()):
            lat = self._stage_facts.get(sid)
            if detail.lat != lat:
                previous = current.details.get(sid)
                details[sid] = detail.model_copy(
                    update={"lat": lat, "rev": previous.rev + 1 if previous else detail.rev}
                )
                changed.add(sid)

        self._published = _Published(details, signatures, parsers)
        return changed

    def _observe(self, sid: str, scan: StageScan) -> dict[str, list[LogSizeSample]]:
        """Record each worker's current log size and return the newest samples per worker.

        A worker that lost its log or disappeared has its history dropped here, so a
        worker that comes back starts a fresh series.
        """
        sizes = worker_log_sizes(scan)
        for key in [key for key in self._activity if key[0] == sid and key[1] not in sizes]:
            del self._activity[key]
        series: dict[str, list[LogSizeSample]] = {}
        for stem, size in sizes.items():
            ring = self._activity.get((sid, stem))
            if ring is None:
                ring = self._activity[(sid, stem)] = ActivitySeries(self._clock)
            ring.record(LOG_KEY, size)
            samples = ring.samples(LOG_KEY)[-LOG_SERIES_LIMIT:]
            series[stem] = [LogSizeSample(at=sample.at, size=sample.size) for sample in samples]
        return series

    def summaries(self) -> list[SessionSummary]:
        """Summaries: active sessions first, then by last activity descending."""
        published = self._published
        details = list(published.details.values())
        details.sort(
            key=lambda detail: (
                detail.status != "active",
                detail.last_activity_at is None,
                -(detail.last_activity_at or 0.0),
                detail.id,
            )
        )
        now = self._clock()
        summaries: list[SessionSummary] = []
        for detail in details:
            worker_series = [
                [(sample.at, sample.size) for sample in worker.log_series]
                for worker in detail.workers
            ]
            summary = summary_of(detail)
            summary = summary.model_copy(
                update={
                    "activity_series": session_series(worker_series, now),
                }
            )
            summaries.append(summary)
        return summaries

    def detail(self, sid: str) -> SessionDetail | None:
        return self._published.details.get(sid)

    def worker_detail(self, sid: str, name: str) -> WorkerDetail | None:
        if not WORKER_STEM_RE.match(name) or name.startswith(REVIEW_PREFIX):
            return None
        published = self._published
        detail = published.details.get(sid)
        stage = self._stages.get(sid)
        if detail is None or stage is None:
            return None
        worker = next((item for item in detail.workers if item.name == name), None)
        if worker is None:
            return None

        brief_path = resolve_brief(stage, name)
        brief_md = (
            read_text_capped(brief_path)
            if brief_path is not None and _inside_stage(stage, brief_path)
            else None
        )
        report_path = stage / name / "REPORT.md"
        report_md = read_text_capped(report_path) if _inside_stage(stage, report_path) else None
        return WorkerDetail(worker=worker, brief_md=brief_md, report_md=report_md)

    def resolve_log(self, sid: str, stem: str) -> Path | None:
        if not WORKER_STEM_RE.match(stem):
            return None
        stage = self._stages.get(sid)
        if stage is None:
            return None
        try:
            stage_real = stage.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        for ext in (".log", ".txt"):
            candidate = stage / f"{stem}{ext}"
            try:
                if not candidate.is_file():
                    continue
                real = candidate.resolve()
            except (OSError, RuntimeError):
                continue
            if real.is_relative_to(stage_real):
                return candidate
        return None


def allowed_roots() -> list[Path]:
    """Roots a stage dir must sit under. Tests widen this."""
    return [Path.home()]


def _allowed_stage(stage: Path) -> bool:
    """Only `<dir>/.stage` or `<dir>/.stage/<run>` under the user's home, never `..`."""
    if not stage.is_absolute() or ".." in stage.parts:
        return False
    if ".stage" not in (stage.name, stage.parent.name):
        return False
    return any(stage.is_relative_to(root) for root in allowed_roots())


def _working(facts: LatFacts | None) -> bool:
    return facts is not None and (
        facts.section == "active" or facts.lead_state in ("live", "background")
    )


def _lat_facts(item: dict[str, Any]) -> LatFacts | None:
    """Copy only well-typed Monitor fields; unknown or malformed facts become None."""
    string_fields = (
        "section", "lead_state", "lead_kind", "level_tag", "project", "parent_session",
        "root_session", "session_id", "session_name", "lead_model", "effort", "goal",
    )
    values: dict[str, Any] = {
        key: value if isinstance(value := item.get(key), str) else None for key in string_fields
    }
    depth = item.get("launch_depth")
    values["launch_depth"] = (
        depth if isinstance(depth, int) and not isinstance(depth, bool) else None
    )
    tokens = item.get("tokens_total")
    values["tokens_total"] = (
        tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None
    )
    cost = item.get("cost_total")
    values["cost_total"] = (
        cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    )
    counts = item.get("counts")
    if isinstance(counts, dict) and all(
        isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
        for key, value in counts.items()
    ):
        values["counts"] = counts
    else:
        values["counts"] = None
    if all(value is None for value in values.values()):
        return None
    return LatFacts(**values)
