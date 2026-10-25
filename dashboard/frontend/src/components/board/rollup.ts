import type { MonitorStage, MonitorWorker } from "../../api/types";

// Monitor's roll-up tree, the shape its terminal shows: projects, then stages nested by parent_session,
// then workers. Pure: no React, no fetching. The caller owns expansion state through `open`.

export type RollupOptions = {
  open: Set<string>;
  showIdle: boolean;
  showClosed: boolean;
  nowSec: number; // decides which live sessions still count as active (ACTIVE_WITHIN_S)
};

export type ProjectCounts = { working: number; idle: number; closed: number };
export type ProjectTotals = { tokens: number; cost: number };

export type RollupNode =
  | { kind: "project"; key: string; depth: -1; project: string; counts: ProjectCounts; totals: ProjectTotals; open: boolean }
  | { kind: "stage"; key: string; depth: number; stage: MonitorStage; kids: number }
  | { kind: "worker"; key: string; depth: number; stage: MonitorStage; worker: MonitorWorker }
  | { kind: "finished"; key: string; depth: number; n: number; scope: "workers" | "stages"; lastActivity: number | null }
  | { kind: "done"; key: string; depth: number; stage: MonitorStage; workers: MonitorWorker[] }
  | { kind: "idle-divider"; key: string; depth: -1; count: number }
  | { kind: "closed-count"; key: string; depth: -1; count: number };

const LIVE_LEAD_STATES = new Set(["live", "background"]);
const LIVE_WORKER_STATES = new Set(["running", "quiet", "stalled"]);

/** Always-on bots and scheduled jobs (OpenClaw, crons): alive by design, so "live" says nothing. */
export function isService(stage: MonitorStage): boolean {
  return stage.level_tag === "CRON" || stage.lead_kind === "openclaw";
}

/** A live session quiet for longer than this is stale: Monitor files it under idle, so it stays out of the working list. */
export const ACTIVE_WITHIN_S = 600;

// A stage is working when Monitor's section is active. A live or backgrounded lead that Monitor calls idle
// counts only while it still shows recent activity. Services never count: they are always live. Missing section means closed.
export function isWorking(stage: MonitorStage, nowSec: number = Date.now() / 1000): boolean {
  if (isService(stage)) return false;
  if (stage.section === "active") return true;
  return LIVE_LEAD_STATES.has(stage.lead_state) && stage.last_activity > 0 && nowSec - stage.last_activity < ACTIVE_WITHIN_S;
}

export function isWorkerLive(worker: MonitorWorker): boolean {
  return LIVE_WORKER_STATES.has(worker.state);
}

// The one "working" number used everywhere: working stages plus live workers across all stages.
export function workingCount(stages: MonitorStage[], nowSec: number = Date.now() / 1000): number {
  let n = 0;
  for (const stage of stages) {
    if (isWorking(stage, nowSec)) n += 1;
    for (const worker of stage.workers ?? []) {
      if (isWorkerLive(worker)) n += 1;
    }
  }
  return n;
}

/** What a session row is doing, for its glyph and colour. */
export type RowState = "working" | "waiting" | "idle" | "service" | "closed";

const WAIT_AFTER_S = 120;

/**
 * Sessions waiting on the user: a live lead linked to the Claude app, with no live worker of its own, no live spawned session,
 * and quiet for over two minutes. A lead with no activity time is unknown, not waiting.
 * Same rule as the backend badge (push.waiting_sessions).
 */
export function waitingStages(stages: MonitorStage[], nowSec: number): Set<MonitorStage> {
  const liveIds = new Set(stages.filter((s) => s.lead_state === "live").map((s) => s.session_id));
  const busyParents = new Set<string>();
  for (const stage of stages) {
    const parent = stage.parent_session;
    if (parent && ((stage.workers ?? []).some(isWorkerLive) || liveIds.has(stage.session_id))) busyParents.add(parent);
  }
  const out = new Set<MonitorStage>();
  for (const stage of stages) {
    // Only sessions the user can answer (linked to the Claude app); bots, crons and headless swarms never wait on him.
    if (stage.lead_state !== "live" || !stage.app?.on || (stage.workers ?? []).some(isWorkerLive)) continue;
    if (stage.session_id && busyParents.has(stage.session_id)) continue;
    if (!(stage.last_activity > 0) || nowSec - stage.last_activity <= WAIT_AFTER_S) continue;
    out.add(stage);
  }
  return out;
}

export function rowState(stage: MonitorStage, waiting: Set<MonitorStage>, nowSec: number = Date.now() / 1000): RowState {
  if (waiting.has(stage)) return "waiting";
  if (isService(stage)) return LIVE_LEAD_STATES.has(stage.lead_state) ? "service" : "closed";
  if (isWorking(stage, nowSec)) return "working";
  return stage.section === "idle" ? "idle" : "closed";
}

type Bucket = "working" | "idle" | "closed";

