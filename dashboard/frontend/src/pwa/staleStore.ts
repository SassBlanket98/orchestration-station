import { staleSince } from "./staleness";

// Module store: stale-cache timestamps per endpoint, read via useSyncExternalStore.
// Fed by api/client.ts; a fresh response (no header) clears its path.
const stale = new Map<string, number>();
const listeners = new Set<() => void>();

/** Records the stale timestamp a response carries for `path`. A fresh response clears it. */
export function reportResponse(path: string, res: Response): void {
  const since = staleSince(res);
  if (since === null) {
    if (!stale.delete(path)) return;
  } else {
    if (stale.get(path) === since) return;
    stale.set(path, since);
  }
  for (const listener of listeners) listener();
}

/** Epoch ms of the oldest cached response still in use, or null when everything is fresh. */
export function getOldestStale(): number | null {
  let oldest: number | null = null;
  for (const since of stale.values()) {
    if (oldest === null || since < oldest) oldest = since;
  }
  return oldest;
}

/** Calls `listener` whenever a path's stale state changes. Returns the unsubscribe function. */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
