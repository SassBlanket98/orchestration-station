// Cleared alerts live in this browser only (localStorage). Storage can be missing or throw, so every access is guarded.
const KEY = "alerts.dismissed";
const MAX = 500;

export function loadDismissed(): Set<string> {
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

export function saveDismissed(keys: ReadonlySet<string>): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify([...keys].slice(-MAX)));
  } catch {
    // Storage unavailable: the clear lasts until the page reloads.
  }
}
