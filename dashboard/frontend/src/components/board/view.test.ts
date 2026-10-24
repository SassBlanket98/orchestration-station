import { describe, expect, it } from "vitest";
import { DEFAULT_VIEW, STORAGE_KEY, loadView, parseView, saveView } from "./view";

function fakeStore(seed: Record<string, string> = {}) {
  const data = new Map(Object.entries(seed));
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
    removeItem: (key: string) => { data.delete(key); },
  };
}

describe("Board view storage", () => {
  it("loads new fold settings and ignores unknown legacy keys", () => {
    const parsed = parseView({
      open: ["p:orchard", "f:/stage/a"],
      showIdle: true,
      showClosed: false,
      statsOpen: true,
      toggles: { "sec:idle": true },
      filter: "working",
      query: "not restored",
      unknown: "ignored",
    });
    expect([...parsed.open]).toEqual(["p:orchard", "f:/stage/a"]);
    expect(parsed).toMatchObject({ showIdle: true, showClosed: false, statsOpen: true, query: "" });
  });

  it("uses defaults for invalid storage and migrates the old sessionStorage entry", () => {
    expect(parseView(null)).toEqual(DEFAULT_VIEW);
    const session = fakeStore({ [STORAGE_KEY]: JSON.stringify({ toggles: { "s:old": true }, showOld: true }) });
    const local = fakeStore();
    const loaded = loadView(local, session);
    expect(loaded).toMatchObject({ showIdle: false, showClosed: false });
    expect([...loaded.open]).toEqual([]);
    expect(session.data.has(STORAGE_KEY)).toBe(false);
    expect(local.getItem(STORAGE_KEY)).not.toBeNull();
  });

  it("persists Set keys and show flags without restoring a typed query", () => {
    const local = fakeStore();
    saveView(local, { ...DEFAULT_VIEW, open: new Set(["p:orchard"]), showIdle: true, query: "orchard" });
    expect(JSON.parse(local.getItem(STORAGE_KEY) ?? "null")).toEqual({
      open: ["p:orchard"], showIdle: true, showClosed: false, query: "", statsOpen: false,
    });
  });

  it("works when browser storage is unavailable", () => {
    expect(loadView(null, null)).toEqual(DEFAULT_VIEW);
    expect(() => saveView(null, DEFAULT_VIEW)).not.toThrow();
  });
});
