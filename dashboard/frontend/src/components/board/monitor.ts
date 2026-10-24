import type { MonitorGroup, MonitorMilestone, MonitorResponse, MonitorStage, SessionSummary } from "../../api/types";

// Monitor's stage tree, overlaid on Board rows and the masthead. Pure: no React, no fetching.

export const CTX_WARN = 150_000;
export const CTX_BAD = 250_000;

// A stage path may carry a `#<session id>` suffix; the directory is the part before it. Trailing slashes go too.
export function stageDir(path: string): string {
  const dir = path.split("#")[0];
  return dir.length > 1 ? dir.replace(/\/+$/, "") : dir;
}

// Live stages first, then the newest activity. Equal keys keep the order the backend sent (Array sort is stable).
function preference(a: MonitorStage, b: MonitorStage): number {
  const live = Number(b.lead_state === "live") - Number(a.lead_state === "live");
  return live || b.last_activity - a.last_activity;
}

export function matchStage(session: SessionSummary, stages: MonitorStage[]): MonitorStage | null {
  const dir = stageDir(session.path);
  if (!dir) return null;
  const matches = stages.filter((stage) => stageDir(stage.path) === dir);
  if (!matches.length) return null;
  return matches.sort(preference)[0];
}

// The stages to overlay. Offline or missing state means no overlay at all, so every row renders as before.
export function onlineStages(data: MonitorResponse | null): MonitorStage[] {
  return data?.online && Array.isArray(data.state?.stages) ? data.state.stages : [];
}

export function ctxLevel(ctx: number | null): "ok" | "warn" | "bad" | null {
  if (ctx == null) return null;
  if (ctx > CTX_BAD) return "bad";
  if (ctx >= CTX_WARN) return "warn";
  return "ok";
}

export function formatCtx(ctx: number | null): string {
  return ctx == null ? "—" : `${Math.round(ctx / 1000)}k`;
}

// Modifier class for a harness tag. Unknown tags get "" and keep the muted colour.
const TAG_CLASS: Record<string, string> = {
  "CC·W": "cc-w",
  "CC·P": "cc-p",
  CDX: "cdx",
  OCode: "ocode",
  OClaw: "oclaw",
  CUR: "cur",
};
export function tagClass(tag: string): string {
  return TAG_CLASS[tag] ?? "";
}

export type Banner = { kind: "working" | "quiet" | "offline"; text: string };

const pad = (value: number) => String(value).padStart(2, "0");

// Local HH:MM for a Unix-second timestamp, or "" when there is none. Used for milestone lines.
export function formatClock(seconds: number | null): string {
  if (seconds == null) return "";
  const at = new Date(seconds * 1000);
  return `${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/** One milestone as a line: `HH:MM text`, or just `text` when the timestamp is missing. */
export function formatMilestone(milestone: MonitorMilestone): string {
  const clock = formatClock(milestone.ts);
  return clock ? `${clock} ${milestone.text}` : milestone.text;
}

/**
 * The milestone line for a row. A matched stage's own latest milestone wins; otherwise the session's
 * own `latest_milestone` string is used. Falls back to a placeholder when neither exists.
 */
export function milestoneLine(stage: MonitorStage | null, session: SessionSummary): string {
  const latest = stage?.latest_milestone;
  const text = latest?.text || session.latest_milestone;
  if (!text) return "No milestone recorded";
  const clock = latest?.text ? formatClock(latest.ts) : "";
  return clock ? `${clock} ${text}` : text;
}

// Fold-state keys for task groups, under the existing localStorage toggles. `g:<id>/<index>` is one
// group's line (index 1 is the previous group, 2+ the older ones); `g:<id>/earlier` is the one line
// that folds every older group.
export const groupKey = (sessionId: string, index: number) => `g:${sessionId}/${index}`;
export const earlierKey = (sessionId: string) => `g:${sessionId}/earlier`;

/** A task group's fold line and the session workers it owns. */
export type StageGroupWorkers = { label: string; done: number; total: number; workers: string[] };

/**
 * The session's worker names split by the matched stage's task groups: the current group (the one flagged
 * `current`, else the first), the previous group (the next one, in the payload's newest-first order) and the older ones.
 * A worker named in no group is not part of the current task (Monitor lists only workers linked to the
 * session), so it folds into the oldest group.
 * Returns null when there is nothing to group (no groups, or fewer than two), so the row renders as before.
 * A group keeps only names the session actually has; a name repeated across groups belongs to the first one listed.
 */
export function groupWorkers(groups: MonitorGroup[] | undefined, workerNames: string[]): {
  current: string[];
  previous: StageGroupWorkers | null;
  older: StageGroupWorkers[];
} | null {
  if (!groups || groups.length < 2) return null;
  const flagged = groups.findIndex((group) => group.current);
  const order = groups.map((_, index) => index);
  const currentIndex = flagged >= 0 ? flagged : 0;
  order.splice(order.indexOf(currentIndex), 1);
  order.unshift(currentIndex);
  const ordered = order.map((index) => groups[index]);
  const owner = new Map<string, number>();
  ordered.forEach((group, index) => {
    for (const name of group.workers ?? []) if (!owner.has(name)) owner.set(name, index);
  });
  const byGroup: string[][] = ordered.map(() => []);
  for (const name of workerNames) byGroup[owner.get(name) ?? ordered.length - 1].push(name);
  const detail = (index: number): StageGroupWorkers => ({
    label: ordered[index].label,
    done: ordered[index].done,
    total: ordered[index].total,
    workers: byGroup[index],
  });
  return { current: byGroup[0], previous: detail(1), older: ordered.slice(2).map((_, offset) => detail(offset + 2)) };
}

export function bannerState(data: MonitorResponse | null): Banner {
  const stages = onlineStages(data);
  if (!data?.online || !Array.isArray(data.state?.stages)) return { kind: "offline", text: "Monitor offline" };
  if (stages.some((stage) => stage.section === "active" || stage.live > 0)) return { kind: "working", text: "Workers are working" };
  const newest = stages.reduce((max, stage) => Math.max(max, stage.last_activity || 0), 0);
  if (!newest) return { kind: "quiet", text: "All quiet." };
  const at = new Date(newest * 1000);
  return { kind: "quiet", text: `All quiet. last finished ${pad(at.getHours())}:${pad(at.getMinutes())}` };
}
