import { describe, expect, it } from "vitest";
import type { SessionSummary } from "../../api/types";
import { isTabActive, nextRadioIndex, runsTarget, withSearch } from "./navigation";

const session = (id: string, status: "active" | "idle"): SessionSummary => ({
  id, status, project: "project", run: null, path: "/tmp", rev: 1,
  counts: { running: 0, finished: 0, exited: 0, unknown: 0 },
  last_activity_at: null, latest_milestone: null, findings_total: 0, running_workers: [],
  lead: null, stream: null, cycle_progress: null, source_warnings: [], worker_previews: [],
  worker_total: 0, activity_series: [],
});

describe("shell navigation helpers", () => {
  it("preserves the current query string", () => {
    expect(withSearch("/quota", "?mock=1&x=2")).toBe("/quota?mock=1&x=2");
  });

  it("opens the runs list, or the first fixture in mock mode", () => {
    const sessions = [session("idle/first", "idle"), session("active second", "active")];
    expect(runsTarget(sessions, false)).toBe("/runs");
    expect(runsTarget([], false)).toBe("/runs");
    expect(runsTarget(sessions, true)).toBe("/runs/idle%2Ffirst");
    expect(runsTarget([], true)).toBeNull();
  });

  it("marks a tab active for its own path and nested paths only", () => {
    expect(isTabActive("/", "/")).toBe(true);
    expect(isTabActive("/", "/runs/abc")).toBe(false);
    expect(isTabActive("/runs", "/runs/abc/timeline")).toBe(true);
    expect(isTabActive("/runs", "/runs/abc")).toBe(true);
    expect(isTabActive("/runs", "/runs")).toBe(true);
    expect(isTabActive("/runs", "/runsx")).toBe(false);
    expect(isTabActive("/quota", "/quotas")).toBe(false);
    expect(isTabActive("/logs", "/")).toBe(false);
  });

  it("moves radio focus with wrapping arrow-key navigation", () => {
    expect(nextRadioIndex(0, 3, "ArrowLeft")).toBe(2);
    expect(nextRadioIndex(2, 3, "ArrowRight")).toBe(0);
    expect(nextRadioIndex(0, 3, "ArrowDown")).toBe(1);
    expect(nextRadioIndex(1, 3, "ArrowUp")).toBe(0);
    expect(nextRadioIndex(1, 3, "Enter")).toBeNull();
  });
});
