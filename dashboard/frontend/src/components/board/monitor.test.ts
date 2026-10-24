import { describe, expect, it } from "vitest";
import type { MonitorGroup, MonitorResponse, MonitorStage, MonitorState, SessionSummary } from "../../api/types";
import { bannerState, ctxLevel, earlierKey, formatClock, formatCtx, formatMilestone, groupKey, groupWorkers, matchStage, milestoneLine, onlineStages, stageDir, tagClass } from "./monitor";

const stage = (path: string, overrides: Partial<MonitorStage> = {}): MonitorStage => ({
  path, label: "stage", tag: "CC·W", lead_ctx: null, lead_state: "closed", live: 0, last_activity: 1_000, solo: false,
  latest_milestone: null, counts: { live: 0, failed: 0, noreport: 0, done: 0, total: 0 }, ...overrides,
});

const session = (path: string): SessionSummary => ({ id: "s1", path }) as SessionSummary;

const state = (stages: MonitorStage[]): MonitorState => ({
  v: 1, now: 2_000, generated: 2_000, stages, hidden_idle: 0, tokens_total: null, cost_total: null,
});

const response = (overrides: Partial<MonitorResponse> = {}): MonitorResponse => ({
  online: true, fetched_at: 2_000, error: null, state: state([]), ...overrides,
});

// Local-time HH:MM for a Unix second, so the assertions hold in any timezone.
const hhmm = (seconds: number) => {
  const at = new Date(seconds * 1000);
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
};

describe("stageDir", () => {
  it("drops a #session suffix and trailing slashes", () => {
    expect(stageDir("/srv/stages/a#abc")).toBe("/srv/stages/a");
    expect(stageDir("/srv/stages/a/")).toBe("/srv/stages/a");
    expect(stageDir("/srv/stages/a/#abc")).toBe("/srv/stages/a");
  });

  it("leaves a bare root alone", () => {
    expect(stageDir("/")).toBe("/");
  });
});

describe("matchStage", () => {
  it("matches on the stage directory, ignoring a #suffix on the stage path", () => {
    const hit = stage("/srv/stages/a#s9");
    expect(matchStage(session("/srv/stages/a"), [stage("/srv/stages/b"), hit])).toBe(hit);
  });

  it("matches a session path that has a trailing slash", () => {
    const hit = stage("/srv/stages/a");
    expect(matchStage(session("/srv/stages/a/"), [hit])).toBe(hit);
  });

  it("returns null when no stage directory matches", () => {
    expect(matchStage(session("/srv/stages/a"), [stage("/srv/stages/ab"), stage("/srv/stages/a/b")])).toBeNull();
  });

  it("returns null for an empty stage list", () => {
    expect(matchStage(session("/srv/stages/a"), [])).toBeNull();
  });

  it("prefers a live lead over a newer closed one", () => {
    const closedNewer = stage("/x#closed", { lead_state: "closed", last_activity: 9_000 });
    const liveOlder = stage("/x#live", { lead_state: "live", last_activity: 1_000 });
    expect(matchStage(session("/x"), [closedNewer, liveOlder])).toBe(liveOlder);
  });

  it("among live leads, prefers the newest last_activity", () => {
    const older = stage("/x#a", { lead_state: "live", last_activity: 1_000 });
    const newer = stage("/x#b", { lead_state: "live", last_activity: 5_000 });
    expect(matchStage(session("/x"), [older, newer])).toBe(newer);
  });

  it("among non-live leads, prefers the newest last_activity", () => {
    const older = stage("/x#a", { lead_state: "closed", last_activity: 1_000 });
    const newer = stage("/x#b", { lead_state: "closed", last_activity: 5_000 });
    expect(matchStage(session("/x"), [older, newer])).toBe(newer);
  });

  it("keeps the backend order on a full tie", () => {
    const first = stage("/x#a", { lead_state: "live", last_activity: 3_000 });
    const second = stage("/x#b", { lead_state: "live", last_activity: 3_000 });
    expect(matchStage(session("/x"), [first, second])).toBe(first);
  });

  it("does not reorder the input array", () => {
    const older = stage("/x#a", { last_activity: 1_000 });
    const newer = stage("/x#b", { last_activity: 5_000 });
    const stages = [older, newer];
    matchStage(session("/x"), stages);
    expect(stages).toEqual([older, newer]);
  });
});

