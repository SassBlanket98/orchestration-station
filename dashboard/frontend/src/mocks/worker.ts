// Illustrative worker detail for `?mock=1`. It matches the approved worker
// modal mockup (fix1, st-f1, audit-expanded-ui, cur-proc). The fixtures in
// fixtures.ts do not carry these workers, and some fields are null there. These
// values are not production facts.
import type { LogSizeSample, StepEvent, Worker, WorkerEvent } from "../api/types";
import { fileEntries, type WorkerFile, type WorkerView } from "../components/worker/evidence";

/** Tab order of the mock run's workers, as the mockup's worker board lists them. */
export const MOCK_WORKER_NAMES: readonly string[] = [
  "fix1",
  "st-f1",
  "audit-expanded-ui",
  "cur-proc",
];

// Captured once so the mock timestamps stay stable across renders.
const T0 = Date.now() / 1000;
const ago = (seconds: number): number => T0 - seconds;

type MockWorker = Pick<Worker, "name" | "status" | "engine" | "log_format"> &
  Partial<Worker>;

function mockWorker(o: MockWorker): Worker {
  return {
    pid: null,
    model: null,
    title: null,
    goal: null,
    has_brief: true,
    has_report: false,
    started_at: null,
    finished_at: null,
    last_activity_at: null,
    now: null,
    summary: [],
    log_stem: o.name,
    log_size: 0,
    flags: [],
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
    duration_s: null,
    log_mtime: null,
    artifact_count: 0,
    ...o,
  };
}

function series(points: [number, number][]): LogSizeSample[] {
  return points.map(([secondsAgo, size]) => ({ at: ago(secondsAgo), size }));
}

const fix1Steps: StepEvent[] = [
  { seq: 37, kind: "exec", text: "apply_patch server/collector/scan.ts", duration_ms: 44, ok: true, at: null },
  { seq: 38, kind: "exec", text: "pnpm -C server exec tsc --noEmit", duration_ms: null, ok: false, at: null },
  { seq: 39, kind: "exec", text: 'rg -n "rssBytes" server/', duration_ms: 31, ok: true, at: null },
  { seq: 40, kind: "exec", text: "sed -n '190,240p' server/collector/scan.ts", duration_ms: 12, ok: true, at: null },
  { seq: 41, kind: "exec", text: "uv run pytest tests/test_scan.py -q", duration_ms: 3992, ok: true, at: null },
];

const fix1Events: WorkerEvent[] = [
  { kind: "stdin_hang", at: ago(15 * 60), text: "attempt 1 hung on stdin · “Reading additional input from stdin...” · 2m 01s" },
  { kind: "retry", at: ago(13 * 60), text: "retry 1 · attempt 2 live, banner seen" },
];

const cursorIdle: WorkerEvent[] = [
  { kind: "idle", at: ago(4 * 60 + 12), text: "pid alive, log not growing for 4 m" },
];

const procEvents: WorkerEvent[] = [
  { kind: "permission_reject", at: ago(3 * 60), text: "external_directory auto-rejected" },
];

const procSteps: StepEvent[] = [
  { seq: 35, kind: "tool_use", text: "read server/collector/proc.ts", duration_ms: 820, ok: true, at: ago(140) },
  { seq: 36, kind: "tool_use", text: "grep -rn readProc server", duration_ms: 410, ok: true, at: ago(120) },
  { seq: 37, kind: "tool_use", text: "read server/collector/proc.ts", duration_ms: 690, ok: true, at: ago(60) },
  { seq: 38, kind: "tool_use", text: "external_directory /usr/lib", duration_ms: null, ok: false, at: ago(30) },
];

const fix1Patch = [
  "@@ -208,9 +208,12 @@ scanProcs",
  " for (const p of procs) {",
  "-  total.rssBytes += p.rssBytes;",
  "+  if (seenMm.has(p.mmId)) continue;",
  "+  seenMm.add(p.mmId);",
  "+  total.rssBytes += p.rssBytes;",
  " }",
  "+// one /proc pass; mm ids dedupe threads",
  "+",
  " return total;",
  "",
].join("\n");

const workers: Record<string, WorkerView> = {};

function add(worker: Worker, extra: { brief?: string | null; report?: string | null; patch?: string | null; files?: WorkerFile[] } = {}): void {
  workers[worker.name] = {
    worker,
    brief_md: extra.brief ?? null,
    report_md: extra.report ?? null,
    patch: extra.patch ?? null,
    files: extra.files ?? fileEntries(worker),
  };
}

