import { useMemo } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { AppShell, EmptyState, Panel } from "../components/ui";
import { useSession } from "../state/hooks";
import { isMockMode } from "../api/client";
import { RUN_ILLUSTRATIONS } from "../mocks/run";
import { RunTimeline } from "../components/run/RunTimeline";
import { RunReviews } from "../components/run/RunReviews";
import { RUN_STATUSES, RUN_TABS, elapsedSeconds, engineColor, filterWorkers, sourceValue, statusFromSearch, tabFromPath } from "../components/run/presentation";
import "../components/run/run.css";

type RunEvidence = {
  lead?: { label?: string | null; model?: string | null; confidence?: string; source_path?: string | null; source_line?: number | null; source?: string; family?: string } | null;
  cycle_progress?: { completed?: number | null; current?: number | null; phase: string; events?: { phase: string; cycle: number | null; label: string; at: number | null }[] } | null;
  stream?: string | null;
};

function age(at: number | null): string {
  if (at === null) return "?";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - at));
  return seconds < 60 ? `${seconds}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}m` : `${Math.floor(seconds / 3600)}h`;
}

export default function Run() {
  const { sid = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { session, conn, notFound } = useSession(sid);
  const tab = tabFromPath(location.pathname);
  const status = statusFromSearch(location.search);
  const mock = isMockMode();
  const workers = session?.workers ?? [];
  const shownWorkers = useMemo(() => filterWorkers(workers, status), [workers, status]);
  const basePath = `/runs/${encodeURIComponent(sid)}`;
  const query = location.search;

  if (notFound) {
    return <AppShell conn={conn}><div className="run-page"><EmptyState title="Run not found" hint={`No stage data for “${sid}”.`} /></div></AppShell>;
  }
  if (session === null) return <AppShell conn={conn}><div className="run-page"><div className="run-loading" aria-label="Loading run"><i /><i /><i /></div></div></AppShell>;
  const running = session.counts.running;
  const stepCount = workers.reduce((sum, worker) => sum + worker.step_events.length, 0);
  const evidence = session as typeof session & RunEvidence;
  const lead: RunEvidence["lead"] = mock ? RUN_ILLUSTRATIONS.lead : evidence.lead;
  const cycle: RunEvidence["cycle_progress"] = mock ? RUN_ILLUSTRATIONS.cycle : evidence.cycle_progress;
  const leadLabel = lead?.label ?? lead?.model ?? "?";
  const leadSource = lead?.confidence ? `${lead.confidence} · ${lead.source_path ?? "?"}${lead.source_line == null ? "" : `:${lead.source_line}`}` : lead?.source ?? "NO SOURCE";
  const cycleLabel = cycle ? `${cycle.current ?? "?"}/3 · ${cycle.phase}` : "? / ?";
  const setStatus = (value: typeof status) => {
    const params = new URLSearchParams(location.search);
    if (value === "all") params.delete("status"); else params.set("status", value);
    navigate(`${location.pathname}${params.size ? `?${params}` : ""}`, { replace: true });
  };
  const evidencedPhases = ["plan", "build", "review"].map((phase) => ({ phase, known: !!cycle?.events?.some((event) => event.phase === phase) || cycle?.phase === phase }));

  return <AppShell conn={conn}>
    <div className="run-page">
      <header className="run-head">
        <div className="run-title-row"><h1>{session.run ?? session.project}</h1><span className={`run-pill ${session.status}`}>{session.status}</span>{session.findings_total > 0 && <span className="run-pill finding">{session.findings_total} findings</span>}</div>
        <div className="run-path">{session.path}</div>
        <div className="run-meta">
          <span>Lead <b>{leadLabel}</b> <span className="run-source">{leadSource}</span></span>
          <span>Stream <b>{sourceValue(evidence.stream)}</b></span>
          <span>Last activity <b>{age(session.last_activity_at)} ago</b></span>
          <span className="run-cycle">Cycle {cycleLabel}</span>
        </div>
        <div className="run-cycle-steps" aria-label="Cycle phase evidence">{evidencedPhases.map(({ phase, known }) => <span className={known ? "known" : "unknown"} key={phase}>{phase}<small>{known ? "recorded" : "unknown"}</small></span>)}</div>
        <nav className="run-tabs" aria-label="Run sections">
          {RUN_TABS.map((item) => <Link key={item.id} aria-current={tab === item.id ? "page" : undefined} className={tab === item.id ? "active" : ""} to={{ pathname: `${basePath}${item.suffix}`, search: query }}>{item.label}</Link>)}
        </nav>
      </header>

      {tab === "summary" && <>
        <div className="run-stat-grid">
          <div><span>Workers</span><b>{workers.length}</b><small>{running} running · {session.counts.finished} finished</small></div>
          <div><span>Structured steps</span><b>{stepCount}</b><small>{stepCount ? "from supported log parsers" : "NO SOURCE"}</small></div>
          <div><span>Reviews</span><b>{session.reviews.length}</b><small>{session.findings_total} findings</small></div>
          <div><span>Revision</span><b>{session.rev}</b><small>snapshot</small></div>
        </div>
        <div className="run-summary-grid">
          <Panel title="Lead and run evidence"><div className="run-lead"><span className="run-lead-mark">{lead ? "LEAD" : "?"}</span><div><h2>{leadLabel === "?" ? "Lead identity unavailable" : leadLabel}</h2><p>{lead?.model ?? lead?.family ?? "NO SOURCE · model unavailable"}</p><span className="run-source">{leadSource}</span></div></div><dl className="run-kv"><dt>Project</dt><dd>{session.project}</dd><dt>Path</dt><dd>{session.path}</dd><dt>Latest milestone</dt><dd>{session.latest_milestone ?? "?"}</dd></dl></Panel>
          <Panel title="Stream highlights"><div className="run-list">{session.milestones.length ? session.milestones.map((item, index) => <div className="run-list-row" key={`${item}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><p>{item}</p><small>time unavailable</small></div>) : <div className="run-no-source">NO SOURCE <span>No explicit milestones were supplied.</span></div>}</div></Panel>
        </div>
        <Panel title="Worker snapshot" right={<Link className="run-inline-link" to={{ pathname: `${basePath}/workers`, search: query }}>View all workers →</Link>}><div className="run-worker-strip">{workers.slice(0, 4).map((worker) => <Link key={worker.name} to={{ pathname: `${basePath}/workers/${encodeURIComponent(worker.name)}`, search: query }} className="run-worker-mini" style={{ "--engine": engineColor(worker.engine) } as React.CSSProperties}><b>{worker.name}</b><span>{worker.model ?? "?"}</span><i>{worker.status}</i></Link>)}{workers.length === 0 && <span className="run-empty">No workers recorded for this run.</span>}</div></Panel>
        <Panel title="Recent picks"><div className="run-list">{session.pick_records.length ? session.pick_records.map((pick, index) => <div className="run-list-row" key={`${pick.at}-${index}`}><span>{pick.at ?? "?"}</span><p>{pick.job ?? "?"} · {pick.assigned.join(", ") || "?"}</p><small>{pick.tier ?? "NO SOURCE"}</small></div>) : <div className="run-no-source">NO SOURCE <span>No pick records were supplied for this run.</span></div>}</div></Panel>
      </>}

      {tab === "timeline" && <RunTimeline session={session} />}
      {tab === "workers" && <section>
        <div className="run-section-head"><div><h2>Workers</h2><p>Run workers · select a status to filter</p></div><span className="run-muted">{workers.length} total</span></div>
        <div className="run-filters">{RUN_STATUSES.map((item) => <button key={item} aria-pressed={status === item} className={status === item ? "selected" : ""} onClick={() => setStatus(item)}>{item[0].toUpperCase() + item.slice(1)} <b>{item === "all" ? workers.length : workers.filter((w) => w.status === item).length}</b></button>)}</div>
        <div className="run-workers-grid">{shownWorkers.map((worker) => <Link key={worker.name} to={{ pathname: `${basePath}/workers/${encodeURIComponent(worker.name)}`, search: query }} className="run-worker-card" style={{ "--engine": engineColor(worker.engine) } as React.CSSProperties}>
          <div className="run-worker-title"><b>{worker.name}</b><span className={`run-pill ${worker.status}`}>{worker.status}</span></div><p>{worker.title ?? "?"}</p>
          <div className="run-chips"><span>{worker.model ?? "?"}</span><span>{worker.effort ?? "effort ?"}</span><span>{worker.engine_family ?? worker.engine}</span><span>{worker.access?.toUpperCase() ?? "access ?"}</span><span>{worker.job ?? "job ?"}</span></div>
          <div className="run-worker-facts"><span>PID <b>{worker.pid ?? "?"}</b></span><span>ELAPSED <b>{elapsedSeconds(worker.started_at, worker.finished_at, Date.now() / 1000, worker.status) === null ? "?" : `${elapsedSeconds(worker.started_at, worker.finished_at, Date.now() / 1000, worker.status)}s`}</b></span><span>LAST OUTPUT <b>{age(worker.last_activity_at)} ago</b></span></div>
          <p className="run-last-line">{worker.step_events.at(-1)?.text ?? worker.now?.text ?? "NO SOURCE · no structured step available"}</p>
          <div className="run-evidence">{worker.has_brief && <span>brief</span>}{worker.log_stem && <span>log</span>}{worker.has_report && <span>report</span>}{worker.flags.map((flag) => <span key={flag}>{flag}</span>)}</div>
        </Link>)}</div>
        {shownWorkers.length === 0 && <div className="run-empty">No workers match this status.</div>}
      </section>}
      {tab === "reviews" && <RunReviews reviews={session.reviews} />}
    </div>
  </AppShell>;
}
