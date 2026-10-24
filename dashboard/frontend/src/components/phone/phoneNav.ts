import type { SessionSummary } from "../../api/types";
import { isTabActive, runsTarget, withSearch } from "../shell/navigation";

export interface PhoneNavItem {
  key: "overview" | "runs" | "quota" | "logs";
  label: string;
  /** Path prefix that marks the item active; `/runs` covers every `/runs/*` page. */
  prefix: string;
  /** Target with the current query string kept; null when there is no run to open. */
  to: string | null;
}

/** Bottom nav items in mockup order. Runs opens the same run the header Runs tab opens. */
export function phoneNavItems(sessions: readonly SessionSummary[], mock: boolean, search: string): PhoneNavItem[] {
  const target = runsTarget(sessions, mock);
  return [
    { key: "overview", label: "Overview", prefix: "/", to: withSearch("/", search) },
    { key: "runs", label: "Runs", prefix: "/runs", to: target ? withSearch(target, search) : null },
    { key: "quota", label: "Quota", prefix: "/quota", to: withSearch("/quota", search) },
    { key: "logs", label: "Logs", prefix: "/logs", to: withSearch("/logs", search) },
  ];
}

export function isPhoneNavActive(item: PhoneNavItem, pathname: string): boolean {
  return isTabActive(item.prefix, pathname);
}
