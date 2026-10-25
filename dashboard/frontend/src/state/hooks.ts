// Data layer hooks (project plan §2 State and SSE).
//
// In `?mock=1` mode every hook returns the F0 fixtures, so pages and demos run
// with no backend. Otherwise the hooks use the typed client in api/client.ts
// plus the SSE plumbing in state/sse.ts.
//
// Every data hook also returns `{ data, loading, error, reload }`:
//   loading -> true from the first render until the first request settles, and
//              again while a reload or poll is in flight.
//   error   -> null, or the Error from the last failed request.
//   reload  -> re-requests. The SSE-backed hooks have nothing to re-request, so
//              their reload is a no-op; their error is set only when the stream
//              drops before its first update.
//
// Frozen signatures (existing fields kept, the rest are additions):
//   useSessions()                -> { sessions, conn, data, loading, error, reload }
//   useSession(id)               -> { session, conn, notFound, data, loading, error, reload }
//   useWorkerDetail(sid, name)   -> { data, loading, error, reload }
//   useLogStream(sid, stem, live)-> { lines, format, truncated, conn }
//   useLogIndex(sid?)            -> { data, loading, error, reload, mock }
//   useQuotaUsage()              -> { data, loading, error, reload, mock }   (polls)
//   useQuotaPick(job, ...)       -> { data, loading, error, reload, mock }   (on demand)
//   useMonitor()             -> { data, loading, error, reload, mock }   (polls)
//   useMonitorStage(path)    -> { data, loading, error, reload, mock }   (on demand, no poll)
import {
  useCallback,
  useEffect,
  useReducer,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import type {
  MonitorResponse,
  MonitorStageResponse,
  LogChunk,
  LogFormat,
  LogLine,
  PickResult,
  QuotaUsage,
  SessionDetail,
  SessionSummary,
  Worker,
  WorkerDetail,
} from "../api/types";
import {
  type LogIndexResponse,
  type QuotaPickQuery,
  apiPaths,
  fetchMonitor,
  fetchMonitorStage,
  fetchLog,
  fetchLogIndex,
  fetchQuotaPick,
  fetchQuotaUsage,
  fetchWorkerDetail,
  isMockMode,
} from "../api/client";
import { demoSessions } from "../mocks/demo";
import {
  MOCK_NOW_LINES,
  fixtureSessionIds,
  logFixture,
  sessionFixture,
  workerDetailFixture,
} from "../mocks/fixtures";
import {
  type LogStreamState,
  applyAppend,
  applyInit,
  applyReset,
  capLines,
  emptyLogStream,
} from "./logstream";
import {
  type EventsSnapshot,
  SseConnection,
  getEventsSnapshot,
  subscribeEvents,
} from "./sse";
import type { ConnState } from "./types";

export type { ConnState } from "./types";

const TICK_MS = 2000;

/** Usage polling interval; paused while the tab is hidden. */
export const QUOTA_POLL_MS = 15_000;

// A single module-level ticker drives the rotating now-line in mock mode. All
// subscribers re-read the same counter, so every hook stays in step.
let tick = 0;
const tickListeners = new Set<() => void>();

if (typeof window !== "undefined") {
  window.setInterval(() => {
    tick += 1;
    for (const listener of tickListeners) listener();
  }, TICK_MS);
}

function subscribeTick(listener: () => void): () => void {
  tickListeners.add(listener);
  return () => {
    tickListeners.delete(listener);
  };
}

function getTick(): number {
  return tick;
}

/** Re-render on each ticker beat; used to rotate the mock now-line. */
export function useTick(): number {
  return useSyncExternalStore(subscribeTick, getTick, getTick);
}

/** Subscribe to the shared `/api/events` snapshot (no-op in mock mode). */
function useEvents(sessionId: string | null, leadSession: string | null = null): EventsSnapshot {
  const mock = isMockMode();
  const subscribe = useCallback(
    (notify: () => void) =>
      mock ? () => {} : subscribeEvents(sessionId, notify, leadSession),
    [mock, sessionId, leadSession],
  );
  return useSyncExternalStore(subscribe, getEventsSnapshot, getEventsSnapshot);
}

function parseJson<T>(data: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Request state machine (pure; the keyed hook below is a thin wrapper)
// ---------------------------------------------------------------------------

export type RequestState<T> = {
  /** The request key this state belongs to; `null` means nothing is requested. */
  key: string | null;
  data: T | null;
  loading: boolean;
  error: Error | null;
};

export type RequestAction<T> =
  | { type: "start"; key: string }
  | { type: "success"; key: string; data: T }
  | { type: "failure"; key: string; error: Error };

export type RequestResult<T> = {
  data: T | null;
  loading: boolean;
  error: Error | null;
  reload: () => void;
};

export const IDLE_REQUEST_STATE: RequestState<never> = {
  key: null,
  data: null,
  loading: false,
  error: null,
};

/**
 * Transitions for one keyed request. A `start` for a new key drops the old
 * data. A `start` for the same key (reload, poll) keeps it until the next
 * result. Results for any other key are stale and ignored.
 */
export function requestReducer<T>(
  state: RequestState<T>,
  action: RequestAction<T>,
): RequestState<T> {
  if (action.type === "start") {
    if (action.key !== state.key) {
      return { key: action.key, data: null, loading: true, error: null };
    }
    return { ...state, loading: true, error: null };
  }
  if (action.key !== state.key) return state;
  if (action.type === "success") {
    return { key: state.key, data: action.data, loading: false, error: null };
  }
  return { ...state, loading: false, error: action.error };
}

/**
 * The state to report for `key` during the render that changes it, before the
 * effect has dispatched `start`. A new non-null key already reads as loading.
 */
export function requestView<T>(
  state: RequestState<T>,
  key: string | null,
): RequestState<T> {
  if (state.key === key) return state;
  return { key, data: null, loading: key !== null, error: null };
}

function toError(err: unknown): Error {
  return err instanceof Error ? err : new Error(String(err));
}

/**
 * One keyed request. `key` null means nothing to request. `pollMs` re-requests
 * on that interval while the tab is visible, and once more when it becomes
 * visible again.
 */
function useRequest<T>(
  key: string | null,
  request: (signal: AbortSignal) => Promise<T>,
  pollMs: number | null = null,
): RequestResult<T> {
  const [state, dispatch] = useReducer(requestReducer<T>, IDLE_REQUEST_STATE as RequestState<T>);
  const [nonce, setNonce] = useState(0);
  const requestRef = useRef(request);

  useEffect(() => {
    requestRef.current = request;
  });

  useEffect(() => {
    if (key === null) return;
    const ctrl = new AbortController();
    dispatch({ type: "start", key });
    requestRef.current(ctrl.signal).then(
      (data) => {
        if (!ctrl.signal.aborted) dispatch({ type: "success", key, data });
      },
      (err: unknown) => {
        if (!ctrl.signal.aborted) {
          dispatch({ type: "failure", key, error: toError(err) });
        }
      },
    );
    return () => ctrl.abort();
  }, [key, nonce]);

  useEffect(() => {
    if (key === null || pollMs === null) return;
    const refreshIfVisible = () => {
      if (!document.hidden) setNonce((n) => n + 1);
    };
    const timer = window.setInterval(refreshIfVisible, pollMs);
    document.addEventListener("visibilitychange", refreshIfVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshIfVisible);
    };
  }, [key, pollMs]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const view = requestView(state, key);
  return { data: view.data, loading: view.loading, error: view.error, reload };
}

const noopReload = (): void => {};

/** Result for a hook that reads fixtures in mock mode. */
function mockResult<T>(data: T | null): RequestResult<T> {
  return { data, loading: false, error: null, reload: noopReload };
}

const STREAM_LOST = new Error("Live connection lost before the first update");

/** Result for a hook fed by the shared SSE snapshot. */
function streamResult<T>(
  data: T | null,
  settled: boolean,
  conn: ConnState,
): RequestResult<T> {
  const lost = !settled && conn === "reconnecting";
  return {
    data,
    loading: !settled && !lost,
    error: lost ? STREAM_LOST : null,
    reload: noopReload,
  };
}

// ---------------------------------------------------------------------------
// Mock-mode helpers (F0 fixture rotation)
// ---------------------------------------------------------------------------

function rotateNow(worker: Worker, at: number): Worker {
  if (worker.status !== "running" || worker.now === null) return worker;
  if (worker.flags.includes("stale")) return worker;
  const text = MOCK_NOW_LINES[at % MOCK_NOW_LINES.length];
  if (text === worker.now.text) return worker;
  return { ...worker, now: { ...worker.now, text } };
}

/** §3 order: running by started_at desc, then rest by finished_at desc, nulls last. */
function sortWorkers(workers: Worker[]): Worker[] {
  const desc = (a: number | null, b: number | null) =>
    (b ?? -Infinity) - (a ?? -Infinity);
  return [...workers].sort((a, b) => {
    const ar = a.status === "running";
    const br = b.status === "running";
    if (ar !== br) return ar ? -1 : 1;
    if (ar && br) return desc(a.started_at, b.started_at);
    return desc(a.finished_at, b.finished_at);
  });
}

function withRotation(detail: SessionDetail, at: number): SessionDetail {
  return {
    ...detail,
    workers: sortWorkers(detail.workers).map((w) => rotateNow(w, at)),
    running_workers: detail.running_workers.map((rw) => {
      const worker = detail.workers.find((w) => w.name === rw.name);
      if (!worker || worker.flags.includes("stale")) return rw;
      const text = MOCK_NOW_LINES[at % MOCK_NOW_LINES.length];
      return { ...rw, now: rw.now ? { ...rw.now, text } : rw.now };
    }),
  };
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export type SessionsResult = RequestResult<SessionSummary[]> & {
  sessions: SessionSummary[];
  conn: ConnState;
};

export function useSessions(): SessionsResult {
  const at = useTick();
  const events = useEvents(null);
  if (isMockMode()) {
    const sessions = demoSessions(at, Date.now() / 1000);
    return { sessions, conn: "live", ...mockResult(sessions) };
  }
  return {
    sessions: events.sessions,
    conn: events.conn,
    ...streamResult(
      events.loaded ? events.sessions : null,
      events.loaded,
      events.conn,
    ),
  };
}

export type SessionResult = RequestResult<SessionDetail> & {
  session: SessionDetail | null;
  conn: ConnState;
  notFound: boolean;
};

export function useSession(id: string, leadSession: string | null = null): SessionResult {
  const at = useTick();
  const events = useEvents(id, leadSession);
  if (isMockMode()) {
    // Demo overview ids have no detail fixture; show the first fixture so run links still open.
    const base = sessionFixture(id) ?? sessionFixture(fixtureSessionIds[0]);
    if (base === null) {
      return {
        session: null,
        conn: "live",
        notFound: true,
        ...mockResult<SessionDetail>(null),
      };
    }
    const rotated = withRotation(base, at);
    return {
      session: rotated,
      conn: "live",
      notFound: false,
      ...mockResult(rotated),
    };
  }
  const session =
    events.session !== null && events.session.id === id ? events.session : null;
  const notFound =
    events.sessionGoneId === id ||
    (session === null &&
      events.loaded &&
      !events.sessions.some((s) => s.id === id));
  return {
    session,
    conn: events.conn,
    notFound,
    ...streamResult(session, session !== null || notFound, events.conn),
  };
}

/** `data` is null for an unknown worker (404) as well as before the first settle. */
export function useWorkerDetail(
  sid: string,
  name: string,
  leadSession: string | null = null,
): RequestResult<WorkerDetail | null> {
  const at = useTick();
  const mock = isMockMode();
  const key = mock || name === "" ? null : JSON.stringify([sid, name, leadSession]);
  const request = useRequest(key, (signal) =>
    fetchWorkerDetail(sid, name, signal, leadSession),
  );
  if (mock) {
    const data = workerDetailFixture(sid, name);
    if (data === null) return mockResult<WorkerDetail>(null);
    return mockResult({ ...data, worker: rotateNow(data.worker, at) });
  }
  return request;
}

export function useLogStream(
  sid: string,
  stem: string,
  live: boolean,
): {
  lines: LogLine[];
  format: LogFormat;
  truncated: boolean;
  conn: ConnState;
} {
  const [state, setState] = useState<LogStreamState & { conn: ConnState }>({
    ...emptyLogStream,
    conn: "connecting",
  });

  useEffect(() => {
    if (isMockMode()) return;

    if (!live) {
      const ctrl = new AbortController();
      setState((s) => ({ ...s, conn: "connecting" }));
      fetchLog(sid, stem, 64, ctrl.signal)
        .then((chunk) =>
          setState({
            lines: capLines(chunk.lines),
            format: chunk.format,
            truncated: chunk.truncated,
            conn: "live",
          }),
        )
        .catch((err: unknown) => {
          if ((err as Error | undefined)?.name !== "AbortError") {
            setState((s) => ({ ...s, conn: "reconnecting" }));
          }
        });
      return () => ctrl.abort();
    }

    setState((s) => ({ ...s, conn: "connecting" }));
    const conn = new SseConnection({
      url: apiPaths.logStream(sid, stem),
      eventNames: ["init", "append", "reset", "ping"],
      onConn: (c) => setState((s) => ({ ...s, conn: c })),
      onEvent: (event, data) => {
        if (event === "init") {
          const chunk = parseJson<LogChunk>(data);
          if (chunk === null) return;
          setState((s) => ({ ...applyInit(s, chunk), conn: "live" }));
        } else if (event === "append") {
          const appended = parseJson<{ offset: number; lines: LogLine[] }>(data);
          if (appended === null) return;
          setState((s) => ({ ...s, ...applyAppend(s, appended) }));
        } else if (event === "reset") {
          setState((s) => ({ ...s, ...applyReset(s) }));
        }
      },
    });
    return () => conn.close();
  }, [sid, stem, live]);

  if (isMockMode()) {
    const chunk = logFixture(sid, stem);
    return {
      lines: chunk.lines,
      format: chunk.format,
      truncated: chunk.truncated,
      conn: "live",
    };
  }
  return {
    lines: state.lines,
    format: state.format,
    truncated: state.truncated,
    conn: state.conn,
  };
}

/** Artifact index for one session, or for every session when `sid` is omitted. */
export function useLogIndex(
  sid?: string,
): RequestResult<LogIndexResponse | null> & { mock: boolean } {
  const mock = isMockMode();
  const request = useRequest(
    mock ? null : JSON.stringify(sid ?? null),
    (signal) => fetchLogIndex(sid, signal),
  );
  return { ...(mock ? mockResult<LogIndexResponse>(null) : request), mock };
}

/** Monitor polling interval; paused while the tab is hidden. */
export const MONITOR_POLL_MS = 3_000;

/** Monitor stage tree, polled every MONITOR_POLL_MS while the tab is visible. */
export function useMonitor(): RequestResult<MonitorResponse> & { mock: boolean } {
  const mock = isMockMode();
  const request = useRequest(
    mock ? null : "monitor",
    (signal) => fetchMonitor(signal),
    MONITOR_POLL_MS,
  );
  return { ...(mock ? mockResult<MonitorResponse>(null) : request), mock };
}

/**
 * One stage's milestone list, requested once when `path` becomes non-null (no polling).
 * `path` null means nothing to request.
 */
export function useMonitorStage(
  path: string | null,
): RequestResult<MonitorStageResponse> & { mock: boolean } {
  const mock = isMockMode();
  const key = mock || !path ? null : path;
  const request = useRequest(key, (signal) =>
    fetchMonitorStage(path ?? "", signal),
  );
  return { ...(mock ? mockResult<MonitorStageResponse>(null) : request), mock };
}

/** quota service usage, polled every QUOTA_POLL_MS while the tab is visible. */
export function useQuotaUsage(): RequestResult<QuotaUsage> & { mock: boolean } {
  const mock = isMockMode();
  const request = useRequest(
    mock ? null : "usage",
    (signal) => fetchQuotaUsage(signal),
    QUOTA_POLL_MS,
  );
  return { ...(mock ? mockResult<QuotaUsage>(null) : request), mock };
}

/**
 * Read-only picker preview. Requests once per distinct argument set and on
 * `reload()`; `job` null means no request. Errors arrive as `ApiError`.
 */
export function useQuotaPick(
  job: string | null,
  slices?: number | null,
  lead?: string | null,
  builder?: string | null,
): RequestResult<PickResult> & { mock: boolean } {
  const mock = isMockMode();
  const key =
    mock || !job
      ? null
      : JSON.stringify([job, slices ?? null, lead ?? null, builder ?? null]);
  const request = useRequest(key, (signal) => {
    const query: QuotaPickQuery = {
      job: job ?? "",
      slices,
      lead,
      builder,
    };
    return fetchQuotaPick(query, signal);
  });
  return { ...(mock ? mockResult<PickResult>(null) : request), mock };
}
