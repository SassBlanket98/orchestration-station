"""Turn a `.stage` directory into the frozen §3 structures.

Reading is bounded and read-only. A worker's directory is never listed: the
only path ever stat'ed inside one is `<worker>/REPORT.md`, so `node_modules`
and worktree contents cost nothing. `StageParser` keeps a per-file cache keyed
by `(mtime_ns, size)` so an unchanged file is never re-read.
"""

import json
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stagewatch import logparse, logsteps, picks, procinfo, tail
from stagewatch.lead import _evidence, parse_lead
from stagewatch.models import (
    Access,
    Artifact,
    ArtifactKind,
    Counts,
    CycleEvent,
    CycleProgress,
    Engine,
    EngineFamily,
    ExtraLog,
    Finding,
    LeadIdentity,
    LogFormat,
    LogIndexEntry,
    NowLine,
    PickCandidate,
    PickRecord,
    Review,
    RunningWorker,
    SessionDetail,
    SessionSummary,
    StepEvent,
    Worker,
    WorkerEvent,
    WorkerPreview,
    WorkerStatus,
)

WORKER_STEM_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# The stream file a lead writes into the stage when there is no lead.json.
STREAM_FILE = "stream.md"
SECRET_NAME_RE = re.compile(r"(?:\.env|id_rsa|credentials|auth\.json)$", re.IGNORECASE)
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
LEAD_WARNING = "lead state unresolved: conflicting or partial evidence"
CHECK_STEM_RE = re.compile(r"^final-|-(?:lint|test|typecheck|red)$")
FINDING_RE = re.compile(r"^- \[P(\d)\]\s*(.+)$")
REVIEW_PREFIX = "review-"
# A worker's `<stem>.session` JSON names its lead and root session; bigger files are ignored.
SESSION_READ_CAP = 64 * 1024
STALE_SECONDS = 300.0
PID_GUARD_SECONDS = 5.0
# A launcher may write `<stem>.pid` seconds after the worker process is up, so
# the lower edge tolerates a start this far before the file; the upper edge
# stays tight to catch a pid recycled by a later process.
PID_REUSE_WINDOW_SECONDS = 120.0
TEXT_CAP = 256 * 1024
STDIN_HANG_PREFIX = "Reading additional input from stdin"
EM_DASH = "\u2014"

# A log signature is the strongest evidence: it must not be overridden by a
# model name that happens to contain another engine's token.
ENGINE_BY_FORMAT: dict[LogFormat, Engine] = {
    "opencode-jsonl": "opencode",
    "codex-text": "codex",
    "claude-stream-json": "claude",
    "cursor-jsonl": "cursor",
    "cursor-text": "cursor",
}
_CLAUDE_MODEL_MARKERS = ("claude", "sonnet", "haiku", "opus")
_CURSOR_MODEL_MARKERS = ("cursor", "composer", "grok")
_CODEX_MODEL_MARKERS = ("codex", "gpt-6-luna", "gpt-6-sol", "gpt-5")


def engine_from_evidence(fmt: LogFormat, model: str | None) -> Engine:
    """Classify a worker's engine: log signature first, then clear model evidence.

    A model token alone never overrides a recognised OpenCode, Cursor, Claude or
    Codex log signature. `unknown` when neither source is decisive.
    """
    by_log = ENGINE_BY_FORMAT.get(fmt)
    if by_log is not None:
        return by_log
    if not model:
        return "unknown"
    lowered = model.lower()
    if "opencode" in lowered:
        return "opencode"
    if any(marker in lowered for marker in _CURSOR_MODEL_MARKERS):
        return "cursor"
    if any(marker in lowered for marker in _CLAUDE_MODEL_MARKERS):
        return "claude"
    if any(marker in lowered for marker in _CODEX_MODEL_MARKERS):
        return "codex"
    return "unknown"


IGNORED_SUFFIXES = (".err", ".json", ".patch")
IGNORED_FILES = frozenset({"contract.md"})
IGNORED_DIRS = frozenset({"demo", "live"})
# Lead inputs: scanned so a change to them shows up in the snapshot signature.
STATE_FILES = frozenset({"PROGRESS.md", "lead.json", STREAM_FILE})

EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})
_WORK_ACCOUNT_RE = re.compile(r"@work\b|\(work\)", re.IGNORECASE)
_MODE_RE = re.compile(r"\(mode\s+([A-Za-z][\w-]*)\)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9._@/-]+")
_CURSOR_ASK_RE = re.compile(r"\(mode\s+ask\)", re.IGNORECASE)
_SANDBOX_LINE_RE = re.compile(r"^sandbox:\s*(\S+)")
_JOB_LINE_RE = re.compile(r"^Job:\s*(.+)$")
# Codex `sandbox:` header values. Anything else is left unknown rather than guessed.
SANDBOX_ACCESS: dict[str, Access] = {
    "read-only": "ro",
    "workspace-write": "rw",
    "danger-full-access": "rw",
}
# Bounds on what a dashboard response carries per worker. The newest entries win.
STEP_EVENT_LIMIT = 200
JOB_TEXT_LIMIT = 120
EVENT_TEXT_LIMIT = 200


@dataclass(frozen=True)
class ModelIdentity:
    engine_family: EngineFamily | None = None
    account: str | None = None
    effort: str | None = None
    mode: str | None = None


