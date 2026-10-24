import { afterEach, describe, expect, it, vi } from "vitest";
import { getOldestStale, reportResponse, subscribe } from "./staleStore";
import { STALE_HEADER } from "./staleness";

const SESSIONS = "/api/sessions";
const USAGE = "/api/quota/usage";

function respond(value?: string): Response {
  const headers = new Headers();
  if (value !== undefined) headers.set(STALE_HEADER, value);
  return new Response("{}", { headers });
}

afterEach(() => {
  reportResponse(SESSIONS, respond());
  reportResponse(USAGE, respond());
});

describe("staleStore", () => {
  it("is null while every response is fresh", () => {
    reportResponse(SESSIONS, respond());
    expect(getOldestStale()).toBeNull();
  });

  it("reports the oldest cached timestamp across paths", () => {
    reportResponse(SESSIONS, respond("1000"));
    reportResponse(USAGE, respond("500"));
    expect(getOldestStale()).toBe(500);
  });

  it("clears a path when its response is fresh again", () => {
    reportResponse(SESSIONS, respond("1000"));
    reportResponse(USAGE, respond("500"));
    reportResponse(USAGE, respond());
    expect(getOldestStale()).toBe(1000);
    reportResponse(SESSIONS, respond());
    expect(getOldestStale()).toBeNull();
  });

  it("notifies subscribers only when a path's stale state changes", () => {
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    reportResponse(SESSIONS, respond());
    expect(listener).not.toHaveBeenCalled();
    reportResponse(SESSIONS, respond("1000"));
    reportResponse(SESSIONS, respond("1000"));
    expect(listener).toHaveBeenCalledTimes(1);
    reportResponse(SESSIONS, respond());
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    reportResponse(SESSIONS, respond("2000"));
    expect(listener).toHaveBeenCalledTimes(2);
  });
});
