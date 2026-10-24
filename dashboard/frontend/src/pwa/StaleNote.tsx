import { useEffect, useState, useSyncExternalStore, type ReactElement } from "react";
import { staleLabel } from "./staleness";
import { getOldestStale, subscribe } from "./staleStore";
import "./stale.css";

/** How often the age in the note is recomputed while data is stale. */
const REFRESH_MS = 30_000;

/** Shown only while some data came from the service worker cache (offline). Null when fresh. */
export function StaleNote(): ReactElement | null {
  const since = useSyncExternalStore(subscribe, getOldestStale, getOldestStale);
  const [, setTick] = useState(0);
  const isStale = since !== null;

  useEffect(() => {
    if (!isStale) return;
    const timer = setInterval(() => setTick((tick) => tick + 1), REFRESH_MS);
    return () => clearInterval(timer);
  }, [isStale]);

  if (since === null) return null;
  return <div className="stale-note" role="status">{staleLabel(since, Date.now())}</div>;
}
