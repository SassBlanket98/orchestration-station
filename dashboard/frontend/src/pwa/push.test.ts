import { afterEach, describe, expect, it, vi } from "vitest";
import { disablePush, enablePush, pushState, pushSupported } from "./push";

afterEach(() => vi.unstubAllGlobals());

describe("push state", () => {
  it("is unsupported when the browser APIs are missing", () => {
    vi.stubGlobal("Notification", undefined);
    vi.stubGlobal("PushManager", undefined);
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: undefined });
    expect(pushSupported()).toBe(false);
  });

  it("reports notification denial", async () => {
    vi.stubGlobal("Notification", { permission: "denied", requestPermission: vi.fn() });
    expect(await pushState()).toBe("denied");
  });

  it("subscribes after permission and sends the subscription to the API", async () => {
    const subscription = { endpoint: "https://push.test/e", toJSON: () => ({ endpoint: "https://push.test/e" }) };
    const subscribe = vi.fn().mockResolvedValue(subscription);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ publicKey: "AQ" }) })
      .mockResolvedValueOnce({ ok: true });
    vi.stubGlobal("Notification", { permission: "default", requestPermission: async () => "granted" });
    vi.stubGlobal("PushManager", function PushManager() {});
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: { ready: Promise.resolve({ pushManager: { subscribe } }) } });
    vi.stubGlobal("fetch", fetchMock);
    await enablePush();
    expect(subscribe).toHaveBeenCalledWith(expect.objectContaining({ userVisibleOnly: true }));
    expect(fetchMock).toHaveBeenLastCalledWith("/api/push/subscribe", expect.objectContaining({ method: "POST" }));
  });

  it("removes a browser subscription and notifies the API", async () => {
    const unsubscribe = vi.fn().mockResolvedValue(true);
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("Notification", { permission: "granted", requestPermission: vi.fn() });
    vi.stubGlobal("PushManager", function PushManager() {});
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: { ready: Promise.resolve({ pushManager: { getSubscription: async () => ({ endpoint: "https://push.test/e", unsubscribe }) } }) } });
    vi.stubGlobal("fetch", fetchMock);
    await disablePush();
    expect(unsubscribe).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith("/api/push/unsubscribe", expect.objectContaining({ method: "POST" }));
  });
});
