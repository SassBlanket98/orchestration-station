import { describe, expect, it } from "vitest";
import { LEVEL_TAGS, formatAge, levelTagShort, levelTagTone, nowLine, treePrefixes } from "./treeText";

const pair = (depths: number[]) => treePrefixes(depths).map((p) => [p.prefix, p.cont]);

describe("treePrefixes", () => {
  it("returns nothing for an empty tree", () => {
    expect(treePrefixes([])).toEqual([]);
  });

  it("draws a lone node as the last branch with blank continuation", () => {
    expect(pair([0])).toEqual([["└─ ", "   "]]);
  });

  it("draws siblings: a branch then a last branch, with the bar only where a sibling follows", () => {
    expect(pair([0, 0])).toEqual([
      ["├─ ", "│  "],
      ["└─ ", "   "],
    ]);
  });

  it("draws a child under a parent that is the last root", () => {
    expect(pair([0, 1])).toEqual([
      ["└─ ", "│  "],
      ["   └─ ", "      "],
    ]);
  });

  it("draws two children, the first a branch and the last closing the group", () => {
    expect(pair([0, 1, 1])).toEqual([
      ["└─ ", "│  "],
      ["   ├─ ", "   │  "],
      ["   └─ ", "      "],
    ]);
  });

  it("walks parent > child > grandchild > sibling", () => {
    // parent (0) has a later root sibling, so its bar runs down through the whole subtree.
    expect(pair([0, 1, 2, 0])).toEqual([
      ["├─ ", "│  "],
      ["│  └─ ", "│  │  "],
      ["│     └─ ", "│        "],
      ["└─ ", "   "],
    ]);
  });

  it("keeps the bar on a branch only while a later sibling exists at that level", () => {
    // the root (0) is the last root; child (1) has a later sibling, grandchild (2) is the last of its group.
    expect(pair([0, 1, 2, 1])).toEqual([
      ["└─ ", "│  "],
      ["   ├─ ", "   │  "],
      ["   │  └─ ", "   │     "],
      ["   └─ ", "      "],
    ]);
  });

  it("clamps a jump deeper than one level to one level below the previous node", () => {
    expect(pair([0, 3])).toEqual(pair([0, 1]));
    expect(pair([2, 2])).toEqual(pair([0, 1]));
  });

  it("never lets a node sit above the root", () => {
    expect(pair([-4, 0])).toEqual(pair([0, 0]));
  });

  it("keeps prefix and continuation the same width on every node", () => {
    const depths = [0, 1, 2, 2, 1, 0, 1, 2, 3, 1];
    for (const { prefix, cont } of treePrefixes(depths)) {
      expect([...prefix].length).toBe([...cont].length);
    }
  });
});

describe("levelTagShort", () => {
  it("shortens the four long tags and keeps LEAD and CRON", () => {
    expect(levelTagShort("LEAD")).toBe("LEAD");
    expect(levelTagShort("SPAWN")).toBe("SPWN");
    expect(levelTagShort("SUB")).toBe("SUB");
    expect(levelTagShort("WORKER")).toBe("WRKR");
    expect(levelTagShort("CRON")).toBe("CRON");
  });

  it("passes unknown tags through unchanged", () => {
    expect(levelTagShort("MYSTERY")).toBe("MYSTERY");
    expect(levelTagShort("")).toBe("");
  });
});

describe("levelTagTone", () => {
  it("maps each level tag to its colour role", () => {
    expect(levelTagTone("LEAD")).toBe("sub");
    expect(levelTagTone("SPAWN")).toBe("violet");
    expect(levelTagTone("SUB")).toBe("teal");
    expect(levelTagTone("WORKER")).toBe("dim");
    expect(levelTagTone("CRON")).toBe("amber");
  });

  it("has no tone for unknown or blank tags", () => {
    expect(levelTagTone("")).toBeNull();
    expect(levelTagTone("worker")).toBeNull();
  });

  it("covers exactly the known level tags", () => {
    expect(LEVEL_TAGS).toEqual(["LEAD", "SPAWN", "SUB", "WORKER", "CRON"]);
    for (const tag of LEVEL_TAGS) expect(levelTagTone(tag)).not.toBeNull();
  });
});

