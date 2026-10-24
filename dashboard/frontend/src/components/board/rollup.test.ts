import { describe, expect, it } from "vitest";
import type { MonitorStage, MonitorWorker } from "../../api/types";
import { buildRollup, isWorkerLive, isWorking, rowState, type RollupNode, type RollupOptions, waitingStages, workingCount } from "./rollup";

const stage = (path: string, overrides: Partial<MonitorStage> = {}): MonitorStage => ({
  path, label: path, tag: "CC·W", lead_ctx: null, lead_state: "closed", live: 0, last_activity: 1_000, solo: false,
  latest_milestone: null, counts: { live: 0, failed: 0, noreport: 0, done: 0, total: 0 }, section: "closed",
  ...overrides,
});

const worker = (name: string, overrides: Partial<MonitorWorker> = {}): MonitorWorker => ({
  name, state: "done", start: 1, ...overrides,
});

const opts = (overrides: Partial<RollupOptions> = {}): RollupOptions => ({
  open: new Set(), showIdle: false, showClosed: false, nowSec: 2_000, ...overrides,
});

const keys = (nodes: RollupNode[]) => nodes.map((node) => node.key);

const projects = (nodes: RollupNode[]) =>
  nodes.flatMap((node) => (node.kind === "project" ? [node.project] : []));

const stageDepths = (nodes: RollupNode[]) => {
  const out: Record<string, number> = {};
  for (const node of nodes) if (node.kind === "stage") out[node.stage.path] = node.depth;
  return out;
};

describe("isWorking and isWorkerLive", () => {
  it("treats active sections as working, and live or background leads only while recently active", () => {
    expect(isWorking(stage("a", { section: "active" }), 2_000)).toBe(true);
    expect(isWorking(stage("b", { lead_state: "background", last_activity: 1_900 }), 2_000)).toBe(true);
    expect(isWorking(stage("c", { lead_state: "live", section: "idle", last_activity: 1_900 }), 2_000)).toBe(true);
    expect(isWorking(stage("d", { section: "idle" }), 2_000)).toBe(false);
    expect(isWorking(stage("e"), 2_000)).toBe(false);
  });

  it("keeps a live session that Monitor calls idle out of the working list once it has been quiet", () => {
    const quiet = stage("q", { lead_state: "live", section: "idle", last_activity: 1_000 });
    const unknown = stage("u", { lead_state: "live", section: "idle", last_activity: 0 });
    expect(isWorking(quiet, 2_000)).toBe(false);
    expect(isWorking(unknown, 2_000)).toBe(false);
    expect(isWorking(quiet, 1_500)).toBe(true);
  });

  it("treats running, quiet and stalled workers as live", () => {
    for (const state of ["running", "quiet", "stalled"]) expect(isWorkerLive(worker("w", { state }))).toBe(true);
    for (const state of ["done", "failed", "noreport"]) expect(isWorkerLive(worker("w", { state }))).toBe(false);
  });
});

describe("workingCount", () => {
  it("adds working stages to live workers across all stages", () => {
    const stages = [
      stage("a", { section: "active", workers: [worker("x", { state: "running" }), worker("y", { state: "stalled" }), worker("z", { state: "done" })] }),
      stage("b", { lead_state: "live", last_activity: 1_900, workers: [worker("q", { state: "quiet" })] }),
      stage("c", { section: "idle", workers: [worker("r", { state: "failed" })] }),
      stage("d"),
    ];
    // a (working) + b (working) + 3 live workers = 5
    expect(workingCount(stages, 2_000)).toBe(5);
    expect(workingCount([], 2_000)).toBe(0);
  });
});

