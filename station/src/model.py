"""Shared data model for the workers dashboard and execution registry."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field


def home_path(rel: str) -> str:
    """A path owned by Station under STATION_HOME (default: $STATION_HOME)."""
    root = os.path.expanduser(os.environ.get("STATION_HOME") or os.path.join(os.path.expanduser("~"), ".local/state/station"))
    return os.path.join(root, rel)


def optional_cmd(name: str) -> str | None:
    """Resolve an optional executable, returning None when it is absent or not executable."""
    expanded = os.path.expanduser(name)
    if os.path.dirname(expanded):
        return expanded if os.path.isfile(expanded) and os.access(expanded, os.X_OK) else None
    return shutil.which(expanded)

MAX_STEPS = 200          # steps kept per log
ACT_BUCKETS = 60         # activity sparkline buckets
ACT_BUCKET_S = 5         # seconds per bucket (60 x 5 s = last 5 minutes)
TOOL_RING_S = 1800       # tool-call ring kept per log: the last 30 minutes, for the TOOLS/MIN sparkline
TOOL_RING_MAX = 2000     # entries kept in the ring at most
LEVEL_TAGS = ("LEAD", "SPAWN", "SUB", "WORKER", "CRON")   # hierarchy tag of a row (docs/monitor-ops-spec.md)
QUIET_S = 90             # running worker with no output this long -> "quiet"
STALL_S = 300            # ... this long and cpu < STALL_CPU -> "stalled"
STALL_CPU = 1.0
SESSION_GAP_S = 2 * 3600  # a quiet gap this long separates two sessions
ACTIVE_S = 900             # an open session with no activity for this long is idle
CLOSED_KEEP_S = 48 * 3600  # a closed session quiet this long drops off the overview (counted as hidden; `a` shows it)
IDLE_KEEP_S = 72 * 3600   # a station with no lead and nothing running stays listed as idle this long
IDLE_LIST_S = 2 * 3600     # ops console: an idle session quiet longer than this is shown as closed (count only, the user 2026-10-24)
CLOSED_LIST_S = 24 * 3600  # and one quiet longer than this is hidden unless its WorkState badge is not clean

TOOL_CATS = ("read", "edit", "shell", "search", "other")
FAMILIES = ("claude", "codex", "grok", "deepseek", "cursor", "other")
# Worker.state values
RUNNING, QUIET, STALLED, DONE, FAILED, NOREPORT = "running", "quiet", "stalled", "done", "failed", "noreport"
LIVE_STATES = (RUNNING, QUIET, STALLED)


@dataclass
class ExecutionRecord:
    execution_id: str
    schema: int = 1
    harness: str = ""
    account: str = ""
    native_id: str = ""
    role: str = "worker"
    parent_id: str = ""
    root_id: str = ""
    linkage: str = "unknown"
    cwd: str = ""
    title: str = ""
    goal: str = ""
    purpose_source: str = ""
    brief_ref: str = ""
    result_ref: str = ""
    pid: int | None = None
    pid_start: int | None = None
    boot_id: str = ""
    registered: float = 0.0
    started: float = 0.0
    activity: float = 0.0
    ended: float | None = None
    state: str = "running"
    outcome: str = "unknown"
    exit_code: int | None = None
    model: str = ""
    effort: str = ""
    current_action: str = ""
    output_available: bool = False
    context_tokens: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cache: int | None = None
    cost: float | None = None
    source: str = "registry"
    revision: int = 0


@dataclass
class ExecutionEvidence:
    record: ExecutionRecord
    event: str = "observe"
    brief: str = ""
    result: str = ""


@dataclass
class RegistrySnapshot:
    records: list[ExecutionRecord] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class Step:
    ts: float      # epoch seconds; 0.0 if the log carries no time
    kind: str      # "tool" | "text" | "error"
    tool: str      # raw tool name for kind == "tool" (lowercase, e.g. "read", "bash"), else ""
    text: str      # one line, newlines collapsed to spaces, not truncated
    dur: float | None = None   # seconds the step took; None when the log does not say
    outcome: str = ""          # "ok" | "failed" | "" (not reported / still running)


@dataclass
class LogState:
    path: str = ""
    offset: int = 0                      # bytes already parsed (incremental parsing)
    harness: str = "unknown"             # "opencode" | "codex" | "claude" | "cursor" | "unknown"
    model: str = ""                      # model id found in the log, "" if none
    steps: list[Step] = field(default_factory=list)          # newest last, at most MAX_STEPS
    steps_total: int = 0                 # steps ever parsed; steps[0] is number steps_total - len(steps)
    tool_counts: dict[str, int] = field(default_factory=dict)  # keys from TOOL_CATS
    errors: int = 0
    tokens_in: int | None = None         # None = this log does not carry it
    tokens_out: int | None = None
    tokens_cache: int | None = None
    cost: float | None = None            # USD
    ctx_tokens: int | None = None        # context size at the latest step
    first_ts: float = 0.0
    last_ts: float = 0.0                 # time of the newest output
    pulses: list[tuple[float, int]] = field(default_factory=list)  # (epoch, events) for the sparkline
    tool_pulses: list[tuple[float, int]] = field(default_factory=list)  # (epoch second, tool calls) at EVENT time, last TOOL_RING_S
    ctx_window: int | None = None        # the model's context window in tokens; None = unknown (show "—", never guess a %)
    carry: str = ""                      # unterminated trailing line kept between parses
    codex_mode: str = ""                 # codex parser: block the last read ended in (logparse)


@dataclass
class CCEvent:
    """One line of the control center's event tail. key is "<stage path>|<worker name>" ("|" alone = the lead row)."""
    ts: float      # event time, epoch seconds; 0.0 if unknown
    key: str
    kind: str      # "tool" | "msg" | "state" | "err"
    text: str      # one line


