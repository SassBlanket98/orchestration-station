import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { useWorkerDetail } from "../../state/hooks";
import { EmptyState, EngineBadge, Markdown, ModelBadge, StatusDot } from "../ui";
import { LogViewer } from "./LogViewer";

type Tab = "brief" | "log" | "report";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "brief", label: "Brief" },
  { id: "log", label: "Log" },
  { id: "report", label: "Report" },
];

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE));
}

/** Side panel for one worker. Modal dialog with focus trap and slide-in motion. */
export function WorkerDrawer({
  sessionId,
  workerName,
  leadSession = null,
  onClose,
}: {
  sessionId: string;
  workerName: string;
  leadSession?: string | null;
  onClose: () => void;
}) {
  const { data, loading } = useWorkerDetail(sessionId, workerName, leadSession);
  const [tab, setTab] = useState<Tab>("brief");
  const reduce = useReducedMotion();
  const layerRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const layer = layerRef.current;
    if (layer === null) return;

    const focusTarget = closeRef.current ?? getFocusable(layer)[0];
    focusTarget?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || layer === null) return;
      const items = getFocusable(layer);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const returnTarget =
        opener ??
        document.querySelector<HTMLElement>(".worker-card--selected") ??
        document.querySelector<HTMLElement>(`.worker-card[href*="/w/${CSS.escape(workerName)}"]`);
      returnTarget?.focus();
    };
  }, [onClose, workerName]);

  return (
    <motion.div
      ref={layerRef}
      className="drawer-layer"
      role="presentation"
      initial={reduce ? { opacity: 0 } : false}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={reduce ? { duration: 0 } : { duration: 0.2, ease: "easeOut" }}
    >
      <button
        type="button"
        className="drawer-backdrop"
        aria-label="Close worker drawer"
        onClick={onClose}
      />
      <motion.aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`worker ${workerName}`}
        initial={reduce ? { opacity: 0 } : { x: "100%", opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={reduce ? { opacity: 0 } : { x: "100%", opacity: 0 }}
        transition={reduce ? { duration: 0 } : { duration: 0.24, ease: "easeOut" }}
      >
        <header className="drawer__head">
          {data !== null && <StatusDot status={data.worker.status} />}
          <span className="mono drawer__name">{workerName}</span>
          {data !== null && <EngineBadge engine={data.worker.engine} />}
          {data !== null && <ModelBadge model={data.worker.model} />}
          <button
            ref={closeRef}
            type="button"
            className="drawer__close"
            onClick={onClose}
            aria-label="Close worker drawer"
          >
            ×
          </button>
        </header>

        {data === null ? (
          loading ? (
            <div className="drawer__body faint">loading…</div>
          ) : (
            <EmptyState title="Worker not found" hint={`No worker "${workerName}" in this session.`} />
          )
        ) : (
          <>
            <div className="drawer__tabs" role="tablist" aria-label="Worker detail">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="drawer__tab"
                  role="tab"
                  aria-selected={tab === t.id}
                  aria-pressed={tab === t.id}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="drawer__body">
              {tab === "brief" && <Markdown source={data.brief_md} />}
              {tab === "report" && <Markdown source={data.report_md} />}
              {tab === "log" &&
                (data.worker.log_stem === null ? (
                  <EmptyState title="No log" hint="This worker has no log file." />
                ) : (
                  <LogViewer
                    sessionId={sessionId}
                    stem={data.worker.log_stem}
                    live={data.worker.status === "running"}
                  />
                ))}
            </div>
          </>
        )}
      </motion.aside>
    </motion.div>
  );
}