def _model_family(lowered: str, account: str | None) -> EngineFamily:
    if "opencode" in lowered:
        return "opencode"
    if any(marker in lowered for marker in _CURSOR_MODEL_MARKERS):
        return "cursor"
    if any(marker in lowered for marker in _CLAUDE_MODEL_MARKERS):
        return "claude-work" if account == "work" else "claude"
    if any(marker in lowered for marker in _CODEX_MODEL_MARKERS):
        return "codex"
    return "unknown"


def parse_model_identity(text: str | None) -> ModelIdentity:
    """Best-effort reading of the `<stem>.model` line. Never reads the worker name.

    The format is undocumented, so each field is only set when its token is
    plainly present: an effort word, `(mode X)`, or a `(work)` / `@work` account.
    With no model text every field is None; with text the family is `unknown`
    when no engine marker matches.
    """
    if not text:
        return ModelIdentity()
    account = "work" if _WORK_ACCOUNT_RE.search(text) else None
    effort = next(
        (token.lower() for token in _TOKEN_RE.findall(text) if token.lower() in EFFORTS),
        None,
    )
    mode_match = _MODE_RE.search(text)
    return ModelIdentity(
        engine_family=_model_family(text.lower(), account),
        account=account,
        effort=effort,
        mode=mode_match.group(1) if mode_match else None,
    )


def _access(model_text: str | None, sandbox: str | None) -> tuple[Access | None, str | None]:
    """`ro`/`rw` from a Codex sandbox header, else Cursor's `(mode ask)`, else unknown."""
    if sandbox is not None and sandbox in SANDBOX_ACCESS:
        return SANDBOX_ACCESS[sandbox], "codex_log_sandbox"
    if model_text and _CURSOR_ASK_RE.search(model_text):
        return "ro", "cursor_model_mode"
    return None, None


def codex_sandbox(path: Path) -> str | None:
    """The `sandbox:` value from a Codex transcript header, or None.

    Only the first 40 lines are read, and only when the `OpenAI Codex` banner is
    among them, so a prose log that happens to contain `sandbox:` is ignored.
    """
    lines = (read_text_capped(path, logparse.HEAD_BYTES) or "").splitlines()[:40]
    if not any(line.startswith("OpenAI Codex") for line in lines):
        return None
    for line in lines:
        match = _SANDBOX_LINE_RE.match(line)
        if match:
            return match.group(1)
    return None


def _brief_job(path: Path) -> str | None:
    """A `Job: <name>` value on the brief's first non-blank line, else None."""
    first = first_nonblank_line(path)
    match = _JOB_LINE_RE.match(first) if first else None
    return _clean(match.group(1), JOB_TEXT_LIMIT) if match else None


def _to_pick_record(record: picks.PickRecord) -> PickRecord:
    """Copy a parsed pick field by field; the parser's `source` and `raw` stay behind."""
    return PickRecord(
        at=record.at,
        job=record.job,
        slices=record.slices,
        tier=record.tier,
        candidates=[
            PickCandidate(
                model=candidate.model,
                effort=candidate.effort,
                group=candidate.group,
                reason=candidate.reason,
            )
            for candidate in record.candidates
        ],
        assigned=record.assigned,
        skipped=record.skipped,
        is_repick=record.is_repick,
    )


def _pick_records(stage: Path) -> list[PickRecord]:
    """The run's pick records, or [] when the picks are missing or unreadable."""
    records, reason = picks.read_picks(stage)
    if reason is not None:
        return []
    return [_to_pick_record(record) for record in records]


def _step_events(steps: logsteps.LogSteps) -> list[StepEvent]:
    return [
        StepEvent(
            seq=step.seq,
            kind=step.kind,
            text=step.text,
            duration_ms=step.duration_ms,
            ok=step.ok,
            at=step.at,
        )
        for step in steps.steps[-STEP_EVENT_LIMIT:]
    ]


def _retry_events(stem: str, scan: "StageScan") -> list[WorkerEvent]:
    """One `retry` per `<stem>.attemptN.log` sibling, in attempt order.

    `at` is the attempt file's mtime in milliseconds, the same unit the log's own
    timestamps use, so the two sources can be compared.
    """
    pattern = re.compile(rf"^{re.escape(stem)}\.attempt(\d+)\.log$")
    attempts = []
    for relpath, fs in scan.files.items():
        match = pattern.match(relpath)
        if match:
            attempts.append((int(match.group(1)), fs))
    return [
        WorkerEvent(kind="retry", at=int(fs.mtime * 1000), text=f"attempt {number}")
        for number, fs in sorted(attempts, key=lambda item: item[0])
    ]


def _worker_events(
    stem: str, scan: "StageScan", derived: "_WorkerLog | None", stale: bool
) -> list[WorkerEvent]:
    """Retries, then the log's own events, then `idle` for a quiet non-empty log.

    An empty log is never `idle`: a `claude -p` run writes nothing until it exits.
    """
    events = _retry_events(stem, scan)
    if derived is not None:
        events.extend(
            WorkerEvent(
                kind=event.kind,
                at=event.at,
                text=_clean(event.text, EVENT_TEXT_LIMIT) or event.kind,
            )
            for event in derived.steps.events
        )
        if stale and derived.size > 0:
            events.append(
                WorkerEvent(
                    kind="idle",
                    at=int(derived.mtime * 1000),
                    text=f"no log output for over {int(STALE_SECONDS // 60)} min",
                )
            )
    return events


# --------------------------------------------------------------------------- #
# Small read helpers (bounded, read-only)
# --------------------------------------------------------------------------- #
def read_text_capped(path: Path, cap: int = TEXT_CAP) -> str | None:
    """Read at most `cap` bytes of a file as UTF-8, or None when unreadable."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(cap)
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


def first_nonblank_line(path: Path, limit: int = 64 * 1024) -> str | None:
    """The first non-blank line within the first `limit` bytes, or None."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit)
    except OSError:
        return None
    for raw in data.split(b"\n"):
        text = raw.decode("utf-8", errors="replace").strip()
        if text:
            return text
    return None


