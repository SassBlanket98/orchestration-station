"""Fake session generator for the dashboard demo: a believable, time-evolving Snapshot with no real workers.

Every value is a pure function of e = now - t0 (seconds since launch) and fixed data, so two snapshots at the
same `now` are equal. Nothing reads files, /proc, subprocesses or threads.
"""
from __future__ import annotations

import functools
import math
import random
import time
import zlib
from dataclasses import dataclass, field

from model import (
    ACT_BUCKET_S,
    ACT_BUCKETS,
    DONE,
    FAILED,
    LIVE_STATES,
    NOREPORT,
    QUIET,
    QUIET_S,
    RUNNING,
    STALLED,
    TOOL_CATS,
    Finding,
    LeadInfo,
    LogState,
    Milestone,
    QuotaAccount,
    QuotaWindow,
    Review,
    Snapshot,
    Stage,
    Step,
    Worker,
)

_dm_TITLE = "demo-project"
_dm_SESSION_START = -1500.0   # S: session start, 25 minutes before launch (e = 0)
_dm_HIDDEN_OLDER = 12
_dm_KEEP_STEPS = 40
_dm_STEP_GAP = 3.0            # seconds between steps; jitter keeps every gap in 2-4 s
_dm_ERROR_RATE = 0.002        # chance a step is an error, before the per-worker cap
_dm_MAX_ERRORS = 2            # a healthy worker has at most this many errors in a session
_dm_IN_PER_STEP = 870
_dm_OUT_PER_STEP = 190
_dm_CACHE_PER_STEP = 3600     # about four times the input, as with prompt caching
_dm_USD_PER_MTOK_IN = 0.25
_dm_USD_PER_MTOK_OUT = 0.75
_dm_USD_PER_MTOK_CACHE = 0.025
_dm_HARNESS = {"deepseek": "opencode", "codex": "codex"}
_dm_TOOL_CAT = {"read": "read", "edit": "edit", "bash": "shell", "grep": "search"}
# (tool, argument only: the tool name is shown separately)
_dm_TOOL_STEPS = (
    ("read", "src/api/routes.ts"),
    ("bash", "pnpm test src/api"),
    ("edit", "src/db/schema.sql"),
    ("grep", '"accountId" in src'),
    ("read", "src/api/middleware/auth.ts"),
    ("bash", "pnpm tsc --noEmit"),
    ("edit", "src/api/usage.ts"),
    ("grep", '"plan_id" in src/db'),
)
_dm_TEXT_STEPS = (
    "Checking how the auth middleware wraps each handler.",
    "Tests for the usage route pass; moving on to the rest.",
    "Plan: update the handler, then add a regression test.",
    "Found two callers still using the old column name.",
    "Writing the migration notes into the slice report.",
    "Diff is small; running the lint pass before reporting.",
)
_dm_ERROR_STEPS = (
    "error: pnpm test exited with code 1",
    "error: tool call timed out after 120 s",
    "error: EACCES writing src/db/schema.sql.lock",
)
_dm_FAIL_TEXT = "error: API overloaded (529), retries exhausted; run aborted"

# Per worker: (summary lines, report file list, test lines). Summary lines also feed Worker.summary.
_dm_REPORTS = {
    "map-routes": (
        (
            "38 routes in 9 files mapped to handler, middleware and test.",
            "4 routes have no test; listed in the table.",
            "No code was changed.",
        ),
        ("src/api/routes.ts", "src/api/usage.ts", "src/api/accounts.ts", "src/api/workers.ts"),
        ("Not run: read-only slice.",),
    ),
    "db-schema": (
        (
            "accounts.plan_id added: uuid, foreign key to plans(id), indexed.",
            "Migration 0042 is reversible; the down script was tested on a copy.",
            "Two read paths still query plans by name; flagged for api-routes.",
        ),
        ("db/migrations/0042_account_plan.sql", "src/db/schema.sql"),
        ("pnpm test src/db: 17 passed",),
    ),
}

