// Frozen interface contract — project plan §3.
// Field names are exact. This file must diff clean against §3 by field name.

export type WorkerStatus = "running" | "finished" | "exited" | "unknown";

export type Engine = "claude" | "codex" | "cursor" | "opencode" | "unknown";
export type EngineFamily = "codex" | "claude" | "claude-work" | "cursor" | "opencode" | "unknown";
export type Access = "ro" | "rw";
export type WorkerEventKind = "retry" | "stdin_hang" | "permission_reject" | "idle" | "review_findings";
export type LeadConfidence = "explicit-stream" | "explicit-progress" | "unresolved" | "missing";
export type CyclePhase = "plan" | "build" | "review" | "unknown";
export type ArtifactKind = "brief" | "report" | "log" | "output" | "patch" | "json" | "other";

export type LeadIdentity = {
  label: string | null;
  model: string | null;
  confidence: LeadConfidence;
  source_path: string | null;
  source_line: number | null;
  evidence: string | null;
  conflicts: string[];
};

export type CycleEvent = {
  phase: CyclePhase;
  cycle: number | null;
  label: string | null;
  at: number | null;
  source_path: string | null;
  source_line: number | null;
};

export type CycleProgress = {
  completed: number;
  current: 1 | 2 | 3 | null;
  phase: CyclePhase;
  events: CycleEvent[];
  over_policy: boolean;
};

export type ActivitySample = { at: number; count: number };

export type Artifact = {
  name: string;
  kind: ArtifactKind;
  size: number;
  mtime: number;
  previewable: boolean;
  preview_url: string | null;
};

export type LogIndexEntry = {
  session_id: string | null;
  worker_name: string | null;
  artifact_name: string | null;
  kind: ArtifactKind | null;
  mtime: number | null;
  size: number | null;
  preview_url: string | null;
};

export type PickCandidate = {
  model: string | null;
  effort: string | null;
  group: string | null;
  reason: string | null;
};

export type PickRecord = {
  at: string | null;
  job: string | null;
  slices: string[];
  tier: string | null;
  candidates: PickCandidate[];
  assigned: string[];
  skipped: string[];
  is_repick: boolean;
};

export type WorkerEvent = { kind: WorkerEventKind; at: number | null; text: string };
export type LogSizeSample = { at: number; size: number };
export type StepEvent = {
  seq: number;
  kind: string;
  text: string;
  duration_ms: number | null;
  ok: boolean | null;
  at: number | null;
};

export type LogFormat =
  | "opencode-jsonl"
  | "codex-text"
  | "claude-stream-json"
  | "cursor-jsonl"
  | "cursor-text"
  | "text"
  | "empty"
  | "missing";

export type LogLine = {
  kind: "text" | "tool" | "step" | "raw";
  text: string;
  tool: string | null;
};

export type NowLine = {
  kind: "text" | "tool" | "raw";
  text: string;
  tool: string | null;
};

export type Worker = {
  name: string;
  status: WorkerStatus;
  pid: number | null;
  model: string | null;
  engine: Engine;
  title: string | null;
  goal: string | null;
  has_brief: boolean;
  has_report: boolean;
  started_at: number | null;
  finished_at: number | null;
  last_activity_at: number | null;
  now: NowLine | null;
  summary: string[];
  log_stem: string | null;
  log_size: number;
  log_format: LogFormat;
  flags: string[];
  duration_s: number | null;
  log_mtime: number | null;
  artifact_count: number;
  engine_family: EngineFamily | null;
  account: string | null;
  effort: string | null;
  mode: string | null;
  access: Access | null;
  access_source: string | null;
  job: string | null;
  worker_events: WorkerEvent[];
  log_series: LogSizeSample[];
  step_events: StepEvent[];
  tokens: number | null;
  tokens_source: string | null;
};

export type WorkerPreview = {
  name: string;
  model: string | null;
  engine: Engine;
  started_at: number | null;
  now: NowLine | null;
  status: WorkerStatus;
  pid: number | null;
  title: string | null;
  goal: string | null;
  finished_at: number | null;
  last_activity_at: number | null;
  summary: string[];
  has_brief: boolean;
  has_report: boolean;
  log_stem: string | null;
  log_size: number;
  log_format: LogFormat;
  flags: string[];
  engine_family: EngineFamily | null;
  account: string | null;
  effort: string | null;
  // Optional for the same reason as on SessionSummary.
  tokens?: number | null;
  cost?: number | null;
};

