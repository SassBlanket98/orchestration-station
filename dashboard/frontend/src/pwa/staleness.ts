/** Set by the service worker on data served from its cache. Value: epoch ms it was cached. */
export const STALE_HEADER = "X-Board-Stale";

/** Epoch ms when the response body was cached, or null when the response is fresh. */
export function staleSince(res: Response): number | null {
  const raw = res.headers.get(STALE_HEADER);
  if (raw === null) return null;
  const text = raw.trim();
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  return Number.isSafeInteger(value) ? value : null;
}

/** e.g. "offline · data from 4m ago". A future timestamp (clock skew) reads as "0s ago". */
export function staleLabel(sinceMs: number, nowMs: number): string {
  const seconds = Math.max(0, Math.floor((nowMs - sinceMs) / 1000));
  return `offline · data from ${ago(seconds)}`;
}

function ago(seconds: number): string {
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