# Per worker: (scope, rules, output) lines for the brief.
_dm_BRIEFS = {
    "map-routes": (
        ("Walk every route file under src/api", "Record handler, middleware and test file per route",
         "Mark routes that have no test", "Note any route that bypasses auth"),
        ("Read only: do not edit files", "Keep the table under 60 rows"),
        ("Routes table in REPORT.md", "REPORT.md with a ## Summary section"),
    ),
    "api-routes": (
        ("Move each src/api route onto requireAuth()", "Keep response shapes unchanged",
         "Update route tests that assert the old 401 body", "Touch no file outside src/api"),
        ("No new dependencies", "Run pnpm test src/api before reporting"),
        ("Diff on the slice branch", "REPORT.md with a ## Summary section"),
    ),
    "db-schema": (
        ("Add accounts.plan_id uuid with a foreign key to plans(id)", "Add an index on accounts(plan_id)",
         "Write an up and a down migration", "Update src/db/schema.sql to match"),
        ("Migration must be reversible", "Do not drop existing columns"),
        ("Migration files and schema.sql", "REPORT.md with a ## Summary section"),
    ),
    "ui-cards": (
        ("Make usage cards wrap below 900 px", "Keep the card markup and class names",
         "Update the card snapshot test", "Touch only src/ui/cards"),
        ("No new CSS framework", "Keep the existing light and dark tokens"),
        ("Diff plus a passing snapshot test", "REPORT.md with a ## Summary section"),
    ),
    "seed-data": (
        ("Create three demo orgs with plans", "Add 30 days of usage rows per org",
         "Write seed SQL under db/seed", "Use fixed ids so reruns are stable"),
        ("No real customer names or emails", "Seeds must be idempotent"),
        ("db/seed/demo.sql", "REPORT.md with a ## Summary section"),
    ),
    "fix-lint": (
        ("Fix the ESLint errors that pnpm lint reports in src/api", "Only fix errors, no refactors",
         "Stop once the lint command passes"),
        ("Do not change behaviour", "Do not disable rules"),
        ("Clean lint run, output in fix-lint.log",),
    ),
    "review-api": (
        ("Read the api-routes diff against main", "Check auth on every changed route",
         "Check error bodies for data leaks", "Check that new code has tests"),
        ("Review only; do not edit code", "Rank findings by severity"),
        ("Lines '- [P1] title - detail' in review-api.log", "P1 for security or data loss"),
    ),
}

_dm_MILESTONES = (
    (_dm_SESSION_START + 0, "plan approved"),
    (_dm_SESSION_START + 180, "slices briefed"),
    (_dm_SESSION_START + 300, "map done; build slices launched"),
    (_dm_SESSION_START + 700, "seed-data failed; re-briefing"),
    (_dm_SESSION_START + 1200, "wrap-up started"),
    (22.0, "db-schema accepted"),
)
_dm_PLAN_FINDINGS = ((2, "Slice api-routes edits db/schema.sql, which db-schema owns"),
                     (3, "Brief for seed-data omits the rollback step"),
                     (3, "ui-cards goal does not say what done looks like"))
_dm_API_FINDINGS = ((1, "PATCH /accounts/:id skips the org check"),
                    (2, "Usage route returns raw SQL errors to the client"),
                    (2, "Session cookie lacks SameSite on the new login path"),
                    (3, "UsageQuery schema duplicated; reuse the shared one"))
