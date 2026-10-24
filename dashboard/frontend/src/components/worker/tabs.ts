// Worker modal tabs. The active tab lives in the `tab` search param so refresh
// and back keep it; Overview is the default and leaves the param off the URL.

export const WORKER_TABS = [
  "overview",
  "steps",
  "brief",
  "report",
  "attempts",
  "files",
] as const;

export type WorkerTab = (typeof WORKER_TABS)[number];

export const WORKER_TAB_LABELS: Record<WorkerTab, string> = {
  overview: "Overview",
  steps: "Step feed",
  brief: "Brief",
  report: "Report/Diff",
  attempts: "Attempts",
  files: "Files",
};

/** Unknown or missing values fall back to Overview. */
export function parseWorkerTab(raw: string | null | undefined): WorkerTab {
  return (WORKER_TABS as readonly string[]).includes(raw ?? "")
    ? (raw as WorkerTab)
    : "overview";
}

/** The search string for `tab`, keeping every other param. Empty for Overview. */
export function tabSearch(search: string, tab: WorkerTab): string {
  const params = new URLSearchParams(search);
  if (tab === "overview") {
    params.delete("tab");
  } else {
    params.set("tab", tab);
  }
  const text = params.toString();
  return text === "" ? "" : `?${text}`;
}