describe("nowLine", () => {
  const NOW = 10_000;

  it("is null when the stage is not live", () => {
    expect(nowLine({ section: "done", lead_state: "closed", level_tag: "LEAD" }, NOW)).toBeNull();
    expect(nowLine({}, NOW)).toBeNull();
  });

  it("is live on section active, lead_state live, or lead_state background", () => {
    const milestone = { ts: 9_950, text: "wrote tests" };
    expect(nowLine({ section: "active", latest_milestone: milestone }, NOW)).toEqual({ text: "wrote tests", ageSec: 50 });
    expect(nowLine({ lead_state: "live", latest_milestone: milestone }, NOW)).toEqual({ text: "wrote tests", ageSec: 50 });
    expect(nowLine({ lead_state: "background", latest_milestone: milestone }, NOW)).toEqual({ text: "wrote tests", ageSec: 50 });
  });

  it("shows the latest milestone with its age", () => {
    const line = nowLine({ section: "active", latest_milestone: { ts: 9_000, text: "running build" } }, NOW);
    expect(line).toEqual({ text: "running build", ageSec: 1_000 });
  });

  it("has no age when the milestone has no timestamp", () => {
    expect(nowLine({ section: "active", latest_milestone: { ts: null, text: "queued" } }, NOW)).toEqual({
      text: "queued",
      ageSec: null,
    });
  });

  it("never reports a negative age for a milestone stamped in the future", () => {
    expect(nowLine({ section: "active", latest_milestone: { ts: 10_500, text: "clock skew" } }, NOW)).toEqual({
      text: "clock skew",
      ageSec: 0,
    });
  });

  it("says no activity was recorded for LEAD, SPAWN, CRON and blank tags with no milestone", () => {
    const none = { ageSec: null, text: "no activity recorded" };
    for (const level_tag of ["LEAD", "SPAWN", "CRON", ""]) {
      expect(nowLine({ section: "active", level_tag, latest_milestone: null }, NOW)).toEqual(none);
    }
    expect(nowLine({ section: "active", latest_milestone: null }, NOW)).toEqual(none);
  });

  it("is null for SUB and WORKER tags with no milestone", () => {
    expect(nowLine({ section: "active", level_tag: "SUB", latest_milestone: null }, NOW)).toBeNull();
    expect(nowLine({ section: "active", level_tag: "WORKER", latest_milestone: null }, NOW)).toBeNull();
  });

  it("treats an empty milestone text as no milestone", () => {
    expect(nowLine({ section: "active", level_tag: "LEAD", latest_milestone: { ts: 9_000, text: "" } }, NOW)).toEqual({
      text: "no activity recorded",
      ageSec: null,
    });
  });
});

describe("formatAge", () => {
  it("shows seconds under a minute", () => {
    expect(formatAge(0)).toBe("0s");
    expect(formatAge(40)).toBe("40s");
    expect(formatAge(59.9)).toBe("59s");
  });

  it("shows whole minutes under an hour", () => {
    expect(formatAge(60)).toBe("1m");
    expect(formatAge(25 * 60 + 30)).toBe("25m");
    expect(formatAge(3599)).toBe("59m");
  });

  it("shows hours and zero-padded minutes under a day", () => {
    expect(formatAge(3600)).toBe("1h 00m");
    expect(formatAge(3600 + 5 * 60)).toBe("1h 05m");
    expect(formatAge(86399)).toBe("23h 59m");
  });

  it("shows whole days from a day up", () => {
    expect(formatAge(86400)).toBe("1d");
    expect(formatAge(3 * 86400 + 7200)).toBe("3d");
  });

  it("reads negative or non-finite input as zero", () => {
    expect(formatAge(-5)).toBe("0s");
    expect(formatAge(Number.NaN)).toBe("0s");
  });
});
