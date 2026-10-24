import { describe, expect, it } from "vitest";
import type { LogChunk, LogLine } from "../../api/types";
import {
  MAX_LOG_LINES,
  applyAppend,
  applyInit,
  applyReset,
  capLines,
  emptyLogStream,
} from "../logstream";

function line(text: string): LogLine {
  return { kind: "raw", text, tool: null };
}

function lines(from: number, count: number): LogLine[] {
  return Array.from({ length: count }, (_v, i) => line(`line-${from + i}`));
}

function chunk(overrides: Partial<LogChunk>): LogChunk {
  return {
    stem: "w",
    format: "text",
    size: 0,
    offset: 0,
    truncated: false,
    lines: [],
    ...overrides,
  };
}

describe("capLines", () => {
  it("keeps the last 2000 lines", () => {
    expect(MAX_LOG_LINES).toBe(2000);
    const capped = capLines(lines(0, 2500));
    expect(capped.length).toBe(2000);
    expect(capped[0].text).toBe("line-500");
    expect(capped[1999].text).toBe("line-2499");
  });

  it("leaves shorter buffers untouched", () => {
    const input = lines(0, 3);
    expect(capLines(input)).toBe(input);
  });
});

describe("applyInit", () => {
  it("seeds the buffer and format from the first chunk", () => {
    const next = applyInit(
      emptyLogStream,
      chunk({ format: "opencode-jsonl", lines: lines(0, 5), truncated: true }),
    );
    expect(next.lines.length).toBe(5);
    expect(next.format).toBe("opencode-jsonl");
    expect(next.truncated).toBe(true);
  });

  it("replaces, not appends", () => {
    const first = applyInit(emptyLogStream, chunk({ lines: lines(0, 5) }));
    const second = applyInit(first, chunk({ lines: lines(100, 2) }));
    expect(second.lines.map((l) => l.text)).toEqual(["line-100", "line-101"]);
  });
});

describe("applyAppend", () => {
  it("appends lines and keeps the 2000-line cap", () => {
    const state = applyInit(emptyLogStream, chunk({ lines: lines(0, 1998) }));
    const next = applyAppend(state, { offset: 10, lines: lines(1998, 10) });
    expect(next.lines.length).toBe(MAX_LOG_LINES);
    expect(next.lines[0].text).toBe("line-8");
    expect(next.lines[next.lines.length - 1].text).toBe("line-2007");
  });
});

describe("applyReset", () => {
  it("clears lines and waits for a fresh init", () => {
    const state = applyInit(emptyLogStream, chunk({ lines: lines(0, 10) }));
    const cleared = applyReset(state);
    expect(cleared.lines).toEqual([]);

    const reinit = applyInit(cleared, chunk({ lines: lines(50, 3) }));
    expect(reinit.lines.map((l) => l.text)).toEqual([
      "line-50",
      "line-51",
      "line-52",
    ]);
  });
});