describe("onlineStages", () => {
  it("returns the stages when online", () => {
    const stages = [stage("/x")];
    expect(onlineStages(response({ state: state(stages) }))).toBe(stages);
  });

  it("returns nothing when offline, null or stateless", () => {
    expect(onlineStages(null)).toEqual([]);
    expect(onlineStages(response({ online: false, state: state([stage("/x")]) }))).toEqual([]);
    expect(onlineStages(response({ state: null }))).toEqual([]);
  });
});

describe("online response without stages", () => {
  const broken = { online: true, fetched_at: 1, error: null, state: {} } as unknown as MonitorResponse;
  it("overlays nothing and reports offline instead of throwing", () => {
    expect(onlineStages(broken)).toEqual([]);
    expect(bannerState(broken).kind).toBe("offline");
  });
});

describe("ctxLevel", () => {
  it("is null when the context is unknown", () => {
    expect(ctxLevel(null)).toBeNull();
  });

  it("is ok below 150k, warn from 150k, and bad only above 250k", () => {
    expect(ctxLevel(0)).toBe("ok");
    expect(ctxLevel(149_999)).toBe("ok");
    expect(ctxLevel(150_000)).toBe("warn");
    expect(ctxLevel(250_000)).toBe("warn");
    expect(ctxLevel(250_001)).toBe("bad");
  });
});

describe("formatCtx", () => {
  it("rounds to whole thousands", () => {
    expect(formatCtx(87_000)).toBe("87k");
    expect(formatCtx(87_400)).toBe("87k");
    expect(formatCtx(87_600)).toBe("88k");
  });

  it("shows a dash when unknown", () => {
    expect(formatCtx(null)).toBe("—");
  });
});

describe("tagClass", () => {
  it("maps each known harness tag to its colour class", () => {
    expect(tagClass("CC·W")).toBe("cc-w");
    expect(tagClass("CC·P")).toBe("cc-p");
    expect(tagClass("CDX")).toBe("cdx");
    expect(tagClass("OCode")).toBe("ocode");
    expect(tagClass("OClaw")).toBe("oclaw");
    expect(tagClass("CUR")).toBe("cur");
  });

  it("returns an empty class for an unknown tag", () => {
    expect(tagClass("")).toBe("");
    expect(tagClass("XYZ")).toBe("");
  });
});

describe("bannerState", () => {
  it("is offline when there is no response, or the response is offline or stateless", () => {
    expect(bannerState(null)).toEqual({ kind: "offline", text: "Monitor offline" });
    expect(bannerState(response({ online: false }))).toEqual({ kind: "offline", text: "Monitor offline" });
    expect(bannerState(response({ state: null }))).toEqual({ kind: "offline", text: "Monitor offline" });
  });

  it("is working when a stage is in the active section", () => {
    const banner = bannerState(response({ state: state([stage("/x", { section: "active" })]) }));
    expect(banner).toEqual({ kind: "working", text: "Workers are working" });
  });

  it("is working when a stage has live workers, whatever its section", () => {
    const banner = bannerState(response({ state: state([stage("/x", { section: "idle", live: 2 })]) }));
    expect(banner.kind).toBe("working");
  });

  it("is quiet with the newest finish time across stages", () => {
    const banner = bannerState(response({
      state: state([stage("/a", { last_activity: 1_000 }), stage("/b", { last_activity: 5_000, section: "idle" })]),
    }));
    expect(banner).toEqual({ kind: "quiet", text: `All quiet. last finished ${hhmm(5_000)}` });
  });

  it("is quiet without a clause when no stage has any activity", () => {
    const banner = bannerState(response({ state: state([stage("/a", { last_activity: 0 })]) }));
    expect(banner).toEqual({ kind: "quiet", text: "All quiet." });
  });

  it("is quiet without a clause when there are no stages", () => {
    expect(bannerState(response())).toEqual({ kind: "quiet", text: "All quiet." });
  });
});

const group = (label: string, workers: string[], overrides: Partial<MonitorGroup> = {}): MonitorGroup => ({
  label, workers, started: null, done: workers.length, total: workers.length, live: 0, finished: true, current: false, ...overrides,
});

