// Which body the worker modal shows while its detail loads. The hook reports a
// failed request and a request not yet started the same way (data null, loading
// false), so the caller passes `failed` only after it has seen a request in flight.

export type WorkerBodyState = "loaded" | "missing" | "loading" | "failed";

export interface WorkerBodyInput {
  /** Detail is available, from the live hook or the mock fixture. */
  hasView: boolean;
  /** The session has loaded and lists workers. */
  sessionKnown: boolean;
  /** The worker's name appears in the session's worker list. */
  inSession: boolean;
  /** A detail request has settled with no data after it was in flight. */
  failed: boolean;
}

/** Loaded wins; a known session without the worker is "missing"; then failed; otherwise loading. */
export function workerBodyState(input: WorkerBodyInput): WorkerBodyState {
  if (input.hasView) return "loaded";
  if (input.sessionKnown && !input.inSession) return "missing";
  if (input.failed) return "failed";
  return "loading";
}

/** Explain the run listing only when the loaded session confirms it. */
export function workerUnavailableHint(sessionKnown: boolean, inSession: boolean): string | undefined {
  if (!sessionKnown || !inSession) return undefined;
  return "The run lists this worker, but its detail did not load.";
}
