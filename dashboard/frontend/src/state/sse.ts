// SSE plumbing (project plan §2 State and SSE).
//
// `SseConnection` wraps a single `EventSource` with an explicit reconnect loop:
// close on error, then reopen after 1, 2, 5 and 10 s (staying at 10 s), and a
// watchdog that forces a reconnect after `idleTimeoutMs` of silence. It never
// replays: every event is handed to the caller as-is.
//
// The `/api/events` connection is process-wide and ref-counted through
// `subscribeEvents`. Multiple hook consumers share one `EventSource`; on the
// session page the shared connection is reopened with `?session=<id>`.
import type {
  SessionDetail,
  SessionSummary,
} from "../api/types";
import { apiPaths } from "../api/client";
import type { ConnState } from "./types";

export const DEFAULT_BACKOFF_MS = [1000, 2000, 5000, 10000] as const;
export const WATCHDOG_MS = 30_000;

type SseEventHandler = (event: string, data: string) => void;

export type SseConnectionOptions = {
  url: string;
  onEvent: SseEventHandler;
  onConn?: (conn: ConnState) => void;
  /** Named SSE events to listen for (on top of the default `message`). */
  eventNames?: string[];
  /** Silence before a forced reconnect; `0` disables the watchdog. */
  idleTimeoutMs?: number;
  backoffMs?: readonly number[];
};

/** Delay for the nth reconnect attempt, clamped to the last entry. */
export function backoffDelay(
  attempt: number,
  backoff: readonly number[] = DEFAULT_BACKOFF_MS,
): number {
  const index = Math.min(Math.max(attempt, 0), backoff.length - 1);
  return backoff[index];
}

export class SseConnection {
  private es: EventSource | null = null;
  private attempt = 0;
  private phase: "initial" | "reconnect" = "initial";
  private closed = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly backoff: readonly number[];
  private readonly eventNames: string[];
  private readonly idleTimeoutMs: number;

  constructor(private readonly options: SseConnectionOptions) {
    this.backoff = options.backoffMs ?? DEFAULT_BACKOFF_MS;
    this.eventNames = options.eventNames ?? [];
    this.idleTimeoutMs = options.idleTimeoutMs ?? WATCHDOG_MS;
    this.open();
  }

  /** Close the connection and stop reconnecting. Idempotent. */
  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.clearRetry();
    this.clearIdle();
    const es = this.es;
    this.es = null;
    es?.close();
  }

  private setConn(conn: ConnState): void {
    this.options.onConn?.(conn);
  }

  private open(): void {
    if (this.closed) return;
    this.setConn(this.phase === "initial" ? "connecting" : "reconnecting");

    const Ctor = globalThis.EventSource as typeof EventSource | undefined;
    if (typeof Ctor !== "function") {
      // No EventSource in this environment (for example a bare test runner).
      this.scheduleRetry();
      return;
    }

    let es: EventSource;
    try {
      es = new Ctor(this.options.url);
    } catch {
      this.handleDrop();
      return;
    }
    this.es = es;

    es.onopen = () => {
      this.attempt = 0;
      this.phase = "initial";
      this.setConn("live");
      this.armIdle();
    };
    es.onmessage = (event: MessageEvent) => {
      this.touch();
      this.options.onEvent("message", event.data);
    };
    for (const name of this.eventNames) {
      es.addEventListener(name, ((event: Event) => {
        this.touch();
        this.options.onEvent(name, (event as MessageEvent).data);
      }) as EventListener);
    }
    es.onerror = () => {
      this.handleDrop();
    };
  }

  /** Unexpected close: back off, then reopen. */
  private handleDrop(): void {
    if (this.closed) return;
    this.clearIdle();
    this.clearRetry();
    const es = this.es;
    this.es = null;
    es?.close();
    this.phase = "reconnect";
    this.setConn("reconnecting");
    this.scheduleRetry();
  }

  private scheduleRetry(): void {
    if (this.closed || this.retryTimer !== null) return;
    const delay = backoffDelay(this.attempt, this.backoff);
    this.attempt += 1;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.open();
    }, delay);
  }

  /** Watchdog fired: reconnect now instead of waiting out the backoff. */
  private forceReconnect(): void {
    if (this.closed) return;
    const es = this.es;
    this.es = null;
    es?.close();
    this.clearRetry();
    this.attempt = 0;
    this.phase = "reconnect";
    this.setConn("reconnecting");
    this.open();
  }

  private touch(): void {
    this.armIdle();
  }

  private armIdle(): void {
    if (this.idleTimeoutMs <= 0) return;
    this.clearIdle();
    this.idleTimer = setTimeout(() => {
      this.idleTimer = null;
      this.forceReconnect();
    }, this.idleTimeoutMs);
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  private clearIdle(): void {
    if (this.idleTimer !== null) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }
}

