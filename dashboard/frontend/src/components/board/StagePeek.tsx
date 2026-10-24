import type { ReactElement } from "react";
import type { MonitorStage } from "../../api/types";
import { formatCtx, ctxLevel } from "./monitor";
import { formatMoney } from "./model";
import { formatAge } from "./treeText";
import { isWorkerLive } from "./rollup";
import { plainText, type Activity } from "./RollupRows";

function tokens(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

/**
 * The quick look under a tapped session: what it is for, what it is doing, what it costs,
 * and one button through to the full run when there is one.
 */
export function StagePeek({ stage, activity, nowSec, onOpenRun }: {
  stage: MonitorStage;
  activity: Activity | undefined;
  nowSec: number;
  onOpenRun: (() => void) | null;
}): ReactElement {
  const workers = stage.workers ?? [];
  const live = workers.filter(isWorkerLive).length;
  const failed = workers.filter((w) => w.state === "failed").length;
  const level = ctxLevel(stage.lead_ctx ?? null);
  const goal = stage.goal ? plainText(stage.goal) : "";
  const age = stage.last_activity > 0 ? `${formatAge(Math.max(0, nowSec - stage.last_activity))} ago` : "unknown";
  return (
    <div className="peek" role="group" aria-label="Session summary">
      {goal ? <p className="peek-goal">{goal}</p> : <p className="peek-goal dim">No goal recorded.</p>}
      {activity ? <p className={`peek-now${activity.kind === "ask" ? " ask" : ""}`}><span>now</span>{plainText(activity.text)}</p> : null}
      <dl className="peek-facts">
        <div><dt>model</dt><dd>{[stage.lead_model, stage.effort].filter(Boolean).join(" · ") || "—"}</dd></div>
        <div><dt>context</dt><dd className={level ?? ""}>{formatCtx(stage.lead_ctx ?? null) || "—"}</dd></div>
        <div><dt>tokens</dt><dd>{tokens(stage.tokens_total ?? stage.lead_tokens)}</dd></div>
        {(stage.cost_total ?? 0) > 0 ? <div><dt>cost</dt><dd>{formatMoney(stage.cost_total ?? 0)}</dd></div> : null}
        <div><dt>workers</dt><dd>{workers.length ? `${live} live · ${workers.length - live} done${failed ? ` · ${failed} failed` : ""}` : "none"}</dd></div>
        <div><dt>last active</dt><dd>{age}</dd></div>
      </dl>
      {onOpenRun ? <button type="button" className="peek-open" onClick={(event) => { event.stopPropagation(); onOpenRun(); }}>Open run →</button> : <p className="peek-note">No run folder for this session.</p>}
    </div>
  );
}
