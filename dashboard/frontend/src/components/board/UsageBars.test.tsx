import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { QuotaLimit, QuotaSubscription, QuotaUsage } from "../../api/types";
import { UsageBars, buildUsageRows, usageTone } from "./UsageBars";

// No DOM in this project's test environment: render to static markup and read the data attributes.
const NOW = 1_700_000_000_000; // epoch ms
const HOUR = 3_600_000;

function limit(over: Partial<QuotaLimit> = {}): QuotaLimit {
  return {
    window: "five_hour",
    name: "5h",
    used_pct: 10,
    pace_pct: null,
    projected_pct: null,
    runs_out_at: null,
    resets_at: NOW + 2 * HOUR,
    ...over,
  };
}

function sub(over: Partial<QuotaSubscription> = {}): QuotaSubscription {
  return { id: "acc", account: null, stale: false, last_resort: false, limits: [limit()], ...over };
}

function quota(subscriptions: QuotaSubscription[], over: Partial<QuotaUsage> = {}): QuotaUsage {
  return { offline: false, generated_at: null, fetched_at: null, error_code: null, subscriptions, ...over };
}

function render(data: QuotaUsage | null | undefined, options: { loading?: boolean; offline?: boolean } = {}): string {
  return renderToStaticMarkup(<UsageBars quota={data} loading={options.loading ?? false} offline={options.offline ?? false} now={NOW} />);
}

function all(markup: string, pattern: RegExp): string[] {
  return [...markup.matchAll(pattern)].map((match) => match[1]);
}

describe("window tone uses the raw value", () => {
  it("keeps 94.6% warn although it displays as 95%", () => {
    const [row] = buildUsageRows([sub({ limits: [limit({ used_pct: 94.6 })] })], 0);
    expect(row.windows[0].used).toBe(95);
    expect(row.windows[0].tone).toBe("warn");
  });
});

describe("usageTone", () => {
  it("is ok below 80, warn from 80, bad from 95", () => {
    expect(usageTone(0)).toBe("ok");
    expect(usageTone(79)).toBe("ok");
    expect(usageTone(80)).toBe("warn");
    expect(usageTone(94)).toBe("warn");
    expect(usageTone(95)).toBe("bad");
    expect(usageTone(100)).toBe("bad");
  });
});

describe("buildUsageRows", () => {
  it("sorts subscriptions by highest used_pct first, keeping ties in source order", () => {
    const rows = buildUsageRows(
      [
        sub({ id: "low", limits: [limit({ used_pct: 12 })] }),
        sub({ id: "mixed", limits: [limit({ used_pct: 5 }), limit({ name: "Week", used_pct: 70 })] }),
        sub({ id: "hot", limits: [limit({ used_pct: 91 })] }),
        sub({ id: "none", limits: [] }),
      ],
      NOW,
    );
    expect(rows.map((row) => row.label)).toEqual(["hot", "mixed", "low", "none"]);
    expect(rows.find((row) => row.label === "none")?.peak).toBe(0);
  });

  it("falls back to the account when there is no id", () => {
    const [row] = buildUsageRows([sub({ id: null, account: "work" })], NOW);
    expect(row.label).toBe("work");
  });
});

describe("UsageBars", () => {
  it("renders subscriptions in sorted order", () => {
    const markup = render(quota([
      sub({ id: "small", limits: [limit({ used_pct: 20 })] }),
      sub({ id: "hot", limits: [limit({ used_pct: 96 })] }),
      sub({ id: "mid", limits: [limit({ used_pct: 55 })] }),
    ]));
    expect(all(markup, /data-sub="([^"]+)"/g)).toEqual(["hot", "mid", "small"]);
  });

  it("colours each window by threshold: 79 ok, 80 warn, 95 bad", () => {
    const markup = render(quota([
      sub({
        id: "acc",
        limits: [
          limit({ name: "5h", used_pct: 79 }),
          limit({ name: "Week", used_pct: 80 }),
          limit({ name: "Auto", used_pct: 95 }),
        ],
      }),
    ]));
    expect(all(markup, /data-tone="(\w+)"/g)).toEqual(["ok", "warn", "bad"]);
    expect(markup).toContain("tone-ok");
    expect(markup).toContain("tone-warn");
    expect(markup).toContain("tone-bad");
    expect(markup).toContain("79%");
    expect(markup).toContain("80%");
    expect(markup).toContain("95%");
  });

  it("shows a runs-out warning when runs_out_at is before resets_at", () => {
    const markup = render(quota([
      sub({ limits: [limit({ used_pct: 60, runs_out_at: NOW + 3 * HOUR, resets_at: NOW + 5 * HOUR })] }),
    ]));
    expect(markup).toContain('<span class="usage-runs">runs out 3h 00m</span>');
    expect(markup).toContain("↻5h 00m");
  });

  it("omits the runs-out warning when the window resets first", () => {
    const markup = render(quota([
      sub({ limits: [limit({ used_pct: 60, runs_out_at: NOW + 6 * HOUR, resets_at: NOW + 5 * HOUR })] }),
    ]));
    expect(markup).not.toContain("runs out");
    expect(markup).not.toContain("usage-runs");
  });

  it("shows the pace tick only when pace_pct is present", () => {
    const withPace = render(quota([sub({ limits: [limit({ used_pct: 40, pace_pct: 33 })] })]));
    expect(withPace).toContain('class="usage-pace" style="left:33%"');
    const withoutPace = render(quota([sub({ limits: [limit({ used_pct: 40, pace_pct: null })] })]));
    expect(withoutPace).not.toContain("usage-pace");
  });

  it("folds stale subscriptions into one line and keeps bars for fresh ones", () => {
    const markup = render(quota([
      sub({ id: "old", stale: true, limits: [limit({ used_pct: 10 })] }),
      sub({ id: "empty", stale: false, limits: [] }),
      sub({ id: "fresh", stale: false, limits: [limit({ used_pct: 50 })] }),
    ]));
    expect(markup).toContain("stale: old · empty");
    expect(markup).not.toContain('data-sub="old"');
    expect(markup).toContain('class="usage-sub" data-sub="fresh"');
  });

  it("shows one dim offline line when the quota is offline", () => {
    const markup = render(quota([sub()]), { offline: true });
    expect(markup).toContain('<div class="usage-state dim">quota offline</div>');
    expect(markup).not.toContain("usage-window");
  });

  it("shows the offline line when there is no data or no subscriptions", () => {
    expect(render(null)).toContain("quota offline");
    expect(render(undefined)).toContain("quota offline");
    expect(render(quota([]))).toContain("quota offline");
  });

  it("shows the loading line while loading", () => {
    const markup = render(quota([sub()]), { loading: true });
    expect(markup).toContain("quota loading…");
    expect(markup).not.toContain("usage-window");
  });
});
