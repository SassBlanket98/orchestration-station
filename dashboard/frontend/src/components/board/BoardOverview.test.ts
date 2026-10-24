import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MonitorResponse, MonitorStage, SessionSummary } from "../../api/types";
import { BoardOverview, filterRollup, reduceOverviewKey } from "./BoardOverview";
import { DEFAULT_VIEW, STORAGE_KEY, serializeView } from "./view";

const hooks = vi.hoisted(() => ({ data: null as MonitorResponse | null }));
vi.mock("../../state/hooks", () => ({
  useQuotaUsage: () => ({ data: null, loading: false, error: null }),
  useMonitor: () => ({ data: hooks.data, loading: false, error: null }),
}));
vi.mock("./BoardParts", () => ({
  Clock: () => null,
  Elapsed: () => null,
  Mark: () => null,
  Flash: ({ children }: { children: unknown }) => children,
}));

const stage = (overrides: Partial<MonitorStage> = {}): MonitorStage => ({
  path: "/stage/lead", label: "lead", tag: "CC·W", lead_ctx: null, lead_state: "live", live: 0,
  last_activity: 1_000, solo: false, latest_milestone: null,
  counts: { live: 0, failed: 0, noreport: 0, done: 0, total: 0 },
  project: "Orchard", session_id: "session-1", section: "active", level_tag: "LEAD",
  workers: [{ name: "worker-1", state: "running" }],
  ...overrides,
});
const data = (stages: MonitorStage[]): MonitorResponse => ({
  online: true, fetched_at: 1_000, error: null,
  state: { v: 1, now: 1_000, generated: 1_000, stages, hidden_idle: 0, tokens_total: null, cost_total: null },
});
const session: SessionSummary = {
  id: "session-1", project: "Orchard", run: null, path: "/stage/lead", rev: 1, status: "active",
  counts: { running: 0, finished: 0, exited: 0, unknown: 0 }, last_activity_at: 1_000,
  latest_milestone: null, findings_total: 0, running_workers: [], lead: null, stream: null,
  cycle_progress: null, source_warnings: [], worker_previews: [], worker_total: 0, activity_series: [],
};
// Runs `fn` with a saved view in localStorage, as the page loads it.
const withSavedView = (open: string[], fn: () => void) => {
  const saved = new Map([[STORAGE_KEY, JSON.stringify(serializeView({ ...DEFAULT_VIEW, open: new Set(open) }))]]);
  const store = { getItem: (k: string) => saved.get(k) ?? null, setItem: () => {}, removeItem: () => {} };
  vi.stubGlobal("window", { localStorage: store, sessionStorage: store });
  try { fn(); } finally { vi.unstubAllGlobals(); }
};
const render = (sessions: SessionSummary[] = [session]) => renderToString(createElement(
  MemoryRouter, null, createElement(BoardOverview, { sessions }),
));

describe("Board overview roll-up", () => {
  afterEach(() => { hooks.data = null; });
  it("renders the Monitor hierarchy in one tree", () => {
    hooks.data = data([stage(), stage({ path: "/stage/spawn", label: "spawn", session_id: "session-2", parent_session: "session-1", level_tag: "SPAWN" })]);
    const html = render();
    expect(html).toContain('role="tree"');
    expect(html).toContain("Orchard");
    expect(html).toContain("lead");
    expect(html).not.toContain("plant-section-active");
    // Children and workers stay folded under their session until it is tapped.
    expect(html).not.toContain("spawn");
    expect(html).not.toContain("worker-1");
    withSavedView(["s:/stage/lead"], () => {
      const open = render();
      expect(open).toContain("spawn");
      expect(open).toContain("worker-1");
    });
  });

  it("shows folded matches while filtering, independent of the project fold", () => {
    const closedProject = stage({ label: "spawn", lead_state: "closed", section: "closed", project: "Hidden orchard", workers: [] });
    const nodes = filterRollup([closedProject], "spawn");
    expect(nodes.filter((node) => node.kind === "stage").map((node) => node.stage.label)).toEqual(["spawn"]);
  });

  it("uses e, h and a for expand, idle and closed view state", () => {
    const initial = { ...DEFAULT_VIEW, open: new Set<string>() };
    const e = reduceOverviewKey(initial, "e", [stage()]);
    expect(e.open.size).toBeGreaterThan(0);
    expect(reduceOverviewKey(e, "e", [stage()]).open.size).toBe(0);
    const h = reduceOverviewKey(initial, "h", []);
    expect(h.showIdle).toBe(true);
    const a = reduceOverviewKey(initial, "a", []);
    expect(a.showClosed).toBe(true);
  });

  it("counts working stages and workers once in the masthead", () => {
    hooks.data = data([stage(), stage({ path: "/stage/idle", project: "Idle plot", lead_state: "closed", section: "idle", workers: [] })]);
    const html = render();
    expect(html).toContain("2<!-- --> working");
    expect(html).toContain("idle · 1");
  });

  it("folds a working project when its key is in the saved open set", () => {
    hooks.data = data([stage()]);
    withSavedView(["s:/stage/lead"], () => expect(render()).toContain("worker-1"));
    withSavedView(["s:/stage/lead", "p:Orchard"], () => {
      const html = render();
      expect(html).toContain("Orchard");
      expect(html).not.toContain("worker-1");
    });
  });
});
