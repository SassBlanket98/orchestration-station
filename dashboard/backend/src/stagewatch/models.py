"""Pydantic models for the frozen API contract (project plan §3).

Field names and nullability must match `frontend/src/api/types.ts` exactly.
All timestamps are epoch seconds as floats; `None` means unknown.
"""

from typing import Any, Literal

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

WorkerStatus = Literal["running", "finished", "exited", "unknown"]
Engine = Literal["claude", "codex", "cursor", "opencode", "unknown"]
EngineFamily = Literal["codex", "claude", "claude-work", "cursor", "opencode", "unknown"]
Access = Literal["ro", "rw"]
WorkerEventKind = Literal["retry", "stdin_hang", "permission_reject", "idle", "review_findings"]
LogFormat = Literal[
    "opencode-jsonl",
    "codex-text",
    "text",
    "empty",
    "missing",
    "claude-stream-json",
    "cursor-jsonl",
    "cursor-text",
]
LeadConfidence = Literal["explicit-stream", "explicit-progress", "unresolved", "missing"]
CyclePhase = Literal["plan", "build", "review", "unknown"]
ArtifactKind = Literal["brief", "report", "log", "output", "patch", "json", "other"]


class LogLine(BaseModel):
    kind: Literal["text", "tool", "step", "raw"]
    text: str
    tool: str | None


class NowLine(BaseModel):
    kind: Literal["text", "tool", "raw"]
    text: str
    tool: str | None


class LeadIdentity(BaseModel):
    label: str | None = None
    model: str | None = None
    confidence: LeadConfidence
    source_path: str | None = None
    source_line: int | None = None
    evidence: str | None = None
    conflicts: list[str] = []


class LatFacts(BaseModel):
    section: str | None = None
    lead_state: str | None = None
    lead_kind: str | None = None
    level_tag: str | None = None
    project: str | None = None
    parent_session: str | None = None
    root_session: str | None = None
    launch_depth: int | None = None
    session_id: str | None = None
    session_name: str | None = None
    lead_model: str | None = None
    effort: str | None = None
    counts: dict[str, int] | None = None
    tokens_total: int | None = None
    cost_total: float | None = None
    goal: str | None = None


class CycleEvent(BaseModel):
    phase: CyclePhase
    cycle: int | None = None
    label: str | None = None
    at: float | None = None
    source_path: str | None = None
    source_line: int | None = None


class CycleProgress(BaseModel):
    completed: int = 0
    current: Literal[1, 2, 3] | None = None
    phase: CyclePhase = "unknown"
    events: list[CycleEvent] = []
    over_policy: bool = False


class Worker(BaseModel):
    name: str
    status: WorkerStatus
    pid: int | None
    model: str | None
    engine: Engine = "unknown"
    title: str | None
    goal: str | None
    has_brief: bool
    has_report: bool
    started_at: float | None
    finished_at: float | None
    last_activity_at: float | None
    now: NowLine | None
    summary: list[str]
    log_stem: str | None
    log_size: int
    log_format: LogFormat
    flags: list[str]
    duration_s: float | None = None
    log_mtime: float | None = None
    artifact_count: int = 0
    engine_family: EngineFamily | None = None
    account: str | None = None
    effort: str | None = None
    mode: str | None = None
    access: Access | None = None
    access_source: str | None = None
    job: str | None = None
    worker_events: list["WorkerEvent"] = []
    log_series: list["LogSizeSample"] = []
    step_events: list["StepEvent"] = []
    tokens: int | None = None
    tokens_source: str | None = None
    cost: float | None = None


class PickCandidate(BaseModel):
    model: str | None = None
    effort: str | None = None
    group: str | None = None
    reason: str | None = None


class PickRecord(BaseModel):
    at: str | None = None
    job: str | None = None
    slices: list[str] = []
    tier: str | None = None
    candidates: list[PickCandidate] = []
    assigned: list[str] = []
    skipped: list[str] = []
    is_repick: bool = False


class WorkerEvent(BaseModel):
    kind: WorkerEventKind
    at: int | None = None
    text: str


class LogSizeSample(BaseModel):
    at: float
    size: int


class StepEvent(BaseModel):
    seq: int
    kind: str
    text: str
    duration_ms: int | None = None
    ok: bool | None = None
    at: int | None = None


class Counts(BaseModel):
    running: int
    finished: int
    exited: int
    unknown: int


class RunningWorker(BaseModel):
    name: str
    model: str | None
    engine: Engine = "unknown"
    started_at: float | None
    now: NowLine | None


class WorkerPreview(BaseModel):
    name: str
    model: str | None = None
    engine: Engine = "unknown"
    started_at: float | None = None
    now: NowLine | None = None
    status: WorkerStatus = "unknown"
    pid: int | None = None
    title: str | None = None
    goal: str | None = None
    finished_at: float | None = None
    last_activity_at: float | None = None
    summary: list[str] = []
    has_brief: bool = False
    has_report: bool = False
    log_stem: str | None = None
    log_size: int = 0
    log_format: LogFormat = "missing"
    flags: list[str] = []
    engine_family: EngineFamily | None = None
    account: str | None = None
    effort: str | None = None
    tokens: int | None = None
    cost: float | None = None


class ActivitySample(BaseModel):
    at: float
    count: int


