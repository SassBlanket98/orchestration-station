// Living demo data for `?mock=1` and the artifact build.
//
// Pure: every value is a function of `tick` (the ticker in state/hooks.ts) and
// `nowSec`, so equal inputs give equal sessions. Active sessions turn over one
// running worker every EVENT_TICKS ticks. Lagging and idle sessions never move.
import type {
  ActivitySample,
  Counts,
  Engine,
  EngineFamily,
  LeadIdentity,
  LogFormat,
  NowLine,
  RunningWorker,
  SessionSummary,
  WorkerPreview,
  WorkerStatus,
} from "../api/types";

/** Seconds per ticker beat. Mirrors TICK_MS in state/hooks.ts, which is not exported. */
const TICK_S = 2;
/** Ticks between worker turnovers in an active session. */
const EVENT_TICKS = 25;
/** Ticks each now-line step stays on screen. */
const STEP_TICKS = 4;
/** Offset for worker numbers, so the finished window never goes negative. */
const BASE = 100;
const SERIES_LEN = 10;
const BUCKET_S = 60;
const HOUR_S = 3_600;
/** How long a lagging worker has been silent. */
const STALE_S = 900;

const FAILED_SUMMARY = "Stopped after the retry budget ran out on one test.";
const SUMMARIES = [
  "Tests green on the touched files.",
  "Left notes for the reviewer.",
  "Review raised one P2; fixed in the same pass.",
  "Typecheck clean; waiting on the lead to merge.",
];

type Rig = {
  engine: Engine;
  family: EngineFamily;
  model: string;
  format: LogFormat;
  /** Rough dollars per token, for the demo cost figures. */
  rate: number;
};

const RIGS: Rig[] = [
  { engine: "claude", family: "claude", model: "claude-sonnet-5-5", format: "claude-stream-json", rate: 0.000015 },
  { engine: "codex", family: "codex", model: "gpt-6-luna", format: "codex-text", rate: 0.00001 },
  { engine: "opencode", family: "opencode", model: "deepseek-v4.1-flash", format: "opencode-jsonl", rate: 0.000002 },
  { engine: "claude", family: "claude", model: "claude-haiku-5-5", format: "claude-stream-json", rate: 0.000004 },
  { engine: "cursor", family: "cursor", model: "composer-2", format: "cursor-jsonl", rate: 0.000008 },
];

type Plan = {
  id: string;
  kind: "active" | "lagging" | "idle";
  rev: number;
  stream: string;
  milestone: string;
  /** The file the now-lines point at. */
  file: string;
  titles: string[];
  lead: LeadIdentity | null;
  /** P1, P2, P3 counts. */
  findings: [number, number, number];
  running: number;
  finished: number;
  /** Shifts this session's turnover phase and its quiet numbers. */
  seed: number;
};

function lead(label: string, model: string): LeadIdentity {
  return {
    label,
    model,
    confidence: "explicit-stream",
    source_path: null,
    source_line: null,
    evidence: null,
    conflicts: [],
  };
}

const OPUS = lead("Opus", "claude-opus-5-5");
const SOL = lead("Sol", "gpt-6.1-sol");
const DEEPSEEK = lead("DeepSeek", "deepseek-v4.1-flash");

