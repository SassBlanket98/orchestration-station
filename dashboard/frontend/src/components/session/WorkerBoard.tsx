import { useEffect, useRef, useState } from "react";
import type { Worker, WorkerStatus } from "../../api/types";
import { CountPill, Panel } from "../ui";
import { WorkerCard } from "./WorkerCard";

const GROUPS: Array<{ label: string; statuses: WorkerStatus[] }> = [
  { label: "running", statuses: ["running"] },
  { label: "finished", statuses: ["finished"] },
  { label: "exited / unknown", statuses: ["exited", "unknown"] },
];

/** Statuses that trigger the one-shot glow sweep when a worker reaches them. */
const SETTLED: ReadonlySet<WorkerStatus> = new Set<WorkerStatus>(["finished", "exited"]);

/**
 * Workers in three status groups. A card that changes status moves between
 * groups with a shared layout animation, and sweeps once when it settles.
 */
export function WorkerBoard({
  sessionId,
  workers,
  selected,
}: {
  sessionId: string;
  workers: Worker[];
  selected: string | null;
}) {
  // Sweeps live here, not on the card: a card remounts when it changes group.
  const previous = useRef<Map<string, WorkerStatus> | null>(null);
  const [sweeps, setSweeps] = useState<Record<string, number>>({});

  useEffect(() => {
    const before = previous.current;
    previous.current = new Map(workers.map((w) => [w.name, w.status]));
    if (before === null) return;
    const settled = workers
      .filter((w) => SETTLED.has(w.status) && before.has(w.name) && before.get(w.name) !== w.status)
      .map((w) => w.name);
    if (settled.length === 0) return;
    setSweeps((current) => {
      const next = { ...current };
      for (const name of settled) next[name] = (next[name] ?? 0) + 1;
      return next;
    });
  }, [workers]);

  return (
    <section className="worker-board" aria-label="workers">
      {GROUPS.map((group) => {
        const members = workers.filter((w) => group.statuses.includes(w.status));
        return (
          <Panel
            key={group.label}
            title={group.label}
            right={<CountPill count={members.length} />}
          >
            {members.length === 0 ? (
              <span className="faint">none</span>
            ) : (
              <div className="worker-board__grid">
                {members.map((worker) => (
                  <WorkerCard
                    key={worker.name}
                    sessionId={sessionId}
                    worker={worker}
                    selected={worker.name === selected}
                    sweep={sweeps[worker.name] ?? 0}
                  />
                ))}
              </div>
            )}
          </Panel>
        );
      })}
    </section>
  );
}
