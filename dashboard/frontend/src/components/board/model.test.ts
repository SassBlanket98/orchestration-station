import { describe, expect, it } from "vitest";
import { formatElapsed, heartbeat, isLiveState, workerMark } from "./model";

describe("remaining Board display helpers", () => {
  it("formats elapsed time without inventing a start", () => {
    expect(formatElapsed(null, 10)).toBe("—");
    expect(formatElapsed(100, 181)).toBe("01m 21s");
    expect(formatElapsed(0, 3660)).toBe("1h 01m");
  });

  it("aggregates worker activity for the expanded masthead", () => {
    expect(heartbeat([[{ at: 1, count: 2 }, { at: 2, count: 4 }]], 3)).toEqual([0.5, 0.75, 1]);
    expect(heartbeat([], 2)).toEqual([0, 0]);
  });

  it("maps worker states to the existing Board marks", () => {
    expect(isLiveState("running")).toBe(true);
    expect(isLiveState("stalled")).toBe(true);
    expect(workerMark("failed")).toBe("fail");
    expect(workerMark("done")).toBe("done");
    expect(workerMark("no-report")).toBe("none");
  });
});