def parse_pid_text(text: str) -> int | None:
    """First whitespace token of a pid file as a positive int, else None."""
    parts = text.split()
    if not parts:
        return None
    try:
        pid = int(parts[0])
    except ValueError:
        return None
    return pid if pid > 0 else None


def pid_is_alive(pid: int | None, pid_mtime: float | None) -> bool:
    """True only when `pid` can be the worker that wrote `<stem>.pid`.

    `PID_GUARD_SECONDS` bounds the upper edge of PID reuse: `procinfo.is_alive`
    rejects a process that started after the pid file was written (a later
    process reused the pid). `PID_REUSE_WINDOW_SECONDS` bounds the lower edge: a
    start more than that before the pid file mtime cannot have written the file
    at startup, so the pid has been recycled.
    """
    if pid is None:
        return False
    not_after = (pid_mtime + PID_GUARD_SECONDS) if pid_mtime is not None else None
    if not procinfo.is_alive(pid, not_after):
        return False
    if pid_mtime is None:
        return True
    started = procinfo.start_time(pid)
    return started is not None and started >= pid_mtime - PID_REUSE_WINDOW_SECONDS


def resolve_brief(stage: Path, stem: str) -> Path | None:
    """`briefs/<stem>.md`, else the first sorted `briefs/<stem>-*.md`."""
    briefs = stage / "briefs"
    exact = briefs / f"{stem}.md"
    try:
        if exact.is_file():
            return exact
    except OSError:
        return None
    prefix = f"{stem}-"
    try:
        names = sorted(
            entry.name
            for entry in briefs.iterdir()
            if entry.name.startswith(prefix) and entry.name.endswith(".md") and entry.is_file()
        )
    except OSError:
        return None
    return briefs / names[0] if names else None


# --------------------------------------------------------------------------- #
# Pure parsers for individual file bodies
# --------------------------------------------------------------------------- #
def parse_brief(text: str) -> tuple[str | None, str | None]:
    """Return `(title, goal)` from a brief body."""
    lines = text.splitlines()
    title: str | None = None
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip() or None
            break

    goal: str | None = None
    for index, line in enumerate(lines):
        if not line.startswith("Goal:"):
            continue
        parts = [line[len("Goal:") :].strip()]
        for cont in lines[index + 1 :]:
            stripped = cont.strip()
            if not stripped or stripped.startswith("#"):
                break
            parts.append(stripped)
        joined = " ".join(part for part in parts if part)
        goal = joined[:400] or None
        break
    return title, goal


def parse_summary(text: str) -> list[str]:
    """Non-empty lines under `## Summary` (max 4), else the not-met fallback."""
    lines = text.splitlines()

    def section(heading: str) -> list[str]:
        out: list[str] = []
        index = 0
        while index < len(lines):
            if lines[index].strip() == heading:
                index += 1
                while index < len(lines):
                    stripped = lines[index].strip()
                    if stripped.startswith("#"):
                        break
                    if stripped:
                        out.append(stripped)
                    index += 1
                break
            index += 1
        return out

    summary = section("## Summary")[:4]
    if summary:
        return summary
    fallback = section("## Plan requirements not met")[:2]
    return [f"not met: {line}" for line in fallback]


def parse_milestones(text: str) -> list[str]:
    """Non-blank PROGRESS.md lines, newest first."""
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line][::-1]


