import type { ActivitySample } from "../../api/types";
import type { TreeWorkerState } from "./state";

export const HEARTBEAT_CELLS = 30;

export function isLiveState(state: TreeWorkerState): boolean {
  return state === "running" || state === "quiet" || state === "stalled";
}

export const formatCount = (value: number | null | undefined) => (value == null ? "—" : value.toLocaleString());
export const formatMoney = (value: number | null | undefined) => (value == null ? "—" : `$${value.toFixed(2)}`);

/** Elapsed time in seconds, with an honest dash when the start or end is unavailable. */
export function formatElapsed(start: number | null, end: number): string {
  if (start == null || end - start < 1) return "—";
  const seconds = Math.max(0, Math.floor(end - start));
  const hours = Math.floor(seconds / 3600);
  if (hours) return `${hours}h ${String(Math.floor((seconds % 3600) / 60)).padStart(2, "0")}m`;
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}m ${String(seconds % 60).padStart(2, "0")}s`;
}

export function workerMark(state: TreeWorkerState): "spin" | "done" | "lag" | "fail" | "none" {
  if (state === "running") return "spin";
  if (state === "quiet" || state === "stalled") return "lag";
  if (state === "failed") return "fail";
  if (state === "done") return "done";
  return "none";
}

/** Aggregate activity bars are retained in the masthead stats view. */
export function heartbeat(series: ActivitySample[][], cells = HEARTBEAT_CELLS): number[] {
  const buckets = series.reduce<number[]>((sum, samples) => {
    samples.forEach(({ count }, index) => { sum[index] = (sum[index] ?? 0) + count; });
    return sum;
  }, []);
  if (!buckets.length) return Array.from({ length: cells }, () => 0);
  const peak = Math.max(...buckets);
  return Array.from({ length: cells }, (_, cell) => {
    const position = buckets.length === 1 ? 0 : (cell * (buckets.length - 1)) / (cells - 1);
    const low = Math.floor(position);
    const high = Math.min(buckets.length - 1, low + 1);
    const value = buckets[low] + (buckets[high] - buckets[low]) * (position - low);
    return peak > 0 ? value / peak : 0;
  });
}
