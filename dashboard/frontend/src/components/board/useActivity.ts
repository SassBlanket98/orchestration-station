import { useEffect, useState } from "react";
import type { Activity } from "./RollupRows";

const POLL_MS = 5_000;

/** Latest action per live stage path from /api/activity. Empty until the backend answers. */
export function useActivity(enabled: boolean): Record<string, Activity> {
  const [items, setItems] = useState<Record<string, Activity>>({});
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let ctrl: AbortController | null = null;
    const load = async () => {
      ctrl?.abort();
      ctrl = new AbortController();
      try {
        const res = await fetch("/api/activity", { signal: ctrl.signal, credentials: "same-origin" });
        if (!res.ok) return;
        const body = (await res.json()) as { items?: Record<string, Activity> };
        if (!stopped && body.items && typeof body.items === "object") setItems(body.items);
      } catch {
        // Offline or not deployed yet: keep the last answer.
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => { stopped = true; ctrl?.abort(); window.clearInterval(timer); };
  }, [enabled]);
  return items;
}
