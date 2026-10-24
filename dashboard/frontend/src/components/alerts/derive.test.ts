import { describe, expect, it } from "vitest";
import type { SessionSummary, WorkerPreview } from "../../api/types";
import { alertsMockFixture } from "../../mocks/alerts";
import { ALERT_WINDOW_S, deriveAlerts, formatAlertTime, inAlertScope } from "./derive";

const NOW = 1_800_000_000;
const HOUR = 3600;

const worker = (over: Partial<WorkerPreview> & { name: string }): WorkerPreview => ({
  model: null, engine: "codex", started_at: NOW - HOUR, now: null, status: "running", pid: 1,
  title: null, goal: null, finished_at: null, last_activity_at: NOW - 60, summary: [],
  has_brief: true, has_report: false, log_stem: null, log_size: 0, log_format: "text",
  flags: [], engine_family: null, account: null, effort: null, ...over,
});

const session = (over: Partial<SessionSummary> & { id: string }): SessionSummary => ({
  project: "quota-service", run: null, path: "/tmp", rev: 1, status: "active",
  counts: { running: 0, finished: 0, exited: 0, unknown: 0 },
  last_activity_at: NOW - 60, latest_milestone: null, findings_total: 0, running_workers: [],
  lead: null, stream: null, cycle_progress: null, source_warnings: [], worker_previews: [],
  worker_total: 0, activity_series: [], ...over,
});

describe("deriveAlerts", () => {
  it("derives a stdin hang from a running worker flag", () => {
    const [alert] = deriveAlerts([session({ id: "s1", worker_previews: [worker({ name: "fix1", flags: ["stdin_hang"] })] })], NOW);
    expect(alert).toEqual({
      id: "stdin_hang:s1:fix1", kind: "stdin_hang", sessionId: "s1",
      text: "fix1 · stdin hang", at: NOW - 60, href: "/runs/s1/workers/fix1",
    });
  });

  it("derives idle from the idle or stale flag on a running worker", () => {
    const alerts = deriveAlerts([session({
      id: "s1",
      worker_previews: [worker({ name: "a", flags: ["idle"] }), worker({ name: "b", flags: ["stale"] })],
    })], NOW);
    expect(alerts.map((a) => a.kind)).toEqual(["idle", "idle"]);
    expect(alerts.map((a) => a.text)).toEqual(["a · idle", "b · idle"]);
  });

  it("ignores stall flags on workers that are not running", () => {
    const alerts = deriveAlerts([session({
      id: "s1", worker_previews: [worker({ name: "p0", status: "exited", has_report: true, flags: ["stdin_hang", "stale"] })],
    })], NOW);
    expect(alerts).toEqual([]);
  });

  it("derives review findings per run, linking to the reviews tab with no time", () => {
    const [alert] = deriveAlerts([session({
      id: "s1", run: "ram", findings_total: 3, last_activity_at: NOW - 60,
    })], NOW);
    expect(alert).toEqual({
      id: "review:s1", kind: "review", sessionId: "s1",
      text: "quota-service / ram · 3 findings", at: null, href: "/runs/s1/reviews",
    });
  });

  it("uses the singular for one finding", () => {
    const [alert] = deriveAlerts([session({ id: "s1", findings_total: 1 })], NOW);
    expect(alert.text).toBe("quota-service · 1 finding");
  });

  it("derives exited without report using finished_at, and keeps a null finish time", () => {
    const alerts = deriveAlerts([session({
      id: "s1",
      worker_previews: [
        worker({ name: "t2", status: "exited", finished_at: NOW - 120 }),
        worker({ name: "wt-score", status: "exited", finished_at: null }),
        worker({ name: "done", status: "exited", has_report: true, finished_at: NOW - 120 }),
      ],
    })], NOW);
    expect(alerts.map((a) => [a.id, a.at, a.href])).toEqual([
      ["exited:s1:t2", NOW - 120, "/runs/s1/workers/t2"],
      ["exited:s1:wt-score", null, "/runs/s1/workers/wt-score"],
    ]);
  });

  it("returns nothing for a run with no alert conditions", () => {
    expect(deriveAlerts([session({ id: "s1", worker_previews: [worker({ name: "ok" })] })], NOW)).toEqual([]);
  });
});

