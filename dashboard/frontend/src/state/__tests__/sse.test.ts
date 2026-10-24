import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SseConnection, backoffDelay } from "../sse";
import type { ConnState } from "../types";
import { FakeEventSource } from "./fakeEventSource";

function connect(options?: {
  eventNames?: string[];
  idleTimeoutMs?: number;
}): SseConnection {
  return new SseConnection({
    url: "/api/events",
    onEvent: () => {},
    eventNames: options?.eventNames,
    idleTimeoutMs: options?.idleTimeoutMs,
  });
}

describe("backoffDelay", () => {
  it("climbs 1, 2, 5, 10 and then stays at 10 seconds", () => {
    expect(backoffDelay(0)).toBe(1000);
    expect(backoffDelay(1)).toBe(2000);
    expect(backoffDelay(2)).toBe(5000);
    expect(backoffDelay(3)).toBe(10000);
    expect(backoffDelay(4)).toBe(10000);
    expect(backoffDelay(99)).toBe(10000);
  });
});

describe("SseConnection reconnect backoff", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.reset();
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reopens after 1, 2, 5, 10, then 10 s on repeated failures", () => {
    const conn = connect();
    expect(FakeEventSource.instances.length).toBe(1);

    FakeEventSource.last().fireError();
    vi.advanceTimersByTime(999);
    expect(FakeEventSource.instances.length).toBe(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances.length).toBe(2);

    FakeEventSource.last().fireError();
    vi.advanceTimersByTime(1999);
    expect(FakeEventSource.instances.length).toBe(2);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances.length).toBe(3);

    FakeEventSource.last().fireError();
    vi.advanceTimersByTime(5000);
    expect(FakeEventSource.instances.length).toBe(4);

    FakeEventSource.last().fireError();
    vi.advanceTimersByTime(10000);
    expect(FakeEventSource.instances.length).toBe(5);

    // Clamped: every further failure still waits 10 s.
    FakeEventSource.last().fireError();
    vi.advanceTimersByTime(9999);
    expect(FakeEventSource.instances.length).toBe(5);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances.length).toBe(6);
    FakeEventSource.last().fireError();
    vi.advanceTimersByTime(10000);
    expect(FakeEventSource.instances.length).toBe(7);

    conn.close();
  });

  it("resets the backoff after a successful open", () => {
    const conn = connect();
    FakeEventSource.last().fireError();
    vi.advanceTimersByTime(1000);
    expect(FakeEventSource.instances.length).toBe(2);

    FakeEventSource.last().fireOpen();
    FakeEventSource.last().fireError();
    vi.advanceTimersByTime(999);
    expect(FakeEventSource.instances.length).toBe(2);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances.length).toBe(3);

    conn.close();
  });

  it("reports connecting, then live, then reconnecting", () => {
    const states: ConnState[] = [];
    const conn = new SseConnection({
      url: "/api/events",
      onEvent: () => {},
      onConn: (c) => states.push(c),
    });
    FakeEventSource.last().fireOpen();
    FakeEventSource.last().fireError();
    expect(states).toEqual(["connecting", "live", "reconnecting"]);
    conn.close();
  });

  it("stops reconnecting once closed", () => {
    const conn = connect();
    const first = FakeEventSource.last();
    conn.close();
    expect(first.closed).toBe(true);
    first.fireError();
    vi.advanceTimersByTime(60000);
    expect(FakeEventSource.instances.length).toBe(1);
  });
});

describe("SseConnection watchdog", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.reset();
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("forces a reconnect after 30 s of silence", () => {
    const conn = connect();
    const first = FakeEventSource.instances[0];
    first.fireOpen();

    vi.advanceTimersByTime(29999);
    expect(FakeEventSource.instances.length).toBe(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances.length).toBe(2);
    expect(first.closed).toBe(true);

    conn.close();
  });

  it("resets the watchdog whenever an event arrives", () => {
    const conn = connect();
    const first = FakeEventSource.instances[0];
    first.fireOpen();

    vi.advanceTimersByTime(20000);
    first.emit("message", "{}");
    vi.advanceTimersByTime(29999);
    expect(FakeEventSource.instances.length).toBe(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances.length).toBe(2);

    conn.close();
  });

  it("can be disabled with idleTimeoutMs 0", () => {
    const conn = connect({ idleTimeoutMs: 0 });
    FakeEventSource.instances[0].fireOpen();
    vi.advanceTimersByTime(120000);
    expect(FakeEventSource.instances.length).toBe(1);
    conn.close();
  });
});
