import type { WorkerPreview } from "../../api/types";

export type TreeWorkerState = "running" | "quiet" | "stalled" | "done" | "no-report" | "failed";

export function deriveWorkerState(worker: WorkerPreview, now = Date.now()): TreeWorkerState {
  const flags = (worker.flags ?? []).join(" ").toLowerCase();
  if (flags.includes("failed") || flags.includes("failure")) return "failed";
  if (worker.status === "running") {
    const idleFor = worker.last_activity_at == null ? 0 : now - worker.last_activity_at * 1000;
    return idleFor >= 300_000 ? "stalled" : idleFor >= 90_000 ? "quiet" : "running";
  }
  if (worker.status === "finished" || worker.status === "exited") return worker.has_report ? "done" : "no-report";
  return "failed";
}
