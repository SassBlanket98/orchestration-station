import type { QuotaLimit, QuotaSubscription, QuotaUsage } from "../../api/types";
import { formatDuration } from "../../lib/format";
import { StaleNote } from "../../pwa/StaleNote";

export type UsageBarsProps = {
  quota: QuotaUsage | null | undefined;
  loading: boolean;
  offline: boolean;
  /** Epoch ms used for the countdowns. Defaults to the current time. */
  now?: number;
};

export type UsageTone = "ok" | "warn" | "bad";

export type UsageWindow = {
  key: string;
  name: string;
  /** Rounded percentage used, or null when upstream does not know. */
  used: number | null;
  tone: UsageTone;
  /** Pace tick position in percent of the bar, or null when absent. */
  paceLeft: number | null;
  /** Time until the window resets, e.g. "2h 03m". Null when resets_at is unknown. */
  resetsIn: string | null;
  /** Time until the window runs out, shown only when that happens before the reset. */
  runsOut: string | null;
};

export type UsageRow = {
  key: string;
  label: string;
  stale: boolean;
  /** Highest used percentage across the windows; 0 when none is known. Drives the sort. */
  peak: number;
  windows: UsageWindow[];
};

/** Colour band for a displayed percentage: ok below 80, warn from 80, bad from 95. */
export function usageTone(used: number): UsageTone {
  if (used >= 95) return "bad";
  if (used >= 80) return "warn";
  return "ok";
}

function clampPct(value: number): number {
  return Math.min(100, Math.max(0, value));
}

/** Highest used_pct across a subscription's windows, 0 when none is known. */
export function peakUsed(subscription: QuotaSubscription): number {
  return Math.max(0, ...subscription.limits.map((limit) => limit.used_pct ?? 0));
}

// Countdown label; a time already passed reads "now" rather than "0s".
function remaining(untilMs: number, nowMs: number): string {
  const seconds = (untilMs - nowMs) / 1000;
  if (seconds <= 0) return "now";
  // Two units at most and no seconds once there are minutes: "33m", "47h 46m".
  const parts = formatDuration(seconds).split(" ");
  return (parts.length > 1 && parts[parts.length - 1].endsWith("s") ? parts.slice(0, -1) : parts).slice(0, 2).join(" ");
}

function windowRow(limit: QuotaLimit, index: number, nowMs: number): UsageWindow {
  const used = limit.used_pct === null ? null : Math.round(limit.used_pct);
  const resetsAt = limit.resets_at;
  const runsOutAt = limit.runs_out_at;
  const runsOutFirst = runsOutAt !== null && (resetsAt === null || runsOutAt < resetsAt);
  return {
    key: `${limit.window ?? limit.name ?? "window"}-${index}`,
    name: limit.name ?? limit.window ?? "—",
    used,
    tone: usageTone(limit.used_pct ?? 0),
    paceLeft: limit.pace_pct === null ? null : clampPct(limit.pace_pct),
    resetsIn: resetsAt === null ? null : remaining(resetsAt, nowMs),
    runsOut: runsOutFirst ? remaining(runsOutAt, nowMs) : null,
  };
}

/** Subscriptions sorted by highest use first (stable for ties), each with its windows in source order. */
export function buildUsageRows(subscriptions: QuotaSubscription[], nowMs: number): UsageRow[] {
  return subscriptions
    .map((subscription, index) => ({
      key: `${subscription.id ?? subscription.account ?? "subscription"}-${index}`,
      label: subscription.id || subscription.account || "subscription",
      stale: subscription.stale === true,
      peak: peakUsed(subscription),
      windows: subscription.limits.map((limit, windowIndex) => windowRow(limit, windowIndex, nowMs)),
    }))
    .sort((a, b) => b.peak - a.peak);
}

// One bar per subscription window; the block bar sits in CSS, the pace tick marks where usage should be by now.
export function UsageBars({ quota, loading, offline, now }: UsageBarsProps) {
  return <section className="plant-usage" aria-label="Quota usage">
    <UsageBody quota={quota} loading={loading} offline={offline} nowMs={now ?? Date.now()} />
    <StaleNote />
  </section>;
}

function UsageBody({ quota, loading, offline, nowMs }: Omit<UsageBarsProps, "now"> & { nowMs: number }) {
  if (loading) return <div className="usage-state">quota loading…</div>;
  const all = buildUsageRows(quota?.subscriptions ?? [], nowMs);
  if (!quota || offline || !all.length) return <div className="usage-state dim">quota offline</div>;
  // Fresh readings get bars; stale or empty ones fold into one line so the phone stays short.
  const rows = all.filter((row) => !row.stale && row.windows.length > 0);
  const folded = all.filter((row) => row.stale || row.windows.length === 0).map((row) => row.label);
  return <div className="usage-grid">
    {folded.length > 0 && <div className="usage-folded dim">{`stale: ${folded.join(" · ")}`}</div>}
    {rows.map((row) => <div key={row.key} className={`usage-sub${row.stale ? " stale" : ""}`} data-sub={row.label}>
      <div className="usage-sub-head">
        <span className="usage-sub-name">{row.label}</span>
        {row.stale && <span className="usage-stale">stale</span>}
      </div>
      {row.windows.map((win) => <div key={win.key} className={`usage-window tone-${win.tone}`} data-tone={win.tone}>
        <span className="usage-win-name">{win.name}</span>
        <span className="usage-track" role="img" aria-label={`${win.name} ${win.used === null ? "unknown" : `${win.used}%`} used`}>
          <span className="usage-fill" style={{ width: `${win.used === null ? 0 : clampPct(win.used)}%` }} />
          {win.paceLeft !== null && <i className="usage-pace" style={{ left: `${win.paceLeft}%` }} />}
        </span>
        <span className="usage-pct">{win.used === null ? "—" : `${win.used}%`}</span>
        {win.resetsIn !== null && <span className="usage-reset">↻{win.resetsIn}</span>}
        {win.runsOut !== null && <span className="usage-runs">runs out {win.runsOut}</span>}
      </div>)}
    </div>)}
  </div>;
}
