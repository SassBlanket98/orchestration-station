import type { SessionDetail } from "../../api/types";
import { CountPill, TimeAgo } from "../ui";

/** Session title, stage path, status counts and last activity. */
export function SessionHeader({ session }: { session: SessionDetail }) {
  return (
    <header className="session-header">
      <div className="session-header__title">
        <h1 className="session-header__heading">
          <span className="session-header__project">{session.project}</span>
          {session.run !== null && (
            <>
              <span className="session-header__sep" aria-hidden="true"> / </span>
              <span className="session-header__run">{session.run}</span>
            </>
          )}
          {session.run !== null && <span className="run-label">Run</span>}
        </h1>
        <span className="session-header__activity">
          <span className="faint">last activity </span>
          <TimeAgo at={session.last_activity_at} />
        </span>
      </div>
      <div className="mono muted session-header__path" title={session.path}>
        {session.path}
      </div>
      <div className="row">
        <CountPill tone="running" count={session.counts.running} label="running" />
        <CountPill tone="finished" count={session.counts.finished} label="finished" />
        <CountPill tone="exited" count={session.counts.exited} label="exited" />
        {session.counts.unknown > 0 && (
          <CountPill count={session.counts.unknown} label="unknown" />
        )}
        <CountPill tone="findings" count={session.findings_total} label="findings" />
      </div>
    </header>
  );
}
