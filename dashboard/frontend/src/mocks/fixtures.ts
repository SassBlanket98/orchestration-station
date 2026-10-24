// Mock fixtures for F0. Typed against api/types.ts so the shapes are checked
// against the frozen contract (§3). F1 keeps these for `?mock=1`.
import type {
  ExtraLog,
  Finding,
  LogChunk,
  LogLine,
  Review,
  SessionDetail,
  SessionSummary,
  Worker,
  WorkerDetail,
  WorkerPreview,
} from "../api/types";

/** Timestamps are anchored once so relative labels stay sensible. */
const NOW = Date.now() / 1000;
const m = (minutes: number) => NOW - minutes * 60;
const h = (hours: number) => NOW - hours * 3600;

/** Lines the running worker's now-line cycles through. */
export const MOCK_NOW_LINES: string[] = [
  "Running the acceptance check suite",
  "Reading src/api/types.ts",
  "Writing frontend/src/mocks/fixtures.ts",
  "Invoking tool: pnpm typecheck",
  "Inspecting frontend/vite.config.ts",
  "Waiting for the test runner",
];

/** Same projection as the backend's `_worker_preview`: every preview field copied from its worker. */
function previewsOf(workers: Worker[]): WorkerPreview[] {
  return workers.map((w) => ({
    name: w.name,
    model: w.model,
    engine: w.engine,
    started_at: w.started_at,
    now: w.now,
    status: w.status,
    pid: w.pid,
    title: w.title,
    goal: w.goal,
    finished_at: w.finished_at,
    last_activity_at: w.last_activity_at,
    summary: w.summary,
    has_brief: w.has_brief,
    has_report: w.has_report,
    log_stem: w.log_stem,
    log_size: w.log_size,
    log_format: w.log_format,
    flags: w.flags,
    engine_family: w.engine_family,
    account: w.account,
    effort: w.effort,
  }));
}

// ---------------------------------------------------------------------------
// stagewatch
// ---------------------------------------------------------------------------

const sdWorkers: Worker[] = [
  {
    name: "f0",
    status: "running",
    pid: 48210,
    model: "deepseek-v4.1-flash",
    engine: "opencode",
    title: "F0 — frontend scaffold, types, mocks, shared UI",
    goal: "build slice F0 of project plan: Vite/React/TS scaffold, the §3 types, mock fixtures and the shared UI primitives",
    has_brief: true,
    has_report: false,
    started_at: m(95),
    finished_at: null,
    last_activity_at: m(0.2),
    now: { kind: "tool", tool: "bash", text: "pnpm build" },
    summary: [],
    log_stem: "f0",
    log_size: 184_320,
    log_format: "opencode-jsonl",
    flags: [],
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    engine_family: null,
    account: null,
    effort: null,
    mode: null,
    access: null,
    access_source: null,
    job: null,
    worker_events: [],
    log_series: [],
    step_events: [],
    tokens: null,
    tokens_source: null,
  },
  {
    name: "f0-fix",
    status: "finished",
    pid: null,
    model: "gpt-6-luna",
    engine: "codex",
    title: "F0 fix: drop the typecheck failures",
    goal: "make pnpm typecheck pass after the scaffold landed",
    has_brief: true,
    has_report: true,
    started_at: h(2),
    finished_at: m(48),
    last_activity_at: m(48),
    now: null,
    summary: [
      "Fixed the tsconfig project references.",
      "Tightened the fixture timestamps.",
      "pnpm test and pnpm build are green.",
    ],
    log_stem: "f0-fix",
    log_size: 42_100,
    log_format: "opencode-jsonl",
    flags: [],
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    engine_family: null,
    account: null,
    effort: null,
    mode: null,
    access: null,
    access_source: null,
    job: null,
    worker_events: [],
    log_series: [],
    step_events: [],
    tokens: null,
    tokens_source: null,
  },
  {
    name: "b0",
    status: "finished",
    pid: null,
    model: "claude-5-opus",
    engine: "claude",
    title: "B0 — backend scaffold, contract models, leaf parsers",
    goal: "a runnable uv project with the §3 models and the pure file and proc readers",
    has_brief: true,
    has_report: true,
    started_at: h(3),
    finished_at: h(1),
    last_activity_at: h(1),
    now: null,
    summary: [
      "models.py matches §3 field for field.",
      "tail and logparse cover the edge cases.",
    ],
    log_stem: "b0",
    log_size: 96_000,
    log_format: "codex-text",
    flags: [],
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    engine_family: null,
    account: null,
    effort: null,
    mode: null,
    access: null,
    access_source: null,
    job: null,
    worker_events: [],
    log_series: [],
    step_events: [],
    tokens: null,
    tokens_source: null,
  },
  {
    name: "p0",
    status: "exited",
    pid: 47120,
    model: "llama-4-30b",
    engine: "opencode",
    title: null,
    goal: null,
    has_brief: false,
    has_report: false,
    started_at: h(5),
    finished_at: h(5),
    last_activity_at: h(5),
    now: null,
    summary: [],
    log_stem: "p0",
    log_size: 2_048,
    log_format: "text",
    flags: ["stdin_hang"],
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    engine_family: null,
    account: null,
    effort: null,
    mode: null,
    access: null,
    access_source: null,
    job: null,
    worker_events: [],
    log_series: [],
    step_events: [],
    tokens: null,
    tokens_source: null,
  },
  {
    name: "scratch",
    status: "unknown",
    pid: null,
    model: null,
    engine: "unknown",
    title: null,
    goal: null,
    has_brief: false,
    has_report: false,
    started_at: null,
    finished_at: null,
    last_activity_at: null,
    now: null,
    summary: [],
    log_stem: null,
    log_size: 0,
    log_format: "missing",
    flags: ["no_log"],
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    engine_family: null,
    account: null,
    effort: null,
    mode: null,
    access: null,
    access_source: null,
    job: null,
    worker_events: [],
    log_series: [],
    step_events: [],
    tokens: null,
    tokens_source: null,
  },
];