export type Counts = {
  running: number;
  finished: number;
  exited: number;
  unknown: number;
};

export type RunningWorker = {
  name: string;
  model: string | null;
  engine: Engine;
  started_at: number | null;
  now: NowLine | null;
};

// The lead's process as the backend sees it. `live` = attached in a terminal, `background` = running detached,
// `closed` = no process. Missing (old backend) means unknown; the frontend then falls back to `status`.
export type LatFacts = {
  section: string | null;
  lead_state: string | null;
  lead_kind: string | null;
  level_tag: string | null;
  project: string | null;
  parent_session: string | null;
  root_session: string | null;
  launch_depth: number | null;
  session_id: string | null;
  session_name: string | null;
  lead_model: string | null;
  effort: string | null;
  counts: Record<string, number> | null;
  tokens_total: number | null;
  cost_total: number | null;
  goal: string | null;
};

export type SessionSummary = {
  id: string;
  project: string;
  run: string | null;
  path: string;
  rev: number;
  status: "active" | "idle";
  counts: Counts;
  last_activity_at: number | null;
  latest_milestone: string | null;
  findings_total: number;
  running_workers: RunningWorker[];
  lead: LeadIdentity | null;
  stream: string | null;
  cycle_progress: CycleProgress | null;
  source_warnings: string[];
  worker_previews: WorkerPreview[];
  worker_total: number;
  activity_series: ActivitySample[];
  // Optional so existing literal mocks still typecheck; the backend always sends them.
  tokens_total?: number | null;
  cost_total?: number | null;
  findings_by_priority?: { P1: number; P2: number; P3: number };
  lat?: LatFacts | null;
};

export type Finding = {
  priority: string; // "P1".."P9"
  text: string;
};

export type Review = {
  name: string;
  mtime: number;
  size: number;
  model: string | null;
  findings: Finding[];
};

export type ExtraLog = {
  name: string;
  kind: "check" | "other";
  mtime: number;
  size: number;
  last_line: string | null;
};

export type SessionDetail = SessionSummary & {
  workers: Worker[];
  milestones: string[];
  reviews: Review[];
  extra_logs: ExtraLog[];
  history: CycleEvent[];
  artifacts: Artifact[];
  log_index: LogIndexEntry[] | null;
  pick_records: PickRecord[];
};

export type WorkerDetail = {
  worker: Worker;
  brief_md: string | null;
  report_md: string | null;
  artifacts: Artifact[];
  brief_truncated: boolean;
  report_truncated: boolean;
};

export type LogChunk = {
  stem: string;
  format: LogFormat;
  size: number;
  offset: number;
  truncated: boolean;
  lines: LogLine[];
  error?: string; // present only when the chunk failed to read
};

// Monitor stage tree (GET /api/monitor). Always HTTP 200; `state` is null when Monitor is offline.
// Timestamps (`last_activity`, `now`, `generated`, `fetched_at`) are Unix epoch SECONDS.
export type MonitorCounts = { live: number; failed: number; noreport: number; done: number; total: number };

// One milestone as the stage endpoint reports it. `ts` is Unix epoch SECONDS or null.
export type MonitorMilestone = { ts: number | null; text: string };

// One task group inside a matched stage: the workers dispatched together for one task.
export type MonitorGroup = {
  label: string;
  workers: string[];
  started: number | null;
  done: number;
  total: number;
  live: number;
  finished: boolean;
  current: boolean;
};

export type MonitorStage = {
  path: string; // stage directory, optionally with a `#<session id>` suffix (several stages can share one directory)
  label: string;
  tag: string; // harness tag, e.g. "CC·W", "CDX", "OCode"
  lead_ctx: number | null;
  lead_state: string;
  live: number;
  last_activity: number;
  solo: boolean;
  latest_milestone: MonitorMilestone | null;
  counts: MonitorCounts;
  // Task groups, newest first (the first is usually current). Missing on older responses; treat as [].
  groups?: MonitorGroup[];
  // Added after the first contract; missing on older responses, so treat absence as "" or unknown.
  session_name?: string;
  lead_model?: string;
  effort?: string;
  section?: "active" | "idle" | "closed";
  session_id?: string;
  cwd?: string;
  // Roll-up fields for the terminal-style tree (rollup.ts). All optional: absence means unknown.
  project?: string;
  lead_kind?: string;
  /** Claude app (Remote Control) link: on = the user can answer this session from the app. */
  app?: { on: boolean; id: string };
  parent_session?: string;
  root_session?: string;
  launch_depth?: number;
  level_tag?: "LEAD" | "SPAWN" | "SUB" | "CRON" | string;
  display_name?: string | null;
  goal?: string;
  lead_tokens?: number | null;
  lead_cost?: number | null;
  lead_tools?: number | null;
  tokens_total?: number | null;
  cost_total?: number | null;
  workers?: MonitorWorker[];
};

