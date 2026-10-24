import type { Worker, WorkerEvent, WorkerEventKind } from "../../api/types";
import { formatBytes, formatTimeAgo } from "../../lib/format";
import { Markdown } from "../ui/Markdown";
import { EmptyState } from "../ui/EmptyState";
import {
  displayField,
  engineChip,
  engineFamilyOf,
  logRateKbPerMin,
  logSizeLabel,
  patchPreview,
  sparkPoints,
  tokensLabel,
  type WorkerView,
} from "./evidence";
import { formatStepDuration, newestFirst, stepBarRatios, stepOutcome, stepSource } from "./steps";
import type { WorkerTab } from "./tabs";

/** The body of the active tab. */
export function WorkerTabBody({ tab, view }: { tab: WorkerTab; view: WorkerView }) {
  switch (tab) {
    case "overview":
      return <OverviewPanel view={view}/>;
    case "steps":
      return <StepsPanel worker={view.worker}/>;
    case "brief":
      return <BriefPanel view={view}/>;
    case "report":
      return <ReportPanel view={view}/>;
    case "attempts":
      return <AttemptsPanel events={view.worker.worker_events}/>;
    case "files":
      return <FilesPanel view={view}/>;
  }
}

function Field({ label, value }: { label: string; value: { text: string; missing: boolean } }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={value.missing ? "is-missing" : undefined}>{value.text}</dd>
    </>
  );
}