def parse_findings(lines: list[str]) -> list[Finding]:
    """`- [Pn] text` lines, deduplicated on the text before the first em dash."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for line in lines:
        match = FINDING_RE.match(line)
        if match is None:
            continue
        text = match.group(2).strip()
        key = text.split(EM_DASH, 1)[0].strip()
        if key in seen:
            continue
        seen.add(key)
        findings.append(Finding(priority=f"P{match.group(1)}", text=text))
    return findings


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FileSig:
    relpath: str
    mtime_ns: int
    size: int

    @property
    def mtime(self) -> float:
        return self.mtime_ns / 1_000_000_000


@dataclass
class StageScan:
    stage: Path
    files: dict[str, FileSig] = field(default_factory=dict)

    def has(self, relpath: str) -> bool:
        return relpath in self.files


def _stat_file(stage: Path, path: Path) -> os.stat_result | None:
    try:
        st = path.stat()
        if not path.resolve(strict=True).is_relative_to(stage.resolve(strict=True)):
            return None
    except (OSError, RuntimeError):
        return None
    import stat as _stat

    return st if _stat.S_ISREG(st.st_mode) else None


def _is_worker_stem(stem: str) -> bool:
    return bool(WORKER_STEM_RE.match(stem)) and not stem.startswith(REVIEW_PREFIX)


def scan_stage(stage: Path) -> StageScan:
    """One `scandir` of the stage dir plus `briefs/`, and a REPORT.md stat each."""
    scan = StageScan(stage)
    try:
        with os.scandir(stage) as iterator:
            entries = list(iterator)
    except OSError:
        return scan

    for entry in entries:
        name = entry.name
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            if name in IGNORED_DIRS or not WORKER_STEM_RE.match(name):
                continue
            st = _stat_file(stage, stage / name / "REPORT.md")
            if st is not None:
                rel = f"{name}/REPORT.md"
                scan.files[rel] = FileSig(rel, st.st_mtime_ns, st.st_size)
            continue
        if name in STATE_FILES:
            st = _stat_file(stage, stage / name)
            if st is not None:
                scan.files[name] = FileSig(name, st.st_mtime_ns, st.st_size)
            continue
        if name in IGNORED_FILES or name.endswith(IGNORED_SUFFIXES):
            continue
        stem, dot, ext = name.rpartition(".")
        if not dot:
            continue
        if ext == "log":
            pass  # reviews and extra logs always matter
        elif ext == "pid":
            pass  # a pid file is worker evidence even for review- stems
        elif ext in ("model", "txt"):
            if not _is_worker_stem(stem):
                continue
        else:
            continue
        st = _stat_file(stage, stage / name)
        if st is not None:
            scan.files[name] = FileSig(name, st.st_mtime_ns, st.st_size)

    briefs = stage / "briefs"
    try:
        if not briefs.resolve(strict=True).is_relative_to(stage.resolve(strict=True)):
            return scan
        with os.scandir(briefs) as iterator:
            brief_entries = list(iterator)
    except (OSError, RuntimeError):
        return scan
    for entry in brief_entries:
        if not entry.name.endswith(".md"):
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        rel = f"briefs/{entry.name}"
        st = _stat_file(stage, briefs / entry.name)
        if st is not None:
            scan.files[rel] = FileSig(rel, st.st_mtime_ns, st.st_size)
    return scan


def _brief_relpath(stem: str, files: dict[str, FileSig]) -> str | None:
    exact = f"briefs/{stem}.md"
    if exact in files:
        return exact
    prefix = f"briefs/{stem}-"
    candidates = sorted(rel for rel in files if rel.startswith(prefix) and rel.endswith(".md"))
    return candidates[0] if candidates else None


def _log_relpath(stem: str, files: dict[str, FileSig]) -> str | None:
    for rel in (f"{stem}.log", f"{stem}.txt"):
        if rel in files:
            return rel
    return None


def worker_stems(scan: StageScan) -> set[str]:
    """Stems that satisfy at least one worker-identification rule.

    A review-prefixed stem is only a worker when it owns a pid file: live
    review agents are workers, pid-less review-*.log output is a Review.
    """
    files = scan.files
    stems: set[str] = set()
    for relpath in files:
        if "/" in relpath:
            continue
        stem, dot, ext = relpath.rpartition(".")
        if not dot:
            continue
        if ext == "pid" and WORKER_STEM_RE.match(stem):
            stems.add(stem)
            continue
        if not _is_worker_stem(stem):
            continue
        if ext in ("model",):
            stems.add(stem)
        elif ext in ("log", "txt") and _brief_relpath(stem, files) is not None:
            stems.add(stem)
    for relpath in files:
        if relpath.endswith("/REPORT.md"):
            stem = relpath[: -len("/REPORT.md")]
            if _is_worker_stem(stem):
                stems.add(stem)
    return stems


def worker_log_sizes(scan: StageScan) -> dict[str, int]:
    """Current log size of each worker that has a log, read from the scan alone."""
    sizes: dict[str, int] = {}
    for stem in worker_stems(scan):
        rel = _log_relpath(stem, scan.files)
        if rel is not None:
            sizes[stem] = scan.files[rel].size
    return sizes


# --------------------------------------------------------------------------- #
# Artifacts and lead state
# --------------------------------------------------------------------------- #
def _artifact_stat(stage: Path, rel: str) -> os.stat_result | None:
    """The stat of an allowlisted stage file, or None when it must not be served.

    `scan_stage` follows symlinks, so this checks the link itself: the file and
    its parent directory must not be symlinks, the file must be regular, and its
    resolved path must stay inside the stage. Secret-like names are refused.
    """
    path = stage / rel
    if SECRET_NAME_RE.search(path.name):
        return None
    try:
        if path.parent != stage and path.parent.is_symlink():
            return None
        st = os.lstat(path)
        real = path.resolve(strict=True)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or not real.is_relative_to(stage.resolve()):
        return None
    return st


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _clean(value: object, limit: int = 120) -> str | None:
    """A short single-line copy of a free-text value, or None when empty."""
    if value is None:
        return None
    text = CONTROL_CHARS_RE.sub(" ", str(value)).strip()
    return text[:limit] or None


# Same byte cap that parse_lead reads stream.md with.
LEAD_READ_CAP = 64 * 1024


def _stream_has_heading(stage: Path) -> bool:
    """True when stream.md has a phase/cycle heading, the evidence parse_lead prefers.

    parse_lead takes the stream's latest heading over PROGRESS.md, so a heading
    here means the stream supplied the phase and cycle. A file without one
    leaves them to PROGRESS.md, whatever `lead_source` says.
    """
    try:
        stream_stat = os.lstat(stage / STREAM_FILE)
    except OSError:
        return False
    if not stat.S_ISREG(stream_stat.st_mode) or stream_stat.st_size > LEAD_READ_CAP:
        return False
    text = read_text_capped(stage / STREAM_FILE, cap=LEAD_READ_CAP) or ""
    return bool(_evidence(text, headings_only=True))


def _lead_state(info: dict[str, Any], stage: Path) -> tuple[LeadIdentity, list[str]]:
    """Map `parse_lead` output onto the lead confidence ladder.

    A parse without both phase and cycle is `missing` when the stage holds no
    lead evidence at all, and `unresolved` when evidence exists but was not
    decisive (a conflict, a nested run, or a partial lead.json).
    """
    label = _clean(info["lead_label"])
    model = _clean(info["lead_model"])
    phase, cycle = info["phase"], info["cycle"]
    if phase is None or cycle is None:
        has_evidence = any(
            os.path.lexists(stage / name) for name in ("lead.json", STREAM_FILE, "PROGRESS.md")
        )
        if not has_evidence:
            return LeadIdentity(label=label, model=model, confidence="missing"), []
        identity = LeadIdentity(label=label, model=model, confidence="unresolved")
        return identity, [LEAD_WARNING]

    if info["lead_source"] == "lead.json":
        source, confidence = "lead.json", "explicit-stream"
    elif _stream_has_heading(stage):
        source, confidence = STREAM_FILE, "explicit-stream"
    else:
        source, confidence = "PROGRESS.md", "explicit-progress"
    identity = LeadIdentity(
        label=label,
        model=model,
        confidence=confidence,
        source_path=source,
        evidence=f"{phase} cycle {cycle}",
    )
    return identity, []


# --------------------------------------------------------------------------- #
# Per-file derived values (cached by mtime/size)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _WorkerLog:
    fmt: LogFormat
    size: int
    mtime: float
    model: str | None
    stdin_hang: bool
    now: NowLine | None
    steps: logsteps.LogSteps
    sandbox: str | None


@dataclass(frozen=True)
class _ReviewLog:
    model: str | None
    findings: list[Finding]


@dataclass(frozen=True)
class _ExtraLog:
    last_line: str | None


class StageParser:
    """Parses one stage dir, caching derived values per file."""

    def __init__(self, stage: Path) -> None:
        self.stage = stage
        # Keyed by (role, relpath): the same file can change role (a `.log` is a
        # worker log or an extra log) with no change to its own mtime.
        self._cache: dict[tuple[str, str], tuple[tuple[int, int], Any]] = {}
        self._scan: StageScan | None = None

    # -- cache ------------------------------------------------------------- #
    def _load(self, role: str, relpath: str, fs: FileSig, loader: Any) -> Any:
        key = (role, relpath)
        sig = (fs.mtime_ns, fs.size)
        cached = self._cache.get(key)
        if cached is not None and cached[0] == sig:
            return cached[1]
        value = loader()
        self._cache[key] = (sig, value)
        return value

    def _text(self, relpath: str, scan: StageScan) -> str:
        fs = scan.files[relpath]
        return self._load("text", relpath, fs, lambda: read_text_capped(self.stage / relpath) or "")

    def _pid(self, relpath: str, scan: StageScan) -> int | None:
        fs = scan.files[relpath]
        return self._load("pid", relpath, fs, lambda: parse_pid_text(self._raw(relpath)))

    def _raw(self, relpath: str) -> str:
        return read_text_capped(self.stage / relpath, 4096) or ""

    def _worker_log(self, relpath: str, scan: StageScan) -> _WorkerLog:
        fs = scan.files[relpath]
        path = self.stage / relpath

        def load() -> _WorkerLog:
            first = first_nonblank_line(path)
            fmt = logparse.detect_format(path)
            return _WorkerLog(
                fmt=fmt,
                size=fs.size,
                mtime=fs.mtime,
                model=logparse.model_from_log(path),
                stdin_hang=bool(first) and first.startswith(STDIN_HANG_PREFIX),
                now=logparse.now_line(path),
                steps=logsteps.parse_log_steps(path),
                sandbox=codex_sandbox(path) if fmt == "codex-text" else None,
            )

        return self._load("worker-log", relpath, fs, load)

    def _review_log(self, relpath: str, scan: StageScan) -> _ReviewLog:
        fs = scan.files[relpath]
        path = self.stage / relpath

        def load() -> _ReviewLog:
            text = read_text_capped(path) or ""
            return _ReviewLog(
                model=logparse.model_from_log(path),
                findings=parse_findings(text.splitlines()),
            )

        return self._load("review-log", relpath, fs, load)

    def _extra_log(self, relpath: str, scan: StageScan) -> _ExtraLog:
        fs = scan.files[relpath]
        path = self.stage / relpath

        def load() -> _ExtraLog:
            chunk = tail.read_tail(path, 4096)
            last = next((line for line in reversed(chunk.lines) if line.strip()), None)
            return _ExtraLog(last_line=last.strip()[:500] if last else None)

        return self._load("extra-log", relpath, fs, load)

    # -- scan / signature -------------------------------------------------- #
    def scan(self) -> StageScan:
        scan = scan_stage(self.stage)
        for key in list(self._cache):
            if key[1] not in scan.files:
                del self._cache[key]
        self._scan = scan
        return scan

    def signature(self, scan: StageScan) -> tuple[tuple[tuple[str, int, int], ...], frozenset[int]]:
        file_sigs = tuple(sorted((fs.relpath, fs.mtime_ns, fs.size) for fs in scan.files.values()))
        alive: set[int] = set()
        for relpath in scan.files:
            if "/" in relpath or not relpath.endswith(".pid"):
                continue
            pid = self._pid(relpath, scan)
            if pid is not None and procinfo.is_alive(pid, None):
                alive.add(pid)
        return file_sigs, frozenset(alive)

    # -- build ------------------------------------------------------------- #
    def build(
        self, sid: str, project: str, scan: StageScan, run: str | None = None
    ) -> SessionDetail:
        pick_records = _pick_records(self.stage)
        built = [
            self._build_worker(stem, scan, pick_records) for stem in sorted(worker_stems(scan))
        ]
        workers = _sort_workers([worker for worker, _ in built])
        artifacts: list[Artifact] = []
        log_index: list[LogIndexEntry] = []
        for worker, found in built:
            artifacts.extend(found)
            log_index.extend(
                LogIndexEntry(
                    session_id=sid,
                    worker_name=worker.name,
                    artifact_name=item.name,
                    kind=item.kind,
                    mtime=item.mtime,
                    size=item.size,
                )
                for item in found
                if item.kind == "log"
            )

        milestones: list[str] = []
        if "PROGRESS.md" in scan.files:
            milestones = parse_milestones(self._text("PROGRESS.md", scan))

        reviews = self._build_reviews(scan)
        extra_logs = self._build_extra_logs(scan)
        counts = _count(workers)
        last_activity = _session_activity(scan)

        lead_info = parse_lead(self.stage, STREAM_FILE)
        lead, warnings = _lead_state(lead_info, self.stage)
        history = [
            CycleEvent(
                phase=item["phase"],
                cycle=item["cycle"],
                label=_clean(item["at"], 200),
                source_path=lead.source_path,
            )
            for item in lead_info["phase_at"]
        ]

        return SessionDetail(
            id=sid,
            project=project,
            run=run,
            path=str(self.stage),
            rev=0,
            status="active" if counts.running > 0 else "idle",
            counts=counts,
            last_activity_at=last_activity,
            latest_milestone=milestones[0] if milestones else None,
            findings_total=sum(len(review.findings) for review in reviews),
            findings_by_priority=_findings_by_priority(reviews),
            tokens_total=_sum_or_none([worker.tokens for worker in workers]),
            cost_total=_sum_or_none([worker.cost for worker in workers]),
            running_workers=_running_workers(workers),
            workers=workers,
            milestones=milestones,
            reviews=reviews,
            extra_logs=extra_logs,
            lead=lead,
            stream=_clean(lead_info["stream"], 200),
            cycle_progress=_cycle_progress(lead_info, history),
            source_warnings=warnings,
            worker_previews=[_worker_preview(worker) for worker in workers],
            worker_total=len(workers),
            history=history,
            artifacts=artifacts,
            log_index=log_index or None,
            pick_records=pick_records,
        )

    def _worker_artifacts(
        self, stem: str, scan: StageScan, log_rel: str | None
    ) -> list[Artifact]:
        """The worker's allowlisted files: brief, report, log, patch, diff, json."""
        candidates: list[tuple[ArtifactKind, str | None]] = [
            ("brief", _brief_relpath(stem, scan.files)),
            ("report", f"{stem}/REPORT.md"),
            ("log", log_rel),
            ("patch", f"{stem}.patch"),
            ("patch", f"{stem}.diff"),
            ("json", f"{stem}.json"),
        ]
        artifacts: list[Artifact] = []
        for kind, rel in candidates:
            if rel is None:
                continue
            st = _artifact_stat(self.stage, rel)
            if st is None:
                continue
            artifacts.append(
                Artifact(name=rel, kind=kind, size=st.st_size, mtime=st.st_mtime, previewable=True)
            )
        return artifacts

    def _build_worker(
        self, stem: str, scan: StageScan, pick_records: list[PickRecord]
    ) -> tuple[Worker, list[Artifact]]:
        files = scan.files
        pid_rel = f"{stem}.pid"
        pid_fs = files.get(pid_rel)
        pid = self._pid(pid_rel, scan) if pid_fs is not None else None
        pid_mtime = pid_fs.mtime if pid_fs is not None else None

        alive = pid_is_alive(pid, pid_mtime)

        report_rel = f"{stem}/REPORT.md"
        report_fs = files.get(report_rel)
        has_report = report_fs is not None
        report_size = report_fs.size if report_fs is not None else 0
        report_nonempty = report_size > 0

        if alive:
            status: WorkerStatus = "running"
        elif report_nonempty:
            status = "finished"
        elif pid_fs is not None and pid is not None:
            status = "exited"
        else:
            status = "unknown"

        log_rel = _log_relpath(stem, files)
        derived = self._worker_log(log_rel, scan) if log_rel is not None else None
        artifacts = self._worker_artifacts(stem, scan, log_rel)

        model_file = self._model_file(stem, scan)
        model = self._worker_model(stem, scan, alive, pid, derived)
        engine = engine_from_evidence(derived.fmt if derived is not None else "missing", model)
        identity = parse_model_identity(model_file)
        sandbox = derived.sandbox if derived is not None else None
        access, access_source = _access(model_file, sandbox)
        job = self._job(stem, scan, pick_records)
        title, goal = self._brief_fields(stem, scan)

        summary: list[str] = []
        if has_report:
            summary = parse_summary(self._text(report_rel, scan))

        started_at = self._started_at(status, pid, pid_mtime)
        finished_at = self._finished_at(status, report_fs, derived)

        stale = (
            status == "running"
            and derived is not None
            and (time.time() - derived.mtime) > STALE_SECONDS
        )
        flags: list[str] = []
        if derived is not None and derived.stdin_hang:
            flags.append("stdin_hang")
        if has_report and report_size == 0:
            flags.append("empty_report")
        if log_rel is None:
            flags.append("no_log")
        elif derived is not None and derived.size == 0:
            flags.append("empty_log")
        if stale:
            flags.append("stale")

        worker = Worker(
            name=stem,
            status=status,
            pid=pid,
            model=model,
            engine=engine,
            title=title,
            goal=goal,
            has_brief=_brief_relpath(stem, files) is not None,
            has_report=has_report,
            started_at=started_at,
            finished_at=finished_at,
            last_activity_at=derived.mtime if derived is not None else None,
            now=derived.now if derived is not None else None,
            summary=summary,
            log_stem=stem if log_rel is not None else None,
            log_size=derived.size if derived is not None else 0,
            log_format=derived.fmt if derived is not None else "missing",
            flags=flags,
            duration_s=(
                finished_at - started_at
                if started_at is not None and finished_at is not None
                else None
            ),
            log_mtime=derived.mtime if derived is not None else None,
            artifact_count=len(artifacts),
            engine_family=identity.engine_family,
            account=identity.account,
            effort=identity.effort,
            mode=identity.mode,
            access=access,
            access_source=access_source,
            job=job,
            worker_events=_worker_events(stem, scan, derived, stale),
            step_events=_step_events(derived.steps) if derived is not None else [],
            tokens=derived.steps.tokens if derived is not None else None,
            tokens_source=derived.steps.tokens_source if derived is not None else None,
            cost=derived.steps.cost if derived is not None else None,
        )
        return worker, artifacts

    def _model_file(self, stem: str, scan: StageScan) -> str | None:
        """The first non-blank line of `<stem>.model`, or None."""
        model_rel = f"{stem}.model"
        if model_rel not in scan.files:
            return None
        text = self._text(model_rel, scan)
        first = text.splitlines()[0].strip() if text.splitlines() else ""
        return first or None

    def _job(self, stem: str, scan: StageScan, pick_records: list[PickRecord]) -> str | None:
        """The job from a pick naming this worker, else `review`, else the brief's `Job:` line."""
        for record in pick_records:
            if record.job and any(item.split("=", 1)[0] == stem for item in record.assigned):
                return record.job
        if stem.startswith(REVIEW_PREFIX):
            return "review"
        rel = _brief_relpath(stem, scan.files)
        if rel is None:
            return None
        return self._load("brief-job", rel, scan.files[rel], lambda: _brief_job(self.stage / rel))

    def _worker_model(
        self,
        stem: str,
        scan: StageScan,
        alive: bool,
        pid: int | None,
        derived: _WorkerLog | None,
    ) -> str | None:
        declared = self._model_file(stem, scan)
        if declared is not None:
            return declared
        if alive and pid is not None:
            model = procinfo.model_from_argv(pid)
            if model:
                return model
        if derived is not None and derived.model:
            return derived.model
        return None

    def _brief_fields(self, stem: str, scan: StageScan) -> tuple[str | None, str | None]:
        rel = _brief_relpath(stem, scan.files)
        if rel is None:
            return None, None
        return parse_brief(self._text(rel, scan))

    @staticmethod
    def _started_at(status: WorkerStatus, pid: int | None, pid_mtime: float | None) -> float | None:
        if status == "running" and pid is not None:
            started = procinfo.start_time(pid)
            if started is not None:
                return started
        return pid_mtime

    @staticmethod
    def _finished_at(
        status: WorkerStatus, report_fs: FileSig | None, derived: _WorkerLog | None
    ) -> float | None:
        if status == "finished" and report_fs is not None:
            return report_fs.mtime
        if status == "exited" and derived is not None:
            return derived.mtime
        return None

    def _build_reviews(self, scan: StageScan) -> list[Review]:
        workers = worker_stems(scan)
        reviews: list[Review] = []
        for relpath, fs in scan.files.items():
            if "/" in relpath or not relpath.endswith(".log"):
                continue
            stem = relpath[: -len(".log")]
            if not stem.startswith(REVIEW_PREFIX) or stem in workers:
                continue
            derived = self._review_log(relpath, scan)
            reviews.append(
                Review(
                    name=stem,
                    mtime=fs.mtime,
                    size=fs.size,
                    model=derived.model,
                    findings=derived.findings,
                )
            )
        reviews.sort(key=lambda review: (-review.mtime, review.name))
        return reviews

    def _build_extra_logs(self, scan: StageScan) -> list[ExtraLog]:
        workers = worker_stems(scan)
        extra_logs: list[ExtraLog] = []
        for relpath, fs in scan.files.items():
            if "/" in relpath or not relpath.endswith(".log"):
                continue
            stem = relpath[: -len(".log")]
            if stem.startswith(REVIEW_PREFIX) or stem in workers:
                continue
            derived = self._extra_log(relpath, scan)
            extra_logs.append(
                ExtraLog(
                    name=stem,
                    kind="check" if CHECK_STEM_RE.match(stem) else "other",
                    mtime=fs.mtime,
                    size=fs.size,
                    last_line=derived.last_line,
                )
            )
        extra_logs.sort(key=lambda extra: (-extra.mtime, extra.name))
        return extra_logs


