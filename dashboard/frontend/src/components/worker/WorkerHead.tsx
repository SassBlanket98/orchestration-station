import { Elapsed } from "../ui/Elapsed";
import { TimeAgo } from "../ui/TimeAgo";
import type { WorkerStatus } from "../../api/types";
import {
  accessLabel,
  displayField,
  engineChip,
  engineFamilyOf,
  logRateKbPerMin,
  logSizeLabel,
  type WorkerView,
} from "./evidence";

const STATUS_TONE: Record<WorkerStatus, string> = {
  running: "good",
  finished: "",
  exited: "bad",
  unknown: "warn",
};

/** Identity, status and the vital facts of one worker. Unknown fields show "?". */
export function WorkerHead({ view }: { view: WorkerView }) {
  const w = view.worker;
  const chip = engineChip(engineFamilyOf(w));
  const retries = w.worker_events.filter((e) => e.kind === "retry").length;
  const rate = logRateKbPerMin(w.log_series);
  const pid =
    w.pid === null
      ? "pid ?"
      : `pid ${w.pid}${w.status === "running" ? " · alive" : ""}`;

  return (
    <div className="wm-id">
      <div className="wm-title-row">
        <span className={`wm-glyph wm-e-${chip.cls}`} title={chip.label} aria-label={chip.label}>
          {chip.glyph}
        </span>
        <h2 id="wm-title">{w.name}</h2>
        <span className={`wm-pill ${STATUS_TONE[w.status]}`.trim()}>{w.status}</span>
        {retries > 0 && <span className="wm-pill warn">retry {retries}</span>}
      </div>
      <p className="wm-sub">{w.title ?? "No title in the brief"}</p>
      <div className="wm-chips">
        <Chip value={displayField(w.model)} fallback="no model"/>
        <Chip value={displayField(w.effort)} label="effort"/>
        <span className="wm-chip">{chip.label}{w.account !== null ? ` · ${w.account}` : ""}</span>
        <Chip value={accessLabel(w.access)}/>
        <Chip value={displayField(w.job)} label="job"/>
      </div>
      <div className="wm-facts">
        <span className="wm-pill">{pid}</span>
        <span className="wm-pill">
          elapsed <Elapsed from={w.started_at} to={w.finished_at}/>
        </span>
        <span className="wm-pill">
          last output <TimeAgo at={w.last_activity_at}/>
        </span>
        <span className="wm-pill">
          log {logSizeLabel(w.log_size)}
          {rate === null ? "" : ` · +${Math.round(rate)} KB/min`}
        </span>
      </div>
    </div>
  );
}

/** A fact chip. Missing values keep a dashed "?" chip rather than disappearing. */
function Chip({
  value,
  label,
  fallback,
}: {
  value: { text: string; missing: boolean };
  label?: string;
  fallback?: string;
}) {
  if (value.missing) {
    const text = fallback ?? (label !== undefined ? `${label} ?` : value.text);
    return <span className="wm-chip is-missing" title="No source reports this value">{text}</span>;
  }
  return <span className="wm-chip">{label !== undefined ? `${label} ${value.text}` : value.text}</span>;
}
