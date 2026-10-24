import { useMemo, useState } from "react";
import type { PickResult, QuotaLimit, QuotaSubscription } from "../api/types";
import { isMockMode } from "../api/client";
import { AppShell } from "../components/ui/AppShell";
import { mockQuotaUsage, mockRecentPicks, mockQuotaPicks } from "../mocks/quota";
import { useQuotaPick, useQuotaUsage } from "../state/hooks";
import { arcOffset, formatMetric, formatUnixMs, formatUnixSeconds, gaugeState, selectPrimaryLimit, visibleScore } from "../components/quota/metrics";
import "../components/quota/quota.css";
import "../components/board/board.css";
import { UsageBars } from "../components/board/UsageBars";

const JOBS = ["build", "review", "triage", "plan"] as const;
const ENGINE_META: Record<string, { label: string; mark: string; color: string }> = {
  codex: { label: "Codex", mark: "CX", color: "#7798ca" },
  claude: { label: "Claude", mark: "CL", color: "#c29274" },
  "claude-work": { label: "Claude", mark: "CW", color: "#bd8fa9" },
  cursor: { label: "Cursor", mark: "CU", color: "#a697ca" },
  opencode: { label: "OpenCode", mark: "OC", color: "#69abaa" },
  unknown: { label: "Subscription", mark: "·", color: "#adb5b7" },
};

function engineFor(sub: QuotaSubscription) {
  const id = sub.id ?? "unknown";
  if (id === "claude-work") return ENGINE_META["claude-work"];
  if (id === "opencode-go") return ENGINE_META.opencode;
  if (id.startsWith("claude")) return ENGINE_META.claude;
  if (id.startsWith("cursor")) return ENGINE_META.cursor;
  return ENGINE_META[id] ?? ENGINE_META.unknown;
}

function limitName(limit: QuotaLimit | undefined): string {
  return limit?.name ?? limit?.window?.replaceAll("_", " ") ?? "window";
}

function Gauge({ subscription, limit }: { subscription: QuotaSubscription; limit: QuotaLimit | undefined }) {
  const meta = engineFor(subscription);
  const used = limit?.used_pct ?? null;
  const pace = limit?.pace_pct ?? null;
  const projected = limit?.projected_pct ?? null;
  const state = gaugeState(used, pace, projected);
  const arcUsed = arcOffset(used ?? null) ?? 0;
  const arcProjected = arcOffset(projected ?? null) ?? 0;
  const reset = formatUnixMs(limit?.resets_at ?? null);
  const runsOut = formatUnixMs(limit?.runs_out_at ?? null);
  const stateLabel = state === "unknown" ? "Usage pace unknown" : state === "danger" ? "Near limit" : state === "warning" ? "Over pace" : "Under pace";
  return <article className={`quota-gauge-card is-${state}`} style={{ "--engine": meta.color } as React.CSSProperties}>
    <header className="quota-engine"><span className="quota-engine-mark">{meta.mark}</span><span><b>{meta.label}</b><small>{subscription.account ?? subscription.id ?? "unknown account"}</small></span></header>
    <div className={`quota-gauge${used === null ? " is-null" : ""}`} aria-label={`${limitName(limit)} used ${formatMetric(used)}`}>
      <svg viewBox="0 0 104 104" aria-hidden="true">
        <circle className="quota-track" cx="52" cy="52" r="42" />
        {projected !== null && <circle className="quota-projection" cx="52" cy="52" r="42" strokeDasharray={`${arcProjected} 264`} />}
        {used !== null && <circle className="quota-used" cx="52" cy="52" r="42" strokeDasharray={`${arcUsed} 264`} />}
        {pace !== null && <line className="quota-pace-tick" x1="99" y1="52" x2="90" y2="52" transform={`rotate(${Math.max(0, Math.min(100, pace)) * 3.6} 52 52)`} />}
      </svg>
      <div className="quota-gauge-center"><b>{formatMetric(used)}</b><small>{limitName(limit)}</small></div>
    </div>
    <div className="quota-metric"><span>Used</span><b>{formatMetric(used)}</b></div>
    <div className="quota-metric"><span>Pace</span><b>{formatMetric(pace)}</b></div>
    <div className="quota-metric"><span>Projected</span><b>{formatMetric(projected)}</b></div>
    <div className="quota-metric"><span>Reset</span><b>{reset}</b></div>
    {runsOut !== "—" && <div className="quota-metric"><span>Runs out</span><b>{runsOut}</b></div>}
    <div className={`quota-pace-state ${state}`}>{stateLabel}</div>
  </article>;
}

