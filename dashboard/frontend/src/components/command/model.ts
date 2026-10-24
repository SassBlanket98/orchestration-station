import type { SessionSummary, WorkerPreview } from "../../api/types";

export type CommandItem = {
  id: string;
  group: "Jump to" | "Workers";
  label: string;
  detail: string;
  status: string;
  searchText: string;
  to: string;
  engine?: string;
};

function workerStatus(worker: WorkerPreview): string {
  return worker.status;
}

export function commandItems(sessions: SessionSummary[]): CommandItem[] {
  const items: CommandItem[] = [];
  for (const session of sessions) {
    const runName = session.run ?? session.path.split("/").filter(Boolean).at(-1) ?? session.id;
    const live = session.counts.running;
    const runLabel = `${session.project} / ${runName}`;
    items.push({
      id: `run-${session.id}`,
      group: "Jump to",
      label: runLabel,
      detail: `Run · ${live} live`,
      status: session.status,
      searchText: `${session.project} ${session.run ?? ""} ${session.path} ${session.id}`,
      to: `/runs/${encodeURIComponent(session.id)}`,
    });
    for (const worker of session.worker_previews) {
      const workerLabel = `${worker.name} · ${worker.model ?? worker.engine}`;
      items.push({
        id: `worker-${session.id}-${worker.name}`,
        group: "Workers",
        label: workerLabel,
        detail: workerStatus(worker),
        status: workerStatus(worker),
        engine: worker.engine,
        searchText: `${session.project} ${session.run ?? ""} ${worker.name} ${worker.model ?? ""} ${worker.engine} ${worker.title ?? ""} ${worker.goal ?? ""}`,
        to: `/runs/${encodeURIComponent(session.id)}/workers/${encodeURIComponent(worker.name)}`,
      });
    }
  }
  return items;
}

export function matchCommandItems(items: CommandItem[], query: string): CommandItem[] {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return items;
  return items
    .map((item, order) => {
      const haystack = item.searchText.toLocaleLowerCase();
      if (!terms.every((term) => haystack.includes(term))) return null;
      const exact = haystack.startsWith(terms.join(" ")) ? 0 : 1;
      return { item, order, score: exact * 100 + terms.reduce((score, term) => score + haystack.indexOf(term), 0) };
    })
    .filter((entry): entry is { item: CommandItem; order: number; score: number } => entry !== null)
    .sort((a, b) => a.score - b.score || a.order - b.order)
    .map(({ item }) => item);
}

export function moveCommandIndex(index: number, delta: number, length: number): number {
  if (length <= 0) return -1;
  return (index + delta + length) % length;
}

export function commandOptionId(id: string): string {
  return `command-option-${encodeURIComponent(id).replace(/%/g, "_")}`;
}

/** Keep keyboard order aligned with the palette's visible group order. */
export function renderedCommandItems(items: CommandItem[]): CommandItem[] {
  return [...items.filter((item) => item.group === "Jump to"), ...items.filter((item) => item.group === "Workers")];
}

export function commandDestination(item: CommandItem, search: string): { pathname: string; search: string } {
  return { pathname: item.to, search };
}
