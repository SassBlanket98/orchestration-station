import type { PickResult, QuotaUsage } from "../api/types";

export const mockQuotaUsage: QuotaUsage = {
  offline: false,
  generated_at: 1791511190896,
  fetched_at: 1791511200,
  error_code: null,
  credits: [
    { account: "opencode-zen", balance_usd: 1.84, burn_per_day_usd: 0.5, runs_out_at: 1791770000000, low: true, reason: "under $5" },
  ],
  subscriptions: [
    { id: "codex", account: "codex · desktop", stale: false, last_resort: false, limits: [
      { window: "five_hour", name: "5h", used_pct: 26, pace_pct: 31, projected_pct: 44, runs_out_at: null, resets_at: 1791525726000 },
      { window: "seven_day", name: "Week", used_pct: 18, pace_pct: 21, projected_pct: 32, runs_out_at: null, resets_at: 1792076523000 },
    ] },
    { id: "claude", account: "claude · personal", stale: false, last_resort: false, limits: [
      { window: "five_hour", name: "5h", used_pct: 51, pace_pct: 27, projected_pct: null, runs_out_at: 1791511354349.8484, resets_at: 1791513000565 },
      { window: "seven_day", name: "Week", used_pct: 59, pace_pct: 29, projected_pct: 202, runs_out_at: 1791633767085.3389, resets_at: 1791939600565 },
    ] },
    { id: "claude-work", account: "claude · work", stale: false, last_resort: false, limits: [
      { window: "five_hour", name: "5h", used_pct: 62, pace_pct: 48, projected_pct: 91, runs_out_at: null, resets_at: 1791518399956 },
      { window: "seven_day", name: "Week", used_pct: 34, pace_pct: 30, projected_pct: 77, runs_out_at: null, resets_at: 1792087199956 },
    ] },
    { id: "cursor-models", account: "cursor", stale: true, last_resort: false, limits: [
      { window: "month_auto", name: "Auto", used_pct: 91, pace_pct: 26, projected_pct: null, runs_out_at: null, resets_at: 1793027628000 },
    ] },
    { id: "opencode-go", account: "opencode-go", stale: false, last_resort: false, limits: [
      { window: "five_hour", name: "5h", used_pct: 18, pace_pct: 27, projected_pct: 34, runs_out_at: null, resets_at: 1791520288000 },
      { window: "thirty_day", name: "Month", used_pct: 18, pace_pct: 27, projected_pct: 34, runs_out_at: null, resets_at: 1793732080000 },
    ] },
  ],
};

const buildPick: PickResult = {
  job: "build", generated_at: 1791511194672, tier_used: "mid", stretched: false, lead: { model: "opus", over_pace: true, suggestion: "sol is available" },
  fallback: { model: "engine-b", effort: "default", subscription: "opencode-go" },
  candidates: [
    { model: "engine-a", effort: "medium", subscription: "codex", score: 82, runway: 1.2, group: "ok", tier: "mid", reason: "affordable", shortest: null, longest: null },
    { model: "engine-a", effort: "medium", subscription: "claude-work", score: 61, runway: 0.8, group: "ok", tier: "mid", reason: "available", shortest: null, longest: null },
    { model: "engine-b", effort: "default", subscription: "opencode-go", score: 58, runway: 0.7, group: "ok", tier: "mid", reason: "available", shortest: null, longest: null },
    { model: "opus", effort: "medium", subscription: "claude", score: 22, runway: 0.2, group: "runs-out", tier: "frontier", reason: "runs out", shortest: null, longest: null },
  ],
  assignment: [{ model: "engine-a", effort: "medium", slices: 2 }, { model: "engine-a", effort: "medium", slices: 1 }, { model: "engine-b", effort: "default", slices: 1 }],
  skipped: [{ model: "grok-high", why: "above available headroom for this preview" }],
  warnings: ["Estimated load is close to the available headroom."],
};

const simplePick = (job: string): PickResult => ({ ...buildPick, job, candidates: buildPick.candidates, assignment: buildPick.assignment });
export const mockQuotaPicks: Record<string, PickResult> = {
  build: buildPick,
  review: simplePick("review"),
  triage: simplePick("triage"),
  plan: simplePick("plan"),
};

export const mockRecentPicks = [
  { at: "16:24", job: "re-pick build", summary: "4 slices · engine-a ×2, engine-a, engine-b" },
  { at: "16:19", job: "review", summary: "engine-a@high · reviewer differs from builder" },
  { at: "15:59", job: "read", summary: "3 slices · engine-a, haiku-work, engine-b" },
];