function SecondWindow({ subscription, limit }: { subscription: QuotaSubscription; limit: QuotaLimit }) {
  const meta = engineFor(subscription);
  const used = limit.used_pct;
  const pace = limit.pace_pct;
  const projected = limit.projected_pct;
  const width = (n: number | null) => `${n === null ? 0 : Math.max(0, Math.min(100, n))}%`;
  return <div className="quota-window-row">
    <div className="quota-window-name"><span style={{ color: meta.color }}>{meta.mark}</span><b>{subscription.id ?? "subscription"}</b><small>{limitName(limit)}</small></div>
    <div className="quota-segments" aria-label={`Used ${formatMetric(used)}, pace ${formatMetric(pace)}, projected ${formatMetric(projected)}`}>
      {used !== null && <span className="quota-segment-used" style={{ width: width(used) }} />}
      {projected !== null && projected > (used ?? 0) && <span className="quota-segment-projected" style={{ left: width(used), width: `${Math.max(0, Math.min(100, projected) - Math.max(0, Math.min(100, used ?? 0)))}%` }} />}
      {pace !== null && <i className="quota-segment-pace" style={{ left: width(pace) }} />}
    </div>
    <span className="quota-window-value">{formatMetric(used)} used · {formatMetric(pace)} pace · {formatMetric(projected)} projected</span>
  </div>;
}

function CandidateRow({ result, candidate, index, offline }: { result: PickResult; candidate: PickResult["candidates"][number]; index: number; offline: boolean }) {
  const sub = candidate.subscription ?? "unknown";
  const meta = engineFor({ id: sub, account: null, stale: null, last_resort: null, limits: [] });
  const rankLabel = [candidate.tier, candidate.group, candidate.reason].filter(Boolean).join(" · ");
  return <div className="quota-rank-row">
    <b className="quota-rank-number">{index + 1}</b><span className="quota-engine-mark" style={{ color: meta.color }}>{meta.mark}</span>
    <div className="quota-rank-name"><b>{candidate.model ?? "Unknown model"}{candidate.effort ? `@${candidate.effort}` : ""}</b><small>{rankLabel || "No ranking detail supplied"}</small></div>
    <b className="quota-score">{visibleScore(candidate.score, offline) === null ? "—" : candidate.score}</b>
    <span className="quota-rank-tag">{result.assignment.some((a) => a.model === candidate.model) ? "assigned" : candidate.group ?? "ranked"}</span>
  </div>;
}

function displayModel(model: string | null, effort: string | null): string {
  return model ? `${model}${effort ? `@${effort}` : ""}` : "Unknown model";
}

