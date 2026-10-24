import { describe, expect, it } from "vitest";
import {
  accessLabel,
  displayField,
  engineChip,
  engineFamilyOf,
  fileEntries,
  logRateKbPerMin,
  logSizeLabel,
  patchLineTone,
  patchPreview,
  sparkPoints,
  tokensLabel,
} from "./evidence";

describe("null-source helpers", () => {
  it("shows a question mark for unknown fields, never a guess", () => {
    expect(displayField(null)).toEqual({ text: "?", missing: true });
    expect(displayField("")).toEqual({ text: "?", missing: true });
    expect(displayField("medium")).toEqual({ text: "medium", missing: false });
    expect(displayField(0)).toEqual({ text: "0", missing: false });
  });
  it("labels access as RW or RO, and marks unknown access", () => {
    expect(accessLabel("rw")).toEqual({ text: "RW", missing: false });
    expect(accessLabel("ro")).toEqual({ text: "RO", missing: false });
    expect(accessLabel(null)).toEqual({ text: "access ?", missing: true });
  });
  it("shows token totals in thousands and marks unknown totals", () => {
    expect(tokensLabel(null)).toEqual({ text: "tokens ?", missing: true });
    expect(tokensLabel(148_200)).toEqual({ text: "148k tokens", missing: false });
    expect(tokensLabel(42)).toEqual({ text: "42 tokens", missing: false });
    expect(tokensLabel(Number.NaN)).toEqual({ text: "tokens ?", missing: true });
  });
  it("uses the parsed engine family, then the engine name, then unknown", () => {
    expect(engineFamilyOf({ engine_family: "claude-work", engine: "claude" })).toBe(
      "claude-work",
    );
    expect(engineFamilyOf({ engine_family: null, engine: "codex" })).toBe("codex");
    expect(engineFamilyOf({ engine_family: null, engine: "unknown" })).toBe("unknown");
    expect(engineChip("unknown").glyph).toBe("?");
    expect(engineChip("opencode")).toEqual({ glyph: "OC", cls: "oc", label: "OpenCode" });
  });
  it("lists the brief, report and log, with absent files marked absent", () => {
    const files = fileEntries({
      name: "fix1",
      log_stem: "fix1",
      log_size: 212_000,
      has_brief: true,
      has_report: false,
    });
    expect(files.map((f) => [f.kind, f.present])).toEqual([
      ["log", true],
      ["brief", true],
      ["report", false],
    ]);
    expect(files[0]).toEqual({ name: "fix1.log", kind: "log", size: 212_000, present: true });
  });
  it("omits the log row when no log stem exists", () => {
    const files = fileEntries({
      name: "w",
      log_stem: null,
      log_size: 0,
      has_brief: false,
      has_report: false,
    });
    expect(files.map((f) => f.kind)).toEqual(["brief", "report"]);
  });
  it("labels log sizes and marks a missing size", () => {
    expect(logSizeLabel(null)).toBe("? B");
    expect(logSizeLabel(0)).toBe("0 B");
  });
});

describe("patchLineTone", () => {
  it("separates headers from additions and removals", () => {
    expect(patchLineTone("@@ -208,9 +208,12 @@ scanProcs")).toBe("hunk");
    expect(patchLineTone("--- a/scan.ts")).toBe("meta");
    expect(patchLineTone("+++ b/scan.ts")).toBe("meta");
    expect(patchLineTone("+ seen.add(id);")).toBe("add");
    expect(patchLineTone("- total += p;")).toBe("del");
    expect(patchLineTone(" unchanged")).toBe("ctx");
  });
});

describe("patchPreview", () => {
  it("counts additions and removals over the whole patch", () => {
    const text = ["@@ -1 +1 @@", "-old", "+new", "+more", ""].join("\n");
    const preview = patchPreview(text);
    expect(preview.added).toBe(2);
    expect(preview.removed).toBe(1);
    expect(preview.omitted).toBe(0);
    expect(preview.lines).toHaveLength(4);
  });
  it("caps the lines shown and reports how many were left out", () => {
    const text = Array.from({ length: 10 }, (_, i) => `+line ${i}`).join("\n");
    const preview = patchPreview(text, 4);
    expect(preview.lines).toHaveLength(4);
    expect(preview.added).toBe(10);
    expect(preview.omitted).toBe(6);
  });
});

describe("log growth", () => {
  it("computes KB per minute between the first and last sample", () => {
    const series = [
      { at: 0, size: 100_000 },
      { at: 120, size: 136_000 },
    ];
    expect(logRateKbPerMin(series)).toBeCloseTo(18, 5);
  });
  it("returns null when the rate cannot be proven", () => {
    expect(logRateKbPerMin([])).toBeNull();
    expect(logRateKbPerMin([{ at: 0, size: 5 }])).toBeNull();
    expect(logRateKbPerMin([{ at: 10, size: 5 }, { at: 10, size: 9 }])).toBeNull();
    expect(logRateKbPerMin([{ at: 0, size: 9 }, { at: 60, size: 5 }])).toBeNull();
  });
  it("draws no sparkline with fewer than two samples", () => {
    expect(sparkPoints([{ at: 0, size: 1 }], 74, 24)).toBe("");
  });
  it("maps the smallest size to the bottom and the largest to the top", () => {
    const points = sparkPoints(
      [
        { at: 0, size: 10 },
        { at: 1, size: 30 },
        { at: 2, size: 20 },
      ],
      100,
      20,
    );
    expect(points).toBe("0,20 50,0 100,10");
  });
  it("draws a flat midline when sizes do not change", () => {
    const points = sparkPoints(
      [
        { at: 0, size: 5 },
        { at: 1, size: 5 },
      ],
      100,
      20,
    );
    expect(points).toBe("0,10 100,10");
  });
});