const PLANS: Plan[] = [
  {
    id: "stagewatch",
    kind: "active",
    rev: 42,
    stream: "board-overview",
    milestone: "Overview tree animates with live now-lines",
    file: "frontend/src/components/board/Tree.tsx",
    titles: [
      "Animated tree rows",
      "Keyboard focus ring",
      "Fit-to-screen check",
      "Live now-lines",
      "Fixture rotation",
      "Series sparkline",
    ],
    lead: OPUS,
    findings: [0, 2, 1],
    running: 4,
    finished: 2,
    seed: 3,
  },
  {
    id: "quota-service",
    kind: "active",
    rev: 17,
    stream: "picker-team",
    milestone: "Retry pass on the picker team",
    file: "src/picker/rank.py",
    titles: [
      "Tie-break determinism",
      "Cache-read cost tier",
      "Retry after quota change",
      "Provider ranking",
      "401 refresh probe",
      "Team summary",
    ],
    lead: SOL,
    findings: [1, 2, 0],
    running: 3,
    finished: 2,
    seed: 11,
  },
  {
    id: "Bot",
    kind: "active",
    rev: 8,
    stream: "stream-intake",
    milestone: "Intake parser merged",
    file: "src/stream/intake.ts",
    titles: [
      "Intake parser",
      "Command palette wiring",
      "Session replay fixture",
      "Retry backoff",
      "Log tail reducer",
      "Stream docs",
    ],
    lead: DEEPSEEK,
    findings: [0, 1, 2],
    running: 3,
    finished: 1,
    seed: 19,
  },
  {
    id: "ledger-sync",
    kind: "lagging",
    rev: 5,
    stream: "sync-backfill",
    milestone: "Backfill batch 14 of 40",
    file: "src/sync/backfill.py",
    titles: ["Backfill batch 14", "Rate-limited page retry"],
    lead: DEEPSEEK,
    findings: [0, 1, 0],
    running: 1,
    finished: 1,
    seed: 7,
  },
  {
    id: "docs-site",
    kind: "idle",
    rev: 11,
    stream: "docs-redesign",
    milestone: "Docs nav rewrite merged",
    file: "docs/nav.md",
    titles: ["Docs nav rewrite", "Code block contrast", "Link checker"],
    lead: null,
    findings: [0, 1, 0],
    running: 0,
    finished: 2,
    seed: 4,
  },
  {
    id: "mail-triage",
    kind: "idle",
    rev: 6,
    stream: "triage-rules",
    milestone: "Rules reviewed, two dropped",
    file: "scripts/triage.py",
    titles: ["Triage rules", "Label migration"],
    lead: null,
    findings: [0, 0, 2],
    running: 0,
    finished: 2,
    seed: 9,
  },
  {
    id: "picker-bench",
    kind: "idle",
    rev: 4,
    stream: "bench-rerun",
    milestone: "Bench rerun archived",
    file: "bench/run.py",
    titles: ["Bench rerun", "Cold-start timing"],
    lead: null,
    findings: [1, 0, 0],
    running: 0,
    finished: 3,
    seed: 6,
  },
  {
    id: "shell-notes",
    kind: "idle",
    rev: 3,
    stream: "shell-aliases",
    milestone: "Aliases consolidated",
    file: "dotfiles/zsh/aliases.sh",
    titles: ["Alias consolidation", "Prompt tweaks"],
    lead: null,
    findings: [0, 0, 0],
    running: 0,
    finished: 2,
    seed: 14,
  },
  {
    id: "kiln",
    kind: "idle",
    rev: 9,
    stream: "kiln-ci",
    milestone: "CI cache warmed",
    file: ".github/workflows/ci.yml",
    titles: ["CI cache keys", "Matrix trim", "Release dry run"],
    lead: null,
    findings: [1, 1, 0],
    running: 0,
    finished: 3,
    seed: 21,
  },
  {
    id: "garden-planner",
    kind: "idle",
    rev: 2,
    stream: "planner-ui",
    milestone: "Bed layout drafted",
    file: "src/planner/bed.ts",
    titles: ["Bed layout", "Season filter"],
    lead: null,
    findings: [0, 0, 1],
    running: 0,
    finished: 2,
    seed: 12,
  },
  {
    id: "photo-index",
    kind: "idle",
    rev: 13,
    stream: "index-rebuild",
    milestone: "Index rebuilt for 2025",
    file: "indexer/scan.py",
    titles: ["Index rebuild", "EXIF date fallback", "Thumbnail queue"],
    lead: null,
    findings: [0, 2, 0],
    running: 0,
    finished: 3,
    seed: 16,
  },
  {
    id: "homelab-notes",
    kind: "idle",
    rev: 1,
    stream: "backup-check",
    milestone: "Backup restore test passed",
    file: "ops/backup.sh",
    titles: ["Backup restore test", "NTFS mount check"],
    lead: null,
    findings: [0, 0, 0],
    running: 0,
    finished: 2,
    seed: 24,
  },
];

const round2 = (x: number) => Math.round(x * 100) / 100;
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

function rigOf(n: number): Rig {
  return RIGS[n % RIGS.length];
}

/** Now-line steps for a running worker, all pointing at the plan's file. */
function nowSteps(file: string): NowLine[] {
  return [
    { kind: "tool", tool: "read", text: `Reading ${file}` },
    { kind: "tool", tool: "edit", text: `Editing ${file}` },
    { kind: "tool", tool: "bash", text: "Running pnpm test" },
    { kind: "text", tool: null, text: "Working out the next change" },
    { kind: "tool", tool: "grep", text: `Searching ${file}` },
    { kind: "text", tool: null, text: "Waiting for review" },
    { kind: "tool", tool: "bash", text: "Running pnpm typecheck" },
  ];
}

type Draft = {
  n: number;
  rig: Rig;
  status: WorkerStatus;
  started_at: number;
  finished_at: number | null;
  last_activity_at: number;
  now: NowLine | null;
  summary: string[];
  flags: string[];
  tokens: number;
  log_size: number;
};

function previewOf(plan: Plan, d: Draft): WorkerPreview {
  const title = plan.titles[d.n % plan.titles.length];
  return {
    name: `w${d.n}`,
    model: d.rig.model,
    engine: d.rig.engine,
    started_at: d.started_at,
    now: d.now,
    status: d.status,
    pid: d.status === "running" ? 40_000 + d.n : null,
    title,
    goal: title,
    finished_at: d.finished_at,
    last_activity_at: d.last_activity_at,
    summary: d.summary,
    has_brief: true,
    has_report: d.status === "finished",
    log_stem: `w${d.n}`,
    log_size: d.log_size,
    log_format: d.rig.format,
    flags: d.flags,
    engine_family: d.rig.family,
    account: null,
    effort: null,
    tokens: d.tokens,
    cost: round2(d.tokens * d.rig.rate),
  };
}