const sdReviews: Review[] = [
  {
    name: "review-f0",
    mtime: m(20),
    size: 3_210,
    model: "gpt-6-luna",
    findings: [
      { priority: "P1", text: "types.ts drifted from §3 on `log_stem`" },
      { priority: "P2", text: "TimeAgo shows a negative age on clock skew" },
    ],
  },
  {
    name: "review-b0",
    mtime: h(1),
    size: 1_940,
    model: "gpt-6-luna",
    findings: [{ priority: "P2", text: "tail window doubling is untested past 1 MB" }],
  },
];

const sdExtraLogs: ExtraLog[] = [
  {
    name: "final-lint",
    kind: "check",
    mtime: m(30),
    size: 8_400,
    last_line: "All checks passed.",
  },
  {
    name: "notes",
    kind: "other",
    mtime: h(1.5),
    size: 1_100,
    last_line: "remember to keep the fixture now-line rotating",
  },
];

const sdDetail: SessionDetail = {
  id: "stagewatch",
  project: "stagewatch",
  run: null,
  path: "/home/user/Projects/stagewatch/.stage",
  rev: 42,
  status: "active",
  counts: { running: 1, finished: 2, exited: 1, unknown: 1 },
  last_activity_at: m(0.2),
  latest_milestone: "F0 scaffold builds and renders both routes",
  findings_total: 3,
  running_workers: [
    {
      name: "f0",
      model: "deepseek-v4.1-flash",
      engine: "opencode",
      started_at: m(95),
      now: { kind: "tool", tool: "bash", text: "pnpm build" },
    },
  ],
  lead: null,
  stream: null,
  cycle_progress: null,
  source_warnings: [],
  worker_previews: previewsOf(sdWorkers),
  worker_total: sdWorkers.length,
  activity_series: [],
  workers: sdWorkers,
  milestones: [
    "F0 scaffold builds and renders both routes",
    "Plan approved by the user",
    "Wave 0 started: B0 and F0 in parallel",
    "Dashboard repo bootstrapped",
  ],
  reviews: sdReviews,
  extra_logs: sdExtraLogs,
  history: [],
  artifacts: [],
  log_index: null,
  pick_records: [],
};