@dataclass
class CCCall:
    """One tool call from a session or worker transcript, for the Tools tab."""
    ts: float
    tool: str
    arg: str = ""                        # one-line gist of the arguments
    outcome: str = ""                    # "ok" | "failed" | "" (not reported / still running)
    dur: float | None = None
    err: str = ""                        # first line of the error when it failed


@dataclass
class Worker:
    name: str
    state: str                           # RUNNING | QUIET | STALLED | DONE | FAILED | NOREPORT
    title: str = ""
    goal: str = ""
    model: str = ""                      # label as written, e.g. "luna@medium"; "" if unknown
    family: str = "other"                # one of FAMILIES
    pid: int | None = None
    start: float = 0.0
    end: float | None = None             # None while live
    cpu: float | None = None             # percent of one core, whole process tree
    rss: int | None = None               # bytes, whole process tree
    nproc: int | None = None
    log: LogState = field(default_factory=LogState)
    activity: list[int] = field(default_factory=list)   # ACT_BUCKETS ints, oldest first
    quiet_s: float = 0.0                 # seconds since the last output
    summary: list[str] = field(default_factory=list)    # REPORT.md "## Summary" lines
    has_report: bool = False
    is_review: bool = False
    job: str = ""                        # brief's "Job:" value, first word lowercased; "" if absent
    effort: str = ""                     # reasoning effort ("low", "medium", "high", ...); "" if unknown
    session: str = ""                    # id of the lead session that launched it (from <name>.session); "" if unlinked
    home: str = ""                       # the stage dir it lives in when its session row sits in another stage; "" = the row's own
    execution_id: str = ""
    account: str = ""
    native_id: str = ""
    parent_id: str = ""
    root_id: str = ""
    linkage: str = "unknown"
    outcome: str = "unknown"
    brief_text: list[str] = field(default_factory=list)
    result_text: list[str] = field(default_factory=list)
    purpose_source: str = ""
    output_available: bool = False
    parent_session: str = ""             # nearest known ancestor session (from <name>.session lineage); "" if none
    root_session: str = ""               # the top-level orchestrator's session id; "" if none
    depth: int = 0                       # launch depth from <name>.session (1 = launched by the top-level lead); 0 = unknown
    parent: str = ""                     # name of the worker in the same session row that launched this one; "" = the lead did
    level: int = 0                       # nesting level under the lead: 0 = direct, 1 = child of a worker, ...
    ancestors: tuple[int, ...] = ()      # pids above the launcher when this worker was launched (from <name>.session); not served
    launched: float = 0.0                # when it was launched (from <name>.session); 0 = unknown
    level_tag: str = ""                  # one of LEVEL_TAGS; "" = not computed (renderer falls back to WORKER)
    flags: frozenset[str] = frozenset()  # file-derived problems: "no log", "empty log", "empty report", "stdin hang"
    failure: str = ""                    # one-line reason a FAILED worker failed; "" if none known
    harness: str = ""                    # "codex" | "claude" | "opencode" | "cursor" | ""; control center row field
    prompt: list[str] = field(default_factory=list)          # the real first user message (not injected instructions)
    system_prompt: list[str] = field(default_factory=list)   # the system prompt when the transcript carries it
    resume_cmd: str = ""                 # e.g. "codex resume <id>"; "" = cannot resume
    calls: list[CCCall] = field(default_factory=list)        # tool calls, newest LAST, at most 200


