import type { LogFormat, LogLine } from "../../api/types";

const FORMAT_LABELS: Record<LogFormat, string> = {
  "opencode-jsonl": "OpenCode events",
  "codex-text": "Codex transcript",
  "claude-stream-json": "Claude stream",
  "cursor-jsonl": "Cursor events",
  "cursor-text": "Cursor transcript",
  text: "Plain text",
  empty: "Empty log",
  missing: "No log file",
};

const KIND_LABELS: Record<LogLine["kind"], string> = {
  text: "message",
  tool: "tool",
  step: "step",
  raw: "raw",
};

export type LogLinePresentation = {
  dataKind: LogLine["kind"];
  kindLabel: string;
  rowClass: string;
  showTool: boolean;
};

/** Human-readable label for a log file format. Unknown values fall back to a raw label. */
export function logFormatLabel(format: LogFormat | string): string {
  if (format in FORMAT_LABELS) return FORMAT_LABELS[format as LogFormat];
  return `raw (${format})`;
}

/** Short column label for a parsed log line kind. */
export function lineKindLabel(kind: LogLine["kind"] | string): string {
  if (kind in KIND_LABELS) return KIND_LABELS[kind as LogLine["kind"]];
  return "raw";
}

/** Presentation for one log line row (labels and styling hooks). */
export function presentLogLine(line: LogLine): LogLinePresentation {
  const kind = line.kind in KIND_LABELS ? line.kind : "raw";
  const kindLabel = lineKindLabel(kind);
  const rowClass = `log-line log-line--${kind}`;
  const showTool = kind === "tool" && line.tool !== null && line.tool !== "";
  return { dataKind: kind, kindLabel, rowClass, showTool };
}