describe("24 h cutoff", () => {
  it("includes active runs regardless of last activity", () => {
    expect(inAlertScope(session({ id: "s", status: "active", last_activity_at: NOW - 10 * 24 * HOUR }), NOW)).toBe(true);
  });

  it("includes an idle run inside the window and excludes one outside it", () => {
    expect(inAlertScope(session({ id: "s", status: "idle", last_activity_at: NOW - ALERT_WINDOW_S + 1 }), NOW)).toBe(true);
    expect(inAlertScope(session({ id: "s", status: "idle", last_activity_at: NOW - ALERT_WINDOW_S - 1 }), NOW)).toBe(false);
    expect(inAlertScope(session({ id: "s", status: "idle", last_activity_at: null }), NOW)).toBe(false);
  });

  it("drops alerts from idle runs older than 24 h", () => {
    const old = session({
      id: "old", status: "idle", last_activity_at: NOW - 2 * ALERT_WINDOW_S, findings_total: 2,
      worker_previews: [worker({ name: "x", flags: ["stdin_hang"] })],
    });
    expect(deriveAlerts([old], NOW)).toEqual([]);
  });

  it("drops exited-without-report workers that finished more than 24 h ago, even in an active run", () => {
    const alerts = deriveAlerts([session({
      id: "s1",
      worker_previews: [
        worker({ name: "stale-exit", status: "exited", finished_at: NOW - ALERT_WINDOW_S - 1 }),
        worker({ name: "fresh-exit", status: "exited", finished_at: NOW - ALERT_WINDOW_S + 1 }),
      ],
    })], NOW);
    expect(alerts.map((a) => a.text)).toEqual(["fresh-exit · exited, no report"]);
  });
});

describe("ordering", () => {
  it("lists latest first, puts null times last, and keeps the order stable", () => {
    const alerts = deriveAlerts([
      session({
        id: "a",
        findings_total: 1,
        worker_previews: [
          worker({ name: "older", flags: ["idle"], last_activity_at: NOW - 600 }),
          worker({ name: "newest", flags: ["stdin_hang"], last_activity_at: NOW - 30 }),
          worker({ name: "unknown", status: "exited", finished_at: null }),
        ],
      }),
      session({ id: "b", worker_previews: [worker({ name: "middle", flags: ["stdin_hang"], last_activity_at: NOW - 300 })] }),
    ], NOW);
    expect(alerts.map((a) => a.text)).toEqual([
      "newest · stdin hang",
      "middle · stdin hang",
      "older · idle",
      "quota-service · 1 finding",
      "unknown · exited, no report",
    ]);
  });

  it("breaks equal times by kind, then id", () => {
    const alerts = deriveAlerts([session({
      id: "s1",
      worker_previews: [
        worker({ name: "z", flags: ["idle"], last_activity_at: NOW }),
        worker({ name: "a", flags: ["stdin_hang"], last_activity_at: NOW }),
      ],
    })], NOW);
    expect(alerts.map((a) => a.kind)).toEqual(["stdin_hang", "idle"]);
  });
});

describe("links", () => {
  it("encodes session ids and worker names in worker and review paths", () => {
    const alerts = deriveAlerts([session({
      id: "run:abc/def", findings_total: 1,
      worker_previews: [worker({ name: "a b", flags: ["idle"] })],
    })], NOW);
    expect(alerts.map((a) => a.href)).toEqual([
      "/runs/run%3Aabc%2Fdef/workers/a%20b",
      "/runs/run%3Aabc%2Fdef/reviews",
    ]);
  });
});

describe("formatAlertTime", () => {
  it("shows a question mark for an unknown time", () => {
    expect(formatAlertTime(null)).toBe("?");
  });

  it("shows zero-padded local HH:MM", () => {
    const at = new Date(2026, 9, 8, 9, 5).getTime() / 1000;
    expect(formatAlertTime(at)).toBe("09:05");
  });
});

describe("mock fixture", () => {
  it("matches the mockup's four alert kinds, with the unknown time last", () => {
    const items = alertsMockFixture(NOW);
    expect(items.map((item) => item.kind)).toEqual(["idle", "stdin_hang", "review", "exited"]);
    expect(items.at(-1)?.at).toBeNull();
  });
});
