import { Link, useLocation } from "react-router-dom";
import type { SessionSummary } from "../api/types";
import { useSessions } from "../state/hooks";
import { AppShell } from "../components/ui/AppShell";
import { formatAge } from "../components/board/treeText";
import { projectName } from "../components/board/RollupRows";
import "../components/run/runs-index.css";

type Bucket = "working" | "idle" | "closed";

function bucket(session: SessionSummary): Bucket {
  const lat = session.lat;
  if (lat?.section === "active" || lat?.lead_state === "live" || session.running_workers.length > 0) return "working";
  return lat?.section === "idle" ? "idle" : "closed";
}

const GLYPH: Record<Bucket, string> = { working: "●", idle: "○", closed: "·" };
const ORDER: Record<Bucket, number> = { working: 0, idle: 1, closed: 2 };

/** Every run with a stage folder, working first, then most recent. Tap one to open it. */
export default function Runs() {
  const { sessions, conn } = useSessions();
  const { search } = useLocation();
  const now = Date.now() / 1000;
  const rows = [...sessions].sort((a, b) =>
    ORDER[bucket(a)] - ORDER[bucket(b)] || (b.last_activity_at ?? 0) - (a.last_activity_at ?? 0));
  return (
    <AppShell><main className="runs-index">
      <h1 className="runs-title">Runs <span>{sessions.length}</span></h1>
      {!rows.length ? <p className="runs-empty">{conn === "live" ? "No runs yet." : "Connecting…"}</p> : (
        <ul className="runs-list">
          {rows.map((session) => {
            const state = bucket(session);
            const name = session.lat?.session_name || session.run || projectName(session.project);
            const running = session.running_workers.length;
            return (
              <li key={session.id}>
                <Link className={`runs-row st-${state}`} to={{ pathname: `/runs/${encodeURIComponent(session.id)}`, search }}>
                  <span className={`runs-glyph s-${state}`} aria-label={state}>{GLYPH[state]}</span>
                  <span className="runs-main">
                    <span className="runs-name">{name}</span>
                    <span className="runs-sub">{projectName(session.project)}{session.lat?.lead_model ? ` · ${session.lat.lead_model}` : ""}</span>
                  </span>
                  <span className="runs-side">
                    <span className="runs-workers">{running ? `${running} running` : `${session.worker_total} workers`}</span>
                    <span className="runs-age">{session.last_activity_at ? formatAge(Math.max(0, now - session.last_activity_at)) : ""}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </main></AppShell>
  );
}
