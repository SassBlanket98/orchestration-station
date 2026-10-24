import { useSyncExternalStore, type ReactNode } from "react";
import type { TreeWorkerState } from "./state";
import { formatElapsed, workerMark } from "./model";
import { SPIN_FRAMES, clockTicker, spinner, subscribeIf, useChangeKey, usePrefersReducedMotion } from "./ticks";

// Re-mounts its child when `value` changes, replaying the one-shot flash animation.
export function Flash({ value, className, children }: { value: unknown; className?: string; children: ReactNode }) {
  const key = useChangeKey(value);
  return <span key={key} className={key ? `plant-flash${className ? ` ${className}` : ""}` : className}>{children}</span>;
}

export function Clock() {
  const now = useSyncExternalStore(clockTicker.subscribe, clockTicker.get);
  return <time dateTime={new Date(now).toISOString()}>{new Date(now).toLocaleTimeString("en-GB", { hour12: false })}</time>;
}

// Elapsed time for a worker; live workers tick once a second, finished ones are fixed.
export function Elapsed({ start, end, live }: { start: number | null; end: number | null; live: boolean }) {
  const now = useSyncExternalStore(subscribeIf(live && end == null, clockTicker), clockTicker.get);
  return <span className="plant-elapsed">{formatElapsed(start, end ?? now / 1000)}</span>;
}

const STATIC_MARKS = { lag: "◌", fail: "✗", done: "✓", none: "·" } as const;

// Spinner while a worker runs, otherwise a fixed glyph. `spin` is false for rows whose session is collapsed.
export function Mark({ state, spin }: { state: TreeWorkerState; spin: boolean }) {
  const reduced = usePrefersReducedMotion();
  const frame = useSyncExternalStore(subscribeIf(spin && !reduced, spinner), spinner.get);
  const mark = workerMark(state);
  if (mark === "spin") return <span className="plant-mark spin" aria-hidden="true">{SPIN_FRAMES[reduced ? 0 : frame]}</span>;
  return <span className={`plant-mark ${mark}`} aria-hidden="true">{STATIC_MARKS[mark]}</span>;
}
