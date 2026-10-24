"""In-memory activity series: observed log-size samples per worker.

The dashboard wants a small, honest history of how each worker's log is growing
so the UI can draw a sparkline. This module holds that history in memory only;
there is no snapshot hookup yet. A later slice calls :meth:`ActivitySeries.record`
from the scan loop and reads the samples back for display.

Two rules keep it honest:

* Only observed samples are stored. ``record()`` appends the ``(at, size)`` it is
  handed; nothing is interpolated and no sample is invented for a time between
  two observations. A gap stays a gap.
* The window is bounded. Samples older than :data:`WINDOW_SECONDS` are dropped,
  so a series never covers more than 30 minutes of observations.

A freshly constructed series is empty: this history never survives a process
restart. The clock is injected so the window is deterministic under test.
"""

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from stagewatch.models import ActivitySample

# How far back a series remembers: 30 minutes.
WINDOW_SECONDS = 30 * 60


@dataclass(frozen=True)
class Sample:
    """One observed ``(at, size)`` reading for a worker, at epoch seconds."""

    at: float
    size: int


class ActivitySeries:
    """Observed log-size samples per worker key, capped at 30 minutes.

    ``clock`` is any zero-argument callable returning epoch seconds. Recording
    stamps each sample with it and pruning compares against it, so a fixed clock
    makes the 30-minute window exact to test.
    """

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        # Each deque is the per-worker ring: oldest first, pruned by time.
        self._by_key: dict[str, deque[Sample]] = {}

    def record(self, key: str, size: int) -> Sample:
        """Append one observed sample for ``key`` and return it.

        Every call is one observation, even when ``size`` is unchanged. Samples
        that have fallen out of the 30-minute window are dropped.
        """
        sample = Sample(at=self._clock(), size=size)
        series = self._by_key.get(key)
        if series is None:
            series = self._by_key[key] = deque()
        series.append(sample)
        self._prune(key, series)
        return sample

    def samples(self, key: str) -> list[Sample]:
        """The observed samples for ``key``, oldest first.

        Returns an empty list for a key that has never been recorded (or whose
        samples have all aged out). The result is a copy: callers cannot mutate
        the stored history. Nothing is added or interpolated here.
        """
        series = self._by_key.get(key)
        if series is None:
            return []
        self._prune(key, series)
        return list(series)

    def log_size_ring(self, key: str) -> list[int]:
        """The sizes of the observed samples for ``key``, for a sparkline.

        This is the same history as :meth:`samples`, just the numbers: one entry
        per observation, in order. A gap between observations is a gap here too.
        """
        return [sample.size for sample in self.samples(key)]

    def keys(self) -> list[str]:
        """The worker keys with at least one sample still in the window."""
        for key in list(self._by_key):
            self._prune(key, self._by_key[key])
        return list(self._by_key)

    def clear(self) -> None:
        """Drop every sample. A later slice calls this when workers disappear."""
        self._by_key.clear()

    def _prune(self, key: str, series: deque[Sample]) -> None:
        """Drop samples older than the window; forget the key once it is empty."""
        cutoff = self._clock() - WINDOW_SECONDS
        while series and series[0].at < cutoff:
            series.popleft()
        if not series:
            self._by_key.pop(key, None)


def session_series(
    worker_series: list[list[tuple[float, int]]],
    now: float,
    buckets: int = 10,
    bucket_s: float = 180.0,
) -> list[ActivitySample]:
    """Bytes appended per time bucket, summed over a session's workers.

    ``worker_series`` holds one ``(at, size)`` list per worker, oldest first. Each
    consecutive pair ``(a, b)`` adds ``max(0, b.size - a.size)`` to the bucket that
    contains ``b.at``, so a rotated (shrinking) log adds nothing. Samples outside
    ``[now - buckets * bucket_s, now]`` are ignored. Returns exactly ``buckets``
    samples, oldest first; bucket ``i`` starts at ``now - (buckets - i) * bucket_s``.
    """
    start = now - buckets * bucket_s
    counts = [0] * buckets
    for series in worker_series:
        for (_, prev_size), (at, size) in zip(series, series[1:], strict=False):
            if not start <= at <= now:
                continue
            index = min(math.floor((at - start) / bucket_s), buckets - 1)
            counts[index] += max(0, size - prev_size)
    return [
        ActivitySample(at=start + i * bucket_s, count=count) for i, count in enumerate(counts)
    ]
