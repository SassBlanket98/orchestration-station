import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  fetchLogIndex,
  fetchQuotaPick,
  fetchQuotaUsage,
} from "./client";
import type { QuotaUsage } from "./types";

type Call = { url: string; init: RequestInit | undefined };

/** Stubs the global fetch with one fixed response and records each call. */
function stubFetch(response: Response): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    // Clone so repeated calls each get an unread body.
    return response.clone();
  });
  return calls;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const OFFLINE_USAGE: QuotaUsage = {
  offline: true,
  generated_at: null,
  fetched_at: 1_760_000_000,
  error_code: "offline",
  subscriptions: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("quota usage", () => {
  it("requests a relative same-origin URL and returns the usage body", async () => {
    const usage: QuotaUsage = { ...OFFLINE_USAGE, offline: false, error_code: null };
    const calls = stubFetch(json(200, usage));

    await expect(fetchQuotaUsage()).resolves.toEqual(usage);
    expect(calls[0].url).toBe("/api/quota/usage");
  });

  it("returns the 503 offline body as data instead of throwing", async () => {
    stubFetch(json(503, OFFLINE_USAGE));

    await expect(fetchQuotaUsage()).resolves.toEqual(OFFLINE_USAGE);
  });

  it("rejects a 503 whose body is not an offline QuotaUsage", async () => {
    stubFetch(new Response("<html>bad gateway</html>", { status: 503 }));

    const err = await fetchQuotaUsage().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(503);
    expect((err as ApiError).detail).toBeNull();
  });

  it("rejects other failures as ApiError with the server detail", async () => {
    stubFetch(json(500, { detail: "boom" }));

    const err = await fetchQuotaUsage().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).detail).toBe("boom");
  });
});

describe("quota pick", () => {
  it("sends every query parameter that is set, in a fixed order", async () => {
    const calls = stubFetch(json(200, { job: "review" }));

    await fetchQuotaPick({
      job: "review",
      slices: 3,
      lead: "claude-opus-5-5",
      builder: "gpt-6-luna",
    });
    expect(calls[0].url).toBe(
      "/api/quota/pick?job=review&slices=3&lead=claude-opus-5-5&builder=gpt-6-luna",
    );
  });

  it("omits null, undefined and empty optional parameters", async () => {
    const calls = stubFetch(json(200, { job: "review" }));

    await fetchQuotaPick({ job: "review", slices: null, lead: "", builder: undefined });
    expect(calls[0].url).toBe("/api/quota/pick?job=review");
  });

  it("encodes reserved characters in parameter values", async () => {
    const calls = stubFetch(json(200, { job: "x" }));

    await fetchQuotaPick({ job: "a&b", lead: "x/y" });
    expect(calls[0].url).toBe("/api/quota/pick?job=a%26b&lead=x%2Fy");
  });

  it.each([
    [400, "invalid quota query"],
    [503, "quota offline"],
  ])("rejects %i as a typed ApiError carrying the detail", async (status, detail) => {
    stubFetch(json(status, { detail }));

    const err = await fetchQuotaPick({ job: "review" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(status);
    expect((err as ApiError).detail).toBe(detail);
  });

  it("forwards the abort signal", async () => {
    const calls = stubFetch(json(200, { job: "review" }));
    const ctrl = new AbortController();

    await fetchQuotaPick({ job: "review" }, ctrl.signal);
    expect(calls[0].init?.signal).toBe(ctrl.signal);
  });
});

describe("log index", () => {
  it("omits sid to index every session", async () => {
    const calls = stubFetch(json(200, { entries: [], truncated: false }));

    await expect(fetchLogIndex()).resolves.toEqual({ entries: [], truncated: false });
    expect(calls[0].url).toBe("/api/logs");
  });

  it("encodes sid", async () => {
    const calls = stubFetch(json(200, { entries: [], truncated: true }));

    await fetchLogIndex("run one");
    expect(calls[0].url).toBe("/api/logs?sid=run+one");
  });

  it("returns null for an unknown session (404)", async () => {
    stubFetch(json(404, { detail: "session not found" }));

    await expect(fetchLogIndex("missing")).resolves.toBeNull();
  });

  it("rejects other failures", async () => {
    stubFetch(json(500, { detail: "boom" }));

    await expect(fetchLogIndex()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("browser requests carry no credentials", () => {
  it("never sends an authorization header", async () => {
    const calls = stubFetch(json(200, { entries: [], truncated: false }));

    await fetchLogIndex();
    await fetchQuotaUsage().catch(() => undefined);
    await fetchQuotaPick({ job: "review" }).catch(() => undefined);

    expect(calls.length).toBeGreaterThan(0);
    for (const call of calls) {
      const names = Object.keys(call.init?.headers ?? {}).map((n) => n.toLowerCase());
      expect(names).not.toContain("authorization");
      expect(call.url.startsWith("/api/")).toBe(true);
      expect(call.url).not.toMatch(/token|secret|bearer/i);
    }
  });
});
