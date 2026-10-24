import type { ActivitySample, QuotaUsage } from "../../api/types";
import { useEffect, useState } from "react";
import { startBadgePolling } from "../../pwa/badge";
import { disablePush, enablePush, pushState, pushSupported } from "../../pwa/push";
import type { PushState } from "../../pwa/push";
import { HEARTBEAT_CELLS, formatCount, heartbeat } from "./model";
import type { Banner } from "./monitor";
import { Clock, Flash } from "./BoardParts";
import { UsageIndicator } from "./UsageIndicator";

type MastheadProps = {
  projects: number;
  workers: number;
  working: number;
  idle: number;
  quota: QuotaUsage | null | undefined;
  quotaLoading: boolean;
  quotaOffline: boolean;
  banner: Banner | null;
  statsOpen: boolean;
  onToggleStats: () => void;
  activitySeries: ActivitySample[][];
};

// One thin line by default; the toggle opens the full stats line with Workers, the heartbeat and the clock.
export function BoardMasthead({ projects, workers, working, idle, quota, quotaLoading, quotaOffline, banner, statsOpen, onToggleStats, activitySeries }: MastheadProps) {
  useEffect(() => startBadgePolling(), []);
  return <header className="plant-masthead">
    <h1 className="plant-title">STATION DASHBOARD<span className="plant-caret" aria-hidden="true" /></h1>
    <div className="plant-status">
      <span className="plant-stat"><Flash value={projects}>{projects}</Flash> projects</span>
      <span className="plant-stat working"><Flash value={working}>{working}</Flash> working</span>
      <span className="plant-stat"><Flash value={idle}>{idle}</Flash> idle</span>
      <UsageIndicator quota={quota} loading={quotaLoading} offline={quotaOffline} />
      <PushToggle />
      <button type="button" className="plant-stats-toggle" aria-expanded={statsOpen} onClick={(event) => {
        // Focus goes back to the page so the list shortcuts keep working after the toggle.
        event.currentTarget.blur();
        onToggleStats();
      }}>{statsOpen ? "− stats" : "+ stats"}</button>
    </div>
    {banner && <div className={`plant-banner ${banner.kind}`}>{banner.text}</div>}
    {statsOpen && <div className="plant-status plant-status-full">
      <span className="plant-stat"><Flash value={workers}>{formatCount(workers)}</Flash> Workers</span>
      <Heartbeat cells={heartbeat(activitySeries)} working={working > 0} />
      <span className="plant-clock"><Clock /></span>
    </div>}
  </header>;
}

function PushToggle() {
  const [state, setState] = useState<PushState>("off");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (pushSupported()) void pushState().then(setState).catch(() => setState("off"));
  }, []);
  if (!pushSupported()) return null;
  const toggle = async () => {
    setBusy(true);
    try {
      if (state === "on") await disablePush();
      else await enablePush();
      setState(await pushState());
    } catch {
      setState(await pushState().catch((): PushState => "off"));
    } finally {
      setBusy(false);
    }
  };
  return <button type="button" className="plant-stats-toggle" disabled={busy || state === "denied"}
    title="On iPhone and iPad, install Station Dashboard to the Home Screen to use push alerts."
    aria-label={state === "on" ? "Turn alerts off" : "Turn alerts on"} onClick={() => void toggle()}>
    alerts: {state === "on" ? "on" : state === "denied" ? "denied" : "off"}
  </button>;
}

// Aggregate activity bars. Heights transition when samples change, so the strip appears to move.
function Heartbeat({ cells, working }: { cells: number[]; working: boolean }) {
  return <span className="plant-heartbeat" aria-hidden="true">
    <i className={`plant-beat-dot${working ? " on" : ""}`} />
    <span className="plant-beat">
      {Array.from({ length: HEARTBEAT_CELLS }, (_, index) => <i key={index} style={{ transform: `scaleY(${Math.max(2, Math.round((cells[index] ?? 0) * 26)) / 26})` }} />)}
    </span>
  </span>;
}

