import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { MonitorStage, MonitorWorker, SessionSummary } from "../../api/types";
import { useMonitor, useQuotaUsage } from "../../state/hooks";
import { buildRollup, isWorking, waitingStages, workingCount, type RollupNode } from "./rollup";
import { useActivity } from "./useActivity";
import { NeedsYou } from "./NeedsYou";
import { StagePeek } from "./StagePeek";
import { WorkerPeek } from "./WorkerPeek";
import { treePrefixes } from "./treeText";
import { stageDir, bannerState, onlineStages } from "./monitor";
import { BoardHelp } from "./BoardHelp";
import { BoardMasthead } from "./BoardMasthead";
import { RollupRow } from "./RollupRows";
import { loadView, saveView, type BoardView } from "./view";
import "./board.css";

type Store = Parameters<typeof loadView>[0];
function browserStore(kind: "localStorage" | "sessionStorage"): Store | null {
  try { return window[kind]; } catch { return null; }
}
type NavItem = { node: RollupNode; open: boolean };
type Latest = { nav: NavItem[]; cursor: string | null; helpOpen: boolean };

const sessionPath = (id: string) => "/runs/" + encodeURIComponent(id);
const stageLabel = (stage: MonitorStage) => stage.display_name || stage.session_name || stage.label;

function matches(stage: MonitorStage, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const fields = [stageLabel(stage), stage.label, stage.project, stage.lead_model, stage.effort, stage.goal,
    ...(stage.workers ?? []).flatMap((worker) => [worker.name, worker.title, worker.model, worker.job])];
  return fields.some((field) => field?.toLowerCase().includes(needle));
}

/** Typed filters expand the data first, then retain every matching stage and its hierarchy context. */
export function filterRollup(stages: MonitorStage[], query: string): RollupNode[] {
  if (!query.trim()) return buildRollup(stages, { open: new Set(), showIdle: false, showClosed: false, nowSec: Date.now() / 1000 });
  const expanded = new Set<string>();
  const projects = new Map<string, MonitorStage[]>();
  for (const stage of stages) {
    const project = stage.project || stage.label;
    projects.set(project, [...(projects.get(project) ?? []), stage]);
    expanded.add("pf:" + project);
    expanded.add("s:" + stage.path);
  }
  for (const [project, members] of projects) {
    const working = members.some((stage) => isWorking(stage));
    if (!working) expanded.add("p:" + project);
  }
  const all = buildRollup(stages, { open: expanded, showIdle: true, showClosed: true, nowSec: Date.now() / 1000 });
  const matching = new Set(stages.filter((stage) => matches(stage, query)));
  // Retain parent stages to keep matches in their tree context.
  let changed = true;
  while (changed) {
    changed = false;
    for (const child of [...matching]) {
      if (!child.parent_session) continue;
      for (const parent of stages) if (parent.session_id === child.parent_session && !matching.has(parent)) {
        matching.add(parent);
        changed = true;
      }
    }
  }
  const queryLower = query.trim().toLowerCase();
  return all.filter((node) => {
    if (node.kind === "project") return [...matching].some((stage) => (stage.project || stage.label) === node.project) || node.project.toLowerCase().includes(queryLower);
    if (node.kind === "stage") return matching.has(node.stage);
    if (node.kind === "worker") return matching.has(node.stage) &&
      (matches({ ...node.stage, workers: [] }, query) || [node.worker.name, node.worker.title, node.worker.model].some((field) => field?.toLowerCase().includes(queryLower)));
    // Finished-worker chips stay with a matching stage.
    if (node.kind === "done") return matching.has(node.stage);
    return false;
  });
}

function projectDefaultOpen(stages: MonitorStage[], project: string): boolean {
  return stages.some((stage) => (stage.project || stage.label) === project && isWorking(stage));
}

/** Pure keyboard transitions for project expansion and the idle/closed roll-up dividers. */
export function reduceOverviewKey(view: BoardView, key: string, stages: MonitorStage[]): BoardView {
  if (key === "h") return { ...view, showIdle: !view.showIdle };
  if (key === "a") return { ...view, showClosed: !view.showClosed };
  if (key !== "e") return view;
  const projects = [...new Set(stages.map((stage) => stage.project || stage.label))];
  const allOpen = projects.length > 0 && projects.every((project) => view.open.has("p:" + project) !== projectDefaultOpen(stages, project));
  const open = new Set(view.open);
  for (const project of projects) {
    if (allOpen === projectDefaultOpen(stages, project)) open.add("p:" + project);
    else open.delete("p:" + project);
  }
  return { ...view, open };
}