describe("buildRollup project order", () => {
  const stages = [
    stage("closed-only", { project: "D", section: "closed", last_activity: 9_000 }),
    stage("idle-one", { project: "C", section: "idle", last_activity: 800 }),
    stage("work-old", { project: "A", section: "active", last_activity: 500 }),
    stage("work-new", { project: "B", section: "active", last_activity: 900 }),
  ];

  it("lists working projects newest first, then hides idle and closed by default", () => {
    const nodes = buildRollup(stages, opts());
    expect(projects(nodes)).toEqual(["B", "A"]);
    expect(nodes.map((n) => n.kind)).toEqual(["project", "stage", "project", "stage", "idle-divider", "closed-count"]);
  });

  it("shows idle projects after the divider only when showIdle is set", () => {
    const nodes = buildRollup(stages, opts({ showIdle: true }));
    expect(projects(nodes)).toEqual(["B", "A", "C"]);
    const divider = nodes.find((n) => n.kind === "idle-divider");
    expect(divider).toMatchObject({ kind: "idle-divider", depth: -1, count: 1 });
  });

  it("emits a closed-count node and shows closed projects only when showClosed is set", () => {
    const hidden = buildRollup(stages, opts());
    expect(hidden.find((n) => n.kind === "closed-count")).toMatchObject({ count: 1 });
    expect(projects(hidden)).not.toContain("D");

    const shown = buildRollup(stages, opts({ showIdle: true, showClosed: true }));
    expect(projects(shown)).toEqual(["B", "A", "C", "D"]);
    expect(shown.find((n) => n.kind === "closed-count")).toMatchObject({ count: 1 });
  });

  it("falls back to the label when project is missing", () => {
    const nodes = buildRollup([stage("p1", { label: "labelled", section: "active" })], opts());
    expect(projects(nodes)).toEqual(["labelled"]);
  });

  it("toggles project keys from their default through open", () => {
    const working = stage("w", { project: "W", section: "active" });
    const idle = stage("i", { project: "I", section: "idle" });

    const closedWorking = buildRollup([working], opts({ open: new Set(["p:W"]) }));
    expect(closedWorking).toHaveLength(1);
    expect(closedWorking[0]).toMatchObject({ kind: "project", open: false });

    const openIdle = buildRollup([idle], opts({ showIdle: true, open: new Set(["p:I"]) }));
    expect(openIdle.map((n) => n.kind)).toEqual(["idle-divider", "project", "stage"]);
  });

  it("sums tokens and cost across a project's stages and counts its buckets", () => {
    const nodes = buildRollup([
      stage("a", { project: "P", section: "active", tokens_total: 100, cost_total: 1.5 }),
      stage("b", { project: "P", section: "idle", tokens_total: null, cost_total: 0.5 }),
      stage("c", { project: "P", section: "closed", tokens_total: 50, cost_total: null }),
    ], opts());
    expect(nodes[0]).toMatchObject({
      kind: "project",
      counts: { working: 1, idle: 1, closed: 1 },
      totals: { tokens: 150, cost: 2 },
    });
  });
});

