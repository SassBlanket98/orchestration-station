// Log-stream reducer for useLogStream (project plan §2).
//
// The client keeps only the last MAX_LOG_LINES lines. `init` seeds the buffer,
// `append` adds lines and trims to the cap, and `reset` clears the buffer while
// the stream waits for a fresh `init`. This module is pure so the cap and reset
// behaviour are unit-testable without a live EventSource.
import type { LogChunk, LogFormat, LogLine } from "../api/types";

export const MAX_LOG_LINES = 2000;

export type LogStreamState = {
  lines: LogLine[];
  format: LogFormat;
  truncated: boolean;
};

export const emptyLogStream: LogStreamState = {
  lines: [],
  format: "missing",
  truncated: false,
};

/** Keep the newest `MAX_LOG_LINES` lines, oldest dropped first. */
export function capLines(lines: LogLine[]): LogLine[] {
  if (lines.length <= MAX_LOG_LINES) return lines;
  return lines.slice(lines.length - MAX_LOG_LINES);
}

/** `init` carries the full tail window and replaces the buffer. */
export function applyInit(_state: LogStreamState, chunk: LogChunk): LogStreamState {
  return {
    lines: capLines(chunk.lines),
    format: chunk.format,
    truncated: chunk.truncated,
  };
}

/** `append` adds complete lines, keeping the cap. */
export function applyAppend(
  state: LogStreamState,
  appended: { offset: number; lines: LogLine[] },
): LogStreamState {
  return {
    ...state,
    lines: capLines(state.lines.concat(appended.lines)),
    truncated: state.truncated || appended.lines.length > 0,
  };
}

/** `reset` clears the lines; a following `init` repopulates them. */
export function applyReset(state: LogStreamState): LogStreamState {
  return { ...state, lines: [] };
}
