import { describe, expect, it } from "vitest";
import type { Worker } from "../../api/types";
import { elapsedSeconds, filterWorkers, hasTimedEvent, RUN_TABS, sourceValue, statusFromSearch, tabFromPath, timelinePositions, timelineRange, timelineTicks } from "./presentation";

const workers = [
  { name: "live", status: "running" },
  { name: "done", status: "finished" },
  { name: "gone", status: "exited" },
  { name: "maybe", status: "unknown" },
] as Worker[];

describe("run page presentation", () => {
  it("maps each tab to its URL and defaults the base route to summary", () => {
    expect(RUN_TABS.map((tab) => [tab.id, tab.suffix])).toEqual([
      ["summary", ""], ["timeline", "/timeline"], ["workers", "/workers"], ["reviews", "/reviews"],
    ]);
    expect(tabFromPath("/runs/abc")).toBe("summary");
    expect(tabFromPath("/runs/abc/timeline")).toBe("timeline");
    expect(tabFromPath("/runs/abc/workers")).toBe("workers");
    expect(tabFromPath("/runs/abc/reviews")).toBe("reviews");
    expect(tabFromPath("/runs/abc/files")).toBe("summary");
    expect(tabFromPath("/runs/abc/workers/build-worker")).toBe("workers");
  });

  it("filters workers by the requested status", () => {
    expect(filterWorkers(workers, "running").map((worker) => worker.name)).toEqual(["live"]);
    expect(filterWorkers(workers, "all")).toHaveLength(4);
  });

  it("positions known timeline events and omits unknown timestamps", () => {
    expect(timelinePositions(10, 20, [
      { at: 10, kind: "start", label: "start" },
      { at: 15, kind: "step", label: "middle" },
      { at: 30, kind: "end", label: "outside" },
      { at: null, kind: "unknown", label: "unknown" },
    ])).toEqual([
      { left: 0, at: 10, kind: "start", label: "start" },
      { left: 50, at: 15, kind: "step", label: "middle" },
      { left: 100, at: 30, kind: "end", label: "outside" },
    ]);
  });

  it("keeps each timed marker's own time and label when untimed events come first", () => {
    const points = timelinePositions(10, 20, [
      { at: null, kind: "pick", label: "untimed pick" },
      { at: 12, kind: "step", label: "timed step" },
      { at: null, kind: "reject", label: "untimed reject" },
      { at: 18, kind: "finding", label: "timed finding" },
    ]);
    expect(points.map((point) => [point.label, point.at])).toEqual([["timed step", 12], ["timed finding", 18]]);
  });

  it("reports whether any run event carries a timestamp", () => {
    expect(hasTimedEvent([{ at: null }, { at: null }])).toBe(false);
    expect(hasTimedEvent([])).toBe(false);
    expect(hasTimedEvent([{ at: null }, { at: 5 }])).toBe(true);
  });

  it("keeps null source values visible as question marks", () => {
    expect(sourceValue(null)).toBe("?");
    expect(sourceValue(undefined)).toBe("?");
    expect(sourceValue(0)).toBe("0");
    expect(sourceValue("available")).toBe("available");
  });

  it("parses only supported URL worker status values", () => {
    expect(statusFromSearch("?status=finished")).toBe("finished");
    expect(statusFromSearch("?status=invalid")).toBe("all");
    expect(statusFromSearch("")).toBe("all");
  });

  it("stops elapsed duration at the finish time when present", () => {
    expect(elapsedSeconds(10, 40, 100, "finished")).toBe(30);
    expect(elapsedSeconds(10, 40, 100, "exited")).toBe(30);
    expect(elapsedSeconds(10, null, 40, "running")).toBe(30);
    expect(elapsedSeconds(null, null, 40, "running")).toBeNull();
  });

  it("shows unknown elapsed time for ended workers without a finish time", () => {
    expect(elapsedSeconds(10, null, 100, "finished")).toBeNull();
    expect(elapsedSeconds(10, null, 100, "exited")).toBeNull();
    expect(elapsedSeconds(10, null, 100, "unknown")).toBeNull();
    expect(elapsedSeconds(null, null, 100, "exited")).toBeNull();
  });

  it("uses evidence bounds for finished timelines and labels five axis ticks", () => {
    expect(timelineRange([10, 20, 30], false, 100)).toEqual({ start: 10, end: 30 });
    expect(timelineRange([10, 20, 30], true, 100)).toEqual({ start: 10, end: 100 });
    expect(timelineRange([], false, 100)).toBeNull();
    expect(timelineTicks(0, 40).map((tick) => tick.left)).toEqual([0, 25, 50, 75, 100]);
    expect(timelineTicks(0, 40).every((tick) => tick.label.length > 0)).toBe(true);
  });
});
