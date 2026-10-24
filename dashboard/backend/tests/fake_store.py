"""In-memory StoreProtocol for the API and SSE tests. Touches no /proc and no scans."""

from pathlib import Path

from stagewatch.models import (
    Counts,
    SessionDetail,
    SessionSummary,
    Worker,
    WorkerDetail,
)

DETAIL_ONLY = {"workers", "milestones", "reviews", "extra_logs"}


def make_worker(name: str, status: str = "running") -> Worker:
    return Worker(
        name=name,
        status=status,
        pid=None,
        model=None,
        title=None,
        goal=None,
        has_brief=False,
        has_report=False,
        started_at=None,
        finished_at=None,
        last_activity_at=None,
        now=None,
        summary=[],
        log_stem=name,
        log_size=0,
        log_format="empty",
        flags=[],
    )


def make_session(sid: str, stage: Path, rev: int = 1, running: int = 0) -> SessionDetail:
    return SessionDetail(
        id=sid,
        project=sid,
        path=str(stage),
        rev=rev,
        status="active" if running else "idle",
        counts=Counts(running=running, finished=0, exited=0, unknown=0),
        last_activity_at=None,
        latest_milestone=None,
        findings_total=0,
        running_workers=[],
        workers=[make_worker(f"w{i}") for i in range(running)],
        milestones=[],
        reviews=[],
        extra_logs=[],
    )


class FakeStore:
    """Holds SessionDetail objects. Ids given to `set_session` come back from the next refresh()."""

    def __init__(self, stages: dict[str, Path]) -> None:
        self.stages = stages
        self.monitor_online = False
        self.sessions: dict[str, SessionDetail] = {}
        self.pending: set[str] = set()
        self.refresh_calls = 0

    def set_session(self, sid: str, detail: SessionDetail | None) -> None:
        if detail is None:
            self.sessions.pop(sid, None)
        else:
            self.sessions[sid] = detail
        self.pending.add(sid)

    def refresh(self) -> set[str]:
        self.refresh_calls += 1
        changed, self.pending = self.pending, set()
        return changed

    def summaries(self) -> list[SessionSummary]:
        return [
            SessionSummary.model_validate(d.model_dump(exclude=DETAIL_ONLY))
            for d in self.sessions.values()
        ]

    def detail(self, sid: str) -> SessionDetail | None:
        return self.sessions.get(sid)

    def worker_detail(self, sid: str, name: str) -> WorkerDetail | None:
        detail = self.sessions.get(sid)
        if detail is None:
            return None
        for worker in detail.workers:
            if worker.name == name:
                return WorkerDetail(worker=worker, brief_md=None, report_md=None)
        return None

    def resolve_log(self, sid: str, stem: str) -> Path | None:
        stage = self.stages.get(sid)
        if stage is None:
            return None
        for ext in (".log", ".txt"):
            candidate = stage / f"{stem}{ext}"
            if candidate.exists():
                return candidate
        return None