@dataclass
class Group:
    """Workers launched together: one dispatch line in PROGRESS.md, or starts within a minute of each other."""
    label: str                           # distinct jobs joined with "+", else the common name prefix, else "workers"
    workers: list[str]                   # worker names, in start order
    started: float                       # first start, epoch seconds
    done: int                            # DONE, FAILED or NOREPORT
    total: int
    live: int                            # workers in LIVE_STATES
    finished: bool = False               # every worker is done
    finished_at: float | None = None     # newest end, once finished
    current: bool = False                # active: has a live worker (any number of groups), or the newest group when none is live

    @property
    def key(self) -> str:
        return "|".join(self.workers)


@dataclass
class Finding:
    prio: int      # 1 = P1 (worst)
    text: str


@dataclass
class Review:
    name: str
    model: str = ""
    family: str = "other"
    start: float = 0.0
    end: float = 0.0                     # log mtime
    running: bool = False
    findings: list[Finding] = field(default_factory=list)


@dataclass
class Milestone:
    ts: float | None   # None when the PROGRESS.md line has no HH:MM prefix
    text: str


@dataclass
class QuotaWindow:
    name: str          # "5h", "Week", ...
    used: float        # percent
    pace: float | None
    stale: bool = False
    window: str = ""                  # raw id: "five_hour", "seven_day", ...; "" when unknown
    resets_at: float | None = None    # epoch seconds; None when the vendor gives no reset time
    projected_pct: float | None = None
    runs_out_at: float | None = None  # epoch seconds the window is projected to hit 100%


@dataclass
class QuotaCredit:
    balance: float | None      # USD left; None until a balance is recorded
    burn_per_day: float | None
    runs_out_at: float | None  # epoch seconds
    low: bool = False


@dataclass
class QuotaExtra:
    usd: float | None   # spent past the cap since it was hit; None = no cost data for this account
    method: str         # "vendor" and "measured" are exact, "estimated" is tokens x list price
    since: float        # epoch seconds the cap was hit


@dataclass
class QuotaAccount:
    name: str          # "codex", "claude-work", ...
    windows: list[QuotaWindow] = field(default_factory=list)
    stale: bool = False
    in_use: bool = False   # a worker in this session runs on this account
    credit: QuotaCredit | None = None   # pay-as-you-go account: shown as a balance line, not a bar
    extra: QuotaExtra | None = None     # a cap is running now: what the account has spent past it


