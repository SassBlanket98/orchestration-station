import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { NowLine as NowLineData } from "../../api/types";

/** The worker's current line. Keyed on text so a change slides and fades. */
export function NowLine({ now }: { now: NowLineData | null }) {
  const reduce = useReducedMotion();

  if (now === null) {
    return <div className="now-line now-line--empty">no activity yet</div>;
  }

  return (
    <div className="now-line" title={now.text}>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={now.text}
          style={{ display: "inline-block", maxWidth: "100%" }}
          initial={reduce ? { opacity: 0 } : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, y: -8 }}
          transition={{ duration: 0.25, ease: "easeOut" }}
        >
          {now.tool !== null && <span className="now-line__tool">{now.tool}</span>}
          {now.text}
        </motion.span>
      </AnimatePresence>
    </div>
  );
}
