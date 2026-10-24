import { isValidElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import type { MonitorStage, MonitorWorker } from "../../api/types";
import type { RollupNode } from "./rollup";
import { RollupRow, type RollupRowProps } from "./RollupRows";

// No DOM in this project's test environment, so the parts that read the clock are stubbed (as in BoardRows.test.ts).
vi.mock("./BoardParts", () => ({
  Mark: () => null,
  Elapsed: () => null,
}));

const NOW = 1_700_000_000;

function stage(over: Partial<MonitorStage> = {}): MonitorStage {
  return {
    path: "/w/alpha",
    label: "alpha",
    tag: "CC·W",
    lead_ctx: 300_000,
    lead_state: "live",
    live: 1,
    last_activity: NOW - 60,
    solo: false,
    latest_milestone: null,
    counts: { live: 1, failed: 0, noreport: 0, done: 0, total: 1 },
    section: "active",
    session_id: "s1",
    project: "alpha",
    level_tag: "SPAWN",
    lead_model: "opus",
    effort: "high",
    tokens_total: 1_234_567,
    cost_total: 0.82,
    workers: [],
    ...over,
  };
}

function worker(over: Partial<MonitorWorker> = {}): MonitorWorker {
  return { name: "builder-1", state: "running", title: "wire the grid", model: "sonnet", start: NOW - 90, end: null, ...over };
}

function props(node: RollupNode, over: Partial<RollupRowProps> = {}): RollupRowProps {
  return {
    node,
    prefix: "├─ ",
    cont: "│  ",
    nowSec: NOW,
    selected: false,
    onToggle: vi.fn(),
    onOpen: vi.fn(),
    onFocus: vi.fn(),
    ...over,
  };
}

const projectNode: RollupNode = {
  kind: "project",
  key: "p:alpha",
  depth: -1,
  project: "alpha",
  counts: { working: 2, idle: 1, closed: 3 },
  totals: { tokens: 1_234_567, cost: 0.82 },
  open: true,
};

const stageNode: RollupNode = { kind: "stage", key: "s:/w/alpha", depth: 0, stage: stage(), kids: 0 };

const workerNode: RollupNode = {
  kind: "worker",
  key: "w:/w/alpha|builder-1",
  depth: 1,
  stage: stage(),
  worker: worker(),
};

const finishedNode: RollupNode = {
  kind: "finished",
  key: "f:/w/alpha",
  depth: 1,
  n: 4,
  scope: "workers",
  lastActivity: NOW - 1500,
};

const dividerNode: RollupNode = { kind: "idle-divider", key: "d:idle", depth: -1, count: 2 };
const closedNode: RollupNode = { kind: "closed-count", key: "d:closed", depth: -1, count: 3 };

// Finds the first onClick in a returned element tree. RollupRow is hook-free, so calling it directly is safe.
function clickHandler(node: ReactNode): (() => void) | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = clickHandler(child);
      if (found) return found;
    }
    return null;
  }
  if (!isValidElement(node)) return null;
  const elementProps = node.props as { onClick?: () => void; children?: ReactNode };
  if (elementProps.onClick) return elementProps.onClick;
  return clickHandler(elementProps.children);
}

describe("RollupRow markup", () => {
  it("draws a project heading with counts, totals and treeitem attributes", () => {
    const html = renderToStaticMarkup(<RollupRow {...props(projectNode)} />);
    expect(html).toContain('role="treeitem"');
    expect(html).toContain('aria-selected="false"');
    expect(html).toContain('data-key="p:alpha"');
    expect(html).toContain("2 working · 1 idle · 3 closed");
    expect(html).toContain("1.2M");
    expect(html).toContain("$0.82");
    expect(html).toContain("▾");
  });

  it("draws a stage with level tag, harness, model·effort, ctx and tokens", () => {
    const html = renderToStaticMarkup(<RollupRow {...props(stageNode)} />);
    expect(html).toContain("├─ ");
    expect(html).toContain("SPWN");
    expect(html).toContain("CC·W");
    expect(html).toContain("opus·hi");
    expect(html).toContain("300k");
    expect(html).toContain("rollup-ctx bad");
    expect(html).toContain("1.2M");
    expect(html).toContain('data-key="s:/w/alpha"');
  });

  it("draws a worker with the WRKR tag, title and model", () => {
    const html = renderToStaticMarkup(<RollupRow {...props(workerNode)} />);
    expect(html).toContain("WRKR");
    expect(html).toContain("builder-1");
    expect(html).toContain("wire the grid");
    expect(html).toContain("sonnet");
    expect(html).toContain('data-key="w:/w/alpha|builder-1"');
  });

  it("draws the finished fold with its count and age", () => {
    const html = renderToStaticMarkup(<RollupRow {...props(finishedNode)} />);
    expect(html).toContain('<span class="rollup-prefix">├─ </span>');
    expect(html).toContain("▸ finished 4  last 25m ago");
  });

  it("draws the idle divider", () => {
    const html = renderToStaticMarkup(<RollupRow {...props(dividerNode)} />);
    expect(html).toContain("╌╌ idle · 2 projects ╌╌╌╌ h shows");
  });

  it("draws the closed-count line", () => {
    const html = renderToStaticMarkup(<RollupRow {...props(closedNode)} />);
    expect(html).toContain("3 closed projects · a shows");
  });

  it("reflects selection in aria-selected", () => {
    const html = renderToStaticMarkup(<RollupRow {...props(stageNode, { selected: true })} />);
    expect(html).toContain('aria-selected="true"');
  });
});