OPENROUTER_ACCOUNT = "openrouter"
OPENROUTER_BALANCE_STALE_S = 30 * 60   # balanceAt older than this -> stale chip
OPENROUTER_MAX_ROWS = 400          # a runaway quota reply cannot swell every refresh and API response


@dataclass
class OpenRouterSpendDay:
    date: str       # UTC YYYY-MM-DD
    usd: float
    requests: int


@dataclass
class OpenRouterSpendModel:
    model: str
    usd: float
    requests: int
    prompt_tokens: int
    completion_tokens: int


@dataclass
class OpenRouterSnapshot:
    balance: float | None
    floor_usd: float | None
    burn_per_day: float | None
    runs_out_at: float | None   # epoch seconds
    balance_at: float | None    # epoch seconds of the last balance reading
    low: bool
    stale: bool
    since: str                  # spend window start (UTC day)
    per_day: list[OpenRouterSpendDay]
    per_model: list[OpenRouterSpendModel]


@dataclass
class Snapshot:
    title: str = ""
    now: float = 0.0
    session_start: float = 0.0
    all_history: bool = False
    workers: list[Worker] = field(default_factory=list)   # live first (oldest first), then finished (newest first)
    hidden_older: int = 0                # workers outside the session
    reviews: list[Review] = field(default_factory=list)   # newest first
    milestones: list[Milestone] = field(default_factory=list)  # oldest first
    quota: list[QuotaAccount] = field(default_factory=list)
    quota_ok: bool = False               # False -> hide the quota panel
    openrouter: OpenRouterSnapshot | None = None      # the usage screen (global view only)
    openrouter_ok: bool = False
    usage_history: dict[str, list[float]] = field(default_factory=dict)   # account -> 24h of % used, oldest first
    tokens_total: int | None = None
    cost_total: float | None = None
    services: list[tuple[str, bool]] = field(default_factory=list)  # non-agent pid files: (name, up)
    groups: list[Group] = field(default_factory=list)     # newest first; the current group is flagged


SCAN_DEPTH = 4           # global view: directory levels searched under each root for a stage dir
RESCAN_S = 30            # global view: seconds between searches for new stage dirs
STAGE_NAMES = (".stage",)
DONE_SHOWN = 3           # global view: finished-ok rows shown per block before "+N more"
GROW_KINDS = ("stage", "milestone", "reviews", "worker")   # selectable tree rows, in block order
GPAGES = ("", "milestones", "reviews", "worker", "stage", "openclaw", "wk")  # "worker"/"stage" reuse the single-stage screens; "openclaw"/"wk" are the ops pages


@dataclass
class LeadInfo:
    model: str = ""      # label as written, e.g. "Luna" or "opus"
    phase: str = ""      # "build", "review", ...; "" if unknown
    stream: str = ""     # stream file name without directory or ".md"; "" if unknown


@dataclass
class TurnTool:
    """One tool call inside an OpenClaw turn."""
    ts: float                            # epoch seconds of the call
    name: str                            # tool name, e.g. "exec", "read"
    ok: bool | None = None               # None while running or when the result was not recorded
    dur: float | None = None             # seconds from call to result; None if unknown
    args: str = ""                       # one-line gist of the arguments, already cut short


@dataclass
class Turn:
    """One OpenClaw turn: a prompt and its reply (trajectory_runtime_events grouped by run_id; last model.completed wins)."""
    run_id: str
    start: float                         # prompt.submitted time, epoch seconds
    end: float | None = None             # model.completed time of the final attempt; None while running
    ask: str = ""                        # first line of the user message, cut short; "" if unknown
    model: str = ""
    provider: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cache: int | None = None      # cacheRead
    cost: float | None = None            # USD; None when the provider is unmetered (show "—")
    latency: float | None = None         # seconds, end - start
    tools: dict[str, int] = field(default_factory=dict)   # tool name -> calls, for the TOOLS cell ("exec×3 read×1")
    calls: list[TurnTool] = field(default_factory=list)   # oldest first, at most 40
    attempts: int = 1                    # model.completed rows for this run_id (2+ = retried)
    outcome: str = "ok"                  # "ok" | "error" | "aborted" | "running"
    error: str = ""                      # one line, the failed attempt's reason


