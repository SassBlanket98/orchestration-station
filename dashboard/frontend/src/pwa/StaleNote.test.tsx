import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaleNote } from "./StaleNote";
import { reportResponse } from "./staleStore";
import { STALE_HEADER } from "./staleness";

const NOW = 1_800_000_000_000;
const SESSIONS = "/api/sessions";

function respond(value?: string): Response {
  const headers = new Headers();
  if (value !== undefined) headers.set(STALE_HEADER, value);
  return new Response("{}", { headers });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  reportResponse(SESSIONS, respond());
  vi.useRealTimers();
});

describe("StaleNote", () => {
  it("renders nothing while every response is fresh", () => {
    expect(renderToStaticMarkup(<StaleNote />)).toBe("");
  });

  it("says how old the cached data is", () => {
    reportResponse(SESSIONS, respond(String(NOW - 4 * 60_000)));
    expect(renderToStaticMarkup(<StaleNote />)).toBe(
      '<div class="stale-note" role="status">offline · data from 4m ago</div>',
    );
  });
});
