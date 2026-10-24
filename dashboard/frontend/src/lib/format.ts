// Small pure formatting helpers shared by the UI. Kept dependency-free so they
// are trivial to unit test.

const DASH = "\u2014"; // em dash, used for "unknown"

function clampInt(n: number): number {
  return Math.max(0, Math.floor(n));
}

/** Seconds between `at` and `now` (epoch seconds), never negative. */
export function secondsSince(at: number, now: number): number {
  return clampInt(now - at);
}

/** Compact "ago" label, e.g. "3s", "5m", "2h", "4d". `null` becomes a dash. */
export function formatTimeAgo(at: number | null, now = Date.now() / 1000): string {
  if (at === null) return DASH;
  const s = secondsSince(at, now);
  if (s < 1) return "now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

/** Compact duration from one timestamp to another (defaults to now). */
export function formatElapsed(
  from: number | null,
  to: number | null = null,
  now = Date.now() / 1000,
): string {
  if (from === null) return DASH;
  return formatDuration((to ?? now) - from);
}

/** A raw number of seconds as "12s", "3m 04s", "1h 02m", "2d 03h". */
export function formatDuration(totalSeconds: number): string {
  const s = clampInt(totalSeconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${String(rs).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  if (h < 48) return `${h}h ${String(rm).padStart(2, "0")}m`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return `${d}d ${String(rh).padStart(2, "0")}h`;
}

/** Byte size as "0 B", "1.2 KB", "534 KB", "2.5 MB". */
export function formatBytes(bytes: number): string {
  const n = clampInt(bytes);
  if (n < 1000) return `${n} B`;
  const kb = n / 1000;
  if (kb < 1000) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  const mb = n / 1_000_000;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

/** One shared label for a worker/session status. */
export function statusLabel(status: string): string {
  switch (status) {
    case "running":
      return "running";
    case "finished":
      return "finished";
    case "exited":
      return "exited";
    case "unknown":
      return "unknown";
    default:
      return status;
  }
}
