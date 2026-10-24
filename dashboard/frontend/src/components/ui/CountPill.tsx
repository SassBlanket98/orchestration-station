import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

export type CountTone =
  | "default"
  | "running"
  | "finished"
  | "exited"
  | "findings";

/** A count with a small digit-roll animation when the number changes. */
export function CountPill({
  count,
  label,
  tone = "default",
}: {
  count: number;
  label?: string;
  tone?: CountTone;
}) {
  const reduce = useReducedMotion();
  return (
    <span className={`count-pill count-pill--${tone}`}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={count}
          className="count-pill__count"
          initial={reduce ? false : { y: -8, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={reduce ? undefined : { y: 8, opacity: 0 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
        >
          {count}
        </motion.span>
      </AnimatePresence>
      {label !== undefined && <span>{label}</span>}
    </span>
  );
}