function bucketOf(stage: MonitorStage, nowSec: number): Bucket {
  if (isWorking(stage, nowSec)) return "working";
  if (isService(stage) && LIVE_LEAD_STATES.has(stage.lead_state)) return "idle";
  return stage.section === "idle" ? "idle" : "closed";
}

// Most recently active first: the order the user last touched things. Stable for equal times.
function stageOrder(a: MonitorStage, b: MonitorStage): number {
  return b.last_activity - a.last_activity;
}

function startOf(worker: MonitorWorker): number {
  return worker.start ?? Number.POSITIVE_INFINITY;
}

function byStart(a: MonitorWorker, b: MonitorWorker): number {
  const x = startOf(a);
  const y = startOf(b);
  return x < y ? -1 : x > y ? 1 : 0;
}

type ProjectGroup = {
  name: string;
  stages: MonitorStage[];
  counts: ProjectCounts;
  lastActivity: number;
  best: Bucket;
};

function groupProjects(stages: MonitorStage[], nowSec: number): ProjectGroup[] {
  const byName = new Map<string, MonitorStage[]>();
  for (const stage of stages) {
    const name = stage.project || stage.label;
    const list = byName.get(name);
    if (list) list.push(stage);
    else byName.set(name, [stage]);
  }
  const groups: ProjectGroup[] = [];
  for (const [name, members] of byName) {
    const counts: ProjectCounts = { working: 0, idle: 0, closed: 0 };
    let lastActivity = Number.NEGATIVE_INFINITY;
    for (const stage of members) {
      counts[bucketOf(stage, nowSec)] += 1;
      lastActivity = Math.max(lastActivity, stage.last_activity);
    }
    const best: Bucket = counts.working > 0 ? "working" : counts.idle > 0 ? "idle" : "closed";
    groups.push({ name, stages: members, counts, lastActivity, best });
  }
  return groups;
}

function sumTotals(stages: MonitorStage[]): ProjectTotals {
  let tokens = 0;
  let cost = 0;
  for (const stage of stages) {
    tokens += stage.tokens_total ?? 0;
    cost += stage.cost_total ?? 0;
  }
  return { tokens, cost };
}

// Parent/child links inside one project. A stage whose parent_session matches no stage here is a root.
// A stage whose link points only at itself or into a cycle is never reached from a root; the walk
// picks those up afterwards so they do not vanish.
type Forest = { roots: MonitorStage[]; kids: Map<MonitorStage, MonitorStage[]>; all: MonitorStage[] };

function buildForest(stages: MonitorStage[]): Forest {
  const bySession = new Map<string, MonitorStage[]>();
  for (const stage of stages) {
    if (!stage.session_id) continue;
    const list = bySession.get(stage.session_id);
    if (list) list.push(stage);
    else bySession.set(stage.session_id, [stage]);
  }
  const kids = new Map<MonitorStage, MonitorStage[]>();
  const roots: MonitorStage[] = [];
  for (const stage of stages) {
    const parents = stage.parent_session ? bySession.get(stage.parent_session) : undefined;
    if (!parents?.length) {
      roots.push(stage);
      continue;
    }
    for (const parent of parents) {
      const list = kids.get(parent);
      if (list) list.push(stage);
      else kids.set(parent, [stage]);
    }
  }
  roots.sort(stageOrder);
  for (const list of kids.values()) list.sort(stageOrder);
  return { roots, kids, all: [...stages].sort(stageOrder) };
}

function workerNode(stage: MonitorStage, worker: MonitorWorker, depth: number): RollupNode {
  return { kind: "worker", key: `w:${stage.path}|${worker.name}`, depth, stage, worker };
}

// Live workers as rows, then one line of chips for the finished ones (any stage that is not closed).
// Inside the open finished fold every stage is finished, so its chips show too.
function workerNodes(stage: MonitorStage, depth: number, nowSec: number, inFold: boolean): RollupNode[] {
  const workers = stage.workers ?? [];
  const out: RollupNode[] = workers
    .filter(isWorkerLive)
    .sort(byStart)
    .map((worker) => workerNode(stage, worker, depth));
  const done = workers.filter((worker) => !isWorkerLive(worker)).sort(byStart);
  if (done.length && (inFold || bucketOf(stage, nowSec) === "working")) {
    out.push({ kind: "done", key: `d:${stage.path}`, depth, stage, workers: done });
  }
  return out;
}