# --------------------------------------------------------------------------- #
# Aggregate helpers
# --------------------------------------------------------------------------- #
def _count(workers: list[Worker]) -> Counts:
    return Counts(
        running=sum(worker.status == "running" for worker in workers),
        finished=sum(worker.status == "finished" for worker in workers),
        exited=sum(worker.status == "exited" for worker in workers),
        unknown=sum(worker.status == "unknown" for worker in workers),
    )


def _session_activity(scan: StageScan) -> float | None:
    if not scan.files:
        return None
    return max(fs.mtime for fs in scan.files.values())


def _cycle_progress(info: dict[str, Any], events: list[CycleEvent]) -> CycleProgress | None:
    if info["phase"] is None and info["cycle"] is None and not info["over_policy"]:
        return None
    return CycleProgress(
        current=info["cycle"],
        phase=info["phase"] or "unknown",
        events=events,
        over_policy=info["over_policy"],
    )


def _worker_preview(worker: Worker) -> WorkerPreview:
    return WorkerPreview.model_validate(
        worker.model_dump(include=set(WorkerPreview.model_fields))
    )




def _sum_or_none[N: (int, float)](values: list[N | None]) -> N | None:
    """The sum of the known values, or None when none is known."""
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _findings_by_priority(reviews: list[Review]) -> dict[str, int]:
    """Finding counts under P1, P2 and P3; P3 also takes priorities above 3."""
    counts = {"P1": 0, "P2": 0, "P3": 0}
    for review in reviews:
        for finding in review.findings:
            rank = int(finding.priority[1:])
            counts[f"P{min(max(rank, 1), 3)}"] += 1
    return counts


