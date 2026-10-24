import { describe, expect, it } from "vitest";
import { workerFacts } from "./WorkerPeek";

const NOW = 10_000;

describe("workerFacts", () => {
  it("lists what Monitor knows about a running worker", () => {
    const facts = Object.fromEntries(workerFacts({ name: "tp2-c", state: "running", model: "composer default", harness: "cursor", job: "build", start: NOW - 125, quiet_s: 9, has_report: false }, NOW));
    expect(facts).toMatchObject({ state: "running", model: "composer default · cursor", job: "build", ran: "2m 05s", "quiet for": "9s", report: "none" });
  });
  it("shows outcome, failure-related counts and the run length of a finished worker", () => {
    const facts = Object.fromEntries(workerFacts({ name: "w", state: "failed", outcome: "failure", start: 100, end: 160, errors: 3, tokens_total: 2_574_104, has_report: true, harness: "unknown" }, NOW));
    expect(facts).toMatchObject({ outcome: "failure", ran: "1m 00s", errors: "3", tokens: "2.6M", report: "yes" });
    expect(facts).not.toHaveProperty("quiet for");
    expect(facts.model).toBeUndefined();
  });
});