// ---------------------------------------------------------------------------
// billing-api
// ---------------------------------------------------------------------------

const twWorkers: Worker[] = [
  {
    name: "a2",
    status: "running",
    pid: 51200,
    model: "claude-5-opus",
    engine: "claude",
    title: "A2 — picker team, retry pass",
    goal: "re-run the picker team after the quota route changed",
    has_brief: true,
    has_report: false,
    started_at: h(2),
    finished_at: null,
    last_activity_at: h(0.1),
    now: { kind: "text", tool: null, text: "Reviewing picker-team output before the retry" },
    summary: [],
    log_stem: "a2",
    log_size: 534_528,
    log_format: "opencode-jsonl",
    flags: ["stale"],
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    engine_family: null,
    account: null,
    effort: null,
    mode: null,
    access: null,
    access_source: null,
    job: null,
    worker_events: [],
    log_series: [],
    step_events: [],
    tokens: null,
    tokens_source: null,
  },
  {
    name: "a2-team",
    status: "finished",
    pid: null,
    model: "gpt-6-luna",
    engine: "codex",
    title: "A2 team — first pass",
    goal: "produce the picker team ranking",
    has_brief: true,
    has_report: true,
    started_at: h(6),
    finished_at: h(4),
    last_activity_at: h(4),
    now: null,
    summary: [
      "Ranking produced for 18 providers.",
      "DeepSeek and Luna were within noise.",
    ],
    log_stem: "a2-team",
    log_size: 210_000,
    log_format: "codex-text",
    flags: [],
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    engine_family: null,
    account: null,
    effort: null,
    mode: null,
    access: null,
    access_source: null,
    job: null,
    worker_events: [],
    log_series: [],
    step_events: [],
    tokens: null,
    tokens_source: null,
  },
  {
    name: "t2",
    status: "exited",
    pid: 50999,
    model: "llama-4-30b",
    engine: "opencode",
    title: null,
    goal: null,
    has_brief: true,
    has_report: false,
    started_at: h(7),
    finished_at: h(7),
    last_activity_at: h(7),
    now: null,
    summary: [],
    log_stem: "t2",
    log_size: 0,
    log_format: "empty",
    flags: ["empty_report", "empty_log"],
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    engine_family: null,
    account: null,
    effort: null,
    mode: null,
    access: null,
    access_source: null,
    job: null,
    worker_events: [],
    log_series: [],
    step_events: [],
    tokens: null,
    tokens_source: null,
  },
  {
    name: "old",
    status: "exited",
    pid: 48001,
    model: null,
    engine: "unknown",
    title: "Old token refresh probe",
    goal: null,
    has_brief: true,
    has_report: false,
    started_at: h(30),
    finished_at: h(29),
    last_activity_at: h(29),
    now: null,
    summary: [],
    log_stem: "old",
    log_size: 3_400,
    log_format: "text",
    flags: [],
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    engine_family: null,
    account: null,
    effort: null,
    mode: null,
    access: null,
    access_source: null,
    job: null,
    worker_events: [],
    log_series: [],
    step_events: [],
    tokens: null,
    tokens_source: null,
  },
];

const twFindings: Finding[] = [
  { priority: "P1", text: "token refresh can spin on a 401 loop" },
  { priority: "P2", text: "cost table is missing the cache-read tier" },
  { priority: "P2", text: "picker tie-breaks are not deterministic" },
];

