import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatDuration,
  formatElapsed,
  formatTimeAgo,
  secondsSince,
  statusLabel,
} from "./format";

describe("secondsSince", () => {
  it("never goes negative", () => {
    expect(secondsSince(100, 90)).toBe(0);
    expect(secondsSince(90, 100)).toBe(10);
  });
});

describe("formatTimeAgo", () => {
  const now = 1_000_000;
  it("renders a dash for unknown", () => {
    expect(formatTimeAgo(null, now)).toBe("\u2014");
  });
  it("counts up through the units", () => {
    expect(formatTimeAgo(now, now)).toBe("now");
    expect(formatTimeAgo(now - 3, now)).toBe("3s ago");
    expect(formatTimeAgo(now - 5 * 60, now)).toBe("5m ago");
    expect(formatTimeAgo(now - 2 * 3600, now)).toBe("2h ago");
    expect(formatTimeAgo(now - 4 * 86400, now)).toBe("4d ago");
  });
});

describe("formatDuration", () => {
  it("formats seconds, minutes, hours and days", () => {
    expect(formatDuration(0)).toBe("0s");
    expect(formatDuration(59)).toBe("59s");
    expect(formatDuration(60)).toBe("1m 00s");
    expect(formatDuration(184)).toBe("3m 04s");
    expect(formatDuration(3720)).toBe("1h 02m");
    expect(formatDuration(2 * 86400 + 3 * 3600)).toBe("2d 03h");
  });
  it("clamps negative input to zero", () => {
    expect(formatDuration(-5)).toBe("0s");
  });
});

describe("formatElapsed", () => {
  it("uses the explicit end when given", () => {
    expect(formatElapsed(100, 160)).toBe("1m 00s");
  });
  it("falls back to now", () => {
    expect(formatElapsed(40, null, 100)).toBe("1m 00s");
  });
  it("dashes on a null start", () => {
    expect(formatElapsed(null, 100, 100)).toBe("\u2014");
  });
});

describe("formatBytes", () => {
  it("scales by powers of 1000", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(1200)).toBe("1.2 KB");
    expect(formatBytes(534_528)).toBe("535 KB");
    expect(formatBytes(2_500_000)).toBe("2.5 MB");
  });
});

describe("statusLabel", () => {
  it("passes known statuses through", () => {
    expect(statusLabel("running")).toBe("running");
    expect(statusLabel("exited")).toBe("exited");
  });
});