describe("groupWorkers", () => {
  it("returns null without groups, or with fewer than two", () => {
    expect(groupWorkers(undefined, ["a"])).toBeNull();
    expect(groupWorkers([], ["a"])).toBeNull();
    expect(groupWorkers([group("current", ["a"], { current: true })], ["a"])).toBeNull();
  });

  it("splits current, previous and older, keeping the session's worker order", () => {
    const groups = [group("now", ["a"], { current: true }), group("before", ["b"]), group("old", ["c"]), group("older", ["d"])];
    expect(groupWorkers(groups, ["a", "b", "c", "d"])).toEqual({
      current: ["a"],
      previous: { label: "before", done: 1, total: 1, workers: ["b"] },
      older: [
        { label: "old", done: 1, total: 1, workers: ["c"] },
        { label: "older", done: 1, total: 1, workers: ["d"] },
      ],
    });
  });

  it("folds workers named in no group into the oldest group, not the current one", () => {
    const groups = [group("now", ["a"], { current: true }), group("before", ["b"]), group("old", ["c"])];
    const split = groupWorkers(groups, ["a", "stray", "b", "c"]);
    expect(split?.current).toEqual(["a"]);
    expect(split?.older[0].workers).toEqual(["stray", "c"]);
  });

  it("keeps only the session's own workers, so a group with none is empty", () => {
    const groups = [group("now", ["a"], { current: true }), group("before", ["gone"])];
    expect(groupWorkers(groups, ["a"])?.previous?.workers).toEqual([]);
  });

  it("assigns a name listed in two groups to the newest", () => {
    const groups = [group("now", [], { current: true }), group("before", ["x"]), group("old", ["x"])];
    const split = groupWorkers(groups, ["x"]);
    expect(split?.previous?.workers).toEqual(["x"]);
    expect(split?.older[0].workers).toEqual([]);
  });

  it("handles a group whose workers field is missing", () => {
    const bare = { ...group("now", [], { current: true }), workers: undefined as unknown as string[] };
    expect(groupWorkers([bare, group("before", [])], ["a"])?.previous?.workers).toEqual(["a"]);
  });

  it("keeps a group's own done and total counts, even when the session has none of its workers", () => {
    const groups = [group("now", ["a"], { current: true, done: 1, total: 1 }), group("before", [], { done: 3, total: 5 })];
    expect(groupWorkers(groups, ["a"])?.previous).toEqual({ label: "before", done: 3, total: 5, workers: [] });
  });
});

describe("groupWorkers: the current flag", () => {
  it("uses the group flagged current even when it is not first", () => {
    const groups = [group("done task", ["a"]), group("running task", ["b"], { current: true }), group("old", ["c"])];
    const split = groupWorkers(groups, ["a", "b", "c"]);
    expect(split?.current).toEqual(["b"]);
    expect(split?.previous?.label).toBe("done task");
    expect(split?.older.map((g) => g.label)).toEqual(["old"]);
  });
});

describe("group fold keys", () => {
  it("names the previous, older and aggregate lines under the session", () => {
    expect(groupKey("s1", 1)).toBe("g:s1/1");
    expect(groupKey("s1", 3)).toBe("g:s1/3");
    expect(earlierKey("s1")).toBe("g:s1/earlier");
  });
});

describe("milestones", () => {
  it("prefixes the local HH:MM when the stage milestone has a timestamp", () => {
    expect(milestoneLine(stage("/x", { latest_milestone: { ts: 5_000, text: "shipped" } }), session("/x"))).toBe(`${hhmm(5_000)} shipped`);
  });

  it("falls back to the session's own milestone, without a clock", () => {
    const own = { ...session("/x"), latest_milestone: "own" };
    expect(milestoneLine(null, own)).toBe("own");
    expect(milestoneLine(stage("/x"), own)).toBe("own");
  });

  it("shows a placeholder when neither has a milestone", () => {
    expect(milestoneLine(null, session("/x"))).toBe("No milestone recorded");
  });

  it("formats a list entry without a clock when ts is null, and with one when set", () => {
    expect(formatMilestone({ ts: null, text: "x" })).toBe("x");
    expect(formatMilestone({ ts: 5_000, text: "x" })).toBe(`${hhmm(5_000)} x`);
  });

  it("renders an empty clock for a missing timestamp", () => {
    expect(formatClock(null)).toBe("");
    expect(formatClock(5_000)).toBe(hhmm(5_000));
  });
});