describe("buildRollup stage nesting", () => {
  it("nests children under parent_session and gives grandchildren depth 2", () => {
    const nodes = buildRollup([
      stage("root", { project: "P", section: "active", session_id: "r", last_activity: 100 }),
      stage("child", { project: "P", section: "active", session_id: "c", parent_session: "r", last_activity: 200 }),
      stage("grand", { project: "P", section: "idle", session_id: "g", parent_session: "c", last_activity: 300 }),
    ], opts({ open: new Set(["s:root", "s:child"]), showIdle: true }));
    expect(stageDepths(nodes)).toEqual({ root: 0, child: 1, grand: 2 });
  });

  it("keeps children folded until their parent is unfolded, and counts them on the parent", () => {
    const stages = [
      stage("root", { project: "P", section: "active", session_id: "r", last_activity: 100, workers: [worker("w", { state: "running" })] }),
      stage("child", { project: "P", section: "active", session_id: "c", parent_session: "r", last_activity: 200 }),
    ];
    const folded = buildRollup(stages, opts());
    expect(stageDepths(folded)).toEqual({ root: 0 });
    expect(folded.find((n) => n.kind === "stage")).toMatchObject({ kids: 2 });
    expect(folded.some((n) => n.kind === "worker")).toBe(false);
    const unfolded = buildRollup(stages, opts({ open: new Set(["s:root"]) }));
    expect(stageDepths(unfolded)).toEqual({ root: 0, child: 1 });
    expect(unfolded.some((n) => n.kind === "worker")).toBe(true);
  });

  it("counts finished workers on the parent so they can be unfolded", () => {
    const nodes = buildRollup([
      stage("S", { project: "P", section: "active", workers: [worker("a"), worker("b", { state: "failed" })] }),
    ], opts());
    expect(nodes.find((n) => n.kind === "stage")).toMatchObject({ kids: 2 });
    expect(nodes.some((n) => n.kind === "done")).toBe(false);
    const open = buildRollup([
      stage("S", { project: "P", section: "active", workers: [worker("a"), worker("b", { state: "failed" })] }),
    ], opts({ open: new Set(["s:S"]) }));
    expect(open.find((n) => n.kind === "done")).toMatchObject({ kind: "done" });
  });

  it("sorts roots by most recent activity and shows idle ones only with showIdle", () => {
    const stages = [
      stage("idle-new", { project: "P", section: "idle", last_activity: 999 }),
      stage("work-old", { project: "P", section: "active", last_activity: 10 }),
      stage("work-new", { project: "P", section: "active", last_activity: 50 }),
    ];
    const stageKeys = (nodes: RollupNode[]) => nodes.filter((n) => n.kind === "stage").map((n) => n.key);
    expect(stageKeys(buildRollup(stages, opts()))).toEqual(["s:work-new", "s:work-old"]);
    expect(stageKeys(buildRollup(stages, opts({ showIdle: true })))).toEqual(["s:idle-new", "s:work-new", "s:work-old"]);
  });

  it("folds quiet live and recently ended sessions of a working project instead of listing them", () => {
    const nodes = buildRollup([
      stage("work", { project: "P", section: "active", last_activity: 1_990 }),
      stage("quiet-live", { project: "P", section: "idle", lead_state: "live", last_activity: 100 }),
      stage("ended", { project: "P", section: "idle", lead_state: "gone", last_activity: 1_500 }),
    ], opts());
    expect(nodes.filter((n) => n.kind === "stage").map((n) => n.key)).toEqual(["s:work"]);
    expect(nodes.at(-1)).toMatchObject({ kind: "finished", key: "pf:P", n: 2 });
  });

  it("treats always-on bots and crons as services: not working, project drops to idle", () => {
    const bot = stage("bot", { project: "openclaw", section: "active", lead_state: "live", lead_kind: "openclaw" });
    const cron = stage("cron", { project: "openclaw", section: "active", lead_state: "live", level_tag: "CRON" });
    expect(workingCount([bot, cron])).toBe(0);
    expect(rowState(bot, new Set())).toBe("service");
    const nodes = buildRollup([bot, cron], opts());
    expect(nodes.map((n) => n.kind)).toEqual(["idle-divider"]);
  });

  it("treats a stage with an unknown parent_session as a root", () => {
    const nodes = buildRollup([
      stage("orphan", { project: "P", section: "active", session_id: "o", parent_session: "missing" }),
    ], opts());
    expect(stageDepths(nodes)).toEqual({ orphan: 0 });
  });

  it("keeps stages in a parent_session cycle instead of dropping them", () => {
    const nodes = buildRollup([
      stage("a", { project: "P", section: "active", session_id: "a", parent_session: "b" }),
      stage("b", { project: "P", section: "active", session_id: "b", parent_session: "a" }),
    ], opts({ open: new Set(["s:a", "s:b"]) }));
    expect(Object.keys(stageDepths(nodes)).sort()).toEqual(["a", "b"]);
  });
});

