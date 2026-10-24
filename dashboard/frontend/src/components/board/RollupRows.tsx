import type { ReactElement } from "react";
import "./rollup.css";
import { rowState, type RollupNode, type RowState } from "./rollup";
import type { MonitorStage } from "../../api/types";
import { levelTagShort, levelTagTone, nowLine, formatAge } from "./treeText";
import { formatCtx, ctxLevel, tagClass } from "./monitor";
import { formatMoney, isLiveState } from "./model";
import { Elapsed, Mark } from "./BoardParts";
import type { TreeWorkerState } from "./state";

// One row of the roll-up tree, drawn like Monitor's terminal rows. Presentational only: the caller owns the
// tree, the prefixes and the selection; this component reports clicks through onToggle and onOpen.

export type RollupRowProps = {
  node: RollupNode;
  prefix: string; // box-drawing prefix for this row (treePrefixes)
  cont: string; // same-width continuation for sub-lines (treePrefixes)
  nowSec: number;
  selected: boolean;
  onToggle: (key: string) => void;
  onOpen: (node: RollupNode) => void;
  onFocus: (key: string) => void;
  /** Sessions waiting on the user (rollup.waitingStages). */
  waiting?: Set<MonitorStage>;
  /** Latest action per stage path, from /api/activity. */
  activity?: Record<string, Activity>;
  /** The session's quick look is open under this row. */
  expanded?: boolean;
  /** The session's children (sub-sessions and workers) are unfolded below this row. */
  unfolded?: boolean;
};

export type Activity = { text: string; ts: number; kind: "tool" | "say" | "ask" };

const STATE_GLYPH: Record<RowState, string> = { working: "●", waiting: "◐", idle: "○", service: "◇", closed: "·" };
const STATE_WORD: Record<RowState, string> = { working: "working", waiting: "needs you", idle: "idle", service: "always-on service", closed: "closed" };

function StateGlyph({ state }: { state: RowState }): ReactElement {
  return <span className={`rollup-glyph s-${state}`} aria-label={STATE_WORD[state]} title={STATE_WORD[state]}>{STATE_GLYPH[state]}</span>;
}

type Kind<K extends RollupNode["kind"]> = Extract<RollupNode, { kind: K }>;

const RULE = "─".repeat(240);
const EFFORT_SHORT: Partial<Record<string, string>> = { low: "lo", medium: "med", high: "hi" };
const WORKER_STATE: Partial<Record<string, TreeWorkerState>> = {
  running: "running",
  quiet: "quiet",
  stalled: "stalled",
  done: "done",
  failed: "failed",
  noreport: "no-report",
  "no-report": "no-report",
};

// Worker states the backend may send beyond the known set read as "no-report", the quietest honest mark.
export function workerTreeState(state: string): TreeWorkerState {
  return WORKER_STATE[state] ?? "no-report";
}

