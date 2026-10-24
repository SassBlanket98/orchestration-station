import { describe, expect, it } from "vitest";
import { neighbours, orderWorkers } from "./nav";

type Row = {
  name: string;
  status: "running" | "finished" | "exited" | "unknown";
  started_at: number | null;
  finished_at: number | null;
};

const row = (name: string, o: Partial<Row> = {}): Row => ({
  name,
  status: "finished",
  started_at: null,
  finished_at: null,
  ...o,
});

describe("orderWorkers", () => {
  it("lists running workers first, newest start first", () => {
    const names = orderWorkers([
      row("old-run", { status: "running", started_at: 100 }),
      row("new-run", { status: "running", started_at: 300 }),
      row("done", { status: "finished", finished_at: 500 }),
    ]);
    expect(names).toEqual(["new-run", "old-run", "done"]);
  });
  it("puts nulls last among finished workers", () => {
    const names = orderWorkers([
      row("no-time"),
      row("early", { finished_at: 10 }),
      row("late", { finished_at: 20 }),
    ]);
    expect(names).toEqual(["late", "early", "no-time"]);
  });
  it("does not mutate its input", () => {
    const input = [row("b", { finished_at: 1 }), row("a", { finished_at: 2 })];
    orderWorkers(input);
    expect(input.map((w) => w.name)).toEqual(["b", "a"]);
  });
});

describe("neighbours", () => {
  const names = ["fix1", "st-f1", "cur-proc"];
  it("returns the earlier and later worker", () => {
    expect(neighbours(names, "st-f1")).toEqual({ prev: "fix1", next: "cur-proc" });
  });
  it("has no previous at the first worker and no next at the last", () => {
    expect(neighbours(names, "fix1")).toEqual({ prev: null, next: "st-f1" });
    expect(neighbours(names, "cur-proc")).toEqual({ prev: "st-f1", next: null });
  });
  it("has neither when the worker is not in the run", () => {
    expect(neighbours(names, "missing")).toEqual({ prev: null, next: null });
  });
  it("has neither for a single-worker run", () => {
    expect(neighbours(["only"], "only")).toEqual({ prev: null, next: null });
  });
});
