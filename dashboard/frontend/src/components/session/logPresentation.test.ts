import { describe, expect, it } from "vitest";
import type { LogFormat } from "../../api/types";
import { lineKindLabel, logFormatLabel, presentLogLine } from "./logPresentation";

const ALL_FORMATS: LogFormat[] = [
  "opencode-jsonl",
  "codex-text",
  "claude-stream-json",
  "cursor-jsonl",
  "cursor-text",
  "text",
  "empty",
  "missing",
];

describe("logFormatLabel", () => {
  it("labels every known LogFormat", () => {
    for (const format of ALL_FORMATS) {
      const label = logFormatLabel(format);
      expect(label).not.toMatch(/^raw \(/);
      expect(label.length).toBeGreaterThan(0);
    }
  });

  it("falls back for unknown format strings", () => {
    expect(logFormatLabel("future-format")).toBe("raw (future-format)");
  });
});

describe("lineKindLabel", () => {
  it("labels parsed line kinds", () => {
    expect(lineKindLabel("text")).toBe("message");
    expect(lineKindLabel("tool")).toBe("tool");
    expect(lineKindLabel("step")).toBe("step");
    expect(lineKindLabel("raw")).toBe("raw");
  });

  it("falls back for unknown kinds", () => {
    expect(lineKindLabel("json")).toBe("raw");
  });
});

describe("presentLogLine", () => {
  it("styles text, tool, step, and raw lines", () => {
    expect(presentLogLine({ kind: "text", text: "hello", tool: null })).toMatchObject({
      kindLabel: "message",
      rowClass: "log-line log-line--text",
      showTool: false,
    });
    expect(presentLogLine({ kind: "tool", text: "cat project plan", tool: "read" })).toMatchObject({
      kindLabel: "tool",
      rowClass: "log-line log-line--tool",
      showTool: true,
    });
    expect(presentLogLine({ kind: "step", text: "step_start", tool: null })).toMatchObject({
      kindLabel: "step",
      rowClass: "log-line log-line--step",
      showTool: false,
    });
    expect(presentLogLine({ kind: "raw", text: '{"type":"unknown"}', tool: null })).toMatchObject({
      kindLabel: "raw",
      rowClass: "log-line log-line--raw",
      showTool: false,
    });
  });
});