export default function Quota() {
  const mock = isMockMode();
  const [job, setJob] = useState<(typeof JOBS)[number]>("build");
  const [slices, setSlices] = useState(4);
  const usageRequest = useQuotaUsage();
  const [lead, setLead] = useState("");
  const [builder, setBuilder] = useState("");
  const pickRequest = useQuotaPick(job, slices, lead, builder);
  const usage = mock ? mockQuotaUsage : usageRequest.data;
  const pick = mock ? mockQuotaPicks[job] : pickRequest.data;
  const subscriptions = usage?.subscriptions ?? [];
  const credits = usage?.credits ?? [];
  const secondWindows = useMemo(() => subscriptions.flatMap((subscription) => {
    const limits = subscription.limits.filter((limit) => limit.window !== selectPrimaryLimit(subscription.limits)?.window);
    return limits.map((limit) => ({ subscription, limit }));
  }), [subscriptions]);
  const offline = usage?.offline === true || (!mock && usageRequest.error !== null);
  const pickFailed = !mock && !offline && pickRequest.error !== null;
  const loading = !mock && (usageRequest.loading || pickRequest.loading);
  const errorCode = usage?.error_code ?? "OFFLINE";

  return <AppShell><div className="quota-page">
    <div className="quota-heading"><div><h1>Capacity and next pick</h1><p>Read-only usage and picker preview{usage?.generated_at ? ` · usage generated ${formatUnixMs(usage.generated_at)}` : ""}{usage?.fetched_at ? ` · fetched ${formatUnixSeconds(usage.fetched_at)}` : ""}</p></div><span className="quota-readonly">Read-only preview</span></div>

    {offline && <div className="quota-offline" role="status"><b>quota service offline</b><span>Code: {errorCode}</span>{usageRequest.error && <span>{usageRequest.error.message}</span>}</div>}
    {loading && <div className="quota-loading" role="status"><span className="quota-spinner" /> Loading quota and preview…</div>}

    <div className="quota-phone-usage"><UsageBars quota={usage} loading={loading} offline={offline} /></div>
    <div className="quota-section-heading gauges"><h2>Subscription pace</h2><span>Arc = used · ghost arc = projected · neutral tick = pace</span></div>
    {!loading && subscriptions.length === 0 && <div className="quota-empty">{offline ? "Usage is unavailable while quota service is offline." : "No subscription usage is available."}</div>}
    {subscriptions.length > 0 && <section className="quota-gauges">{subscriptions.map((subscription) => <Gauge key={subscription.id ?? subscription.account} subscription={subscription} limit={selectPrimaryLimit(subscription.limits)} />)}</section>}

    {credits.length > 0 && <><div className="quota-section-heading"><h2>API credits</h2><span>Balance is entered by hand; burn is measured from updates</span></div><section className="quota-gauges">{credits.map((credit) => <div key={credit.account} className={`quota-gauge-card quota-credit${credit.low ? " quota-credit-low" : ""}`}><div className="quota-engine"><div><b>{credit.account}</b><small>{credit.low ? `LOW · ${credit.reason ?? ""}` : "pay-as-you-go"}</small></div></div><div className="quota-credit-balance">{credit.balance_usd === null ? "no balance" : `$${credit.balance_usd.toFixed(2)}`}</div><div className="quota-credit-meta">{credit.burn_per_day_usd !== null ? `$${credit.burn_per_day_usd.toFixed(2)}/day` : "burn unknown yet"}{credit.runs_out_at !== null ? ` · runs out ${formatUnixMs(credit.runs_out_at)}` : ""}</div></div>)}</section></>}

    {secondWindows.length > 0 && <section className="quota-second-windows"><h2>Additional windows</h2><div className="quota-window-list">{secondWindows.map(({ subscription, limit }, index) => <SecondWindow key={`${subscription.id}-${limit.window}-${index}`} subscription={subscription} limit={limit} />)}</div></section>}

    <div className="quota-main-grid">
      <section className="quota-panel">
        <header className="quota-panel-head"><h2>Picker ranking</h2><span>{pick?.generated_at ? `Preview generated ${formatUnixMs(pick.generated_at)}` : "Current preview"}</span></header>
        <div className="quota-controls">
          <label>Job<select value={job} onChange={(event) => setJob(event.target.value as (typeof JOBS)[number])}>{JOBS.map((item) => <option key={item} value={item}>{item[0].toUpperCase() + item.slice(1)}</option>)}</select></label>
          <label>Slices<input type="number" min={1} max={32} value={slices} onChange={(event) => setSlices(Math.max(1, Math.min(32, Number(event.target.value) || 1)))} /></label>
          <label>Lead<input type="text" defaultValue={lead} key={`lead-${lead}`} spellCheck={false} onBlur={(event) => setLead(event.target.value.trim() || lead)} onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} /></label>
          <label>Builder<input type="text" defaultValue={builder} key={`builder-${builder}`} spellCheck={false} onBlur={(event) => setBuilder(event.target.value.trim() || builder)} onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} /></label>
          <button type="button" className="quota-preview-button" onClick={pickRequest.reload} disabled={mock || pickRequest.loading}>Preview pick</button>
        </div>
        <p className="quota-preview-note">Changing these fields only updates a read-only preview query. It does not launch or assign work.</p>
        {pickRequest.loading && !mock && <div className="quota-inline-state">Loading ranking…</div>}
        {pickFailed && <div className="quota-inline-state" role="status">Picker preview failed: {pickRequest.error?.message ?? "unknown error"}. Check the lead and builder ids.</div>}
        {!pick && !loading && !pickFailed && <div className="quota-inline-state">{offline ? "Picker preview is unavailable offline." : "No picker preview is available."}</div>}
        {pick && <>
          {pick.candidates.length === 0 && <div className="quota-inline-state">No ranked candidates returned.</div>}
          {pick.candidates.length > 0 && <div className="quota-ranking">{pick.candidates.map((candidate, index) => <CandidateRow key={`${candidate.model}-${candidate.effort}-${index}`} result={pick} candidate={candidate} index={index} offline={offline} />)}</div>}
          <div className="quota-summary">{pick.assignment.length > 0 && <p><b>Assignment preview:</b> {pick.assignment.map((item) => `${displayModel(item.model, item.effort)}${item.slices && item.slices > 1 ? ` ×${item.slices}` : ""}`).join(" · ")}</p>}
            {pick.lead && <p><b>Lead suggestion:</b> {pick.lead.model ?? "Lead unavailable"}{pick.lead.over_pace ? " is over pace" : ""}{pick.lead.suggestion ? ` · ${pick.lead.suggestion}` : ""}</p>}
            {pick.fallback && <p><b>Fallback:</b> {displayModel(pick.fallback.model, pick.fallback.effort)} · {pick.fallback.subscription ?? "subscription unknown"}</p>}
            <p><b>Tier:</b> {pick.tier_used ?? "—"} · <b>Stretched:</b> {pick.stretched === null ? "—" : pick.stretched ? "yes" : "no"}</p>
            {pick.warnings.length > 0 && <ul className="quota-warning-list">{pick.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>}
            {pick.skipped.length > 0 && <p><b>Skipped:</b> {pick.skipped.map((item) => `${item.model ?? "Unknown model"} — ${item.why ?? "reason unavailable"}`).join(" · ")}</p>}
          </div>
        </>}
      </section>
      <aside className="quota-side-stack">
        <section className="quota-panel"><header className="quota-panel-head"><h2>Recent picks</h2><span>{mock ? "session illustration" : "session history"}</span></header><div className="quota-recent-list">{mock ? mockRecentPicks.map((item, index) => <div className="quota-recent-item" key={`${item.at}-${index}`}><b>{item.at} · {item.job}</b><p>{item.summary}</p></div>) : <div className="quota-empty-history">No session pick history is available on this page.</div>}{mock && <p className="quota-history-note">Historical scores: no source. Scores above belong to the current preview.</p>}</div></section>
        <section className="quota-panel quota-footnote">Additional windows show usage bars. The available endpoint supplies no token or cost history.</section>
      </aside>
    </div>
  </div></AppShell>;
}
