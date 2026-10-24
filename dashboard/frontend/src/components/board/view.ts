// Everything the overview can fold or filter. Persisted in localStorage across visits.
export const STORAGE_KEY = "board.view";

export type BoardView = {
  // Keys whose open state differs from the defaults in buildRollup.
  open: Set<string>;
  showIdle: boolean;
  showClosed: boolean;
  query: string;
  statsOpen: boolean;
};

export const DEFAULT_VIEW: BoardView = { open: new Set(), showIdle: false, showClosed: false, query: "", statsOpen: false };

export function parseView(raw: unknown): BoardView {
  if (!raw || typeof raw !== "object") return DEFAULT_VIEW;
  const saved = raw as Record<string, unknown>;
  const open = Array.isArray(saved.open) ? saved.open.filter((key): key is string => typeof key === "string") : [];
  return {
    open: new Set(open),
    showIdle: saved.showIdle === true,
    showClosed: saved.showClosed === true,
    query: "",
    statsOpen: saved.statsOpen === true,
  };
}

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

// Reads the saved view. Legacy fields are ignored; only the existing storage key and stats preference survive.
export function loadView(local: Store | null, session: Store | null): BoardView {
  try {
    const saved = local?.getItem(STORAGE_KEY) ?? null;
    if (saved !== null) return parseView(JSON.parse(saved));
    return migrateLegacy(local, session);
  } catch {
    return DEFAULT_VIEW;
  }
}

function migrateLegacy(local: Store | null, session: Store | null): BoardView {
  const legacy = session?.getItem(STORAGE_KEY) ?? null;
  if (legacy === null) return DEFAULT_VIEW;
  const view = parseView(JSON.parse(legacy));
  try {
    local?.setItem(STORAGE_KEY, JSON.stringify(serializeView(view)));
    if (local?.getItem(STORAGE_KEY) != null) session?.removeItem(STORAGE_KEY);
  } catch {
    // Storage is blocked or full; keep the legacy copy for the next visit.
  }
  return view;
}

export function serializeView(view: BoardView) {
  return { ...view, open: [...view.open].sort(), query: "" };
}

export function saveView(local: Store | null, view: BoardView): void {
  try {
    local?.setItem(STORAGE_KEY, JSON.stringify(serializeView(view)));
  } catch {
    // Storage can be blocked; the view still works for this visit.
  }
}
