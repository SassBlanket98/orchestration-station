import { describe, expect, it } from "vitest";
import type { SessionSummary } from "../../api/types";
import { isPhoneNavActive, phoneNavItems } from "./phoneNav";

const session = (id: string, status: "active" | "idle"): SessionSummary => ({
  id, status, project: "project", run: null, path: "/tmp", rev: 1,
  counts: { running: 0, finished: 0, exited: 0, unknown: 0 },
  last_activity_at: null, latest_milestone: null, findings_total: 0, running_workers: [],
  lead: null, stream: null, cycle_progress: null, source_warnings: [], worker_previews: [],
  worker_total: 0, activity_series: [],
});

const item = (sessions: SessionSummary[], mock: boolean, search: string, key: string) => {
  const found = phoneNavItems(sessions, mock, search).find((entry) => entry.key === key);
  if (found === undefined) throw new Error(`no nav item ${key}`);
  return found;
};

describe("phone nav items", () => {
  it("lists Overview, Runs, Quota and Logs in mockup order", () => {
    expect(phoneNavItems([], false, "").map((entry) => entry.label)).toEqual(["Overview", "Runs", "Quota", "Logs"]);
  });

  it("keeps the query string on every target", () => {
    const sessions = [session("active one", "active")];
    const search = "?mock=1&density=compact";
    expect(item(sessions, false, search, "overview").to).toBe("/?mock=1&density=compact");
    expect(item(sessions, false, search, "runs").to).toBe("/runs?mock=1&density=compact");
    expect(item(sessions, false, search, "quota").to).toBe("/quota?mock=1&density=compact");
    expect(item(sessions, false, search, "logs").to).toBe("/logs?mock=1&density=compact");
  });

  it("points Runs at the runs list, and at the demo run in mock mode", () => {
    const sessions = [session("idle/first", "idle"), session("active second", "active")];
    expect(item(sessions, false, "", "runs").to).toBe("/runs");
    expect(item(sessions, true, "", "runs").to).toBe("/runs/idle%2Ffirst");
    expect(item([], true, "", "runs").to).toBeNull();
  });
});

describe("phone nav active state", () => {
  it("marks Runs active on every /runs/* page", () => {
    const runs = item([], false, "", "runs");
    expect(isPhoneNavActive(runs, "/runs/abc")).toBe(true);
    expect(isPhoneNavActive(runs, "/runs/abc/workers/fix1")).toBe(true);
    expect(isPhoneNavActive(runs, "/runsx")).toBe(false);
    expect(isPhoneNavActive(runs, "/")).toBe(false);
  });

  it("marks Overview active only on the root path", () => {
    const overview = item([], false, "", "overview");
    expect(isPhoneNavActive(overview, "/")).toBe(true);
    expect(isPhoneNavActive(overview, "/runs/abc")).toBe(false);
  });

  it("marks Quota and Logs active for their own paths only", () => {
    expect(isPhoneNavActive(item([], false, "", "quota"), "/quota")).toBe(true);
    expect(isPhoneNavActive(item([], false, "", "quota"), "/quotas")).toBe(false);
    expect(isPhoneNavActive(item([], false, "", "logs"), "/logs")).toBe(true);
    expect(isPhoneNavActive(item([], false, "", "logs"), "/quota")).toBe(false);
  });
});