def _sort_workers(workers: list[Worker]) -> list[Worker]:
    running = [worker for worker in workers if worker.status == "running"]
    rest = [worker for worker in workers if worker.status != "running"]
    running.sort(key=lambda worker: (worker.started_at is None, -(worker.started_at or 0.0)))
    rest.sort(key=lambda worker: (worker.finished_at is None, -(worker.finished_at or 0.0)))
    return running + rest


def _running_workers(workers: list[Worker]) -> list[RunningWorker]:
    return [
        RunningWorker(
            name=worker.name,
            model=worker.model,
            engine=worker.engine,
            started_at=worker.started_at,
            now=worker.now,
        )
        for worker in workers
        if worker.status == "running"
    ]


# --------------------------------------------------------------------------- #
# Session scope: one lead session's workers out of a shared `.stage`
# --------------------------------------------------------------------------- #
# Suffixes of the per-worker files `_worker_artifacts` lists under the worker's stem.
_STEM_ARTIFACT_SUFFIXES = (".log", ".txt", ".patch", ".diff", ".json")


def session_lead_matches(stage: Path, stem: str, lead_session: str) -> bool:
    """Whether `<stem>.session` names `lead_session` as its lead or its root session.

    A missing, unreadable, oversized, non-object or malformed file never matches.
    """
    path = stage / f"{stem}.session"
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > SESSION_READ_CAP:
            return False
        with open(path, "rb") as fh:
            data = json.loads(fh.read(SESSION_READ_CAP + 1))
    except (OSError, ValueError, RecursionError):
        return False
    if not isinstance(data, dict):
        return False
    return lead_session in (data.get("lead_session"), data.get("root_session"))


