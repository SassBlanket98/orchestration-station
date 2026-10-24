"""Store interface that the API layer depends on (project plan §3)."""

from pathlib import Path
from typing import Protocol

from stagewatch.models import SessionDetail, SessionSummary, WorkerDetail


class StoreProtocol(Protocol):
    monitor_online: bool

    def refresh(self) -> set[str]:
        """Re-scan sources and return the ids of sessions that changed."""
        ...

    def summaries(self) -> list[SessionSummary]: ...

    def detail(self, sid: str) -> SessionDetail | None: ...

    def worker_detail(self, sid: str, name: str) -> WorkerDetail | None: ...

    def resolve_log(self, sid: str, stem: str) -> Path | None: ...