describe("workers and the finished fold", () => {
  const parent = stage("S", {
    project: "P", section: "active", session_id: "s",
    workers: [
      worker("late", { state: "running", start: 30 }),
      worker("early", { state: "stalled", start: 10 }),
      worker("d2", { state: "done", start: 40 }),
      worker("d1", { state: "failed", start: 20 }),
    ],
  });

  it("shows live workers at depth+1 sorted by start and the finished ones as one chip row", () => {
    const nodes = buildRollup([parent], opts({ open: new Set(["s:S"]) }));
    const workers = nodes.filter((n) => n.kind === "worker");
    expect(workers.map((n) => (n.kind === "worker" ? n.worker.name : ""))).toEqual(["early", "late"]);
    expect(workers.every((n) => n.depth === 1)).toBe(true);
    const done = nodes.find((n) => n.kind === "done");
    expect(done).toMatchObject({ key: "d:S", depth: 1 });
    expect(done && done.kind === "done" ? done.workers.map((w) => w.name) : []).toEqual(["d1", "d2"]);
  });
});

describe("waiting on the user", () => {
  it("counts a quiet live lead, but not one with a busy spawn, live workers, or no activity time", () => {
    const quiet = stage("quiet", { lead_state: "live", last_activity: 100, session_id: "q", app: { on: true, id: "x" } });
    const bot = stage("bot", { lead_state: "live", last_activity: 100, session_id: "b" });
    const noTime = stage("no-time", { lead_state: "live", last_activity: 0, session_id: "n" });
    const parentLead = stage("parent", { lead_state: "live", last_activity: 100, session_id: "p" });
    const child = stage("child", { lead_state: "live", last_activity: 990, session_id: "c", parent_session: "p", workers: [{ name: "w", state: "running" }] });
    const recent = stage("recent", { lead_state: "live", last_activity: 950, session_id: "r" });
    const waiting = waitingStages([quiet, bot, noTime, parentLead, child, recent], 1000);
    expect([...waiting].map((s) => s.label)).toEqual(["quiet"]);
    expect(rowState(quiet, waiting)).toBe("waiting");
    expect(rowState(recent, waiting, 1000)).toBe("working");
  });
});

describe("closed stages in a project", () => {
  it("folds 300 closed stages into one node", () => {
    const closed = Array.from({ length: 300 }, (_, i) => stage(`c${i}`, { project: "Big", section: "closed", last_activity: i }));
    const nodes = buildRollup([stage("live", { project: "Big", section: "active" }), ...closed], opts());
    expect(nodes.length).toBeLessThanOrEqual(3);
    expect(nodes.map((n) => n.kind)).toEqual(["project", "stage", "finished"]);
    expect(nodes[2]).toMatchObject({ key: "pf:Big", n: 300, scope: "stages", lastActivity: 299 });
  });

  it("shows closed stages after the fold only when pf is open", () => {
    const stages = [
      stage("live", { project: "P", section: "active" }),
      stage("gone", { project: "P", section: "closed", last_activity: 5 }),
    ];
    const open = buildRollup(stages, opts({ open: new Set(["pf:P"]) }));
    // The fold sits above its closed stages, so opening it never pushes rows above the tap.
    expect(keys(open).slice(-2)).toEqual(["pf:P", "s:gone"]);
    expect(open.at(-2)).toMatchObject({ kind: "finished", key: "pf:P", n: 1 });
  });

  it("keeps a working child visible when its closed parent is folded", () => {
    const nodes = buildRollup([
      stage("closed-parent", { project: "P", section: "closed", session_id: "cp", last_activity: 1 }),
      stage("working-child", { project: "P", section: "active", session_id: "wc", parent_session: "cp" }),
    ], opts());
    expect(stageDepths(nodes)).toEqual({ "working-child": 0 });
    expect(nodes.at(-1)).toMatchObject({ kind: "finished", key: "pf:P", n: 1 });
  });
});
