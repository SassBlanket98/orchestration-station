import type { SessionSummary } from "../../api/types";

export function withSearch(path: string, search: string): string {
  return `${path}${search}`;
}

export function runsTarget(sessions: readonly SessionSummary[], mock: boolean): string | null {
  // Runs opens the list of runs; mock mode keeps jumping straight into the demo run.
  if (mock) return sessions[0] ? `/runs/${encodeURIComponent(sessions[0].id)}` : null;
  return "/runs";
}

export function isTabActive(prefix: string, pathname: string): boolean {
  if (prefix === "/") return pathname === "/";
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

export function nextRadioIndex(current: number, length: number, key: string): number | null {
  if (length < 1) return null;
  if (key === "ArrowRight" || key === "ArrowDown") return (current + 1) % length;
  if (key === "ArrowLeft" || key === "ArrowUp") return (current - 1 + length) % length;
  return null;
}
