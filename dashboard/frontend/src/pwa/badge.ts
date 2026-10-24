const POLL_MS = 60_000;

export async function pollBadge(): Promise<void> {
  if (typeof navigator === "undefined") return;
  try {
    const response = await fetch("/api/push/badge");
    if (!response.ok) return;
    const { waiting } = await response.json() as { waiting: number };
    if (waiting > 0) {
      await navigator.setAppBadge?.(waiting);
    } else {
      await navigator.clearAppBadge?.();
    }
  } catch {
    // Badge state is best-effort; a transient network failure should not affect the dashboard.
  }
}

export function startBadgePolling(): () => void {
  void pollBadge();
  const timer = window.setInterval(() => void pollBadge(), POLL_MS);
  return () => window.clearInterval(timer);
}