@dataclass
class Vitals:
    """An OpenClaw session's vitals strip (session_nodes.entry_json)."""
    model: str = ""
    provider: str = ""
    ctx_used: int | None = None          # contextBudgetStatus.estimatedPromptTokens
    ctx_budget: int | None = None        # contextTokens (window); None = unknown
    compactions: int = 0
    cost: float | None = None            # estimatedCostUsd; None when unmetered
    runtime: float | None = None         # seconds (runtimeMs / 1000)
    status: str = ""                     # "running" | "done" | "failed" | "killed" | "timeout" | ""
    last_error: str = ""


@dataclass
class WorkState:
    """Git state of the directory a session worked in, for the work-state badge and the session sweep."""
    branch: str = ""
    dirty: bool | None = None            # uncommitted changes; None = not a git checkout / unknown
    ahead: int | None = None             # commits not on its upstream; None = no upstream
    unmerged: bool | None = None         # branch has commits not in the default branch; None = unknown / is the default branch
    checked: float = 0.0                 # epoch of the check (cache key with the dir's HEAD/index mtime)

    def badge(self) -> str:
        """The overview badge text: worst state first, "" when clean."""
        if self.dirty:
            return "uncommitted"
        if self.ahead:
            return f"{self.ahead} unpushed"
        if self.unmerged:
            return "unmerged"
        if self.unmerged is False and self.branch and self.dirty is False:
            return "merged"
        return ""