// One worker dispatched by a stage's lead. `state` is running|quiet|stalled|done|failed|noreport|...
export type MonitorWorker = {
  name: string;
  state: string;
  level_tag?: string;
  depth?: number;
  title?: string;
  model?: string;
  job?: string;
  is_review?: boolean;
  start?: number | null;
  end?: number | null;
  tokens_total?: number | null;
  cost?: number | null;
  goal?: string | null;
  harness?: string | null;
  has_report?: boolean;
  errors?: number | null;
  failure?: string | null;
  outcome?: string | null;
  quiet_s?: number | null;
  flags?: string[] | null;
};

export type MonitorState = {
  v: number;
  now: number;
  generated: number;
  stages: MonitorStage[];
  hidden_idle: number;
  tokens_total: number | null;
  cost_total: number | null;
};

export type MonitorResponse = {
  online: boolean;
  fetched_at: number;
  error: string | null;
  state: MonitorState | null;
};

export type MonitorStageDetail = {
  milestones: MonitorMilestone[];
  latest_milestone: MonitorMilestone | null;
};

// GET /api/monitor/stage?path=<stage path> (HTTP 200 even when Monitor is down; `online` says which).
// Milestones are oldest first. `stage` is null when the stage is unknown or Monitor is offline.
export type MonitorStageResponse = {
  online: boolean;
  fetched_at: number;
  error: string | null;
  stage: MonitorStageDetail | null;
};

// quota service quota proxy (GET /api/quota/usage, GET /api/quota/pick). Units differ from the
// rest of this file: generated_at, runs_out_at and resets_at are Unix epoch MILLISECONDS
// (upstream clock); fetched_at is Unix epoch SECONDS (dashboard clock). Never mix them.
export type QuotaLimit = {
  window: string | null;
  name: string | null;
  used_pct: number | null;
  pace_pct: number | null;
  projected_pct: number | null;
  runs_out_at: number | null; // ms
  resets_at: number | null; // ms
};

export type QuotaWindowRef = QuotaLimit & { headroom_pct: number | null };

export type QuotaSubscription = {
  id: string | null;
  account: string | null;
  stale: boolean | null;
  last_resort: boolean | null;
  limits: QuotaLimit[];
};

export type QuotaCredit = {
  account: string | null;
  balance_usd: number | null;
  burn_per_day_usd: number | null;
  runs_out_at: number | null; // ms
  low: boolean | null;
  reason: string | null;
};

export type QuotaUsage = {
  offline: boolean | null;
  generated_at: number | null; // ms
  fetched_at: number | null; // s
  error_code: string | null;
  subscriptions: QuotaSubscription[];
  credits?: QuotaCredit[]; // absent on older backends
};

export type QuotaPickCandidate = {
  model: string | null;
  effort: string | null;
  subscription: string | null;
  score: number | null;
  runway: number | null;
  group: string | null;
  tier: string | null;
  reason: string | null;
  shortest: QuotaWindowRef | null;
  longest: QuotaWindowRef | null;
};

export type QuotaAssignment = {
  model: string | null;
  effort: string | null;
  slices: number | null;
};

export type QuotaSkip = { model: string | null; why: string | null };

export type QuotaFallback = {
  model: string | null;
  effort: string | null;
  subscription: string | null;
};

export type QuotaLead = {
  model: string | null;
  over_pace: boolean | null;
  suggestion: string | null;
};

// A live `/api/pick` preview, not history. Past picks come from SessionDetail.pick_records.
export type PickResult = {
  job: string | null;
  generated_at: number | null; // ms
  tier_used: string | null;
  stretched: boolean | null;
  lead: QuotaLead | null;
  fallback: QuotaFallback | null;
  candidates: QuotaPickCandidate[];
  assignment: QuotaAssignment[];
  skipped: QuotaSkip[];
  warnings: string[];
};
