import { useRef, useSyncExternalStore } from "react";

// Shared timers. Each one runs only while something subscribes, so a page of closed sessions stays idle.
type Ticker<T> = { subscribe: (listener: () => void) => () => void; get: () => T };

export function makeTicker<T>(ms: number, initial: T, next: (current: T) => T): Ticker<T> {
  let value = initial;
  let timer: number | undefined;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    subscribe(listener) {
      listeners.add(listener);
      if (timer === undefined) {
        value = next(value);
        timer = window.setInterval(() => {
          value = next(value);
          listeners.forEach((notify) => notify());
        }, ms);
      }
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0 && timer !== undefined) {
          window.clearInterval(timer);
          timer = undefined;
        }
      };
    },
  };
}

export const SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
export const spinner = makeTicker(80, 0, (frame) => (frame + 1) % SPIN_FRAMES.length);
export const clockTicker = makeTicker(1000, Date.now(), () => Date.now());

const noopSubscribe = () => () => {};
export const subscribeIf = <T>(enabled: boolean, ticker: Ticker<T>) => (enabled ? ticker.subscribe : noopSubscribe);

const motionQuery = "(prefers-reduced-motion: reduce)";
const subscribeMotion = (listener: () => void) => {
  const query = window.matchMedia(motionQuery);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
};
const readMotion = () => window.matchMedia(motionQuery).matches;
export const usePrefersReducedMotion = () => useSyncExternalStore(subscribeMotion, readMotion, () => false);

// Returns a number that changes each time `value` changes, so a keyed element remounts and its flash animation replays once.
export function useChangeKey(value: unknown): number {
  const seen = useRef({ value, key: 0 });
  if (!Object.is(seen.current.value, value)) seen.current = { value, key: seen.current.key + 1 };
  return seen.current.key;
}