class SessionSummary(BaseModel):
    id: str
    project: str
    run: str | None = None
    path: str
    rev: int
    status: Literal["active", "idle"]
    counts: Counts
    last_activity_at: float | None
    latest_milestone: str | None
    findings_total: int
    running_workers: list[RunningWorker]
    lead: LeadIdentity | None = None
    lat: LatFacts | None = None
    stream: str | None = None
    cycle_progress: CycleProgress | None = None
    source_warnings: list[str] = []
    worker_previews: list[WorkerPreview] = []
    worker_total: int = 0
    activity_series: list[ActivitySample] = []
    tokens_total: int | None = None
    cost_total: float | None = None
    findings_by_priority: dict[str, int] = {"P1": 0, "P2": 0, "P3": 0}


class Finding(BaseModel):
    priority: str
    text: str


class Review(BaseModel):
    name: str
    mtime: float
    size: int
    model: str | None
    findings: list[Finding]


class ExtraLog(BaseModel):
    name: str
    kind: Literal["check", "other"]
    mtime: float
    size: int
    last_line: str | None


class Artifact(BaseModel):
    name: str
    kind: ArtifactKind
    size: int
    mtime: float
    previewable: bool
    preview_url: str | None = None


class LogIndexEntry(BaseModel):
    session_id: str | None = None
    worker_name: str | None = None
    artifact_name: str | None = None
    kind: ArtifactKind | None = None
    mtime: float | None = None
    size: int | None = None
    preview_url: str | None = None


class SessionDetail(SessionSummary):
    workers: list[Worker]
    milestones: list[str]
    reviews: list[Review]
    extra_logs: list[ExtraLog]
    history: list[CycleEvent] = []
    artifacts: list[Artifact] = []
    log_index: list[LogIndexEntry] | None = None
    pick_records: list[PickRecord] = []


class WorkerDetail(BaseModel):
    worker: Worker
    brief_md: str | None
    report_md: str | None
    artifacts: list[Artifact] = []
    brief_truncated: bool = False
    report_truncated: bool = False


class LogChunk(BaseModel):
    stem: str
    format: LogFormat
    size: int
    offset: int
    truncated: bool
    lines: list[LogLine]
    error: str | None = None

    @model_serializer(mode="wrap")
    def _omit_null_error(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # The frozen log-chunk contract has no `error` key on success.
        data: dict[str, Any] = handler(self)
        if data.get("error") is None:
            data.pop("error", None)
        return data


# quota_service quota shapes, built from an allowlist in `quota.py`. Deliberately no credential,
# config path or header fields. Units differ from the rest of this module: `generated_at`,
# `runs_out_at` and `resets_at` are Unix epoch MILLISECONDS as upstream sends them, while
# `fetched_at` is this dashboard's own Unix epoch SECONDS.
class QuotaLimit(BaseModel):
    window: str | None = None
    name: str | None = None
    used_pct: float | None = None
    pace_pct: float | None = None
    projected_pct: float | None = None
    runs_out_at: float | None = None  # ms
    resets_at: float | None = None  # ms


class QuotaWindowRef(QuotaLimit):
    """The shortest or longest window a pick candidate was ranked on."""

    headroom_pct: float | None = None


class QuotaSubscription(BaseModel):
    id: str | None = None
    account: str | None = None
    stale: bool | None = None
    last_resort: bool | None = None
    limits: list[QuotaLimit] = []


class QuotaCredit(BaseModel):
    """Pay-as-you-go API credits. USD amounts; `runs_out_at` is epoch ms like the limits."""

    account: str | None = None
    balance_usd: float | None = None
    burn_per_day_usd: float | None = None
    runs_out_at: float | None = None  # ms
    low: bool | None = None
    reason: str | None = None


class QuotaUsage(BaseModel):
    offline: bool | None = None
    generated_at: float | None = None  # ms, upstream clock
    fetched_at: float | None = None  # s, dashboard clock
    error_code: str | None = None
    subscriptions: list[QuotaSubscription] = []
    credits: list[QuotaCredit] = []


class QuotaPickCandidate(BaseModel):
    model: str | None = None
    effort: str | None = None
    subscription: str | None = None
    score: float | None = None
    runway: float | None = None
    group: str | None = None
    tier: str | None = None
    reason: str | None = None
    shortest: QuotaWindowRef | None = None
    longest: QuotaWindowRef | None = None


class QuotaAssignment(BaseModel):
    model: str | None = None
    effort: str | None = None
    slices: int | None = None


class QuotaSkip(BaseModel):
    model: str | None = None
    why: str | None = None


class QuotaFallback(BaseModel):
    model: str | None = None
    effort: str | None = None
    subscription: str | None = None


class QuotaLead(BaseModel):
    model: str | None = None
    over_pace: bool | None = None
    suggestion: str | None = None


class PickResult(BaseModel):
    """A live `/api/pick` preview. It is not history; past picks come from run `pick_records`."""

    job: str | None = None
    generated_at: float | None = None  # ms, upstream clock
    tier_used: str | None = None
    stretched: bool | None = None
    lead: QuotaLead | None = None
    fallback: QuotaFallback | None = None
    candidates: list[QuotaPickCandidate] = []
    assignment: list[QuotaAssignment] = []
    skipped: list[QuotaSkip] = []
    warnings: list[str] = []
