import type { Engine, Worker, WorkerStatus } from "../../api/types";

export type RunTab = "summary" | "timeline" | "workers" | "reviews";
export const RUN_TABS: { id: RunTab; label: string; suffix: string }[] = [
  { id: "summary", label: "Summary", suffix: "" },
  { id: "timeline", label: "Timeline", suffix: "/timeline" },
  { id: "workers", label: "Workers", suffix: "/workers" },
  { id: "reviews", label: "Reviews", suffix: "/reviews" },
];

export function tabFromPath(pathname: string): RunTab {
  if (/\/workers\/[^/]+\/?$/.test(pathname)) return "workers";
  const tail = pathname.split("/").filter(Boolean).at(-1);
  return RUN_TABS.find((tab) => tab.suffix !== "" && tab.suffix.slice(1) === tail)?.id ?? "summary";
}

export const RUN_STATUSES = ["all", "running", "finished", "exited", "unknown"] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];
export function statusFromSearch(search: string): RunStatus {
  const value = new URLSearchParams(search).get("status");
  return RUN_STATUSES.find((status) => status === value) ?? "all";
}

export function elapsedSeconds(startedAt: number | null, finishedAt: number | null, now: number, status: WorkerStatus): number | null {
  if (startedAt === null) return null;
  const end = finishedAt ?? (status === "running" ? now : null);
  return end === null ? null : Math.max(0, Math.floor(end - startedAt));
}

export function timelineRange(times: number[], live: boolean, now: number): { start: number; end: number } | null {
  if (!times.length) return null;
  return { start: Math.min(...times), end: Math.max(...times, ...(live ? [now] : [])) };
}

export function timelineTicks(start: number, end: number) {
  return Array.from({ length: 5 }, (_, index) => {
    const at = start + ((end - start) * index) / 4;
    return { at, left: index * 25, label: new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) };
  });
}

export function filterWorkers(workers: Worker[], status: WorkerStatus | "all"): Worker[] {
  return status === "all" ? workers : workers.filter((worker) => worker.status === status);
}

export function sourceValue(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === "" ? "?" : String(value);
}

export function engineColor(engine: Engine): string {
  return ({ claude: "var(--cl)", codex: "var(--cx)", cursor: "var(--cu)", opencode: "var(--oc)", unknown: "var(--dim)" })[engine];
}

export type TimelineMark = { at: number | null; kind: string; label: string };
export type TimelinePoint = { left: number; at: number; kind: string; label: string };
export function timelinePositions(start: number, end: number, marks: TimelineMark[]): TimelinePoint[] {
  const span = Math.max(1, end - start);
  return marks.flatMap((mark) => mark.at === null ? [] : [{
    left: Math.max(0, Math.min(100, ((mark.at - start) / span) * 100)),
    at: mark.at,
    kind: mark.kind,
    label: mark.label,
  }]);
}

export function hasTimedEvent(events: { at: number | null }[]): boolean {
  return events.some((event) => event.at !== null);
}
