import type { QuotaLimit } from "../../api/types";

/** A percentage suitable for a circular SVG arc, clamped to its track. */
export function arcOffset(value: number | null, circumference = 264): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  return (Math.max(0, Math.min(100, value)) / 100) * circumference;
}

export function formatMetric(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : `${Math.round(value)}%`;
}

export function isOverPace(used: number | null, pace: number | null, projected: number | null): boolean {
  return (used !== null && pace !== null && used > pace) || (projected !== null && projected >= 100);
}

export type GaugeState = "unknown" | "calm" | "warning" | "danger";

export function gaugeState(used: number | null, pace: number | null, projected: number | null): GaugeState {
  if ((used !== null && used >= 85) || (projected !== null && projected >= 100)) return "danger";
  if (used !== null && pace !== null && used > pace) return "warning";
  if ((used !== null && pace !== null) || projected !== null) return "calm";
  return "unknown";
}

export function visibleScore(score: number | null, offline: boolean): number | null {
  return offline ? null : score;
}

export function selectPrimaryLimit(limits: QuotaLimit[]): QuotaLimit | undefined {
  return limits.find((limit) => limit.window === "five_hour") ?? limits[0];
}

function validDate(value: number | null, divisor: number): Date | null {
  if (value === null || !Number.isFinite(value)) return null;
  const date = new Date(value / divisor);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** quota service timestamps and generated_at are milliseconds. */
export function formatUnixMs(value: number | null): string {
  const date = validDate(value, 1);
  return date ? new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date) : "—";
}

/** Dashboard fetched_at is seconds. */
export function formatUnixSeconds(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  return date ? new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date) : "—";
}
