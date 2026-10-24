import { useEffect, useRef } from "react";
import type { MonitorStage, MonitorWorker } from "../../api/types";
import { formatDuration } from "../../lib/format";
import { formatMoney } from "./model";
import { formatAge } from "./treeText";

function tokens(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

/** The facts Monitor has on one worker, as rows. Pure so it can be tested without a DOM. */
export function workerFacts(worker: MonitorWorker, nowSec: number): [string, string][] {
  const rows: [string, string][] = [["state", worker.state]];
  if (worker.outcome && worker.outcome !== "unknown") rows.push(["outcome", worker.outcome]);
  const model = [worker.model, worker.harness && worker.harness !== "unknown" ? worker.harness : ""].filter(Boolean).join(" · ");
  if (model) rows.push(["model", model]);
  if (worker.job) rows.push(["job", worker.is_review ? `${worker.job} (review)` : worker.job]);
  if (worker.start != null) rows.push(["started", `${formatAge(Math.max(0, nowSec - worker.start))} ago`]);
  if (worker.start != null) rows.push(["ran", formatDuration(Math.max(0, (worker.end ?? nowSec) - worker.start))]);
  if (worker.tokens_total != null) rows.push(["tokens", tokens(worker.tokens_total)]);
  if ((worker.cost ?? 0) > 0) rows.push(["cost", formatMoney(worker.cost)]);
  if (worker.quiet_s != null && worker.end == null) rows.push(["quiet for", formatDuration(worker.quiet_s)]);
  if (worker.errors) rows.push(["errors", String(worker.errors)]);
  rows.push(["report", worker.has_report ? "yes" : "none"]);
  return rows;
}

/**
 * A worker's quick look, built from Monitor's own data: the Board run folder often has no worker of the same
 * name (worktree and native-task workers), so the page cannot look it up there. Mounted only while open.
 */
export function WorkerPeek({ stage, worker, nowSec, onOpenRun, onClose }: {
  stage: MonitorStage;
  worker: MonitorWorker;
  nowSec: number;
  onOpenRun: (() => void) | null;
  onClose: () => void;
}) {
  const close = useRef<HTMLButtonElement>(null);
  // Read through a ref so a parent re-render (the page ticks every 10 s) does not re-run the focus effect.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    close.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); onCloseRef.current(); } };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); previous?.focus(); };
  }, []);
  const session = stage.display_name || stage.session_name || stage.label;
  return <div className="plant-help-backdrop" onMouseDown={onClose}>
    <div role="dialog" aria-modal="true" aria-label={`Worker ${worker.name}`} className="plant-help plant-worker-peek" onMouseDown={(event) => event.stopPropagation()}>
      <h2>Worker · {session}</h2>
      <p className="peek-name">{worker.name}</p>
      {worker.title ? <p className="peek-goal">{worker.title}</p> : null}
      {worker.goal && worker.goal !== worker.title ? <p className="peek-goal dim">{worker.goal}</p> : null}
      {worker.failure ? <p className="peek-now ask"><span>failed</span>{worker.failure}</p> : null}
      <dl className="peek-facts">{workerFacts(worker, nowSec).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      <div className="plant-usage-actions">
        {onOpenRun ? <button type="button" className="peek-open" onClick={() => { onClose(); onOpenRun(); }}>Open run →</button> : <span className="peek-note">No run folder for this session.</span>}
        <button ref={close} type="button" className="plant-help-close" onClick={onClose}>close <kbd>esc</kbd></button>
      </div>
    </div>
  </div>;
}