add(
  mockWorker({
    name: "fix1",
    status: "running",
    engine: "codex",
    engine_family: "codex",
    log_format: "codex-text",
    pid: 3388997,
    model: "gpt-6-luna",
    effort: "medium",
    account: "personal",
    access: "rw",
    access_source: "codex sandbox workspace-write",
    job: "build",
    title: "Fix RSS double count in collector scan",
    goal: "Stop RSS being summed once per thread in scanProcs; keep the single /proc pass.",
    started_at: ago(15 * 60 + 12),
    last_activity_at: ago(4),
    now: { kind: "tool", tool: "exec", text: "exec #41 succeeded in 3992ms · uv run pytest tests/test_scan.py -q" },
    log_size: 212_000,
    flags: ["retry"],
    worker_events: fix1Events,
    log_series: series([[600, 32_000], [420, 92_000], [240, 150_000], [60, 200_000], [0, 212_000]]),
    step_events: fix1Steps,
  }),
  {
    brief: [
      "# Fix RSS double count in collector scan",
      "",
      "Goal: stop RSS being summed once per thread in scanProcs; keep the single /proc pass.",
      "",
      "Paths: server/collector/scan.ts, tests/test_scan.py",
      "Out of scope: UI, history, commits.",
      "Output: fix1/REPORT.md · Delegation: none",
    ].join("\n"),
    patch: fix1Patch,
    files: [
      ...fileEntries({ name: "fix1", log_stem: "fix1", log_size: 212_000, has_brief: true, has_report: false }),
      { name: "changes.patch", kind: "patch", size: 1900, present: true },
      { name: "fix1.attempt1.log", kind: "attempt", size: 41, present: true },
    ],
  },
);

add(
  mockWorker({
    name: "st-f1",
    status: "running",
    engine: "claude",
    engine_family: "claude-work",
    log_format: "claude-stream-json",
    pid: 3391204,
    model: "claude-sonnet-5-5",
    effort: "medium",
    account: "work",
    job: "build",
    title: "F1 overview rows and engine badge",
    goal: "Build the overview run rows and the engine badge.",
    started_at: ago(16 * 60 + 3),
    now: { kind: "raw", tool: null, text: "claude -p writes its answer on exit" },
    log_size: 0,
    log_series: series([[600, 0], [0, 0]]),
  }),
  { brief: "# F1 overview rows and engine badge\n\nGoal: build the overview run rows and the engine badge." },
);

add(
  mockWorker({
    name: "audit-expanded-ui",
    status: "running",
    engine: "cursor",
    engine_family: "cursor",
    log_format: "cursor-text",
    pid: 3392760,
    model: "composer-2.5",
    access: "ro",
    access_source: "cursor mode ask",
    job: "read",
    title: "Audit expanded worker rows (read-only)",
    goal: "Read-only audit of the expanded worker rows.",
    started_at: ago(14 * 60 + 8),
    last_activity_at: ago(4 * 60 + 12),
    now: { kind: "text", tool: null, text: "# audit-expanded-ui (read-only)" },
    log_size: 3100,
    has_report: true,
    flags: ["idle"],
    worker_events: cursorIdle,
    log_series: series([[600, 3100], [0, 3100]]),
  }),
  {
    brief: "# Audit expanded worker rows (read-only)\n\nGoal: read-only audit of the expanded worker rows.",
    report: "## Summary\nNo expanded-row regressions found in the read-only pass.\n",
  },
);

add(
  mockWorker({
    name: "cur-proc",
    status: "running",
    engine: "opencode",
    engine_family: "opencode",
    log_format: "opencode-jsonl",
    pid: 3394412,
    model: "deepseek-v4.1-flash",
    account: "opencode-go",
    title: "Map /proc readers in collector",
    goal: "Map the /proc readers used by the collector.",
    started_at: ago(12 * 60 + 31),
    last_activity_at: ago(1),
    now: { kind: "tool", tool: "read", text: "tool_use read completed · step 37 · server/collector/proc.ts" },
    log_size: 684_000,
    has_report: true,
    flags: ["permission"],
    worker_events: procEvents,
    log_series: series([[600, 74_000], [300, 310_000], [0, 684_000]]),
    step_events: procSteps,
    tokens: 148_000,
    tokens_source: "opencode step_finish",
  }),
  {
    brief: "# Map /proc readers in collector\n\nGoal: map the /proc readers used by the collector.",
    report: "## Summary\nDraft map of the /proc readers; still reading.\n",
  },
);

/** The mock view for a worker name, or null when the mock run does not have it. */
export function workerMockView(name: string): WorkerView | null {
  return workers[name] ?? null;
}
