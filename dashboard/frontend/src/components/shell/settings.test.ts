import { describe, expect, it } from "vitest";
import { browserStorage, isMockMode, readPreference, writePreference, DENSITY_KEY, THEME_KEY } from "./settings";
import { routePaths } from "./routeTable";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
}

describe("F-shell route table", () => {
  it("includes each final route pattern", () => {
    expect(routePaths).toEqual([
      "/", "/quota", "/logs", "/runs/:sid", "/runs/:sid/timeline",
      "/runs/:sid/workers", "/runs/:sid/reviews",
      "/runs/:sid/workers/:name",
    ]);
  });
});

describe("display preference persistence", () => {
  it("round trips density and theme and rejects unknown values", () => {
    const storage = memoryStorage();
    writePreference(storage, DENSITY_KEY, "compact");
    writePreference(storage, THEME_KEY, "system");
    expect(readPreference(storage, DENSITY_KEY, "comfortable", ["comfortable", "compact"])).toBe("compact");
    expect(readPreference(storage, THEME_KEY, "dark", ["dark", "system", "light"])).toBe("system");
    writePreference(storage, THEME_KEY, "solarized");
    expect(readPreference(storage, THEME_KEY, "dark", ["dark", "system", "light"])).toBe("dark");
  });

  it("falls back when storage is unavailable", () => {
    expect(readPreference(null, DENSITY_KEY, "comfortable", ["comfortable", "compact"])).toBe("comfortable");
    expect(() => writePreference(null, DENSITY_KEY, "compact")).not.toThrow();
  });

  it("treats an inaccessible storage getter as unavailable", () => {
    expect(browserStorage()).toBeNull();
  });
});

describe("mock mode gating", () => {
  it("requires the explicit mock=1 query parameter", () => {
    expect(isMockMode("?mock=1")).toBe(true);
    expect(isMockMode("?mock=0")).toBe(false);
    expect(isMockMode("?mock=true")).toBe(false);
    expect(isMockMode("?other=1")).toBe(false);
  });
});
