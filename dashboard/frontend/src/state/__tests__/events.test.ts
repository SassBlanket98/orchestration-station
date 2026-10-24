import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionDetail, SessionSummary } from "../../api/types";
import {
  __resetEventsStore,
  getEventsSnapshot,
  subscribeEvents,
} from "../sse";
import { FakeEventSource } from "./fakeEventSource";

function summary(id: string, rev: number): SessionSummary {
  return {
    id,
    project: id,
    run: null,
    path: `/tmp/${id}/.stage`,
    rev,
    status: "active",
    counts: { running: 0, finished: 0, exited: 0, unknown: 0 },
    last_activity_at: null,
    latest_milestone: null,
    findings_total: 0,
    running_workers: [],
    lead: null,
    stream: null,
    cycle_progress: null,
    source_warnings: [],
    worker_previews: [],
    worker_total: 0,
    activity_series: [],
  };
}

function detail(id: string, rev: number): SessionDetail {
  return {
    ...summary(id, rev),
    workers: [],
    milestones: [],
    reviews: [],
    extra_logs: [],
    history: [],
    artifacts: [],
    log_index: null,
    pick_records: [],
  };
}

describe("shared /api/events connection", () => {
  beforeEach(() => {
    FakeEventSource.reset();
    __resetEventsStore();
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => {
    __resetEventsStore();
    vi.unstubAllGlobals();
  });

  it("ref-counts one EventSource across two consumers", () => {
    const un1 = subscribeEvents(null, () => {});
    const un2 = subscribeEvents(null, () => {});
    expect(FakeEventSource.instances.length).toBe(1);

    un1();
    expect(FakeEventSource.instances.length).toBe(1);
    expect(FakeEventSource.instances[0].closed).toBe(false);

    un2();
    expect(FakeEventSource.instances.length).toBe(1);
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  it("replaces the sessions snapshot wholesale on every event", () => {
    let notifications = 0;
    const un = subscribeEvents(null, () => {
      notifications += 1;
    });
    const es = FakeEventSource.last();

    es.emit(
      "sessions",
      JSON.stringify({ generated_at: 1, sessions: [summary("a", 1)] }),
    );
    expect(getEventsSnapshot().sessions.map((s) => s.id)).toEqual(["a"]);
    expect(getEventsSnapshot().loaded).toBe(true);

    // A second snapshot must replace the first, not merge with it.
    es.emit(
      "sessions",
      JSON.stringify({ generated_at: 2, sessions: [summary("b", 2)] }),
    );
    expect(getEventsSnapshot().sessions.map((s) => s.id)).toEqual(["b"]);
    expect(notifications).toBe(2);

    un();
  });

  it("reopens with ?session=<id> when a session consumer subscribes", () => {
    const unOverview = subscribeEvents(null, () => {});
    expect(FakeEventSource.instances.length).toBe(1);
    expect(FakeEventSource.last().url).not.toContain("session=");

    const unSession = subscribeEvents("quota-service", () => {});
    expect(FakeEventSource.instances.length).toBe(2);
    expect(FakeEventSource.instances[0].closed).toBe(true);
    expect(FakeEventSource.last().url).toContain("session=quota-service");

    unSession();
    // Back to the session-less stream for the remaining consumer.
    expect(FakeEventSource.instances.length).toBe(3);
    expect(FakeEventSource.last().url).not.toContain("session=");

    unOverview();
  });

  it("stores and clears the session detail on session and session_gone", () => {
    const un = subscribeEvents("quota-service", () => {});
    const es = FakeEventSource.last();

    es.emit("session", JSON.stringify(detail("quota-service", 7)));
    expect(getEventsSnapshot().session?.rev).toBe(7);
    expect(getEventsSnapshot().sessionGoneId).toBeNull();

    es.emit("session_gone", JSON.stringify({ id: "quota-service" }));
    expect(getEventsSnapshot().session).toBeNull();
    expect(getEventsSnapshot().sessionGoneId).toBe("quota-service");

    un();
  });

  it("ignores malformed payloads", () => {
    const un = subscribeEvents(null, () => {});
    const es = FakeEventSource.last();
    es.emit("sessions", "not json");
    expect(getEventsSnapshot().sessions).toEqual([]);
    expect(getEventsSnapshot().loaded).toBe(false);
    un();
  });
});