const twDetail: SessionDetail = {
  id: "billing-api",
  project: "billing-api",
  run: null,
  path: "/home/user/Projects/billing-api/.stage",
  rev: 17,
  status: "active",
  counts: { running: 1, finished: 1, exited: 2, unknown: 0 },
  last_activity_at: h(0.1),
  latest_milestone: "Retry pass kicked off",
  findings_total: 3,
  running_workers: [
    {
      name: "a2",
      model: "claude-5-opus",
      engine: "claude",
      started_at: h(2),
      now: { kind: "text", tool: null, text: "Reviewing picker-team output before the retry" },
    },
  ],
  lead: null,
  stream: null,
  cycle_progress: null,
  source_warnings: [],
  worker_previews: previewsOf(twWorkers),
  worker_total: twWorkers.length,
  activity_series: [],
  workers: twWorkers,
  milestones: [
    "Retry pass kicked off",
    "First pass ranking landed",
    "Quota route changed upstream",
  ],
  reviews: [
    {
      name: "review-a2",
      mtime: h(3),
      size: 2_700,
      model: "gpt-6-luna",
      findings: twFindings,
    },
  ],
  extra_logs: [
    {
      name: "final-test",
      kind: "check",
      mtime: h(4),
      size: 5_200,
      last_line: "184 passed in 3.1s",
    },
    {
      name: "probe",
      kind: "other",
      mtime: h(28),
      size: 640,
      last_line: "401 unauthorized",
    },
  ],
  history: [],
  artifacts: [],
  log_index: null,
  pick_records: [],
};

// ---------------------------------------------------------------------------
// billing-api / ram-2026-10-08 (nested run, one worker per engine)
// ---------------------------------------------------------------------------

const runWorker = (
  name: string,
  engine: Worker["engine"],
  model: string | null,
  logFormat: Worker["log_format"],
  status: Worker["status"],
  title: string,
): Worker => ({
  name,
  status,
  pid: status === "running" ? 51000 : null,
  model,
  engine,
  title,
  goal: title,
  has_brief: true,
  has_report: status === "finished",
  started_at: m(40),
  finished_at: status === "running" ? null : m(10),
  last_activity_at: status === "running" ? m(0.1) : m(10),
  now:
    status === "running"
      ? { kind: "text", tool: null, text: `${engine} worker is checking RAM headroom` }
      : null,
  summary: [],
  log_stem: name,
  log_size: 8_192,
  log_format: logFormat,
  flags: [],
  duration_s: null,
  log_mtime: null,
  artifact_count: 0,
  engine_family: null,
  account: null,
  effort: null,
  mode: null,
  access: null,
  access_source: null,
  job: null,
  worker_events: [],
  log_series: [],
  step_events: [],
  tokens: null,
  tokens_source: null,
});

const runWorkers: Worker[] = [
  runWorker("ram-claude", "claude", "claude-5-opus", "claude-stream-json", "running", "Claude: survey RAM use per service"),
  runWorker("ram-codex", "codex", "gpt-6-luna", "codex-text", "finished", "Codex: model memory budget"),
  runWorker("ram-cursor", "cursor", "composer-2", "cursor-jsonl", "running", "Cursor: live review of the budget"),
  runWorker("ram-opencode", "opencode", "deepseek-v4.1-flash", "opencode-jsonl", "finished", "OpenCode: collect docker stats"),
];

const runDetail: SessionDetail = {
  id: "run:L2hvbWUvdXNlci9Qcm9qZWN0cy9iaWxsaW5nLWFwaS8uc3RhZ2UvcmFtLTIwMjYtMTAtMDg",
  project: "billing-api",
  run: "ram-2026-10-08",
  path: "/home/user/Projects/billing-api/.stage/ram-2026-10-08",
  rev: 9,
  status: "active",
  counts: { running: 2, finished: 2, exited: 0, unknown: 0 },
  last_activity_at: m(0.1),
  latest_milestone: "Four engines started on the RAM budget",
  findings_total: 0,
  running_workers: runWorkers
    .filter((w) => w.status === "running")
    .map((w) => ({
      name: w.name,
      model: w.model,
      engine: w.engine,
      started_at: w.started_at,
      now: w.now,
    })),
  lead: null,
  stream: null,
  cycle_progress: null,
  source_warnings: [],
  worker_previews: previewsOf(runWorkers),
  worker_total: runWorkers.length,
  activity_series: [],
  workers: runWorkers,
  milestones: ["Four engines started on the RAM budget"],
  reviews: [],
  extra_logs: [],
  history: [],
  artifacts: [],
  log_index: null,
  pick_records: [],
};

