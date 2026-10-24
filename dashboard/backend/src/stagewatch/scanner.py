"""Async rescan loop and change fan-out for the SSE streams (project plan §1)."""

import asyncio
import logging
import time

from stagewatch.config import Config
from stagewatch.contracts import StoreProtocol

log = logging.getLogger(__name__)


class Scanner:
    """Calls `store.refresh()` on a timer and hands each changed-id set to every subscriber."""

    def __init__(self, store: StoreProtocol, cfg: Config) -> None:
        self.store = store
        self.scan_interval_s = cfg.scan_interval_s
        self.discover_interval_s = cfg.discover_interval_s
        self._subscribers: set[asyncio.Queue[frozenset[str]]] = set()

    def subscribe(self) -> asyncio.Queue[frozenset[str]]:
        queue: asyncio.Queue[frozenset[str]] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[frozenset[str]]) -> None:
        self._subscribers.discard(queue)

    async def scan_once(self) -> frozenset[str]:
        """Run one refresh off the loop and publish the changed ids if there are any."""
        changed = frozenset(await asyncio.to_thread(self.store.refresh))
        if changed:
            for queue in self._subscribers:
                published = changed
                if queue.full():
                    published = queue.get_nowait() | changed
                queue.put_nowait(published)
        return changed

    async def run(self) -> None:
        next_discover = 0.0
        while True:
            try:
                if time.monotonic() >= next_discover:
                    await self._discover()
                    next_discover = time.monotonic() + self.discover_interval_s
                await self.scan_once()
            except Exception:
                log.exception("scan failed; retrying on the next tick")
            await asyncio.sleep(self.scan_interval_s)

    async def _discover(self) -> None:
        # Discovery is not part of StoreProtocol. A store that has it exposes `discover()`.
        discover = getattr(self.store, "discover", None)
        if callable(discover):
            await asyncio.to_thread(discover)