// Depth-first walk. A hidden stage emits nothing, and its children take its depth, so they stay attached
// to the nearest visible ancestor level instead of vanishing. `inFold` marks the walk over the finished fold.
function walk(
  stage: MonitorStage,
  depth: number,
  forest: Forest,
  shown: (stage: MonitorStage) => boolean,
  open: Set<string>,
  nowSec: number,
  visited: Set<MonitorStage>,
  inFold: boolean,
  out: RollupNode[],
): void {
  if (visited.has(stage)) return;
  visited.add(stage);
  if (!shown(stage)) {
    for (const kid of forest.kids.get(stage) ?? []) walk(kid, depth, forest, shown, open, nowSec, visited, inFold, out);
    return;
  }
  // Children are built first so the row knows how many it has; they show only while the stage is unfolded.
  const below: RollupNode[] = workerNodes(stage, depth + 1, nowSec, inFold);
  for (const kid of forest.kids.get(stage) ?? []) walk(kid, depth + 1, forest, shown, open, nowSec, visited, inFold, below);
  // Finished workers are one chip row but count one each, so a stage with only finished workers can still be unfolded.
  const kids = below.reduce((n, node) => n + (node.kind === "done" ? node.workers.length : node.kind === "stage" || node.kind === "worker" ? 1 : 0), 0);
  const key = `s:${stage.path}`;
  out.push({ kind: "stage", key, depth, stage, kids });
  if (open.has(key)) out.push(...below);
}

// Stages inside one open project: the tree of visible stages, then one fold for closed stages.
// The fold is expanded (closed stages shown) only when its key is open.
function projectChildren(group: ProjectGroup, open: Set<string>, showIdle: boolean, nowSec: number): RollupNode[] {
  const foldKey = `pf:${group.name}`;
  const foldOpen = open.has(foldKey);
  // Working stages show; idle ones only while the idle toggle is on. Everything else sits in the fold.
  const shown = (stage: MonitorStage) => {
    const bucket = bucketOf(stage, nowSec);
    return bucket === "working" || (showIdle && bucket === "idle");
  };
  const closed = group.stages.filter((stage) => !shown(stage));
  const forest = buildForest(group.stages);
  const visited = new Set<MonitorStage>();
  const out: RollupNode[] = [];
  for (const root of forest.roots) walk(root, 0, forest, shown, open, nowSec, visited, false, out);
  for (const stage of forest.all) {
    if (!visited.has(stage) && shown(stage)) walk(stage, 0, forest, shown, open, nowSec, visited, false, out);
  }
  // The fold comes first and its closed stages open BELOW it, so tapping it never pushes rows above the finger.
  if (closed.length) {
    const lastActivity = closed.reduce((max, stage) => Math.max(max, stage.last_activity), Number.NEGATIVE_INFINITY);
    out.push({ kind: "finished", key: foldKey, depth: 0, n: closed.length, scope: "stages", lastActivity });
    if (foldOpen) {
      // The closed stages form their own forest, so a closed child sits under its closed lead. Roots are the
      // closed stages whose parent is not also closed; they are walked at depth 1, newest first, like live ones.
      const closedForest = buildForest(closed);
      const closedVisited = new Set<MonitorStage>();
      const everything = () => true;
      for (const root of closedForest.roots) walk(root, 1, closedForest, everything, open, nowSec, closedVisited, true, out);
      for (const stage of closedForest.all) {
        if (!closedVisited.has(stage)) walk(stage, 1, closedForest, everything, open, nowSec, closedVisited, true, out);
      }
    }
  }
  return out;
}

// Project keys toggle from their default: working projects start open, idle and closed ones start shut.
function projectNodes(group: ProjectGroup, open: Set<string>, showIdle: boolean, nowSec: number): RollupNode[] {
  const key = `p:${group.name}`;
  const isOpen = open.has(key) !== (group.best === "working");
  const node: RollupNode = {
    kind: "project",
    key,
    depth: -1,
    project: group.name,
    counts: group.counts,
    totals: sumTotals(group.stages),
    open: isOpen,
  };
  return isOpen ? [node, ...projectChildren(group, open, showIdle, nowSec)] : [node];
}

export function buildRollup(stages: MonitorStage[], opts: RollupOptions): RollupNode[] {
  const groups = groupProjects(stages, opts.nowSec);
  const byActivity = (a: ProjectGroup, b: ProjectGroup) => b.lastActivity - a.lastActivity;
  const working = groups.filter((g) => g.best === "working").sort(byActivity);
  const idle = groups.filter((g) => g.best === "idle").sort(byActivity);
  const closed = groups.filter((g) => g.best === "closed").sort(byActivity);

  const out: RollupNode[] = [];
  for (const group of working) out.push(...projectNodes(group, opts.open, opts.showIdle, opts.nowSec));
  if (idle.length) {
    out.push({ kind: "idle-divider", key: "d:idle", depth: -1, count: idle.length });
    if (opts.showIdle) for (const group of idle) out.push(...projectNodes(group, opts.open, opts.showIdle, opts.nowSec));
  }
  if (closed.length) {
    out.push({ kind: "closed-count", key: "d:closed", depth: -1, count: closed.length });
    if (opts.showClosed) for (const group of closed) out.push(...projectNodes(group, opts.open, opts.showIdle, opts.nowSec));
  }
  return out;
}
