import { afterEach, describe, expect, it, vi } from "vitest";
import { pollBadge } from "./badge";

afterEach(() => vi.unstubAllGlobals());

describe("app badge", () => {
  it("sets the badge to the waiting count", async () => {
    const setAppBadge = vi.fn();
    vi.stubGlobal("navigator", { setAppBadge, clearAppBadge: vi.fn() });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ waiting: 3 }) }));
    await pollBadge();
    expect(setAppBadge).toHaveBeenCalledWith(3);
  });

  it("clears the badge when nothing is waiting", async () => {
    const clearAppBadge = vi.fn();
    vi.stubGlobal("navigator", { setAppBadge: vi.fn(), clearAppBadge });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ waiting: 0 }) }));
    await pollBadge();
    expect(clearAppBadge).toHaveBeenCalledOnce();
  });
});