# name, in_use, stale, windows of (window name, raw id, used %, pace, creeps with e, reset seconds from now)
_dm_QUOTA = (
    ("codex", True, False, (("5h", "five_hour", 41.0, 0.9, True, 3 * 3600 + 600), ("Week", "seven_day", 37.0, 0.8, False, 4 * 86400 + 5 * 3600))),
    ("claude", True, False, (("5h", "five_hour", 62.0, 1.1, True, 1 * 3600 + 20 * 60), ("Week", "seven_day", 48.0, 1.0, False, 3 * 86400 + 2 * 3600))),
    ("claude-work", False, True, (("5h", "five_hour", 12.0, 0.3, True, 4 * 3600), ("Week", "seven_day", 22.0, 0.5, False, 6 * 86400))),
    ("opencode-go", True, False, (("5h", "five_hour", 8.0, 0.4, True, 2 * 3600 + 30 * 60), ("Week", "seven_day", 15.0, 0.5, False, 5 * 86400))),
    ("cursor-models", True, False, (("5h", "five_hour", 73.0, 1.4, True, 45 * 60), ("Week", "seven_day", 100.0, 1.2, False, 2 * 86400 + 9 * 3600))),
)


@dataclass(frozen=True)
class _dm_Spec:
    name: str
    title: str
    goal: str
    model: str
    family: str
    start: float                  # e at which the worker launches
    end: float | None             # e at which it ends; None while live
    final: str = DONE             # DONE | FAILED | NOREPORT once ended
    pid: int | None = None        # fake pid, live workers only
    quiet_at: float | None = None
    stall_at: float | None = None
    has_report: bool = False
    is_review: bool = False


_dm_SPECS = (
    _dm_Spec("map-routes", "Map API routes",
             "List every HTTP route in src/api with its handler, middleware and test.",
             "luna@medium", "codex", _dm_SESSION_START, _dm_SESSION_START + 240, DONE, has_report=True),
    _dm_Spec("api-routes", "Move routes to shared auth",
             "Put every src/api route behind the shared auth middleware and keep tests green.",
             "sonnet-work medium", "claude", _dm_SESSION_START + 300, None, pid=48211),
    _dm_Spec("db-schema", "Add plan_id to accounts",
             "Add accounts.plan_id with a foreign key, an index and a reversible migration.",
             "opencode-go/deepseek-v4.1-flash", "deepseek", _dm_SESSION_START + 420, 20.0, DONE,
             has_report=True, pid=48390),
    _dm_Spec("ui-cards", "Fix usage card wrapping",
             "Make the usage cards wrap cleanly below 900 px and keep the snapshot test passing.",
             "composer-2.5", "cursor", _dm_SESSION_START + 420, None, pid=48517, quiet_at=30.0,
             stall_at=75.0),
    _dm_Spec("seed-data", "Seed demo accounts",
             "Create three demo orgs with plans and 30 days of usage rows.",
             "claude-haiku-5-5 (medium)", "claude", _dm_SESSION_START + 600, _dm_SESSION_START + 700, FAILED),
    _dm_Spec("fix-lint", "Fix lint errors in src/api",
             "Clear the ESLint errors that pnpm lint reports in src/api.",
             "cursor-grok-4.6-medium", "grok", _dm_SESSION_START + 900, _dm_SESSION_START + 1000, NOREPORT),
    _dm_Spec("review-api", "Review api-routes slice",
             "Review the api-routes diff for auth gaps, data leaks and missing tests.",
             "codex gpt-6-luna high (reviewer)", "codex", 35.0, 60.0, DONE, pid=48733, is_review=True),
)


def _dm_seed(*parts: object) -> int:
    return zlib.crc32(":".join(str(p) for p in parts).encode())


def _dm_phase_offset(name: str) -> float:
    """Fixed phase in radians so each worker's cpu and rss curves differ."""
    return (zlib.crc32(name.encode()) % 628) / 100.0


@dataclass
class _dm_Stream:
    rng: random.Random
    steps: list[tuple[float, str, str, str]] = field(default_factory=list)
    errors: int = 0


_dm_STREAMS: dict[str, _dm_Stream] = {}