// ---------------------------------------------------------------------------
// Shared `/api/events` connection
// ---------------------------------------------------------------------------

export type EventsSnapshot = {
  sessions: SessionSummary[];
  session: SessionDetail | null;
  sessionGoneId: string | null;
  conn: ConnState;
  loaded: boolean;
};

const EMPTY_SNAPSHOT: EventsSnapshot = {
  sessions: [],
  session: null,
  sessionGoneId: null,
  conn: "connecting",
  loaded: false,
};

type EventsListener = { sessionId: string | null; notify: () => void };

let snapshot: EventsSnapshot = EMPTY_SNAPSHOT;
let connection: SseConnection | null = null;
let connectionKey: string | null = null;
const listeners = new Set<EventsListener>();

function parseJson<T>(data: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
}

function emit(next: EventsSnapshot): void {
  snapshot = next;
  for (const listener of listeners) listener.notify();
}

function setConn(conn: ConnState): void {
  if (snapshot.conn === conn) return;
  emit({ ...snapshot, conn });
}

function onSessionsEvent(data: string): void {
  const payload = parseJson<{ generated_at: number; sessions: SessionSummary[] }>(data);
  if (payload === null) return;
  // Full snapshot: replace, never merge.
  emit({ ...snapshot, sessions: payload.sessions, loaded: true });
}

function onSessionEvent(data: string): void {
  const detail = parseJson<SessionDetail>(data);
  if (detail === null) return;
  emit({ ...snapshot, session: detail, sessionGoneId: null });
}

function onSessionGoneEvent(data: string): void {
  const payload = parseJson<{ id: string }>(data);
  if (payload === null) return;
  emit({ ...snapshot, session: null, sessionGoneId: payload.id });
}

function desiredKey(): string | null {
  let key: string | null = null;
  for (const listener of listeners) {
    if (listener.sessionId !== null) key = listener.sessionId;
  }
  return key;
}

function teardownConnection(): void {
  connection?.close();
  connection = null;
  connectionKey = null;
}

function ensureConnection(): void {
  if (listeners.size === 0) {
    teardownConnection();
    if (snapshot.session !== null || snapshot.sessionGoneId !== null) {
      emit({ ...snapshot, session: null, sessionGoneId: null });
    }
    return;
  }
  const key = desiredKey();
  if (connection !== null && connectionKey === key) return;
  teardownConnection();
  connectionKey = key;
  connection = new SseConnection({
    url: apiPaths.events(key ?? undefined),
    eventNames: ["sessions", "session", "session_gone", "ping"],
    onConn: setConn,
    onEvent: (event, data) => {
      if (event === "sessions") onSessionsEvent(data);
      else if (event === "session") onSessionEvent(data);
      else if (event === "session_gone") onSessionGoneEvent(data);
    },
    idleTimeoutMs: WATCHDOG_MS,
  });
}

export function getEventsSnapshot(): EventsSnapshot {
  return snapshot;
}

/**
 * Subscribe to the shared `/api/events` connection. `sessionId` requests the
 * `?session=<id>` stream; the connection is ref-counted, so two subscribers
 * share one `EventSource`. The returned function releases the subscription.
 */
export function subscribeEvents(
  sessionId: string | null,
  notify: () => void,
): () => void {
  const listener: EventsListener = { sessionId, notify };
  listeners.add(listener);
  ensureConnection();
  return () => {
    listeners.delete(listener);
    ensureConnection();
  };
}

/** Test-only: drop every subscriber and close the shared connection. */
export function __resetEventsStore(): void {
  listeners.clear();
  teardownConnection();
  snapshot = EMPTY_SNAPSHOT;
}