@dataclass
class Stage:
    path: str                            # absolute stage directory
    label: str                           # project name shown, e.g. "ornith-dash", "worker-dashboard/backend"
    snap: Snapshot = field(default_factory=Snapshot)   # that stage's normal snapshot (quota left empty)
    live: int = 0                        # workers in LIVE_STATES
    last_activity: float = 0.0           # newest worker start/end, milestone or review time; 0.0 if none
    lead: LeadInfo | None = None         # None when unknown or older than the session window
    lead_state: str = "gone"             # "live" | "background" | "gone": the station's interactive lead process
    lead_pid: int | None = None          # the lead process; None when gone
    lead_since: float = 0.0              # the lead process's start time, epoch seconds; 0.0 when gone
    lead_kind: str = ""                  # "claude", "claude work", "claude bg", "codex", "t3 code", "cursor", "opencode", "lead.json"; "" when gone
    lead_ctx: int | None = None          # the lead's context window now (tokens); None when its transcript is unreadable
    solo: bool = False                   # a lead is running and has delegated nothing this session; true for a station with no stage dir
    # one Stage is one SESSION row (see docs/streams/monitor-design.md); `path` stays unique per row
    dir: str = ""                        # the real stage directory the workers live in; "" means `path` itself
    session_id: str = ""                 # the lead session this row stands for; "" for an unlinked group of older workers
    session_name: str = ""               # short name, at most 3 words: lead.json "name", else the harness title cut to 3 words
    tag: str = ""                        # harness tag: "CC·W" claude work, "CC·P" claude personal, "CDX" codex, "OCode" opencode, "OClaw" openclaw, "CUR" cursor
    parent: str = ""                     # the session_id of the session that spawned this one; "" for none
    app: bool = False                   # reachable through Claude Remote Control
    app_id: str = ""                     # Claude bridge session id
    tmux: str = ""                       # tmux session name, without window/pane locator
    root_session: str = ""               # root launching session id, when exported
    launch_depth: int = 0                # launch chain depth, when exported
    cluster: str = ""                    # "cron" for an OpenClaw automation: the overview lists these in a group of their own
    lead_model: str = ""                 # the session's own model as a short label, e.g. "sonnet-5.5"; "" if unknown
    effort: str = ""                     # the session's own effort/thinking level; "" if unknown
    unlinked: bool = False               # the "session unknown" group: workers with no <name>.session, shown in the idle area
    state: str = ""                      # "active" | "idle" | "closed": lead open on this machine (local or ssh tty) and active in the last ACTIVE_S, open but quiet, or not open
    pinned: str = ""                     # "idle" | "active" when the user set this station's state by hand (overrides file); "" when automatic
    activity: float = 0.0                # epoch of the session's last activity (harness transcript, its workers' output); 0.0 if unknown
    cwd: str = ""                        # the directory the session was started in ("" if unknown)
    workdirs: list[str] = field(default_factory=list)   # directories its workers run in (brief Cwd:), when they differ from the stage project
    doing: list[tuple[float, str]] = field(default_factory=list)   # a solo claude lead's latest actions from its transcript, oldest first: (epoch, "Edit render.py")
    execution_id: str = ""
    account: str = ""
    parent_id: str = ""
    root_id: str = ""
    goal: str = ""
    brief_text: list[str] = field(default_factory=list)
    linkage: str = "unknown"
    native_id: str = ""                  # the root execution's harness-native session id; "" if unknown
    outcome: str = ""                    # the root execution's reported outcome; "" if unknown
    purpose_source: str = ""             # where the root execution's goal came from; "" if unknown
    unattached: bool = False             # the "unattached" diagnostic: workers with no known lead; listed, but the renderer hides it by default
    doing_note: str = ""                 # why a solo claude lead shows no doing: "no session file", "no transcript"; "" when it has doing or is not solo claude
    level_tag: str = ""                  # one of LEVEL_TAGS for the session row; "" = not computed (renderer: LEAD at depth 0, else SPAWN)
    display_name: str = ""               # unique row name when session_name repeats (OpenClaw: from the session key); "" = use session_name
    lead_ctx_window: int | None = None   # the lead's context window in tokens; None = unknown
    lead_tokens: int | None = None       # the lead session's cumulative tokens; None = unknown
    lead_cost: float | None = None       # the lead session's cumulative USD; None = unknown / subscription
    lead_tools: int | None = None        # the lead session's tool calls so far; None = unknown
    lead_tool_pulses: list[tuple[float, int]] = field(default_factory=list)   # lead tool calls at event time, last TOOL_RING_S
    vitals: Vitals | None = None         # OpenClaw sessions only
    turns: list[Turn] = field(default_factory=list)   # OpenClaw sessions only, newest LAST, at most 50
    work: WorkState | None = None        # git state of cwd/dir; None = not checked
    harness: str = ""                    # "codex" | "claude" | "opencode" | "openclaw" | "cursor" | ""; control center row field
    prompt: list[str] = field(default_factory=list)          # the session's real first user message
    system_prompt: list[str] = field(default_factory=list)
    resume_cmd: str = ""                 # "codex resume <id>" / "claude --resume <id>"; "" = cannot resume
    transcript_path: str = ""            # the session's transcript/rollout file; "" = unknown
    calls: list[CCCall] = field(default_factory=list)        # the lead's tool calls, newest LAST, at most 200


@dataclass
class GlobalSnapshot:
    now: float = 0.0
    all_history: bool = False
    stages: list[Stage] = field(default_factory=list)   # live first, then newest last_activity first
    hidden_idle: int = 0                 # stages left out: nothing live and no activity for SESSION_GAP_S
    dismissed: int = 0                   # stages the user closed; counted so shown + hidden_idle + dismissed = discovered
    unattached: int = 0                  # workers in unattached stages (Stage.unattached); a diagnostic count, hidden by default
    partial: bool = False                # the first quick pass: the masthead shows "loading…" until the full pass lands
    errors: list[str] = field(default_factory=list)  # collector or parse failures, shown instead of an empty tree
    quota: list[QuotaAccount] = field(default_factory=list)
    quota_ok: bool = False
    openrouter: OpenRouterSnapshot | None = None
    openrouter_ok: bool = False          # False -> treat openrouter block as absent
    usage_history: dict[str, list[float]] = field(default_factory=dict)   # account -> 24h of % used, oldest first
    tokens_total: int | None = None      # summed over the stages shown
    cost_total: float | None = None