export function BoardOverview({ sessions, connecting = false }: { sessions: SessionSummary[]; connecting?: boolean }) {
  const page = useRef<HTMLElement>(null);
  const filterInput = useRef<HTMLInputElement>(null);
  const latest = useRef<Latest>({ nav: [], cursor: null, helpOpen: false });
  // The key handler lives in a long-lived effect; it reaches the current openNode (and its view/peek) through this.
  const openNodeRef = useRef<(node: RollupNode) => void>(() => undefined);
  const [view, setView] = useState<BoardView>(() => loadView(browserStore("localStorage"), browserStore("sessionStorage")));
  const [cursor, setCursor] = useState<string | null>(null);
  const [peek, setPeek] = useState<string | null>(null);
  const [workerLook, setWorkerLook] = useState<{ stage: MonitorStage; worker: MonitorWorker } | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 10_000);
    return () => window.clearInterval(timer);
  }, []);
  const quota = useQuotaUsage();
  const monitor = useMonitor();
  const activity = useActivity(!monitor.mock);
  // A failed poll (Monitor restarting, a slow phone network) keeps the last good list for two minutes
  // with a "reconnecting" note instead of wiping the page.
  const fresh = onlineStages(monitor.data);
  const lastGood = useRef<{ stages: MonitorStage[]; at: number }>({ stages: [], at: 0 });
  if (fresh.length) lastGood.current = { stages: fresh, at: Date.now() };
  const holding = !fresh.length && lastGood.current.stages.length > 0 && Date.now() - lastGood.current.at < 120_000;
  const stages = holding ? lastGood.current.stages : fresh;
  const working = workingCount(stages, nowMs / 1000);
  const idle = stages.filter((stage) => stage.section === "idle").length;
  const banner = monitor.mock || (monitor.loading && !monitor.data) ? null : bannerState(monitor.data);
  const navigate = useNavigate();
  const { search } = useLocation();
  const waiting = useMemo(() => waitingStages(stages, nowMs / 1000), [stages, nowMs]);
  const nodes = useMemo(() => {
    if (view.query.trim()) return filterRollup(stages, view.query);
    return buildRollup(stages, { open: view.open, showIdle: view.showIdle, showClosed: view.showClosed, nowSec: nowMs / 1000 });
  }, [stages, view.query, view.open, view.showIdle, view.showClosed, nowMs]);

  const toggleOpen = useCallback((key: string, open: boolean) => setView((current) => {
    const next = new Set(current.open);
    if (open) next.add(key); else next.delete(key);
    return { ...current, open: next };
  }), []);
  const toggleNode = useCallback((node: RollupNode) => {
    if (node.kind === "project") toggleOpen(node.key, !view.open.has(node.key));
    else if (node.kind === "idle-divider") setView((current) => ({ ...current, showIdle: !current.showIdle }));
    else if (node.kind === "closed-count") setView((current) => ({ ...current, showClosed: !current.showClosed }));
    else toggleOpen(node.key, !view.open.has(node.key));
  }, [toggleOpen, view.open]);
  const sessionFor = (stage: MonitorStage) => {
    if (stage.session_id) {
      const byId = sessions.find((session) => session.lat?.session_id === stage.session_id);
      if (byId) return byId;
    }
    const dir = stageDir(stage.path);
    const byDir = sessions.filter((session) => stageDir(session.path) === dir);
    // Several Monitor sessions can share one stage folder; the run page is the folder's, so any match will do.
    return byDir[0] ?? null;
  };
  // Tapping a session opens its quick look in place; the look has the button through to the run.
  const openNode = (node: RollupNode) => {
    if (node.kind === "stage") {
      setCursor(node.key);
      // A session with children: tap unfolds them, the next tap opens the quick look, the third folds both.
      // One without children goes straight to the quick look.
      const unfolded = view.open.has(node.key);
      if (node.kids > 0 && !unfolded) { toggleOpen(node.key, true); return; }
      const looking = peek === node.stage.path;
      if (node.kids > 0 && looking) toggleOpen(node.key, false);
      setPeek(looking ? null : node.stage.path);
    } else if (node.kind === "worker") {
      setCursor(node.key);
      // Monitor's worker names often do not exist in the Board run folder, so the look is built from its data.
      setWorkerLook({ stage: node.stage, worker: node.worker });
    }
  };
  openNodeRef.current = openNode;
  const nav = nodes.map((node) => ({
    node,
    open: node.kind === "project" ? node.open :
      node.kind === "idle-divider" ? view.showIdle :
      node.kind === "closed-count" ? view.showClosed : view.open.has(node.key),
  }));
  latest.current = { nav, cursor, helpOpen };

  // A fold or filter can remove the selected row: move the selection to the nearest row above it.
  const previousKeys = useRef<string[]>([]);
  useEffect(() => {
    const keys = nodes.map((node) => node.key);
    if (cursor && !keys.includes(cursor)) {
      const before = previousKeys.current.slice(0, Math.max(0, previousKeys.current.indexOf(cursor)));
      const fallback = [...before].reverse().find((key) => keys.includes(key)) ?? keys[0] ?? null;
      setCursor(fallback);
      if (fallback) page.current?.querySelector<HTMLElement>('[data-nav="' + CSS.escape(fallback) + '"]')?.focus();
    }
    previousKeys.current = keys;
  }, [nodes, cursor]);

  useEffect(() => { saveView(browserStore("localStorage"), view); }, [view]);

  useEffect(() => {
    const focusKey = (key: string) => {
      setCursor(key);
      page.current?.querySelector<HTMLElement>('[data-nav="' + CSS.escape(key) + '"]')?.focus();
    };
    const onKey = (event: KeyboardEvent) => {
      const { nav: items, cursor: selectedKey, helpOpen: isHelpOpen } = latest.current;
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.shiftKey && event.key !== "G" && event.key !== "?") return;
      if (isHelpOpen) {
        if (event.key === "Escape" || event.key === "?") { event.preventDefault(); setHelpOpen(false); }
        return;
      }
      if (document.querySelector('[aria-modal="true"]')) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      // The project uses a native button: let Enter trigger its click exactly once.
      if (event.key === "Enter" && target?.closest("button[data-nav]")) return;
      if (target?.closest("[role='dialog'],[role='menu'],[aria-modal='true'],.shell-popover")) return;
      if (target?.closest("a[href],button:not([data-nav])")) return;
      if (target?.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) return;
      const index = items.findIndex(({ node }) => node.key === selectedKey);
      const current = index >= 0 ? items[index] : undefined;
      const go = (to: number) => { const item = items[Math.min(items.length - 1, Math.max(0, to))]; if (item) focusKey(item.node.key); };
      const toggleCurrent = () => { if (current) toggleNode(current.node); };
      switch (event.key) {
        case "j": case "ArrowDown": event.preventDefault(); go(index < 0 ? 0 : index + 1); break;
        case "k": case "ArrowUp": event.preventDefault(); go(index < 0 ? 0 : index - 1); break;
        case "g": event.preventDefault(); go(0); break;
        case "G": event.preventDefault(); go(items.length - 1); break;
        case "Enter":
          if (!current) break;
          event.preventDefault();
          if (current.node.kind === "stage" || current.node.kind === "worker") openNodeRef.current(current.node); else toggleCurrent();
          break;
        case "ArrowRight":
          if (current && (current.node.kind === "project" || current.node.kind === "finished" || current.node.kind === "idle-divider" || current.node.kind === "closed-count")) {
            event.preventDefault();
            if (!current.open) toggleCurrent();
          } else if (current?.node.kind === "stage" && current.node.kids > 0 && !current.open) {
            event.preventDefault();
            toggleCurrent();
          }
          break;
        case "ArrowLeft": if (current?.open && (current.node.kind !== "stage" || current.node.kids > 0)) { event.preventDefault(); toggleCurrent(); } break;
        case "e": setView((currentView) => reduceOverviewKey(currentView, "e", stages)); break;
        case "h": setView((currentView) => reduceOverviewKey(currentView, "h", stages)); break;
        case "a": setView((currentView) => reduceOverviewKey(currentView, "a", stages)); break;
        case "/": event.preventDefault(); filterInput.current?.focus(); break;
        case "?": event.preventDefault(); setHelpOpen(true); break;
        case "Escape": if (view.query) setView((currentView) => ({ ...currentView, query: "" })); break;
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navigate, search, stages, toggleNode, view.query]);

  const prefixes = new Map<string, { prefix: string; cont: string }>();
  for (let start = 0; start < nodes.length;) {
    if (nodes[start].depth < 0) { start += 1; continue; }
    let end = start;
    while (end < nodes.length && nodes[end].depth >= 0) end += 1;
    treePrefixes(nodes.slice(start, end).map(({ depth }) => depth))
      .forEach((item, offset) => prefixes.set(nodes[start + offset].key, item));
    start = end;
  }
  const workers = sessions.reduce((sum, session) => sum + session.worker_total, 0);
  const activitySeries = sessions.map(({ activity_series }) => activity_series);

  return <main className="plant-page" ref={page}>
    <BoardMasthead projects={stages.length} workers={workers} working={working} idle={idle} quota={quota.data}
      quotaLoading={quota.loading} quotaOffline={Boolean(quota.error || quota.data?.offline)} banner={banner} statsOpen={view.statsOpen}
      onToggleStats={() => setView((current) => ({ ...current, statsOpen: !current.statsOpen }))} activitySeries={activitySeries} />
    <div className="plant-toolbar">
      <input ref={filterInput} className="plant-filter" type="text" placeholder="filter" aria-label="Filter roll-up"
        value={view.query} onChange={(event) => setView((current) => ({ ...current, query: event.target.value }))}
        onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); setView((current) => ({ ...current, query: "" })); event.currentTarget.blur(); } }} />
    </div>
    {holding ? <div className="plant-reconnect" role="status">reconnecting to Monitor… showing the last update</div> : null}
    <NeedsYou stages={[...waiting]} activity={activity} nowSec={nowMs / 1000} onOpen={(stage) => openNode({ kind: "stage", key: "s:" + stage.path, depth: 0, stage, kids: 0 })} />
    <div className="plant-list rollup-list" role="tree" aria-label="Station Dashboard roll-up">
      {stages.length === 0 ? (connecting || (!monitor.data && !monitor.error) ? <Skeleton /> : monitor.data?.online === false || monitor.error ? <div className="plant-empty">Monitor is offline.</div> : <div className="plant-empty">No sessions.</div>) : nodes.length === 0
        ? <div className="plant-empty">No stages match.</div>
        : nodes.map((node) => {
          const prefix = prefixes.get(node.key) ?? { prefix: "", cont: "" };
          const row = <RollupRow key={node.key} node={node} prefix={prefix.prefix} cont={prefix.cont} nowSec={nowMs / 1000}
            selected={cursor === node.key} onToggle={() => { setCursor(node.key); toggleNode(node); }} onOpen={openNode} onFocus={setCursor} waiting={waiting} activity={activity}
            expanded={node.kind === "stage" && peek === node.stage.path} unfolded={node.kind === "stage" && view.open.has(node.key)} />;
          if (node.kind !== "stage" || peek !== node.stage.path) return row;
          const session = sessionFor(node.stage);
          return <Fragment key={node.key}>{row}<StagePeek stage={node.stage} activity={activity[node.stage.path]} nowSec={nowMs / 1000}
            onOpenRun={session ? () => navigate({ pathname: sessionPath(session.id), search }) : null} /></Fragment>;
        })}
      <div className="plant-sr" aria-live="polite" />
    </div>
    <footer className="plant-hints"><span><kbd>j</kbd>/<kbd>k</kbd> move</span><span><kbd>Enter</kbd> open/toggle</span><span><kbd>→</kbd>/<kbd>←</kbd> fold</span><span><kbd>e</kbd> projects</span><span><kbd>h</kbd> idle</span><span><kbd>a</kbd> closed</span><span><kbd>g</kbd>/<kbd>G</kbd> first/last</span><span><kbd>/</kbd> filter</span><span><kbd>?</kbd> keys</span></footer>
    {workerLook && <WorkerPeek stage={workerLook.stage} worker={workerLook.worker} nowSec={nowMs / 1000}
      onOpenRun={sessionFor(workerLook.stage) ? () => { const session = sessionFor(workerLook.stage); if (session) navigate({ pathname: sessionPath(session.id), search }); } : null}
      onClose={() => setWorkerLook(null)} />}
    {helpOpen && <BoardHelp onClose={() => setHelpOpen(false)} />}
  </main>;
}

function Skeleton() {
  return <div className="plant-skeleton" aria-hidden="true"><i /><i /><i /><div className="plant-empty">connecting…</div></div>;
}
