import { describe, expect, it } from "vitest";
import type { CommandItem } from "./model";
import { commandDestination, commandOptionId, matchCommandItems, moveCommandIndex, renderedCommandItems } from "./model";

const items: CommandItem[] = [
  { id: "run-a", group: "Jump to", label: "billing-api / ram-2026-10-08", detail: "Run · 4 live", status: "active", searchText: "billing-api ram-2026-10-08", to: "/runs/a" },
  { id: "worker-a-fix", group: "Workers", label: "fix1 · gpt-6-luna", detail: "running", status: "running", searchText: "billing-api ram-2026-10-08 fix1 gpt-6-luna codex", to: "/runs/a/workers/fix1" },
  { id: "run-b", group: "Jump to", label: "stagewatch / redesign", detail: "Run · 2 live", status: "active", searchText: "stagewatch redesign", to: "/runs/b" },
];

describe("command palette model", () => {
  it("matches case-insensitively across terms and ranks closer matches first", () => {
    expect(matchCommandItems(items, "billing-api gpt-6").map((item) => item.id)).toEqual(["worker-a-fix"]);
    expect(matchCommandItems(items, "redesign")[0].id).toBe("run-b");
  });

  it("wraps keyboard movement around the result list", () => {
    expect(moveCommandIndex(0, -1, 3)).toBe(2);
    expect(moveCommandIndex(2, 1, 3)).toBe(0);
    expect(moveCommandIndex(-1, 1, 0)).toBe(-1);
  });

  it("uses the visible group order for keyboard indices", () => {
    const ranked = [items[1], items[0], items[2]];
    expect(renderedCommandItems(ranked).map((item) => item.id)).toEqual(["run-a", "run-b", "worker-a-fix"]);
  });

  it("creates valid stable option ids when names contain spaces", () => {
    const id = commandOptionId("worker-run:abc-agent with spaces");
    expect(id).not.toMatch(/\s/);
    expect(id).toBe(commandOptionId("worker-run:abc-agent with spaces"));
  });

  it("matches all four mock rows for run", async () => {
    const { commandMockItems } = await import("../../mocks/command");
    expect(matchCommandItems(commandMockItems, "run")).toHaveLength(4);
  });

  it("preserves the current query when creating a destination", () => {
    expect(commandDestination(items[1], "?mock=1&density=compact")).toEqual({
      pathname: "/runs/a/workers/fix1",
      search: "?mock=1&density=compact",
    });
  });
});
