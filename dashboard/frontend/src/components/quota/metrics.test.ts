import { describe, expect, it } from "vitest";
import type { QuotaPickCandidate } from "../../api/types";
import { arcOffset, formatMetric, formatUnixMs, formatUnixSeconds, gaugeState, isOverPace, selectPrimaryLimit, visibleScore } from "./metrics";

describe("quota metrics", () => {
  it("maps percentages to clamped arc lengths and suppresses null values", () => {
    expect(arcOffset(50)).toBe(132);
    expect(arcOffset(120)).toBe(264);
    expect(arcOffset(-10)).toBe(0);
    expect(arcOffset(null)).toBeNull();
    expect(formatMetric(null)).toBe("—");
  });

  it("warns only when usage is ahead of pace or projection reaches 100", () => {
    expect(isOverPace(51, 50, 80)).toBe(true);
    expect(isOverPace(50, 50, 99.9)).toBe(false);
    expect(isOverPace(null, 70, null)).toBe(false);
    expect(isOverPace(20, null, 100)).toBe(true);
  });

  it("derives calm, over-pace, near-limit, and unknown gauge states", () => {
    expect(gaugeState(20, 30, 40)).toBe("calm");
    expect(gaugeState(51, 50, 80)).toBe("warning");
    expect(gaugeState(91, 26, null)).toBe("danger");
    expect(gaugeState(20, null, 100)).toBe("danger");
    expect(gaugeState(null, null, null)).toBe("unknown");
    expect(gaugeState(20, null, null)).toBe("unknown");
  });

  it("formats millisecond upstream time separately from second fetched time", () => {
    expect(formatUnixMs(1791511190896)).not.toBe("—");
    expect(formatUnixSeconds(1791511200)).not.toBe("—");
    expect(formatUnixSeconds(1791511200)).toBe(new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(1791511200 * 1000)));
    expect(formatUnixMs(1791511200)).not.toBe(formatUnixSeconds(1791511200));
    expect(formatUnixMs(null)).toBe("—");
  });

  it("hides offline scores without replacing a null score with zero", () => {
    expect(visibleScore(82, true)).toBeNull();
    expect(visibleScore(null, false)).toBeNull();
    expect(visibleScore(0, false)).toBe(0);
  });

  it("selects the five-hour window when present and preserves candidate ranking order", () => {
    const limits = [
      { window: "seven_day", name: "Week", used_pct: 12, pace_pct: null, projected_pct: null, runs_out_at: null, resets_at: null },
      { window: "five_hour", name: "5h", used_pct: 3, pace_pct: null, projected_pct: null, runs_out_at: null, resets_at: null },
    ];
    expect(selectPrimaryLimit(limits)?.window).toBe("five_hour");
    const candidates: QuotaPickCandidate[] = ["first", "second", "third"].map((model) => ({ model, effort: null, subscription: null, score: null, runway: null, group: null, tier: null, reason: null, shortest: null, longest: null }));
    expect(candidates.map((candidate) => candidate.model)).toEqual(["first", "second", "third"]);
  });
});
