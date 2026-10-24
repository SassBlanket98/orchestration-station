import { describe, expect, it } from "vitest";
import { WORKER_TABS, parseWorkerTab, tabSearch } from "./tabs";

describe("parseWorkerTab", () => {
  it("maps each tab id to itself", () => {
    for (const tab of WORKER_TABS) {
      expect(parseWorkerTab(tab)).toBe(tab);
    }
  });
  it("falls back to overview for missing or unknown values", () => {
    expect(parseWorkerTab(null)).toBe("overview");
    expect(parseWorkerTab(undefined)).toBe("overview");
    expect(parseWorkerTab("")).toBe("overview");
    expect(parseWorkerTab("Step feed")).toBe("overview");
    expect(parseWorkerTab("report ")).toBe("overview");
  });
});

describe("tabSearch", () => {
  it("omits the param for overview", () => {
    expect(tabSearch("", "overview")).toBe("");
    expect(tabSearch("?tab=steps", "overview")).toBe("");
  });
  it("sets the param for other tabs", () => {
    expect(tabSearch("", "steps")).toBe("?tab=steps");
    expect(tabSearch("?tab=steps", "files")).toBe("?tab=files");
  });
  it("keeps unrelated params, such as the mock flag", () => {
    expect(tabSearch("?mock=1", "brief")).toBe("?mock=1&tab=brief");
    expect(tabSearch("?tab=brief&mock=1", "overview")).toBe("?mock=1");
  });
});
