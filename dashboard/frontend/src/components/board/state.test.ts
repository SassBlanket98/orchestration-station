import { describe, expect, it } from "vitest";
import type { WorkerPreview } from "../../api/types";
import { deriveWorkerState } from "./state";

const worker = (fields: Partial<WorkerPreview> = {}): WorkerPreview => ({
  name: "worker", model: null, engine: "unknown", started_at: null, now: null,
  status: "running", pid: null, title: null, goal: null, finished_at: null,
  last_activity_at: 10, summary: [], has_brief: false, has_report: false,
  log_stem: null, log_size: 0, log_format: "empty", flags: [], engine_family: "codex",
  account: null, effort: null, ...fields,
});

describe("board worker state", () => {
  it("uses quiet and stalled thresholds before completion state", () => {
    expect(deriveWorkerState(worker(), 99_999)).toBe("running");
    expect(deriveWorkerState(worker(), 100_000)).toBe("quiet");
    expect(deriveWorkerState(worker(), 310_000)).toBe("stalled");
  });
  it("distinguishes reported completion, missing report, and failures", () => {
    expect(deriveWorkerState(worker({ status: "finished", has_report: true }))).toBe("done");
    expect(deriveWorkerState(worker({ status: "exited" }))).toBe("no-report");
    expect(deriveWorkerState(worker({ status: "exited", flags: ["failed"] }))).toBe("failed");
  });
});