def _dm_next_step(stream: _dm_Stream) -> tuple[float, str, str, str]:
    """Draw the next step of a worker. Its text differs from the previous step's text."""
    rng = stream.rng
    prev = stream.steps[-1][3] if stream.steps else ""
    jitter = rng.uniform(-0.5, 0.5)
    roll = rng.random()
    if roll < _dm_ERROR_RATE and stream.errors < _dm_MAX_ERRORS:
        stream.errors += 1
        text = rng.choice(_dm_ERROR_STEPS)
        while text == prev:
            text = rng.choice(_dm_ERROR_STEPS)
        return jitter, "error", "", text
    if roll < 0.55:
        tool, text = rng.choice(_dm_TOOL_STEPS)
        while text == prev:
            tool, text = rng.choice(_dm_TOOL_STEPS)
        return jitter, "tool", tool, text
    text = rng.choice(_dm_TEXT_STEPS)
    while text == prev:
        text = rng.choice(_dm_TEXT_STEPS)
    return jitter, "text", "", text


def _dm_step(name: str, k: int) -> tuple[float, str, str, str]:
    """Jitter, kind, tool and text of step k of a worker. Deterministic: each worker's steps come from one
    seeded stream, generated in order, so step k is the same however often it is asked for."""
    stream = _dm_STREAMS.get(name)
    if stream is None:
        stream = _dm_STREAMS[name] = _dm_Stream(random.Random(_dm_seed(name, "step")))
    while len(stream.steps) <= k:
        stream.steps.append(_dm_next_step(stream))
    return stream.steps[k]


@functools.lru_cache(maxsize=8192)
def _dm_bucket(name: str, index: int) -> int:
    return random.Random(_dm_seed(name, "act", index)).randint(1, 9)


def _dm_phase(spec: _dm_Spec, e: float) -> str | None:
    if e < spec.start:
        return None
    if spec.end is not None and e >= spec.end:
        return spec.final
    if spec.stall_at is not None and e >= spec.stall_at:
        return STALLED
    if spec.quiet_at is not None and e >= spec.quiet_at:
        return QUIET
    return RUNNING


def _dm_limit(spec: _dm_Spec, e: float) -> float:
    """Latest e at which the worker can still write output."""
    limit = e
    if spec.end is not None:
        limit = min(limit, spec.end)
    if spec.quiet_at is not None:
        limit = min(limit, spec.quiet_at)
    return limit


def _dm_count(spec: _dm_Spec, limit: float) -> int:
    # Step times are monotonic and within 0.5 s of start + 3 * (k + 1), so a point 3 steps
    # short of the estimate is always counted; only a few steps need checking from there.
    n = max(0, int((limit - spec.start) / _dm_STEP_GAP) - 3)
    while spec.start + _dm_STEP_GAP * (n + 1) + _dm_step(spec.name, n)[0] <= limit:
        n += 1
    return n


def _dm_log(spec: _dm_Spec, e: float, t0: float) -> LogState:
    n = _dm_count(spec, _dm_limit(spec, e))
    forced_fail = spec.final == FAILED and spec.end is not None and e >= spec.end
    tool_counts = {cat: 0 for cat in TOOL_CATS}
    errors = 0
    steps: list[Step] = []
    for k in range(n):
        jitter, kind, tool, text = _dm_step(spec.name, k)
        if forced_fail and k == n - 1:
            kind, tool, text = "error", "", _dm_FAIL_TEXT
        if kind == "tool":
            tool_counts[_dm_TOOL_CAT.get(tool, "other")] += 1
        elif kind == "error":
            errors += 1
        if k >= n - _dm_KEEP_STEPS:
            ts = t0 + spec.start + _dm_STEP_GAP * (k + 1) + jitter
            steps.append(Step(ts=ts, kind=kind, tool=tool, text=text))
    tokens_in = tokens_out = tokens_cache = cost = ctx_tokens = None
    if spec.family not in ("cursor", "grok"):
        tokens_in = n * _dm_IN_PER_STEP
        tokens_out = n * _dm_OUT_PER_STEP
        tokens_cache = n * _dm_CACHE_PER_STEP
        cost = round((tokens_in * _dm_USD_PER_MTOK_IN + tokens_out * _dm_USD_PER_MTOK_OUT
                      + tokens_cache * _dm_USD_PER_MTOK_CACHE) / 1_000_000, 4)
        ctx_tokens = min(190_000, 9_000 + n * 350)
    return LogState(
        harness=_dm_HARNESS.get(spec.family, "unknown"),
        model=spec.model,
        steps=steps,
        tool_counts=tool_counts,
        errors=errors,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cache=tokens_cache,
        cost=cost,
        ctx_tokens=ctx_tokens,
        first_ts=t0 + spec.start,
        last_ts=steps[-1].ts if steps else t0 + spec.start,
        pulses=[(s.ts, 1) for s in steps],
    )


