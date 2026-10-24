import { motion, useReducedMotion } from "framer-motion";
import { Link, useLocation } from "react-router-dom";
import type { Worker } from "../../api/types";
import { Elapsed, EngineBadge, FlagChip, ModelBadge, NowLine, StatusDot, TimeAgo } from "../ui";

/**
 * One worker on the board. `sweep` counts settle events (finished or exited);
 * each bump replays the accent glow sweep once.
 */
export function WorkerCard({
  sessionId,
  worker,
  selected,
  sweep,
}: {
  sessionId: string;
  worker: Worker;
  selected: boolean;
  sweep: number;
}) {
  const reduce = useReducedMotion();
  // Keep the query string (e.g. ?mock=1) when moving to the drawer route.
  const { search } = useLocation();
  const running = worker.status === "running";
  const cls = [
    "worker-card",
    `worker-card--${worker.status}`,
    selected ? "worker-card--selected" : "",
  ]
    .filter((c) => c !== "")
    .join(" ");

  return (
    <motion.div
      className="worker-card-wrap"
      layout
      layoutId={`worker-${worker.name}`}
      transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 34 }}
    >
      <Link
        to={{ pathname: `/s/${sessionId}/w/${worker.name}`, search }}
        className={cls}
        aria-current={selected ? "true" : undefined}
      >
        {sweep > 0 && !reduce && (
          <motion.span
            key={sweep}
            className="worker-card__sweep"
            aria-hidden="true"
            initial={{ x: "-100%" }}
            animate={{ x: "100%" }}
            transition={{ duration: 0.9, ease: "easeInOut" }}
          />
        )}
        <div className="worker-card__head">
          <StatusDot status={worker.status} />
          <span className="mono worker-card__name">{worker.name}</span>
          <EngineBadge engine={worker.engine} />
          <ModelBadge model={worker.model} />
        </div>
        {worker.title !== null && <div className="worker-card__title">{worker.title}</div>}
        {worker.goal !== null && <div className="worker-card__goal muted">{worker.goal}</div>}
        <div className="worker-card__live">
          {running ? (
            <NowLine now={worker.now} />
          ) : (
            <span className="muted">{worker.summary[0] ?? "no summary yet"}</span>
          )}
        </div>
        <div className="worker-card__foot">
          {running ? (
            <Elapsed from={worker.started_at} />
          ) : (
            <span className="worker-card__done">
              {worker.status === "finished" ? "finished" : "exited"} <TimeAgo at={worker.finished_at} />
            </span>
          )}
          <span className="worker-card__flags">
            {worker.flags.map((flag) => (
              <FlagChip key={flag} flag={flag} />
            ))}
          </span>
        </div>
      </Link>
    </motion.div>
  );
}
