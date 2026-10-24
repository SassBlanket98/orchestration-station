import type { SessionDetail } from "../../api/types";
import { engineColor, hasTimedEvent, timelinePositions, timelineRange, timelineTicks, type TimelineMark } from "./presentation";

type TimedEvent = { phase: string; cycle: number | null; label: string; at: number | null };

export function RunTimeline({ session }: { session: SessionDetail }) {
  const evidence = session as SessionDetail & { history?: TimedEvent[]; cycle_progress?: { events: TimedEvent[] } | null };
  const workers = session.workers;
  const runEvents = [...(evidence.history ?? []), ...(evidence.cycle_progress?.events ?? [])];
  const knownTimes = [...workers.flatMap((worker) => [worker.started_at, worker.finished_at, worker.last_activity_at, ...worker.worker_events.map((e) => e.at), ...worker.step_events.map((e) => e.at)]), ...runEvents.map((event) => event.at)].filter((at): at is number => at !== null);
  const live = session.status === "active" || workers.some((worker) => worker.status === "running");
  const range = timelineRange(knownTimes, live, Date.now() / 1000);
  const start = range?.start ?? 0;
  const end = range?.end ?? 1;
  const now = live && knownTimes.length ? Math.max(0, Math.min(100, ((Date.now() / 1000 - start) / Math.max(1, end - start)) * 100)) : null;
  const milestones = session.milestones;
  const hasTimedRunEvidence = hasTimedEvent(runEvents);
  const ticks = range ? timelineTicks(start, end) : [];
  const runMarks: TimelineMark[] = runEvents.map((event) => ({ at: event.at, kind: event.phase, label: `${event.label} · ${event.phase} · cycle ${event.cycle ?? "?"}` }));
  return <section>
    <div className="run-section-head"><div><h2>Run timeline</h2><p>Worker activity by engine · event times only where recorded</p></div><span className="run-source">{knownTimes.length ? "recorded timestamps" : "NO SOURCE"}</span></div>
    {knownTimes.length ? <div className="run-timeline panel">
      <div className="run-axis"><span>Lane / worker</span><div className="run-tick-track">{ticks.map((tick) => <i key={tick.left} style={{ left: `${tick.left}%` }}>{tick.label}</i>)}</div></div>
      {hasTimedRunEvidence && <div className="run-lane run-lead-lane">
        <div className="run-lane-name"><i />Lead / run<small>phase evidence</small></div>
        <div className="run-track" aria-label="Lead and run phase events">{timelinePositions(start, end, runMarks).map((point, index) => <span key={`run-${index}`} className="run-marker" tabIndex={0} aria-label={`${point.label}, ${new Date(point.at * 1000).toLocaleString()}`} style={{ left: `${point.left}%` }}>{point.kind.slice(0, 1).toUpperCase()}<span className="run-marker-tip">{point.label} · {new Date(point.at * 1000).toLocaleString()}</span></span>)}{now !== null && <span className="run-now-line" style={{ left: `${now}%` }}><span>now</span></span>}</div>
      </div>}
      {workers.map((worker) => {
        const marks: TimelineMark[] = [
          ...worker.worker_events.map((event) => ({ at: event.at, kind: event.kind, label: event.text })),
          ...worker.step_events.map((event) => ({ at: event.at, kind: event.kind, label: event.text })),
        ];
        const points = timelinePositions(start, end, marks);
        const left = worker.started_at === null ? null : ((worker.started_at - start) / Math.max(1, end - start)) * 100;
        const rightAt = worker.finished_at ?? (worker.status === "running" ? Date.now() / 1000 : worker.last_activity_at);
        const width = left === null || rightAt === null ? null : Math.max(1, ((rightAt - worker.started_at!) / Math.max(1, end - start)) * 100);
        return <div className="run-lane" key={worker.name}>
          <div className="run-lane-name"><span className="run-engine-chip" style={{ "--engine": engineColor(worker.engine) } as React.CSSProperties}>{worker.engine === "claude" ? "C" : worker.engine === "codex" ? "X" : worker.engine === "cursor" ? "◇" : worker.engine === "opencode" ? "O" : "?"}</span>{worker.name}<small>{worker.engine}</small></div>
          <div className="run-track" aria-label={`${worker.name} timeline`}>{left !== null && width !== null && <span className="run-bar" style={{ left: `${Math.max(0, Math.min(100, left))}%`, width: `${Math.max(0, Math.min(width, 100 - Math.max(0, left)))}%`, "--engine": engineColor(worker.engine) } as React.CSSProperties} />}{points.map((point, index) => <span className={`run-marker ${point.kind.includes("reject") || point.kind.includes("finding") ? "bad" : point.kind.includes("pick") ? "pick" : ""}`} key={`${point.kind}-${index}`} tabIndex={0} aria-label={`${worker.name}: ${point.label}, ${new Date(point.at * 1000).toLocaleString()}`} title={point.label} style={{ left: `${point.left}%` }}>{point.kind.slice(0, 1).toUpperCase()}<span className="run-marker-tip">{point.label}</span></span>)}{now !== null && <span className="run-now-line" style={{ left: `${now}%` }}>{worker === workers[0] && <span>now</span>}</span>}</div>
        </div>;
      })}
      <div className="run-legend"><span>Worker interval</span><span>Step / event marker</span><span>Recorded phase</span><span>now</span></div>
    </div> : <div className="run-empty">NO SOURCE · timestamps are unavailable, so a timeline cannot be placed.</div>}
    <div className="run-history panel"><h3>Run history</h3>{runEvents.length || milestones.length ? <>{runEvents.map((event, index) => <div className="run-history-row" key={`event-${index}`}><time>{event.at === null ? "time unavailable" : new Date(event.at * 1000).toLocaleString()}</time><span>{event.label}</span><small>{event.phase} · cycle {event.cycle ?? "?"}</small></div>)}{milestones.map((item, index) => <div className="run-history-row" key={`milestone-${index}`}><time>time unavailable</time><span>{item}</span><small>PROGRESS.md · timestamp absent</small></div>)}</> : <div className="run-no-source">NO SOURCE <span>No explicit run history was supplied.</span></div>}</div>
  </section>;
}