def _dm_activity(spec: _dm_Spec, e: float, phase: str | None) -> list[int]:
    if phase not in LIVE_STATES:
        return [0] * ACT_BUCKETS
    last_out = e
    if phase != RUNNING and spec.quiet_at is not None:
        last_out = spec.quiet_at
    buckets: list[int] = []
    for b in range(ACT_BUCKETS):
        bucket_end = e - ACT_BUCKET_S * (ACT_BUCKETS - 1 - b)
        if bucket_end > last_out or bucket_end - ACT_BUCKET_S < spec.start:
            buckets.append(0)
        else:
            buckets.append(_dm_bucket(spec.name, math.floor(bucket_end / ACT_BUCKET_S)))
    return buckets


def _dm_worker(spec: _dm_Spec, e: float, now: float, t0: float) -> Worker | None:
    phase = _dm_phase(spec, e)
    if phase is None:
        return None
    log = _dm_log(spec, e, t0)
    live = phase in LIVE_STATES
    pid = cpu = rss = nproc = None
    if live:
        off = _dm_phase_offset(spec.name)
        pid = spec.pid
        if phase == RUNNING:
            cpu = 55.0 + 30.0 * math.sin(e / 37.0 + off)
        else:
            cpu = 0.2 + 0.15 * (1.0 + math.sin(e / 23.0 + off))
        rss = int((300.0 + 140.0 * math.sin(e / 410.0 + off)) * (1 << 20))
        nproc = 2 + zlib.crc32(spec.name.encode()) % 4
    if phase in (QUIET, STALLED) and spec.quiet_at is not None:
        quiet_s = QUIET_S + (e - spec.quiet_at)
    else:
        quiet_s = max(0.0, now - log.last_ts)
    return Worker(
        name=spec.name,
        state=phase,
        title=spec.title,
        goal=spec.goal,
        model=spec.model,
        family=spec.family,
        pid=pid,
        start=t0 + spec.start,
        end=None if live or spec.end is None else t0 + spec.end,
        cpu=cpu,
        rss=rss,
        nproc=nproc,
        log=log,
        activity=_dm_activity(spec, e, phase),
        quiet_s=quiet_s,
        summary=list(_dm_REPORTS[spec.name][0]) if spec.has_report else [],
        has_report=spec.has_report,
        is_review=spec.is_review,
    )


def _dm_reviews(e: float, t0: float) -> list[Review]:
    reviews = [Review(
        name="review-plan",
        model="gpt-6-luna high",
        family="codex",
        start=t0 + _dm_SESSION_START + 260,
        end=t0 + _dm_SESSION_START + 280,
        running=False,
        findings=[Finding(prio, text) for prio, text in _dm_PLAN_FINDINGS],
    )]
    if e >= 35:
        done = e >= 60
        reviews.append(Review(
            name="review-api",
            model="codex gpt-6-luna high",
            family="codex",
            start=t0 + 35,
            end=t0 + min(e, 60.0),
            running=not done,
            findings=[Finding(prio, text) for prio, text in _dm_API_FINDINGS] if done else [],
        ))
    reviews.sort(key=lambda r: r.end, reverse=True)
    return reviews