describe("RollupRow NOW line", () => {
  it("shows the NOW sub-line for a live stage, with the milestone's age", () => {
    const live = stage({ latest_milestone: { ts: NOW - 180, text: "running tests" } });
    const html = renderToStaticMarkup(<RollupRow {...props({ kind: "stage", key: "s:/w/alpha", depth: 0, stage: live, kids: 0 })} />);
    expect(html).toContain('<span class="rollup-now-text">running tests</span>');
    expect(html).toContain('<span class="rollup-now-age">3m</span>');
    expect(html).toContain("s-working");
  });

  it("prefers the live activity feed and marks a question in amber", () => {
    const live = stage({ latest_milestone: { ts: NOW - 180, text: "old milestone" } });
    const html = renderToStaticMarkup(<RollupRow {...props({ kind: "stage", key: "s:/w/alpha", depth: 0, stage: live, kids: 0 })}
      activity={{ [live.path]: { text: "asked the user a question", ts: NOW - 60, kind: "ask" } }} />);
    expect(html).toContain("asked the user a question");
    expect(html).not.toContain("old milestone");
    expect(html).toContain("rollup-now ask");
  });

  it("marks a waiting session with the amber glyph and a needs-you label", () => {
    const live = stage({});
    const html = renderToStaticMarkup(<RollupRow {...props({ kind: "stage", key: "s:/w/alpha", depth: 0, stage: live, kids: 0 })} waiting={new Set([live])} />);
    expect(html).toContain("s-waiting");
    expect(html).toContain("needs you");
  });

  it("omits the NOW sub-line for a closed stage", () => {
    const closed = stage({
      section: "closed",
      lead_state: "idle",
      latest_milestone: { ts: NOW - 180, text: "finished the grid" },
    });
    const html = renderToStaticMarkup(<RollupRow {...props({ kind: "stage", key: "s:/w/alpha", depth: 0, stage: closed, kids: 0 })} />);
    expect(html).not.toContain("NOW");
    expect(html).not.toContain("rollup-now");
  });
});

describe("RollupRow clicks", () => {
  it("project: the arrow/name button calls onToggle with the node key", () => {
    const p = props(projectNode);
    clickHandler(RollupRow(p))?.();
    expect(p.onToggle).toHaveBeenCalledWith("p:alpha");
    expect(p.onOpen).not.toHaveBeenCalled();
  });

  it("stage: a click calls onOpen with the node", () => {
    const p = props(stageNode);
    clickHandler(RollupRow(p))?.();
    expect(p.onOpen).toHaveBeenCalledWith(stageNode);
    expect(p.onToggle).not.toHaveBeenCalled();
  });

  it("worker: a click calls onOpen with the node", () => {
    const p = props(workerNode);
    clickHandler(RollupRow(p))?.();
    expect(p.onOpen).toHaveBeenCalledWith(workerNode);
  });

  it("finished: a click calls onToggle with its key", () => {
    const p = props(finishedNode);
    clickHandler(RollupRow(p))?.();
    expect(p.onToggle).toHaveBeenCalledWith("f:/w/alpha");
  });

  it("idle divider: a click calls onToggle with its key", () => {
    const p = props(dividerNode);
    clickHandler(RollupRow(p))?.();
    expect(p.onToggle).toHaveBeenCalledWith("d:idle");
  });

  it("closed-count: a click calls onToggle with its key", () => {
    const p = props(closedNode);
    clickHandler(RollupRow(p))?.();
    expect(p.onToggle).toHaveBeenCalledWith("d:closed");
  });
});

describe("rollup.css narrow screens", () => {
  it("hides tokens, harness and ctx at 560px and below", () => {
    const css = readFileSync(new URL("./rollup.css", import.meta.url), "utf8");
    const at = css.indexOf("@media (max-width: 560px)");
    expect(at).toBeGreaterThanOrEqual(0);
    const block = css.slice(at);
    expect(block).toContain(".rollup-tokens");
    expect(block).toContain(".rollup-harness");
    expect(block).toContain(".rollup-ctx");
  });
});