function formatTokens(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

/** `/home/user/Projects/foo` -> `foo`; anything without a slash stays as is. */
export function projectName(project: string): string {
  const parts = project.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : project;
}

/** Activity lines are transcript text: drop markdown emphasis and code marks for a clean one-liner. */
export function plainText(text: string): string {
  return text.replace(/\*\*|__|`/g, "").replace(/\s+/g, " ").trim();
}

function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

function rowClass(base: string, selected: boolean): string {
  return selected ? `rollup-row ${base} is-selected` : `rollup-row ${base}`;
}

function projectRow(node: Kind<"project">, selected: boolean, onToggle: (key: string) => void, onFocus: (key: string) => void): ReactElement {
  const { counts, totals } = node;
  return (
    <div role="treeitem" aria-selected={selected} aria-expanded={node.open} data-key={node.key} className={rowClass("rollup-project", selected)}>
      <div className="rollup-line">
        <button type="button" data-nav={node.key} className="rollup-toggle" onClick={() => onToggle(node.key)} onFocus={() => onFocus(node.key)}>
          <span className="rollup-arrow">{node.open ? "▾" : "▸"}</span>
          <span className={`rollup-name${counts.working > 0 ? " working" : " muted"}`}>{projectName(node.project)}</span>
        </button>
        <span className="rollup-rule" aria-hidden="true">{RULE}</span>
        <span className="rollup-counts">{`${counts.working} working · ${counts.idle} idle · ${counts.closed} closed`}</span>
        <span className="rollup-totals">
          <span className="rollup-tokens">{formatTokens(totals.tokens)}</span>
          {totals.cost > 0 ? <span className="rollup-cost">{formatMoney(totals.cost)}</span> : null}
        </span>
      </div>
    </div>
  );
}

function stageRow(
  node: Kind<"stage">,
  prefix: string,
  cont: string,
  nowSec: number,
  selected: boolean,
  onOpen: (node: RollupNode) => void,
  onFocus: (key: string) => void,
  waiting: Set<MonitorStage>,
  activity: Record<string, Activity>,
  expanded: boolean,
  unfolded: boolean,
): ReactElement {
  const { stage } = node;
  const state = rowState(stage, waiting, nowSec);
  const name = stage.display_name || stage.session_name || stage.label;
  const tag = stage.level_tag ?? "";
  const tone = levelTagTone(tag);
  const effort = stage.effort ? (EFFORT_SHORT[stage.effort] ?? stage.effort) : "";
  const model = [stage.lead_model ?? "", effort].filter(Boolean).join("·");
  const ctx = stage.lead_ctx ?? null;
  const level = ctxLevel(ctx);
  // No timestamp (0) gives no age rather than "20750d".
  const sinceActivity = stage.last_activity > 0 ? formatAge(Math.max(0, nowSec - stage.last_activity)) : "";
  const live = state === "working" || state === "waiting";
  const act = live ? activity[stage.path] : undefined;
  const fallback = live ? nowLine(stage, nowSec) : null;
  const nowText = act ? plainText(act.text) : fallback && fallback.text !== "no activity recorded" ? fallback.text : null;
  const nowAge = act ? formatAge(Math.max(0, nowSec - act.ts)) : fallback?.ageSec != null ? formatAge(fallback.ageSec) : "";
  return (
    <div role="treeitem" tabIndex={-1} data-nav={node.key} aria-selected={selected} data-key={node.key} data-state={state} aria-expanded={node.kids > 0 ? unfolded : undefined} aria-describedby={undefined} className={rowClass(`rollup-stage st-${state}${expanded ? " is-expanded" : ""}`, selected)} onClick={() => onOpen(node)} onFocus={() => onFocus(node.key)}>
      <div className="rollup-line">
        <span className="rollup-prefix">{prefix}</span>
        <StateGlyph state={state} />
        {tag ? <span className={`rollup-tag${tone ? ` ${tone}` : ""}`}>{levelTagShort(tag)}</span> : null}
        <span className="rollup-name">{name}</span>
        <span className="rollup-meta">
          {node.kids > 0 ? <span className="rollup-kids" title={unfolded ? "Tap again for the quick look" : "Tap to show sub-sessions and workers"}>{unfolded ? "▾" : "▸"}{node.kids}</span> : null}
          {state === "waiting" ? <span className="rollup-needs">needs you</span> : null}
          {stage.tag ? <span className={`rollup-harness ${tagClass(stage.tag)}`}>{stage.tag}</span> : null}
          {model ? <span className="rollup-model">{model}</span> : null}
          <span className={`rollup-ctx${level ? ` ${level}` : ""}`}>{formatCtx(ctx)}</span>
          <span className="rollup-tokens">{formatTokens(stage.tokens_total)}</span>
          <span className="rollup-elapsed">{sinceActivity}</span>
        </span>
      </div>
      {nowText ? (
        <div className={`rollup-now${act?.kind === "ask" ? " ask" : ""}`}>
          <span className="rollup-prefix">{cont}</span>
          <span className="rollup-now-mark" aria-hidden="true">↳</span>
          <span className="rollup-now-text">{nowText}</span>
          {nowAge ? <span className="rollup-now-age">{nowAge}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

function workerRow(
  node: Kind<"worker">,
  prefix: string,
  selected: boolean,
  onOpen: (node: RollupNode) => void,
  onFocus: (key: string) => void,
): ReactElement {
  const { worker } = node;
  const state = workerTreeState(worker.state);
  const live = isLiveState(state);
  return (
    <div role="treeitem" tabIndex={-1} data-nav={node.key} aria-selected={selected} data-key={node.key} className={rowClass("rollup-worker", selected)} onClick={() => onOpen(node)} onFocus={() => onFocus(node.key)}>
      <div className="rollup-line">
        <span className="rollup-prefix">{prefix}</span>
        <span className="rollup-mark">
          <Mark state={state} spin={live} />
        </span>
        <span className="rollup-tag dim">WRKR</span>
        <span className="rollup-name">{worker.name}</span>
        {worker.title ? <span className="rollup-title">{worker.title}</span> : null}
        <span className="rollup-meta">
          {worker.model ? <span className="rollup-model">{worker.model}</span> : null}
          <span className="rollup-elapsed">
            <Elapsed start={worker.start ?? null} end={worker.end ?? null} live={live} />
          </span>
        </span>
      </div>
    </div>
  );
}

const CHIP_MAX = 10;
const HASHY = /^[0-9a-f]{8}-|^ses_|^[0-9a-f-]{20,}$/i;

/** Worker ids that are hashes read as their title (short) instead. */
function chipLabel(name: string, title?: string): string {
  if (!HASHY.test(name)) return name;
  const text = (title || name).trim();
  return text.length > 22 ? text.slice(0, 21) + "…" : text;
}

function doneRow(
  node: Kind<"done">,
  prefix: string,
  selected: boolean,
  onOpen: (node: RollupNode) => void,
  onFocus: (key: string) => void,
): ReactElement {
  return (
    <div role="treeitem" tabIndex={-1} data-nav={node.key} aria-selected={selected} data-key={node.key} className={rowClass("rollup-done-row", selected)} onFocus={() => onFocus(node.key)}>
      <div className="rollup-line">
        <span className="rollup-prefix">{prefix}</span>
        <span className="rollup-done-label">done</span>
        <span className="rollup-chips">
          {node.workers.slice(-CHIP_MAX).map((worker, index) => {
            const failed = worker.state === "failed";
            const missing = worker.state === "noreport" || worker.state === "no-report";
            const tone = failed ? "bad" : missing ? "miss" : "ok";
            return (
              <button type="button" key={`${index}:${worker.name}`} className={`rollup-chip ${tone}`} title={worker.title || worker.name}
                onClick={(event) => { event.stopPropagation(); onOpen({ kind: "worker", key: `w:${node.stage.path}|${worker.name}`, depth: node.depth, stage: node.stage, worker }); }}>
                {failed ? "✗" : missing ? "?" : "✓"}{chipLabel(worker.name, worker.title)}
              </button>
            );
          })}
          {node.workers.length > CHIP_MAX ? <span className="rollup-chip-more">{`+${node.workers.length - CHIP_MAX} older`}</span> : null}
        </span>
      </div>
    </div>
  );
}

function finishedRow(
  node: Kind<"finished">,
  prefix: string,
  nowSec: number,
  selected: boolean,
  onToggle: (key: string) => void,
  onFocus: (key: string) => void,
): ReactElement {
  const ago = node.lastActivity == null ? "" : `  last ${formatAge(Math.max(0, nowSec - node.lastActivity))} ago`;
  return (
    <div role="treeitem" tabIndex={-1} data-nav={node.key} aria-selected={selected} data-key={node.key} className={rowClass("rollup-finished-row", selected)} onClick={() => onToggle(node.key)} onFocus={() => onFocus(node.key)}>
      <div className="rollup-line">
        <span className="rollup-prefix">{prefix}</span>
        <span className="rollup-finished">{`▸ finished ${node.n}${ago}`}</span>
      </div>
    </div>
  );
}

function dividerRow(node: Kind<"idle-divider">, selected: boolean, onToggle: (key: string) => void, onFocus: (key: string) => void): ReactElement {
  return (
    <div role="treeitem" tabIndex={-1} data-nav={node.key} aria-selected={selected} data-key={node.key} className={rowClass("rollup-divider", selected)} onClick={() => onToggle(node.key)} onFocus={() => onFocus(node.key)}>
      <div className="rollup-line">
        <span className="rollup-divider-text">{`╌╌ idle · ${node.count} ${plural(node.count, "project", "projects")} ╌╌╌╌ h shows`}</span>
      </div>
    </div>
  );
}

function closedRow(node: Kind<"closed-count">, selected: boolean, onToggle: (key: string) => void, onFocus: (key: string) => void): ReactElement {
  return (
    <div role="treeitem" tabIndex={-1} data-nav={node.key} aria-selected={selected} data-key={node.key} className={rowClass("rollup-closed-count", selected)} onClick={() => onToggle(node.key)} onFocus={() => onFocus(node.key)}>
      <div className="rollup-line">
        <span className="rollup-closed-text">{`${node.count} closed ${plural(node.count, "project", "projects")} · a shows`}</span>
      </div>
    </div>
  );
}

const NO_STAGES = new Set<MonitorStage>();
const NO_ACTIVITY: Record<string, Activity> = {};

export function RollupRow(props: RollupRowProps): ReactElement {
  const { node, prefix, cont, nowSec, selected, onToggle, onOpen, onFocus } = props;
  const waiting = props.waiting ?? NO_STAGES;
  const activity = props.activity ?? NO_ACTIVITY;
  switch (node.kind) {
    case "project":
      return projectRow(node, selected, onToggle, onFocus);
    case "stage":
      return stageRow(node, prefix, cont, nowSec, selected, onOpen, onFocus, waiting, activity, props.expanded ?? false, props.unfolded ?? false);
    case "worker":
      return workerRow(node, prefix, selected, onOpen, onFocus);
    case "done":
      return doneRow(node, prefix, selected, onOpen, onFocus);
    case "finished":
      return finishedRow(node, prefix, nowSec, selected, onToggle, onFocus);
    case "idle-divider":
      return dividerRow(node, selected, onToggle, onFocus);
    case "closed-count":
      return closedRow(node, selected, onToggle, onFocus);
  }
}
