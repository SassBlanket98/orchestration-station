import { useCallback, useState } from "react";
import { AnimatePresence } from "framer-motion";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  AppShell,
  CrumbSep,
  EmptyState,
  Panel,
} from "../components/ui";
import { ExtraLogsPanel } from "../components/session/ExtraLogsPanel";
import { LogViewer } from "../components/session/LogViewer";
import { MilestoneTimeline } from "../components/session/MilestoneTimeline";
import { ReviewsPanel } from "../components/session/ReviewsPanel";
import { SessionHeader } from "../components/session/SessionHeader";
import { WorkerBoard } from "../components/session/WorkerBoard";
import { WorkerDrawer } from "../components/session/WorkerDrawer";
import "../components/session/session.css";
import { useSession } from "../state/hooks";

/** Session page. `/s/:sessionId/w/:worker` is the same page with the worker drawer open. */
export default function Session() {
  const { sessionId = "", worker } = useParams();
  const navigate = useNavigate();
  const { search } = useLocation();
  const { session, conn, notFound } = useSession(sessionId, new URLSearchParams(search).get("lead_session"));
  const [openLog, setOpenLog] = useState<string | null>(null);

  const closeDrawer = useCallback(() => {
    navigate({ pathname: `/s/${encodeURIComponent(sessionId)}`, search });
  }, [navigate, search, sessionId]);

  if (notFound || session === null) {
    const breadcrumb = (
      <>
        <Link to={{ pathname: "/", search }}>overview</Link>
        <CrumbSep />
        <span>{sessionId}</span>
      </>
    );
    return (
      <AppShell conn={conn} breadcrumb={breadcrumb}>
        <EmptyState
          title="Session not found"
          hint={`No stage directory for "${sessionId}".`}
        />
      </AppShell>
    );
  }

  const breadcrumb = (
    <>
      <Link to={{ pathname: "/", search }}>overview</Link>
      <CrumbSep />
      <span>
        {session.run !== null ? `${session.project} / ${session.run}` : session.project}
      </span>
    </>
  );

  return (
    <AppShell conn={conn} breadcrumb={breadcrumb}>
      <div className="page">
        <SessionHeader session={session} />
        <WorkerBoard
          sessionId={session.id}
          workers={session.workers}
          selected={worker ?? null}
        />
        <div className="session-columns">
          <MilestoneTimeline milestones={session.milestones} />
          <ReviewsPanel reviews={session.reviews} />
        </div>
        <ExtraLogsPanel
          logs={session.extra_logs}
          openStem={openLog}
          onOpen={setOpenLog}
        />
        {openLog !== null && (
          <Panel
            title={`log: ${openLog}`}
            right={
              <button type="button" className="extra-logs__toggle" onClick={() => setOpenLog(null)}>
                close
              </button>
            }
          >
            <LogViewer sessionId={session.id} stem={openLog} live={false} />
          </Panel>
        )}
      </div>

      <AnimatePresence>
        {worker !== undefined && (
          <WorkerDrawer
            key={`${session.id}/${worker}`}
            sessionId={session.id}
            workerName={worker}
            onClose={closeDrawer}
          />
        )}
      </AnimatePresence>
    </AppShell>
  );
}
