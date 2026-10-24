// Frontend-only state types. `ConnState` is not part of the frozen §3 contract
// (it is the SSE connection state from §2), so it lives here rather than in
// api/types.ts.
export type ConnState = "connecting" | "live" | "reconnecting";