@dataclass
class UIState:
    title_aspect: float = 0.0            # width/height of the masthead title image in terminal cells; 0.0 = no image support (set by the app)
    title_place: tuple[int, int] = (0, 0)   # columns, rows the last render gave the title image; (0, 0) = drawn as text
    view: str = "overview"               # "overview" | "detail" | "quota" | "openrouter" (global)
    sel: int = 0                         # index into Snapshot.workers
    tab: str = "live"                    # "live" | "brief" | "report"
    scroll: int = 0                      # lines scrolled up from the bottom (live) or down from the top
    follow: bool = True
    muted: bool = False
    density: str = "full"                # "full" | "compact"
    allow_actions: bool = False           # process actions require the explicit --allow-actions opt-in
    lead_open: bool = False              # overview: the milestone list is expanded
    # mouse: the hit map of the frame just drawn, as (row0, row1, col0, col1, action, target), 1-based and inclusive.
    # actions: key, back, lead, toggle_panel, select, card (click); scroll (wheel). Set by render_frame / render_global.
    hits: list[tuple[int, int, int, int, str, str]] = field(default_factory=list)
    last_click: tuple[int, float, str, str] = (0, -1e9, "", "")   # previous left press: row, time, action, target
    pscroll: dict[str, int] = field(default_factory=dict)   # wheel offset per panel; absent = follow the cursor
    ptop: dict[str, int] = field(default_factory=dict)      # top row each scrollable panel was last drawn at
    detail_text: list[str] = field(default_factory=list)   # brief/report lines for the detail view
    flashes: dict[str, float] = field(default_factory=dict)  # worker name or "reviews" -> epoch of the event
    # global view (all orchestrators); unused in the single-stage view.
    # A tree row is (stage path, kind, name): kind is one of GROW_KINDS, name is the worker name for
    # "worker" rows and "" otherwise.
    gsel: int = 0                        # index into render.global_rows(...)
    gopen: set[tuple[str, str, str]] = field(default_factory=set)   # rows opened in place: stage, worker detail, "more"
    gfolded: set[str] = field(default_factory=set)     # stage paths folded shut although they have live workers
    gexpanded: set[str] = field(default_factory=set)   # stage paths showing every finished worker
    gfilter: str = "all"                 # overview filter: all | working | lagging | problems
    gquery: str = ""                     # overview text filter (project, stream, lead, model)
    gedit: bool = False                  # typing into the text filter
    ghide_idle: bool = False             # overview: the idle section (open but quiet sessions) hidden (h toggles)
    ghide_closed: bool = False           # overview: the closed section hidden (H toggles)
    gunattached: bool = False            # overview: show the unattached runs (A toggles)
    gquips: bool = True                  # overview: one quip line per station (t toggles)
    glegend: bool = False                # overview: the lead/worker legend and column headings (i toggles)
    gpage: str = ""                      # full-screen page over the tree: one of GPAGES ("" = the tree)
    gstage: str = ""                     # stage path the open page belongs to
    # station view keys
    panel: str = ""                      # cursor on a panel title row: "timeline" | "reviews"; "" = a worker row
    folded: set[str] = field(default_factory=set)   # panels folded to their one-line title
    expanded: set[str] = field(default_factory=set)   # worker names shown open: a card (live) or summary (finished)
    group_open: set[str] = field(default_factory=set)  # Group.key of earlier groups unfolded in the station view
    pinned: set[str] = field(default_factory=set)     # finished workers the user opened; kept open if they run again
    help: bool = False                   # ? overlay open
    # global view: close actions
    close_ask: tuple[str, int] | None = None   # (station path, lead pid) waiting on the y/n confirm box
    closing: set[str] = field(default_factory=set)  # stations whose lead got SIGTERM; dismissed once it is gone
    # station pages (view "lead" | "cost" | "files") and the compact action
    note: str = ""                       # one status line under the station or page: the last action's result
    # ops console: expand choices the user made by hand, keyed "s:<session_id>" or "p:<project root>"; persisted in
    # Station's status-overrides.json under "expand". A key in gopen_keys is open, in gshut_keys shut; neither = default
    # (open only while live).
    gopen_keys: set[str] = field(default_factory=set)
    gshut_keys: set[str] = field(default_factory=set)
    gpin_keys: set[str] = field(default_factory=set)  # expansion choices opened while the row was already idle
    gexpand_order: list[str] = field(default_factory=list)  # most recently chosen keys last, for the persisted cap
    gturn: str = ""                      # openclaw page: run_id of the turn opened to its tool calls; "" = none
    gworker: str = ""                    # worker page ("wk"): the worker name on stage gstage
    gsess: bool = False                  # the worker page was opened from the session page: Esc goes back there
    # control center (master-detail view, >=120 cols): see docs/monitor-control-center-plan.md
    cc: bool = False                     # master-detail view on (the app turns it on in global mode; v flips to the classic tree)
    cc_active: bool = False              # the last frame was drawn by the control center (render sets it; cckeys keys on it)
    cc_focus: str = "table"              # "table" | "pane" | "strip": which part takes arrows and wheel
    cc_pane_open: bool = False           # the detail pane is shown (the user 2026-10-24: closed by default; a click or `d` exposes it)
    cc_tab: str = "Overview"             # detail pane tab, one of ccpane.PANE_TABS
    cc_strip: str = "Usage"              # bottom strip tab, one of ccstrip.STRIP_TABS
    cc_strip_open: bool = True           # the bottom strip shown (e toggles)
    cc_pane_scroll: int = 0              # detail pane: lines scrolled down from the top
    cc_strip_scroll: int = 0             # strip: lines scrolled back from the newest
    cc_confirm: tuple[str, str, str] | None = None   # (action, row key "<stage path>|<worker>", label) waiting on y
    cc_spawn: bool = False               # the spawn model picker is open
    cc_spawn_i: int = 0                  # highlighted choice in the spawn picker
    cc_spawn_model: str = ""             # picker id chosen in the spawn picker; "" = still choosing; then cc_input takes the task text
    cc_spawn_row: str = ""               # row key ("<stage path>|") the spawn picker was opened from; the project dir is resolved from it, not from the live selection
    cc_input: str = ""                   # the task text being typed for a spawn (one line)


def nest_workers(workers: list[Worker]) -> list[Worker]:
    """``workers`` with each nested worker moved right after the worker that launched it (children in the order given,
    at any depth). A worker whose ``parent`` is not in the list, or that would close a loop, stays where it was."""
    names = {wk.name for wk in workers}
    kids: dict[str, list[Worker]] = {}
    for wk in workers:
        if wk.parent and wk.parent in names and wk.parent != wk.name:
            kids.setdefault(wk.parent, []).append(wk)
    out: list[Worker] = []
    seen: set[str] = set()

    def add(wk: Worker) -> None:
        if wk.name in seen:
            return
        seen.add(wk.name)
        out.append(wk)
        for child in kids.get(wk.name, ()):
            add(child)

    for wk in workers:
        if not (wk.parent and wk.parent in names and wk.parent != wk.name):
            add(wk)
    for wk in workers:   # a parent loop has no root: keep its members listed
        add(wk)
    return out
