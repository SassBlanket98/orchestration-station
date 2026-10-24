// Pure alert derivation for the bell dropdown. No React imports, so the tests run in node.
import type { SessionSummary, WorkerPreview } from "../../api/types";

export type AlertKind = "stdin_hang" | "idle" | "review" | "exited";

export type AlertItem = {
  id: string;
  kind: AlertKind;
  sessionId: string;
  text: string;
  /** Unix seconds, or null when the source has no timestamp (rendered as "?"). */
  at: number | null;
  /** Evidence path without search; the menu appends the current location.search. */
  href: string;
};

export const ALERT_WINDOW_S = 24 * 60 * 60;

/** Flags on a running worker that mean it has stopped producing output. stage.py sets "stale". */
const IDLE_FLAGS = ["idle", "stale"];

const KIND_ORDER: Record<AlertKind, number> = { stdin_hang: 0, idle: 1, review: 2, exited: 3 };

/** Active runs, plus idle runs whose last activity falls inside the 24 h window. */
export function inAlertScope(session: SessionSummary, nowS: number): boolean {
  if (session.status === "active") return true;
  return session.last_activity_at !== null && nowS - session.last_activity_at <= ALERT_WINDOW_S;
}

function runLabel(session: SessionSummary): string {
  return session.run ? `${session.project} / ${session.run}` : session.project;
}

function sessionPath(sessionId: string): string {
  return `/runs/${encodeURIComponent(sessionId)}`;
}

function workerPath(sessionId: string, name: string): string {
  return `${sessionPath(sessionId)}/workers/${encodeURIComponent(name)}`;
}

function workerAlert(
  session: SessionSummary,
  worker: WorkerPreview,
  nowS: number,
): AlertItem | null {
  const base = { sessionId: session.id, href: workerPath(session.id, worker.name) };
  if (worker.status === "running") {
    if (worker.flags.includes("stdin_hang")) {
      return {
        ...base,
        id: `stdin_hang:${session.id}:${worker.name}`,
        kind: "stdin_hang",
        text: `${worker.name} · stdin hang`,
        at: worker.last_activity_at,
      };
    }
    if (worker.flags.some((flag) => IDLE_FLAGS.includes(flag))) {
      return {
        ...base,
        id: `idle:${session.id}:${worker.name}`,
        kind: "idle",
        text: `${worker.name} · idle`,
        at: worker.last_activity_at,
      };
    }
    return null;
  }
  if (worker.status === "exited" && !worker.has_report) {
    // Old exits are history; an unknown finish time stays visible as "?".
    if (worker.finished_at !== null && nowS - worker.finished_at > ALERT_WINDOW_S) return null;
    return {
      ...base,
      id: `exited:${session.id}:${worker.name}`,
      kind: "exited",
      text: `${worker.name} · exited, no report`,
      at: worker.finished_at,
    };
  }
  return null;
}

/**
 * Alerts for every run in scope, latest first. Items with a null time sort last.
 * Review findings come from the summary count, so they link to the Reviews tab and have no time.
 */
export function deriveAlerts(sessions: readonly SessionSummary[], nowS: number): AlertItem[] {
  const alerts: AlertItem[] = [];
  for (const session of sessions) {
    if (!inAlertScope(session, nowS)) continue;
    for (const worker of session.worker_previews) {
      const alert = workerAlert(session, worker, nowS);
      if (alert) alerts.push(alert);
    }
    if (session.findings_total > 0) {
      const count = session.findings_total;
      alerts.push({
        id: `review:${session.id}`,
        kind: "review",
        sessionId: session.id,
        text: `${runLabel(session)} · ${count} ${count === 1 ? "finding" : "findings"}`,
        at: null,
        href: `${sessionPath(session.id)}/reviews`,
      });
    }
  }
  return alerts.sort(compareAlerts);
}

export function compareAlerts(a: AlertItem, b: AlertItem): number {
  if (a.at !== b.at) {
    if (a.at === null) return 1;
    if (b.at === null) return -1;
    return b.at - a.at;
  }
  if (a.kind !== b.kind) return KIND_ORDER[a.kind] - KIND_ORDER[b.kind];
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/** HH:MM in local time, or "?" when the source has no timestamp. */
export function formatAlertTime(at: number | null): string {
  if (at === null) return "?";
  const date = new Date(at * 1000);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/** Identity of one alert occurrence. A new timestamp makes a dismissed alert come back. */
export function alertKey(item: AlertItem): string {
  return `${item.id}@${item.at ?? "?"}`;
}

/** The alerts that were not cleared. */
export function withoutDismissed(items: readonly AlertItem[], dismissed: ReadonlySet<string>): AlertItem[] {
  return items.filter((item) => !dismissed.has(alertKey(item)));
}
