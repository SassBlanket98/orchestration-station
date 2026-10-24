// Previous and next worker inside one run, in the same order the run page shows.
import type { Worker } from "../../api/types";

type Ordered = Pick<Worker, "name" | "status" | "started_at" | "finished_at">;

/** Running first (newest start first), then the rest (newest finish first); nulls last. */
export function orderWorkers(workers: Ordered[]): string[] {
  const desc = (a: number | null, b: number | null) =>
    (b ?? -Infinity) - (a ?? -Infinity);
  return [...workers]
    .sort((a, b) => {
      const ar = a.status === "running";
      const br = b.status === "running";
      if (ar !== br) return ar ? -1 : 1;
      if (ar && br) return desc(a.started_at, b.started_at);
      return desc(a.finished_at, b.finished_at);
    })
    .map((w) => w.name);
}

/** Neighbours of `current` in `names`. Ends do not wrap; an unknown name has none. */
export function neighbours(
  names: string[],
  current: string,
): { prev: string | null; next: string | null } {
  const index = names.indexOf(current);
  if (index === -1) return { prev: null, next: null };
  return {
    prev: index > 0 ? names[index - 1] : null,
    next: index < names.length - 1 ? names[index + 1] : null,
  };
}
