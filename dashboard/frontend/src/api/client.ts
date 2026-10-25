// Typed HTTP client for the stagewatch API (project plan §3).
//
// F0 ships this complete but the state stubs do not call it yet; F1 wires the
// hooks to it. Paths are relative so the Vite dev proxy (see vite.config.ts)
// and the production SPA mount both work. The browser never sends, stores or
// reads a bearer: quota service credentials stay on the server behind /api/quota.
import type {
  MonitorResponse,
  MonitorStageResponse,
  LogChunk,
  LogIndexEntry,
  PickResult,
  QuotaUsage,
  SessionDetail,
  SessionSummary,
  WorkerDetail,
} from "./types";
import { reportResponse } from "../pwa/staleStore";

const API_ROOT = "/api";

/** True when the URL carries `?mock=1`, or when this is the artifact build. */
export function isMockMode(): boolean {
  if (import.meta.env.VITE_ARTIFACT === "1") return true;
  if (typeof window === "undefined") return true;
  return new URLSearchParams(window.location.search).get("mock") === "1";
}

export class ApiError extends Error {
  readonly status: number;
  /** The server's `detail` message when the error body carried one. */
  readonly detail: string | null;
  constructor(status: number, message: string, detail: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Parsed JSON body, or `null` when the body is empty or not JSON. */
async function readBody(res: Response): Promise<unknown> {
  return res.json().catch(() => null);
}

function failure(res: Response, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  return new ApiError(
    res.status,
    `${res.status} ${res.statusText}`,
    typeof detail === "string" ? detail : null,
  );
}

/** Endpoints whose stale-cache header feeds the offline note (see pwa/staleStore). */
const STALE_TRACKED_URLS = new Set([
  `${API_ROOT}/monitor`,
  `${API_ROOT}/quota/usage`,
  `${API_ROOT}/sessions`,
]);

async function fetchApi(path: string, signal?: AbortSignal): Promise<Response> {
  const url = `${API_ROOT}${path}`;
  const res = await fetch(url, {
    headers: { accept: "application/json" },
    signal,
  });
  if (STALE_TRACKED_URLS.has(url)) reportResponse(url, res);
  return res;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetchApi(path, signal);
  if (!res.ok) throw failure(res, await readBody(res));
  return (await res.json()) as T;
}

export type SessionsResponse = {
  generated_at: number;
  sessions: SessionSummary[];
};

export function fetchSessions(signal?: AbortSignal): Promise<SessionsResponse> {
  return getJson<SessionsResponse>("/sessions", signal);
}

/**
 * Returns `null` when the session is unknown (HTTP 404).
 * `leadSession` names the lead the overview opened this run from; it is sent as `lead_session` when present.
 */
export async function fetchSession(
  sid: string,
  signal?: AbortSignal,
  leadSession: string | null = null,
): Promise<SessionDetail | null> {
  const query = leadSession ? `?${new URLSearchParams({ lead_session: leadSession })}` : "";
  try {
    return await getJson<SessionDetail>(
      `/sessions/${encodeURIComponent(sid)}${query}`,
      signal,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

/** Returns `null` when the worker is unknown (HTTP 404). */
export async function fetchWorkerDetail(
  sid: string,
  name: string,
  signal?: AbortSignal,
): Promise<WorkerDetail | null> {
  try {
    return await getJson<WorkerDetail>(
      `/sessions/${encodeURIComponent(sid)}/workers/${encodeURIComponent(name)}`,
      signal,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export function fetchLog(
  sid: string,
  stem: string,
  tailKb = 64,
  signal?: AbortSignal,
): Promise<LogChunk> {
  const q = new URLSearchParams({ tail_kb: String(tailKb) });
  return getJson<LogChunk>(
    `/sessions/${encodeURIComponent(sid)}/logs/${encodeURIComponent(stem)}?${q}`,
    signal,
  );
}

/**
 * quota service usage through the server proxy. When the server is offline it
 * answers 503 with a QuotaUsage body (`offline: true`); that body is returned
 * as data, so callers render the offline state instead of an error.
 */
export async function fetchQuotaUsage(signal?: AbortSignal): Promise<QuotaUsage> {
  const res = await fetchApi("/quota/usage", signal);
  const body = await readBody(res);
  if (res.ok) return body as QuotaUsage;
  const offline = body as Partial<QuotaUsage> | null;
  if (res.status === 503 && offline?.offline === true) return offline as QuotaUsage;
  throw failure(res, body);
}

/** Monitor stage tree. The server answers 200 even when Monitor is down; `online` says which. */
export function fetchMonitor(signal?: AbortSignal): Promise<MonitorResponse> {
  return getJson<MonitorResponse>("/monitor", signal);
}

/** One stage's milestones. The server answers 200 even when Monitor is down; `online` says which. */
export function fetchMonitorStage(
  path: string,
  signal?: AbortSignal,
): Promise<MonitorStageResponse> {
  const q = new URLSearchParams({ path });
  return getJson<MonitorStageResponse>(`/monitor/stage?${q}`, signal);
}

export type QuotaPickQuery = {
  job: string;
  slices?: number | null;
  lead?: string | null;
  builder?: string | null;
};

/** Read-only picker preview. A 400 or 503 rejects with `ApiError` (status and `detail`). */
export function fetchQuotaPick(
  query: QuotaPickQuery,
  signal?: AbortSignal,
): Promise<PickResult> {
  const params = new URLSearchParams({ job: query.job });
  if (query.slices != null) params.set("slices", String(query.slices));
  if (query.lead) params.set("lead", query.lead);
  if (query.builder) params.set("builder", query.builder);
  return getJson<PickResult>(`/quota/pick?${params}`, signal);
}

export type LogIndexResponse = {
  entries: LogIndexEntry[];
  truncated: boolean;
};

/**
 * Bounded artifact index. Omit `sid` for every session. Returns `null` when
 * the named session is unknown (HTTP 404).
 */
export async function fetchLogIndex(
  sid?: string,
  signal?: AbortSignal,
): Promise<LogIndexResponse | null> {
  const path =
    sid === undefined ? "/logs" : `/logs?${new URLSearchParams({ sid })}`;
  try {
    return await getJson<LogIndexResponse>(path, signal);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export const apiPaths = {
  health: "/api/health",
  sessions: "/api/sessions",
  session: (sid: string) => `/api/sessions/${encodeURIComponent(sid)}`,
  worker: (sid: string, name: string) =>
    `/api/sessions/${encodeURIComponent(sid)}/workers/${encodeURIComponent(name)}`,
  log: (sid: string, stem: string, tailKb = 64) =>
    `/api/sessions/${encodeURIComponent(sid)}/logs/${encodeURIComponent(stem)}?tail_kb=${tailKb}`,
  logStream: (sid: string, stem: string, tailKb = 64) =>
    `/api/sessions/${encodeURIComponent(sid)}/logs/${encodeURIComponent(stem)}/stream?tail_kb=${tailKb}`,
  events: (sid?: string, leadSession?: string) =>
    sid
      ? `/api/events?session=${encodeURIComponent(sid)}` +
        (leadSession ? `&lead_session=${encodeURIComponent(leadSession)}` : "")
      : "/api/events",
} as const;
