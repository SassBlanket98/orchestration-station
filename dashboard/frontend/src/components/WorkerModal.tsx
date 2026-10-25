import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { isMockMode } from "../api/client";
import { useSession, useWorkerDetail } from "../state/hooks";
import { MOCK_WORKER_NAMES, workerMockView } from "../mocks/worker";
import { EmptyState } from "./ui/EmptyState";
import { workerBodyState, workerUnavailableHint } from "./worker/detail";
import { neighbours, orderWorkers } from "./worker/nav";
import { fileEntries, type WorkerView } from "./worker/evidence";
import { WorkerHead } from "./worker/WorkerHead";
import { WorkerTabBody } from "./worker/WorkerPanels";
import { WORKER_TAB_LABELS, WORKER_TABS, parseWorkerTab, tabSearch } from "./worker/tabs";
import "./worker/worker.css";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Worker detail as a modal over the run page. Renders nothing off `/runs/:sid/workers/:name`. */
export default function WorkerModal() {
  const { sid = "", name = "" } = useParams();
  // Bumping `attempt` remounts the dialog, which makes useWorkerDetail request again.
  const [attempt, setAttempt] = useState(0);
  if (name === "") return null;
  return (
    <WorkerDialog
      key={`${sid}/${name}#${attempt}`}
      sid={sid}
      name={name}
      onRetry={() => setAttempt((n) => n + 1)}
    />
  );
}

function WorkerDialog({
  sid,
  name,
  onRetry,
}: {
  sid: string;
  name: string;
  onRetry: () => void;
}) {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const [params] = useSearchParams();
  const tab = parseWorkerTab(params.get("tab"));
  const dialogRef = useRef<HTMLDivElement>(null);

  const mockView = isMockMode() ? workerMockView(name) : null;
  const leadSession = params.get("lead_session");
  const live = useWorkerDetail(sid, name, leadSession);
  const { session } = useSession(sid, leadSession);

  const view: WorkerView | null =
    mockView ??
    (live.data === null
      ? null
      : {
          worker: live.data.worker,
          brief_md: live.data.brief_md,
          report_md: live.data.report_md,
          patch: null,
          files: fileEntries(live.data.worker),
        });

  // The hook starts with loading false, the same as a failed request. Count a null
  // result as failed only once a request has been in flight for this dialog.
  const [requested, setRequested] = useState(false);
  useEffect(() => {
    if (live.loading) setRequested(true);
  }, [live.loading]);
  const inSession = session !== null && session.workers.some((w) => w.name === name);
  const bodyState = workerBodyState({
    hasView: view !== null,
    sessionKnown: session !== null,
    inSession,
    failed: requested && !live.loading && live.data === null,
  });

  const names =
    mockView !== null
      ? [...MOCK_WORKER_NAMES]
      : session !== null
        ? orderWorkers(session.workers)
        : [name];
  const { prev, next } = neighbours(names, name);

  const runPath = `/runs/${encodeURIComponent(sid)}`;
  const close = () => navigate({ pathname: runPath, search });
  const go = (target: string) =>
    navigate({
      pathname: `${runPath}/workers/${encodeURIComponent(target)}`,
      search,
    });

  // Move focus into the dialog on open and hand it back to the opener on close.
  useEffect(() => {
    const opener = document.activeElement;
    dialogRef.current?.focus();
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  // Escape closes; focus that leaves the dialog (e.g. a click on the overlay) comes back.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        navigate({ pathname: runPath, search });
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      const dialog = dialogRef.current;
      if (dialog === null || !(event.target instanceof Node)) return;
      if (!dialog.contains(event.target)) dialog.focus();
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
    };
  }, [navigate, runPath, search]);

  // Tab wraps between the first and last control inside the dialog.
  function trapTab(event: ReactKeyboardEvent<HTMLDivElement>) {
    const dialog = dialogRef.current;
    if (event.key !== "Tab" || dialog === null) return;
    const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const runLabel = session === null ? sid : session.run ?? session.project;

  return (
    <>
      <div className="wm-overlay" onClick={close} aria-hidden="true"/>
      <div
        ref={dialogRef}
        className="wm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="wm-title"
        tabIndex={-1}
        onKeyDown={trapTab}
      >
        <header className="wm-head">
          <div className="wm-topline">
            <span className="wm-cap">{runLabel} · worker</span>
            <div className="wm-actions">
              <button
                type="button"
                className="wm-btn"
                disabled={prev === null}
                onClick={() => prev !== null && go(prev)}
                aria-label="Previous worker"
              >
                ↑ prev
              </button>
              <button
                type="button"
                className="wm-btn"
                disabled={next === null}
                onClick={() => next !== null && go(next)}
                aria-label="Next worker"
              >
                ↓ next
              </button>
              <button type="button" className="wm-btn wm-btn--icon" onClick={close} aria-label="Close worker">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M5 5 19 19M19 5 5 19"/>
                </svg>
              </button>
            </div>
          </div>
          {view !== null && <WorkerHead view={view}/>}
        </header>

        <nav className="wm-tabs" aria-label="Worker sections">
          {WORKER_TABS.map((t) => (
            <Link
              key={t}
              to={{ pathname, search: tabSearch(search, t) }}
              aria-current={t === tab ? "page" : undefined}
            >
              {WORKER_TAB_LABELS[t]}
            </Link>
          ))}
        </nav>

        <div className="wm-body">
          {view !== null ? (
            <WorkerTabBody tab={tab} view={view}/>
          ) : bodyState === "missing" ? (
            <EmptyState title="Worker not found" hint={`No worker "${name}" in this run.`}/>
          ) : bodyState === "failed" ? (
            <div className="wm-unavailable">
              <EmptyState
                title="Worker detail could not be loaded"
                hint={workerUnavailableHint(session !== null, inSession)}
              />
              <button type="button" className="wm-btn" onClick={onRetry}>
                Retry
              </button>
            </div>
          ) : (
            <EmptyState title="Loading worker"/>
          )}
        </div>
      </div>
    </>
  );
}
