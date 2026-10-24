import { describe, expect, it } from "vitest";
import { demoSessions } from "./demo";

const NOW = 1_760_000_000;
/** Ticks per worker turnover in demo.ts (EVENT_TICKS). */
const EVENT_TICKS = 25;

describe("demoSessions", () => {
  it("returns twelve sessions: four active (one lagging), eight idle", () => {
    const sessions = demoSessions(0, NOW);
    expect(sessions).toHaveLength(12);
    expect(sessions.filter((s) => s.status === "active")).toHaveLength(4);
    expect(sessions.filter((s) => s.status === "idle")).toHaveLength(8);
  });

  it("keeps counts, running_workers and findings consistent with the previews", () => {
    for (const s of demoSessions(7, NOW)) {
      const byStatus = (status: string) =>
        s.worker_previews.filter((w) => w.status === status);
      expect(s.worker_total).toBe(s.worker_previews.length);
      expect(s.counts.running).toBe(byStatus("running").length);
      expect(s.counts.finished).toBe(byStatus("finished").length);
      expect(s.counts.exited).toBe(byStatus("exited").length);
      expect(s.running_workers.map((w) => w.name)).toEqual(
        byStatus("running").map((w) => w.name),
      );
      const p = s.findings_by_priority;
      expect(p).toBeDefined();
      expect(s.findings_total).toBe((p?.P1 ?? 0) + (p?.P2 ?? 0) + (p?.P3 ?? 0));
    }
  });

  it("gives every session a ten-bucket activity series", () => {
    for (const s of demoSessions(3, NOW)) {
      expect(s.activity_series).toHaveLength(10);
    }
  });

  it("keeps active sessions at two to six workers", () => {
    for (const s of demoSessions(0, NOW).filter((s) => s.status === "active")) {
      expect(s.worker_total).toBeGreaterThanOrEqual(2);
      expect(s.worker_total).toBeLessThanOrEqual(6);
    }
  });

  it("marks exactly one running worker as stale", () => {
    const stale = demoSessions(0, NOW).filter((s) =>
      s.worker_previews.some((w) => w.flags.includes("stale")),
    );
    expect(stale).toHaveLength(1);
    const worker = stale[0].worker_previews.find((w) => w.flags.includes("stale"));
    expect(worker?.status).toBe("running");
  });

  it("keeps idle sessions quiet: no running workers, zero activity", () => {
    for (const s of demoSessions(12, NOW).filter((s) => s.status === "idle")) {
      expect(s.running_workers).toHaveLength(0);
      expect(s.activity_series.every((b) => b.count === 0)).toBe(true);
    }
  });

  it("turns over a running worker in active sessions", () => {
    const before = demoSessions(0, NOW)[0];
    const after = demoSessions(EVENT_TICKS, NOW)[0];
    const names = (s: typeof before) => s.running_workers.map((w) => w.name);
    expect(names(after)).not.toEqual(names(before));
    expect(after.worker_total).toBe(before.worker_total);
  });

  it("changes between tick 0 and tick 30", () => {
    expect(demoSessions(30, NOW)).not.toEqual(demoSessions(0, NOW));
  });

  it("is deterministic for equal inputs", () => {
    expect(demoSessions(9, NOW)).toEqual(demoSessions(9, NOW));
  });
});