// ---------------------------------------------------------------------------
// Lookups
// ---------------------------------------------------------------------------

const DETAILS: Record<string, SessionDetail> = {
  "stagewatch": sdDetail,
  "billing-api": twDetail,
  [runDetail.id]: runDetail,
};

/** Overview rows, active sessions first, then by last activity. */
export function sessionsFixture(): SessionSummary[] {
  const rows: SessionSummary[] = Object.values(DETAILS).map((d) => ({
    id: d.id,
    project: d.project,
    run: d.run,
    path: d.path,
    rev: d.rev,
    status: d.status,
    counts: d.counts,
    last_activity_at: d.last_activity_at,
    latest_milestone: d.latest_milestone,
    findings_total: d.findings_total,
    running_workers: d.running_workers,
    lead: d.lead,
    stream: d.stream,
    cycle_progress: d.cycle_progress,
    source_warnings: d.source_warnings,
    worker_previews: d.worker_previews,
    worker_total: d.worker_total,
    activity_series: d.activity_series,
  }));
  return rows.sort((a, b) => {
    if (a.status !== b.status) return a.status === "active" ? -1 : 1;
    return (b.last_activity_at ?? 0) - (a.last_activity_at ?? 0);
  });
}

export function sessionFixture(id: string): SessionDetail | null {
  return DETAILS[id] ?? null;
}

export function workerDetailFixture(
  sid: string,
  name: string,
): WorkerDetail | null {
  const detail = DETAILS[sid];
  if (!detail) return null;
  const worker = detail.workers.find((w) => w.name === name);
  if (!worker) return null;
  return {
    worker,
    brief_md: briefFor(sid, name),
    report_md: reportFor(sid, name),
    artifacts: [],
    brief_truncated: false,
    report_truncated: false,
  };
}

function briefFor(sid: string, name: string): string | null {
  if (sid === "stagewatch" && name === "f0") {
    return `# F0 — frontend scaffold, types, mocks, shared UI

Delegation: none

Goal: build slice F0 of project plan; section 2 and 3 are binding.

## Owned files
- \`src/api/types.ts\`, \`src/api/client.ts\`
- \`src/mocks/fixtures.ts\`, \`src/state/*\`
- \`src/components/ui/*\`, \`src/styles/*\`
`;
  }
  if (sid === "stagewatch" && name === "b0") {
    return `# B0 — backend scaffold

Goal: a runnable uv project with the section 3 models.
`;
  }
  if (sid === "billing-api" && name === "a2") {
    return `# A2 — picker team retry

Goal: re-run the picker team after the quota route changed.
`;
  }
  return null;
}

