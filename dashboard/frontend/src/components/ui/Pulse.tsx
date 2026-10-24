import { motion, useReducedMotion } from "framer-motion";

/** A small looping ring pulse. Turns static under `prefers-reduced-motion`. */
export function Pulse({
  color = "var(--accent-run)",
  active = true,
  title,
}: {
  color?: string;
  active?: boolean;
  title?: string;
}) {
  const reduce = useReducedMotion();
  return (
    <span className="pulse" style={{ color }} title={title} aria-hidden="true">
      <span className="pulse__core" />
      {active && !reduce && (
        <motion.span
          className="pulse__ring"
          initial={{ scale: 1, opacity: 0.7 }}
          animate={{ scale: 2.4, opacity: 0 }}
          transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }}
        />
      )}
    </span>
  );
}
