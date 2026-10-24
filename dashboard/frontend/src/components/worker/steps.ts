// Step feed helpers. Step events come only from engines whose logs carry
// structured steps (Codex exec, OpenCode tool_use). Prose logs have none, and
// the UI must say so rather than infer steps.
import type { StepEvent, Worker } from "../../api/types";
import { formatDuration } from "../../lib/format";

/** "3992ms" under one minute, then "1m 04s". Null is an em dash (unknown). */
export function formatStepDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 60_000) return `${Math.round(ms)}ms`;
  return formatDuration(ms / 1000);
}

export type StepOutcome = { label: string; tone: "good" | "bad" | "dim" };

/** `ok` true → ok, false → failed, null → not reported. */
export function stepOutcome(ok: boolean | null): StepOutcome {
  if (ok === true) return { label: "ok", tone: "good" };
  if (ok === false) return { label: "failed", tone: "bad" };
  return { label: "—", tone: "dim" };
}

/** Newest step first, as the feed reads. Returns a copy. */
export function newestFirst(steps: StepEvent[]): StepEvent[] {
  return [...steps].sort((a, b) => b.seq - a.seq);
}

/** Bar heights as a 0..1 ratio of the longest known duration. Unknown durations get the minimum bar. */
export function stepBarRatios(steps: StepEvent[], minRatio = 0.08): number[] {
  const known = steps
    .map((s) => s.duration_ms)
    .filter((d): d is number => d !== null && Number.isFinite(d) && d >= 0);
  const max = known.length > 0 ? Math.max(...known) : 0;
  return steps.map((s) => {
    if (s.duration_ms === null || max === 0) return minRatio;
    return Math.max(minRatio, s.duration_ms / max);
  });
}

// Formats whose logs are free text; none of them yields step events today.
const PROSE_FORMATS: ReadonlySet<Worker["log_format"]> = new Set([
  "claude-stream-json",
  "cursor-text",
  "cursor-jsonl",
  "text",
]);

export type StepSource =
  | { kind: "steps"; count: number }
  | { kind: "none"; reason: string };

/** Which source the step feed can show, and why it is empty when it cannot. */
export function stepSource(worker: Pick<Worker, "step_events" | "log_format">): StepSource {
  if (worker.step_events.length > 0) {
    return { kind: "steps", count: worker.step_events.length };
  }
  if (worker.log_format === "missing" || worker.log_format === "empty") {
    return { kind: "none", reason: "no log yet" };
  }
  if (PROSE_FORMATS.has(worker.log_format)) {
    return { kind: "none", reason: "prose log · no step events" };
  }
  return { kind: "none", reason: "no step events" };
}