def _artifact_in_scope(rel: str, members: set[str], stems: set[str]) -> bool:
    """Whether the worker artifact `rel` belongs to one of the `members` stems.

    `stems` is every worker in the stage, so a brief goes to its most specific owner:
    `briefs/alpha-x.md` is `alpha-x`'s when that worker exists, not `alpha`'s.
    """
    if rel.startswith("briefs/") and rel.endswith(".md"):
        # `briefs/<stem>.md`, or `briefs/<stem>-<rest>.md` as `_brief_relpath` allows.
        base = rel[len("briefs/") : -len(".md")]
        owners = [base, *(base[:i] for i, char in enumerate(base) if char == "-")]
        owner = max((o for o in owners if o in stems), key=len, default=None)
        return owner in members
    if rel.endswith("/REPORT.md"):
        return rel[: -len("/REPORT.md")] in members
    return any(
        rel.endswith(suffix) and rel[: -len(suffix)] in members
        for suffix in _STEM_ARTIFACT_SUFFIXES
    )


def scope_detail(detail: SessionDetail, lead_session: str) -> SessionDetail:
    """The session limited to the workers that `lead_session` owns (see `session_lead_matches`).

    Only worker-derived fields change: counts, status, running workers, previews,
    totals, artifacts and the log index. Reviews, milestones and the lead belong to
    the stage as a whole, so they are kept as they are.
    """
    stage = Path(detail.path)
    members = {
        worker.name
        for worker in detail.workers
        if session_lead_matches(stage, worker.name, lead_session)
    }
    stems = {worker.name for worker in detail.workers}
    workers = [worker for worker in detail.workers if worker.name in members]
    counts = _count(workers)
    return detail.model_copy(
        update={
            "status": "active" if counts.running > 0 else "idle",
            "counts": counts,
            "running_workers": _running_workers(workers),
            "workers": workers,
            "worker_previews": [_worker_preview(worker) for worker in workers],
            "worker_total": len(workers),
            "tokens_total": _sum_or_none([worker.tokens for worker in workers]),
            "cost_total": _sum_or_none([worker.cost for worker in workers]),
            "artifacts": [
                item for item in detail.artifacts if _artifact_in_scope(item.name, members, stems)
            ],
            "log_index": [entry for entry in detail.log_index or [] if entry.worker_name in members]
            or None,
        }
    )


def summary_of(detail: SessionDetail) -> SessionSummary:
    """The `SessionSummary` view of a `SessionDetail`."""
    return SessionSummary(
        id=detail.id,
        project=detail.project,
        run=detail.run,
        path=detail.path,
        rev=detail.rev,
        status=detail.status,
        counts=detail.counts,
        last_activity_at=detail.last_activity_at,
        latest_milestone=detail.latest_milestone,
        findings_total=detail.findings_total,
        running_workers=detail.running_workers,
        lead=detail.lead,
        lat=detail.lat,
        stream=detail.stream,
        cycle_progress=detail.cycle_progress,
        source_warnings=detail.source_warnings,
        worker_previews=detail.worker_previews,
        worker_total=detail.worker_total,
        tokens_total=detail.tokens_total,
        cost_total=detail.cost_total,
        findings_by_priority=detail.findings_by_priority,
    )
