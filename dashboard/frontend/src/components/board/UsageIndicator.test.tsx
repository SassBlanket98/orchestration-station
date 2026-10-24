import { describe, expect, it } from "vitest";
import type { QuotaLimit, QuotaSubscription, QuotaUsage } from "../../api/types";
import { summarizeUsage } from "./UsageIndicator";

const NOW = 1_700_000_000_000;

function limit(used: number, name = "5h"): QuotaLimit {
  return { window: "five_hour", name, used_pct: used, pace_pct: null, projected_pct: null, runs_out_at: null, resets_at: NOW + 3_600_000 };
}
function sub(id: string, limits: QuotaLimit[], stale = false): QuotaSubscription {
  return { id, account: null, stale, last_resort: false, limits };
}
function quota(subscriptions: QuotaSubscription[]): QuotaUsage {
  return { offline: false, generated_at: null, fetched_at: null, error_code: null, subscriptions };
}

describe("summarizeUsage", () => {
  it("shows the most-used fresh window with its tone", () => {
    const s = summarizeUsage({ quota: quota([sub("a", [limit(40)]), sub("b", [limit(20), limit(87, "7d")])]), loading: false, offline: false, now: NOW });
    expect(s.text).toBe("usage 87%");
    expect(s.tone).toBe("warn");
    expect(s.title).toContain("b 7d");
  });
  it("ignores stale subscriptions", () => {
    const s = summarizeUsage({ quota: quota([sub("a", [limit(99)], true), sub("b", [limit(10)])]), loading: false, offline: false, now: NOW });
    expect(s.text).toBe("usage 10%");
    expect(s.tone).toBe("ok");
  });
  it("reports loading and offline", () => {
    expect(summarizeUsage({ quota: null, loading: true, offline: false }).text).toBe("usage …");
    expect(summarizeUsage({ quota: quota([sub("a", [limit(5)])]), loading: false, offline: true }).text).toBe("usage offline");
  });
});