function reportFor(sid: string, name: string): string | null {
  if (sid === "stagewatch" && name === "f0-fix") {
    return `## Summary
Fixed the tsconfig project references.
Tightened the fixture timestamps.
pnpm test and pnpm build are green.

## Verification
- \`pnpm typecheck\` clean
- \`pnpm test\` 12 passed
`;
  }
  if (sid === "stagewatch" && name === "b0") {
    return `## Summary
models.py matches section 3 field for field.
tail and logparse cover the edge cases.
`;
  }
  if (sid === "billing-api" && name === "a2-team") {
    return `## Summary
Ranking produced for 18 providers.
DeepSeek and Luna were within noise.
`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Logs
// ---------------------------------------------------------------------------

const jsonlF0: LogLine[] = [
  { kind: "step", text: "step_start", tool: null },
  { kind: "text", text: "Reading the plan and the frozen contract.", tool: null },
  { kind: "tool", text: "cat project plan", tool: "read" },
  { kind: "tool", text: "pnpm create vite frontend --template react-ts", tool: "bash" },
  { kind: "text", text: "Scaffold in place, adding the contract types.", tool: null },
  { kind: "tool", text: "frontend/src/api/types.ts", tool: "write" },
  { kind: "step", text: "step_finish", tool: null },
  { kind: "step", text: "step_start", tool: null },
  { kind: "tool", text: "pnpm typecheck", tool: "bash" },
  { kind: "text", text: "Typecheck clean. Building fixtures next.", tool: null },
  { kind: "step", text: "step_finish", tool: null },
  { kind: "step", text: "step_start", tool: null },
  { kind: "tool", text: "pnpm build", tool: "bash" },
  { kind: "text", text: "Build finished. Writing REPORT.md.", tool: null },
];

const jsonlA2: LogLine[] = [
  { kind: "step", text: "step_start", tool: null },
  { kind: "text", text: "Picker team first pass complete.", tool: null },
  { kind: "tool", text: "scripts/rank.py --providers all", tool: "bash" },
  { kind: "text", text: "DeepSeek and Luna within noise on the ranking.", tool: null },
  { kind: "raw", text: '{"type":"unknown_kind","note":"kept as raw"}', tool: null },
  { kind: "step", text: "step_finish", tool: null },
];

const codexB0: LogLine[] = [
  { kind: "raw", text: "Reading additional input from stdin...", tool: null },
  { kind: "raw", text: "codex> reading backend/src/stagewatch/models.py", tool: null },
  { kind: "raw", text: "codex> writing tests/test_tail.py", tool: null },
  { kind: "raw", text: "codex> pytest -q", tool: null },
  { kind: "raw", text: "24 passed", tool: null },
];

const codexA2Team: LogLine[] = [
  { kind: "raw", text: "codex> loading provider list", tool: null },
  { kind: "raw", text: "codex> ranking 18 providers", tool: null },
  { kind: "raw", text: "codex> writing ranking.md", tool: null },
  { kind: "raw", text: "done", tool: null },
];

const textP0: LogLine[] = [
  { kind: "raw", text: "Reading additional input from stdin...", tool: null },
  { kind: "raw", text: "(no further output)", tool: null },
];

const textOld: LogLine[] = [
  { kind: "raw", text: "probe: GET /token", tool: null },
  { kind: "raw", text: "401 unauthorized", tool: null },
];

const LOGS: Record<string, LogChunk> = {
  "stagewatch/f0": {
    stem: "f0",
    format: "opencode-jsonl",
    size: 184_320,
    offset: 184_320,
    truncated: false,
    lines: jsonlF0,
  },
  "stagewatch/f0-fix": {
    stem: "f0-fix",
    format: "opencode-jsonl",
    size: 42_100,
    offset: 42_100,
    truncated: false,
    lines: jsonlF0.slice(0, 8),
  },
  "stagewatch/b0": {
    stem: "b0",
    format: "codex-text",
    size: 96_000,
    offset: 96_000,
    truncated: true,
    lines: codexB0,
  },
  "stagewatch/p0": {
    stem: "p0",
    format: "text",
    size: 2_048,
    offset: 2_048,
    truncated: false,
    lines: textP0,
  },
  "billing-api/a2": {
    stem: "a2",
    format: "opencode-jsonl",
    size: 534_528,
    offset: 534_528,
    truncated: true,
    lines: jsonlA2,
  },
  "billing-api/a2-team": {
    stem: "a2-team",
    format: "codex-text",
    size: 210_000,
    offset: 210_000,
    truncated: false,
    lines: codexA2Team,
  },
  "billing-api/t2": {
    stem: "t2",
    format: "empty",
    size: 0,
    offset: 0,
    truncated: false,
    lines: [],
  },
  "billing-api/old": {
    stem: "old",
    format: "text",
    size: 3_400,
    offset: 3_400,
    truncated: false,
    lines: textOld,
  },
};

export function logFixture(sid: string, stem: string): LogChunk {
  const found = LOGS[`${sid}/${stem}`];
  if (found) return found;
  return {
    stem,
    format: "missing",
    size: 0,
    offset: 0,
    truncated: false,
    lines: [],
  };
}

export const fixtureSessionIds = ["stagewatch", "billing-api", "run:L2hvbWUvdXNlci9Qcm9qZWN0cy9iaWxsaW5nLWFwaS8uc3RhZ2UvcmFtLTIwMjYtMTAtMDg"] as const;
