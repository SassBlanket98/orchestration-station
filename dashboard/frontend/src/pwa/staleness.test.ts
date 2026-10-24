import { describe, expect, it } from "vitest";
import { STALE_HEADER, staleLabel, staleSince } from "./staleness";

function respond(value?: string): Response {
  const headers = new Headers();
  if (value !== undefined) headers.set(STALE_HEADER, value);
  return new Response("{}", { headers });
}

describe("staleSince", () => {
  it("is null for a fresh response without the header", () => {
    expect(staleSince(respond())).toBeNull();
  });

  it("reads the epoch ms the service worker stored", () => {
    expect(staleSince(respond("1792800000000"))).toBe(1792800000000);
  });

  it("tolerates surrounding whitespace", () => {
    expect(staleSince(respond(" 1700000000000 "))).toBe(1700000000000);
  });

  it("rejects values that are not a whole number of ms", () => {
    expect(staleSince(respond(""))).toBeNull();
    expect(staleSince(respond("abc"))).toBeNull();
    expect(staleSince(respond("-5"))).toBeNull();
    expect(staleSince(respond("1.5"))).toBeNull();
    expect(staleSince(respond("1e12"))).toBeNull();
  });
});

describe("staleLabel", () => {
  const now = 1_800_000_000_000;

  it("shows minutes for a few minutes old data", () => {
    expect(staleLabel(now - 4 * 60_000, now)).toBe("offline · data from 4m ago");
  });

  it("shows seconds under a minute and rounds down", () => {
    expect(staleLabel(now - 30_000, now)).toBe("offline · data from 30s ago");
    expect(staleLabel(now - 59_999, now)).toBe("offline · data from 59s ago");
  });

  it("moves to minutes at exactly 60s", () => {
    expect(staleLabel(now - 60_000, now)).toBe("offline · data from 1m ago");
  });

  it("shows hours and then days", () => {
    expect(staleLabel(now - 2 * 3_600_000, now)).toBe("offline · data from 2h ago");
    expect(staleLabel(now - 23 * 3_600_000, now)).toBe("offline · data from 23h ago");
    expect(staleLabel(now - 3 * 86_400_000, now)).toBe("offline · data from 3d ago");
  });

  it("treats a timestamp in the future as just cached", () => {
    expect(staleLabel(now + 10_000, now)).toBe("offline · data from 0s ago");
  });
});
