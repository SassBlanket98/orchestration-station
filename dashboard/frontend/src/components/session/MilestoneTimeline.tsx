import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Panel } from "../ui";

const HIGHLIGHT_MS = 2400;

/** Milestones, newest first. A milestone that appears later slides in and is briefly highlighted. */
export function MilestoneTimeline({ milestones }: { milestones: string[] }) {
  const reduce = useReducedMotion();
  const known = useRef<Set<string> | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());

  useEffect(() => () => window.clearTimeout(timer.current), []);

  useEffect(() => {
    const seen = known.current;
    known.current = new Set(milestones);
    if (seen === null) return;
    const added = milestones.filter((text) => !seen.has(text));
    if (added.length === 0) return;
    setFresh(new Set(added));
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setFresh(new Set()), HIGHLIGHT_MS);
  }, [milestones]);

  return (
    <Panel title="milestones">
      {milestones.length === 0 ? (
        <span className="faint">no milestones yet</span>
      ) : (
        <ol className="timeline">
          {/* initial={false}: milestones present on first render do not animate. */}
          <AnimatePresence initial={false}>
            {milestones.map((text) => (
              <motion.li
                key={text}
                className={fresh.has(text) ? "timeline__item timeline__item--fresh" : "timeline__item"}
                initial={reduce ? { opacity: 0 } : { opacity: 0, y: -14 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.3, ease: "easeOut" }}
              >
                <span className="timeline__dot" aria-hidden="true" />
                <span>{text}</span>
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
      )}
    </Panel>
  );
}
