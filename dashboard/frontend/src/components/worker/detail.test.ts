import { describe, expect, it } from "vitest";
import { workerBodyState, workerUnavailableHint, type WorkerBodyInput } from "./detail";

const base: WorkerBodyInput = {
  hasView: false,
  sessionKnown: false,
  inSession: false,
  failed: false,
};

describe("workerBodyState", () => {
  it("shows loading while the request is in flight or the session is not loaded", () => {
    expect(workerBodyState(base)).toBe("loading");
    expect(
      workerBodyState({ ...base, sessionKnown: true, inSession: true }),
    ).toBe("loading");
  });
  it("shows loaded once detail is available", () => {
    expect(workerBodyState({ ...base, hasView: true })).toBe("loaded");
  });
  it("shows failed when the request settled empty and the session still lists the worker", () => {
    expect(
      workerBodyState({ ...base, sessionKnown: true, inSession: true, failed: true }),
    ).toBe("failed");
  });
  it("shows failed when the session has not loaded yet", () => {
    expect(workerBodyState({ ...base, failed: true })).toBe("failed");
  });
  it("shows missing when the session loaded without the worker", () => {
    expect(workerBodyState({ ...base, sessionKnown: true })).toBe("missing");
    expect(
      workerBodyState({ ...base, sessionKnown: true, failed: true }),
    ).toBe("missing");
  });
  it("prefers loaded over missing", () => {
    expect(
      workerBodyState({ ...base, hasView: true, sessionKnown: true }),
    ).toBe("loaded");
  });
});

describe("workerUnavailableHint", () => {
  it("only says the run lists the worker when the loaded session confirms it", () => {
    expect(workerUnavailableHint(false, false)).toBeUndefined();
    expect(workerUnavailableHint(true, false)).toBeUndefined();
    expect(workerUnavailableHint(true, true)).toBe(
      "The run lists this worker, but its detail did not load.",
    );
  });
});