function runningDraft(plan: Plan, n: number, v: number, tick: number, nowSec: number): Draft {
  // Ticks since this worker entered the running window.
  const elapsed = v - (n - plan.running + 1 - BASE) * EVENT_TICKS;
  const stale = plan.kind === "lagging";
  const steps = nowSteps(plan.file);
  const now: NowLine = stale
    ? { kind: "text", tool: null, text: "Waiting for the test runner" }
    : steps[Math.floor((tick + n * 3) / STEP_TICKS) % steps.length];
  return {
    n,
    rig: rigOf(n),
    status: "running",
    started_at: nowSec - elapsed * TICK_S,
    finished_at: null,
    last_activity_at: stale ? nowSec - STALE_S : nowSec - ((tick + n) % 5) * TICK_S,
    now,
    summary: [],
    flags: stale ? ["stale"] : [],
    tokens: 20_000 + (n % 5) * 8_000 + elapsed * 60,
    log_size: 18_000 + elapsed * 900,
  };
}

function finishedDraft(n: number, finishedAt: number): Draft {
  const failed = n % 7 === 3;
  const duration = 240 + ((n * 53) % 600);
  return {
    n,
    rig: rigOf(n),
    status: failed ? "exited" : "finished",
    started_at: finishedAt - duration,
    finished_at: finishedAt,
    last_activity_at: finishedAt,
    now: null,
    summary: failed ? [FAILED_SUMMARY] : [SUMMARIES[n % SUMMARIES.length], SUMMARIES[(n + 2) % SUMMARIES.length]],
    flags: [],
    tokens: 40_000 + (n % 7) * 15_000,
    log_size: 40_000 + (n % 7) * 6_000,
  };
}

/** Running workers first, then finished, as the backend sorts them. */
function workersFor(plan: Plan, tick: number, nowSec: number): WorkerPreview[] {
  const active = plan.kind === "active";
  const v = active ? tick + plan.seed : plan.seed;
  const event = active ? Math.floor(v / EVENT_TICKS) + BASE : BASE;

  const running: WorkerPreview[] = [];
  for (let n = event; n < event + plan.running; n++) {
    running.push(previewOf(plan, runningDraft(plan, n, v, tick, nowSec)));
  }

  const finished: WorkerPreview[] = [];
  for (let n = event - plan.finished; n < event; n++) {
    const k = n - (event - plan.finished);
    const finishedAt = active
      ? nowSec - (v - (n + 1 - BASE) * EVENT_TICKS) * TICK_S
      : nowSec - (plan.seed + k + 1) * HOUR_S;
    finished.push(previewOf(plan, finishedDraft(n, finishedAt)));
  }

  return [...running, ...finished];
}

function activitySeries(plan: Plan, tick: number, nowSec: number): ActivitySample[] {
  const newest = Math.floor(nowSec / BUCKET_S) * BUCKET_S;
  return Array.from({ length: SERIES_LEN }, (_, i) => {
    const at = newest - (SERIES_LEN - 1 - i) * BUCKET_S;
    if (plan.kind === "idle") return { at, count: 0 };
    if (plan.kind === "lagging") return { at, count: i < 6 ? 2 + (i % 3) : 0 };
    const wave = (tick + plan.seed + i * 3) % 4;
    return { at, count: 2 + i + wave + (i === SERIES_LEN - 1 ? 6 : 0) };
  });
}

function summarize(plan: Plan, tick: number, nowSec: number): SessionSummary {
  const workers = workersFor(plan, tick, nowSec);
  const running = workers.filter((w) => w.status === "running");
  const counts: Counts = {
    running: running.length,
    finished: workers.filter((w) => w.status === "finished").length,
    exited: workers.filter((w) => w.status === "exited").length,
    unknown: 0,
  };
  const runningWorkers: RunningWorker[] = running.map((w) => ({
    name: w.name,
    model: w.model,
    engine: w.engine,
    started_at: w.started_at,
    now: w.now,
  }));
  const activity = workers
    .map((w) => w.last_activity_at)
    .filter((t): t is number => t !== null);
  const [p1, p2, p3] = plan.findings;
  return {
    id: plan.id,
    project: plan.id,
    run: null,
    path: `/home/user/Projects/${plan.id}/.stage`,
    rev: plan.rev,
    status: plan.kind === "idle" ? "idle" : "active",
    counts,
    last_activity_at: activity.length > 0 ? Math.max(...activity) : null,
    latest_milestone: plan.milestone,
    findings_total: p1 + p2 + p3,
    findings_by_priority: { P1: p1, P2: p2, P3: p3 },
    running_workers: runningWorkers,
    lead: plan.lead,
    stream: plan.stream,
    cycle_progress: null,
    source_warnings: [],
    worker_previews: workers,
    worker_total: workers.length,
    activity_series: activitySeries(plan, tick, nowSec),
    tokens_total: sum(workers.map((w) => w.tokens ?? 0)),
    cost_total: round2(sum(workers.map((w) => w.cost ?? 0))),
  };
}

/**
 * Twelve demo sessions: three active, one lagging, eight idle. Pass the ticker
 * value and the current time in seconds.
 */
export function demoSessions(tick: number, nowSec: number): SessionSummary[] {
  return PLANS.map((plan) => summarize(plan, tick, nowSec));
}