def _dm_quota(e: float) -> list[QuotaAccount]:
    creep = max(e, 0.0) / 30.0
    accounts = []
    for name, in_use, stale, windows in _dm_QUOTA:
        wins = [QuotaWindow(wname, used + (creep if creeps else 0.0), pace, stale=stale,
                            window=wid, resets_at=e + reset)
                for wname, wid, used, pace, creeps, reset in windows]
        accounts.append(QuotaAccount(name=name, windows=wins, stale=stale, in_use=in_use))
    return accounts


def _dm_brief(spec: _dm_Spec) -> list[str]:
    scope, rules, output = _dm_BRIEFS[spec.name]
    return [
        f"# {spec.title}",
        f"Goal: {spec.goal}",
        "",
        "## Scope",
        *(f"- {line}" for line in scope),
        "",
        "## Rules",
        *(f"- {line}" for line in rules),
        "",
        "## Output",
        *(f"- {line}" for line in output),
    ]


def _dm_report(spec: _dm_Spec) -> list[str]:
    summary, files, tests = _dm_REPORTS[spec.name]
    return [
        f"# {spec.title}",
        "",
        "## Summary",
        *summary,
        "",
        "## Files",
        *(f"- {line}" for line in files),
        "",
        "## Tests",
        *(f"- {line}" for line in tests),
    ]


_dm_SOLO_LABEL = "side-tools"
_dm_SOLO_PATH = "/demo/side-tools/.stage#48902"   # virtual: no stage directory, one lead process
_dm_SOLO_SINCE = -600.0                           # the lead started 10 minutes before launch


class DemoCollector:
    def __init__(self, stage_dir: str = "") -> None:
        self._t0 = time.time()
        self._stage_dir = stage_dir

    def snapshot(self, now: float | None = None, all_history: bool = False) -> Snapshot:
        t = time.time() if now is None else now
        t0 = self._t0
        e = t - t0
        built = [w for w in (_dm_worker(s, e, t, t0) for s in _dm_SPECS) if w is not None]
        live = sorted((w for w in built if w.end is None), key=lambda w: w.start)
        dead = sorted((w for w in built if w.end is not None), key=lambda w: w.end or 0.0, reverse=True)
        token_parts = [v for w in built for v in (w.log.tokens_in, w.log.tokens_out) if v is not None]
        cost_parts = [w.log.cost for w in built if w.log.cost is not None]
        return Snapshot(
            title=_dm_TITLE,
            now=t,
            session_start=t0 + _dm_SESSION_START,
            all_history=all_history,
            workers=live + dead,
            hidden_older=0 if all_history else _dm_HIDDEN_OLDER,
            reviews=_dm_reviews(e, t0),
            milestones=[Milestone(ts=t0 + off, text=text) for off, text in _dm_MILESTONES if off <= e],
            quota=_dm_quota(e),
            quota_ok=True,
            tokens_total=sum(token_parts) if token_parts else None,
            cost_total=sum(cost_parts) if cost_parts else None,
            services=[("dash", True)],
        )

    def detail(self, name: str, tab: str) -> list[str]:
        spec = next((s for s in _dm_SPECS if s.name == name), None)
        if spec is None:
            return []
        if tab == "brief":
            return _dm_brief(spec)
        if tab == "report" and spec.has_report:
            return _dm_report(spec)
        return []

    def solo_stages(self) -> list[Stage]:
        """One solo station: a live lead with no workers, on a virtual path. Fixed data, no I/O."""
        return [Stage(
            path=_dm_SOLO_PATH, label=_dm_SOLO_LABEL, snap=Snapshot(title=_dm_SOLO_LABEL),
            live=0, last_activity=0.0, lead=LeadInfo(model="claude", phase="build", stream="side-tools"),
            lead_state="live", lead_pid=48902, lead_since=self._t0 + _dm_SOLO_SINCE, lead_kind="claude",
            solo=True,
        )]

    def close(self) -> None:
        pass
