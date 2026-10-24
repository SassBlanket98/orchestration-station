// Evidence helpers for the worker modal: null-source labels, the file list,
// the patch preview and the log-growth line. Nothing here invents a value:
// a null input stays a "?" or a NO SOURCE label.
import type { Access, Engine, EngineFamily, LogSizeSample, Worker } from "../../api/types";
import { formatBytes } from "../../lib/format";

/** What the modal shows for one worker: the detail plus file and patch evidence. */
export type WorkerFile = {
  name: string;
  kind: "log" | "brief" | "report" | "patch" | "attempt" | "other";
  size: number | null;
  present: boolean;
};

export type WorkerView = {
  worker: Worker;
  brief_md: string | null;
  report_md: string | null;
  patch: string | null;
  files: WorkerFile[];
};

/** A field value, or "?" when it is unknown. `missing` drives the dashed styling. */
export function displayField(value: string | number | null): { text: string; missing: boolean } {
  if (value === null || value === "") return { text: "?", missing: true };
  return { text: String(value), missing: false };
}

/** Access as the chip reads it: RW, RO, or "access ?" when no source reports it. */
export function accessLabel(access: Access | null): { text: string; missing: boolean } {
  if (access === null) return { text: "access ?", missing: true };
  return { text: access.toUpperCase(), missing: false };
}

/** Token total from the worker log, or "tokens ?". Counts of 1000+ show as "148k". */
export function tokensLabel(tokens: number | null): { text: string; missing: boolean } {
  if (tokens === null || !Number.isFinite(tokens)) return { text: "tokens ?", missing: true };
  if (tokens >= 1000) return { text: `${Math.round(tokens / 1000)}k tokens`, missing: false };
  return { text: `${tokens} tokens`, missing: false };
}

const ENGINE_CHIP: Record<EngineFamily, { glyph: string; cls: string; label: string }> = {
  codex: { glyph: "CX", cls: "cx", label: "Codex" },
  claude: { glyph: "CL", cls: "cl", label: "Claude" },
  "claude-work": { glyph: "CW", cls: "cw", label: "Claude work" },
  cursor: { glyph: "CU", cls: "cu", label: "Cursor" },
  opencode: { glyph: "OC", cls: "oc", label: "OpenCode" },
  unknown: { glyph: "?", cls: "", label: "Unknown engine" },
};

/** The family to show: the parsed `engine_family`, else the engine name, else unknown. */
export function engineFamilyOf(worker: Pick<Worker, "engine_family" | "engine">): EngineFamily {
  if (worker.engine_family !== null) return worker.engine_family;
  const fromEngine: Record<Engine, EngineFamily> = {
    claude: "claude",
    codex: "codex",
    cursor: "cursor",
    opencode: "opencode",
    unknown: "unknown",
  };
  return fromEngine[worker.engine];
}

/** Glyph, colour class and label for an engine family. Colour is never the only cue. */
export function engineChip(family: EngineFamily): { glyph: string; cls: string; label: string } {
  return ENGINE_CHIP[family];
}

/** Files the modal can name from the worker detail alone (no artifact manifest yet). */
export function fileEntries(
  worker: Pick<Worker, "name" | "log_stem" | "log_size" | "has_brief" | "has_report">,
): WorkerFile[] {
  const files: WorkerFile[] = [];
  if (worker.log_stem !== null) {
    files.push({
      name: `${worker.log_stem}.log`,
      kind: "log",
      size: worker.log_size,
      present: true,
    });
  }
  files.push({
    name: `briefs/${worker.name}.md`,
    kind: "brief",
    size: null,
    present: worker.has_brief,
  });
  files.push({
    name: `${worker.name}/REPORT.md`,
    kind: "report",
    size: null,
    present: worker.has_report,
  });
  return files;
}

export type PatchLine = { text: string; tone: "add" | "del" | "hunk" | "meta" | "ctx" };

export type PatchPreview = {
  lines: PatchLine[];
  added: number;
  removed: number;
  omitted: number;
};

/** Tone for one diff line. `+++`/`---` headers are meta, not add/remove. */
export function patchLineTone(line: string): PatchLine["tone"] {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("diff ") || line.startsWith("index ")) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}

/** First `maxLines` of a patch with +/- counts over the whole patch. */
export function patchPreview(text: string, maxLines = 40): PatchPreview {
  const all = text.split("\n");
  if (all.length > 0 && all[all.length - 1] === "") all.pop();
  let added = 0;
  let removed = 0;
  const lines: PatchLine[] = [];
  for (const raw of all) {
    const tone = patchLineTone(raw);
    if (tone === "add") added += 1;
    if (tone === "del") removed += 1;
    if (lines.length < maxLines) lines.push({ text: raw, tone });
  }
  return { lines, added, removed, omitted: Math.max(0, all.length - maxLines) };
}

/** KB per minute between the first and last sample. Null with fewer than two samples, a zero span, or a shrinking log. */
export function logRateKbPerMin(series: LogSizeSample[]): number | null {
  if (series.length < 2) return null;
  const first = series[0];
  const last = series[series.length - 1];
  const minutes = (last.at - first.at) / 60;
  const growth = last.size - first.size;
  if (minutes <= 0 || growth < 0) return null;
  return growth / 1000 / minutes;
}

/** Log size label, e.g. "212 KB". Null size is "? B". */
export function logSizeLabel(size: number | null): string {
  return size === null ? "? B" : formatBytes(size);
}

/** SVG polyline points for a sparkline. Empty when there are fewer than two samples. */
export function sparkPoints(series: LogSizeSample[], width: number, height: number): string {
  if (series.length < 2) return "";
  const sizes = series.map((s) => s.size);
  const min = Math.min(...sizes);
  const max = Math.max(...sizes);
  const span = max - min;
  const step = width / (series.length - 1);
  return series
    .map((s, i) => {
      const x = i * step;
      const y = span === 0 ? height / 2 : height - ((s.size - min) / span) * height;
      return `${round1(x)},${round1(y)}`;
    })
    .join(" ");
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}