function OverviewPanel({ view }: { view: WorkerView }) {
  const w = view.worker;
  const family = engineFamilyOf(w);
  const cls = engineChip(family).cls;
  const source = stepSource(w);
  const rate = logRateKbPerMin(w.log_series);
  const spark = sparkPoints(w.log_series, 240, 40);
  const lastLine = w.now?.text ?? null;

  return (
    <div className="wm-stack">
      <div className="wm-grid2">
        <section className="wm-card">
          <h3 className="wm-cap">Step timeline</h3>
          {source.kind === "steps" ? (
            <>
              <p className="wm-note">Duration per step on one scale.</p>
              <div className="wm-bars" role="img" aria-label={`${source.count} steps`}>
                {stepBarRatios(w.step_events).map((ratio, i) => (
                  <i
                    key={w.step_events[i].seq}
                    className={`wm-bar wm-e-${cls}`}
                    style={{ height: `${Math.round(ratio * 100)}%` }}
                    title={`#${w.step_events[i].seq} ${formatStepDuration(w.step_events[i].duration_ms)}`}
                  />
                ))}
              </div>
            </>
          ) : (
            <NoSource reason={source.reason} last={lastLine}/>
          )}
        </section>

        <section className="wm-card">
          <h3 className="wm-cap">Identity and launch</h3>
          <dl className="wm-dl">
            <Field label="engine" value={displayField(family === "unknown" ? null : engineChip(family).label)}/>
            <Field label="account" value={displayField(w.account)}/>
            <Field label="model" value={displayField(w.model)}/>
            <Field label="effort" value={displayField(w.effort)}/>
            <Field label="mode" value={displayField(w.mode)}/>
            <Field label="access" value={displayField(w.access)}/>
            <Field label="access source" value={displayField(w.access_source)}/>
            <Field label="job" value={displayField(w.job)}/>
            <Field label="tokens" value={tokensLabel(w.tokens)}/>
            <Field label="tokens source" value={displayField(w.tokens_source)}/>
          </dl>
        </section>
      </div>

      <div className="wm-grid2">
        <section className="wm-card">
          <h3 className="wm-cap">Last step</h3>
          <p className="wm-mono">{lastLine ?? "no output yet"}</p>
          {w.goal !== null && <p className="wm-note">Goal: {w.goal}</p>}
        </section>

        <section className="wm-card">
          <h3 className="wm-cap">Log growth</h3>
          {spark === "" ? (
            <p className="wm-note">No samples yet since the server started.</p>
          ) : (
            <svg className={`wm-spark wm-e-${cls}`} viewBox="0 0 240 40" aria-label="log growth sparkline">
              <polyline points={spark}/>
            </svg>
          )}
          <p className="wm-note">
            Log {logSizeLabel(w.log_size)}
            {rate === null ? " · rate ?" : ` · +${Math.round(rate)} KB/min`}
          </p>
        </section>
      </div>

      {w.flags.length > 0 && (
        <div className="wm-flags">
          {w.flags.map((flag) => (
            <span key={flag} className="wm-pill warn">{flag}</span>
          ))}
        </div>
      )}
      {w.summary.length > 0 && (
        <section className="wm-card">
          <h3 className="wm-cap">Summary</h3>
          <ul className="wm-list">
            {w.summary.map((line, i) => (
              <li key={`${i}-${line}`}>{line}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function NoSource({ reason, last }: { reason: string; last: string | null }) {
  return (
    <div className="wm-nosource-block">
      <span className="wm-nosource">NO SOURCE</span>
      <p className="wm-note">{reason}</p>
      {last !== null && <p className="wm-mono">{last}</p>}
    </div>
  );
}

const KIND_LABEL: Record<WorkerEventKind, string> = {
  retry: "retry",
  stdin_hang: "stdin hang",
  permission_reject: "permission rejected",
  idle: "idle",
  review_findings: "review findings",
};

function StepsPanel({ worker }: { worker: Worker }) {
  const source = stepSource(worker);
  if (source.kind !== "steps") {
    return (
      <EmptyState
        title="No step events"
        hint={`${source.reason}. Steps come only from engines whose logs carry them.`}
      />
    );
  }
  const rows = newestFirst(worker.step_events);
  return (
    <section className="wm-card wm-card--flush" aria-label="Step feed">
      {rows.map((step) => {
        const outcome = stepOutcome(step.ok);
        return (
          <div key={step.seq} className="wm-step">
            <b>#{step.seq}</b>
            <span className="wm-kind">{step.kind}</span>
            <code title={step.text}>{step.text}</code>
            <span className="wm-mono">{formatStepDuration(step.duration_ms)}</span>
            <span className={`wm-outcome ${outcome.tone}`}>{outcome.label}</span>
          </div>
        );
      })}
      {worker.status === "running" && (
        <p className="wm-note wm-foot">Waiting for the next step · tokens pending until exit</p>
      )}
    </section>
  );
}

function BriefPanel({ view }: { view: WorkerView }) {
  if (view.brief_md === null) {
    return <EmptyState title="No brief" hint={`briefs/${view.worker.name}.md is absent.`}/>;
  }
  return (
    <section className="wm-card">
      <Markdown source={view.brief_md}/>
    </section>
  );
}

function ReportPanel({ view }: { view: WorkerView }) {
  return (
    <div className="wm-stack">
      <section className="wm-card">
        <h3 className="wm-cap">Report</h3>
        {view.report_md === null ? (
          <EmptyState title="REPORT.md missing" hint="The worker has not written its report yet."/>
        ) : (
          <Markdown source={view.report_md}/>
        )}
      </section>

      <section className="wm-card">
        <h3 className="wm-cap">Diff</h3>
        {view.patch === null ? (
          <EmptyState title="No patch artifact" hint="Only a .patch or .diff file in the worker directory is previewed."/>
        ) : (
          <Diff text={view.patch}/>
        )}
      </section>
    </div>
  );
}

function Diff({ text }: { text: string }) {
  const preview = patchPreview(text);
  const total = preview.lines.length + preview.omitted;
  return (
    <>
      <pre className="wm-patch">
        {preview.lines.map((line, i) => (
          <span key={i} className={`wm-diff-${line.tone}`}>{`${line.text}\n`}</span>
        ))}
      </pre>
      <p className="wm-note">
        +{preview.added} −{preview.removed} · {preview.lines.length} of {total} lines shown
        {preview.omitted > 0 ? " · capped preview" : ""}
      </p>
    </>
  );
}

function AttemptsPanel({ events }: { events: WorkerEvent[] }) {
  if (events.length === 0) {
    return (
      <EmptyState
        title="No attempts recorded"
        hint="Retries, stdin hangs and permission rejects appear here when the logs show them."
      />
    );
  }
  return (
    <section className="wm-card wm-card--flush" aria-label="Attempts">
      {events.map((event, i) => (
        <div key={`${event.kind}-${i}`} className="wm-row">
          <span className="wm-kind">{KIND_LABEL[event.kind]}</span>
          <span className="wm-mono">{event.at === null ? "time unknown" : formatTimeAgo(event.at)}</span>
          <span className="wm-row__text">{event.text}</span>
        </div>
      ))}
    </section>
  );
}

function FilesPanel({ view }: { view: WorkerView }) {
  return (
    <section className="wm-card wm-card--flush" aria-label="Files">
      {view.files.map((file) => (
        <div key={file.name} className="wm-row wm-row--files">
          <code>{file.name}</code>
          <span className="wm-kind">{file.kind}</span>
          <span className="wm-mono">{file.size === null ? "?" : formatBytes(file.size)}</span>
          <span className={`wm-outcome ${file.present ? "good" : "dim"}`}>
            {file.present ? "present" : "absent"}
          </span>
        </div>
      ))}
    </section>
  );
}
