import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { QuotaUsage } from "../../api/types";
import { withSearch } from "../shell/navigation";
import { UsageBars, buildUsageRows } from "./UsageBars";
import type { UsageTone } from "./UsageBars";

type IndicatorProps = {
  quota: QuotaUsage | null | undefined;
  loading: boolean;
  offline: boolean;
  /** Epoch ms used for the countdowns. Defaults to the current time. */
  now?: number;
};

export type UsageSummary = { text: string; tone: UsageTone | "dim"; title: string };

/** One-chip summary: the most-used window across fresh subscriptions. */
export function summarizeUsage({ quota, loading, offline, now }: IndicatorProps): UsageSummary {
  if (loading) return { text: "usage …", tone: "dim", title: "Quota loading" };
  const rows = buildUsageRows(quota?.subscriptions ?? [], now ?? Date.now());
  if (!quota || offline || !rows.length) return { text: "usage offline", tone: "dim", title: "Quota offline" };
  let worst: { label: string; name: string; used: number; tone: UsageTone } | null = null;
  for (const row of rows) {
    if (row.stale) continue;
    for (const win of row.windows) {
      if (win.used !== null && (!worst || win.used > worst.used)) worst = { label: row.label, name: win.name, used: win.used, tone: win.tone };
    }
  }
  if (!worst) return { text: "usage —", tone: "dim", title: "No fresh quota reading" };
  return { text: `usage ${worst.used}%`, tone: worst.tone, title: `Highest: ${worst.label} ${worst.name} ${worst.used}% used` };
}

// A brief chip in the masthead; the full bars open on demand in a modal.
export function UsageIndicator(props: IndicatorProps) {
  const [open, setOpen] = useState(false);
  const summary = summarizeUsage(props);
  return <>
    <button type="button" className={`plant-stats-toggle plant-usage-chip tone-${summary.tone}`} title={summary.title}
      aria-haspopup="dialog" aria-expanded={open} onClick={(event) => { event.currentTarget.blur(); setOpen(true); }}>
      {summary.text}
    </button>
    {open && <UsageModal {...props} onClose={() => setOpen(false)} />}
  </>;
}

// Mounted only while open. Focus moves in on open and returns to the previous element on close.
function UsageModal({ quota, loading, offline, now, onClose }: IndicatorProps & { onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  // Read through a ref so a parent re-render (the page ticks every 10 s) does not re-run the focus effect.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const location = useLocation();
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    close.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); onCloseRef.current(); } };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); previous?.focus(); };
  }, []);
  return <div className="plant-help-backdrop" onMouseDown={onClose}>
    <div role="dialog" aria-modal="true" aria-label="Quota usage" className="plant-help plant-usage-modal" onMouseDown={(event) => event.stopPropagation()}>
      <h2>Usage</h2>
      <UsageBars quota={quota} loading={loading} offline={offline} now={now} />
      <div className="plant-usage-actions">
        <Link to={withSearch("/quota", location.search)} onClick={onClose}>full quota page →</Link>
        <button ref={close} type="button" className="plant-help-close" onClick={onClose}>close <kbd>esc</kbd></button>
      </div>
    </div>
  </div>;
}
