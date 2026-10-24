import { describe, expect, it } from "vitest";
import type { StepEvent } from "../../api/types";
import {
  formatStepDuration,
  newestFirst,
  stepBarRatios,
  stepOutcome,
  stepSource,
} from "./steps";

const step = (seq: number, o: Partial<StepEvent> = {}): StepEvent => ({
  seq,
  kind: "exec",
  text: `cmd ${seq}`,
  duration_ms: null,
  ok: null,
  at: null,
  ...o,
});

describe("formatStepDuration", () => {
  it("shows milliseconds under one minute", () => {
    expect(formatStepDuration(12)).toBe("12ms");
    expect(formatStepDuration(3992)).toBe("3992ms");
    expect(formatStepDuration(59_999)).toBe("59999ms");
  });
  it("switches to minutes and seconds at one minute", () => {
    expect(formatStepDuration(60_000)).toBe("1m 00s");
    expect(formatStepDuration(184_000)).toBe("3m 04s");
  });
  it("renders unknown and invalid durations as a dash", () => {
    expect(formatStepDuration(null)).toBe("—");
    expect(formatStepDuration(Number.NaN)).toBe("—");
    expect(formatStepDuration(-5)).toBe("—");
  });
});

describe("stepOutcome", () => {
  it("maps ok true to ok and false to failed", () => {
    expect(stepOutcome(true)).toEqual({ label: "ok", tone: "good" });
    expect(stepOutcome(false)).toEqual({ label: "failed", tone: "bad" });
  });
  it("does not claim an outcome when none was reported", () => {
    expect(stepOutcome(null)).toEqual({ label: "—", tone: "dim" });
  });
});

describe("newestFirst", () => {
  it("orders by sequence, highest first, without mutating the input", () => {
    const input = [step(1), step(3), step(2)];
    expect(newestFirst(input).map((s) => s.seq)).toEqual([3, 2, 1]);
    expect(input.map((s) => s.seq)).toEqual([1, 3, 2]);
  });
});

describe("stepBarRatios", () => {
  it("scales to the longest known duration", () => {
    const ratios = stepBarRatios([
      step(1, { duration_ms: 1000 }),
      step(2, { duration_ms: 4000 }),
    ]);
    expect(ratios).toEqual([0.25, 1]);
  });
  it("gives unknown durations the minimum bar", () => {
    const ratios = stepBarRatios([step(1), step(2, { duration_ms: 2000 })]);
    expect(ratios).toEqual([0.08, 1]);
  });
  it("is safe with no steps and with no known durations", () => {
    expect(stepBarRatios([])).toEqual([]);
    expect(stepBarRatios([step(1)])).toEqual([0.08]);
  });
});

describe("stepSource", () => {
  it("uses structured steps when present", () => {
    expect(
      stepSource({ step_events: [step(1)], log_format: "codex-text" }),
    ).toEqual({ kind: "steps", count: 1 });
  });
  it("says prose logs have no step events instead of inventing them", () => {
    expect(
      stepSource({ step_events: [], log_format: "claude-stream-json" }),
    ).toEqual({ kind: "none", reason: "prose log · no step events" });
  });
  it("says so when there is no log yet", () => {
    expect(stepSource({ step_events: [], log_format: "missing" })).toEqual({
      kind: "none",
      reason: "no log yet",
    });
    expect(stepSource({ step_events: [], log_format: "empty" })).toEqual({
      kind: "none",
      reason: "no log yet",
    });
  });
  it("falls back to a generic reason for other formats", () => {
    expect(stepSource({ step_events: [], log_format: "codex-text" })).toEqual({
      kind: "none",
      reason: "no step events",
    });
  });
});
