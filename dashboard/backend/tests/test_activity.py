"""ActivitySeries keeps an honest, in-memory history of observed log sizes."""

from stagewatch.activity import WINDOW_SECONDS, ActivitySeries, Sample, session_series


class FakeClock:
    """A clock the test moves by hand; the series never reads wall time."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_new_series_is_empty() -> None:
    clock = FakeClock()
    series = ActivitySeries(clock)

    assert series.samples("w") == []
    assert series.log_size_ring("w") == []
    assert series.keys() == []


def test_clock_is_injected_and_stamps_every_sample() -> None:
    clock = FakeClock(1_700_000_000.0)
    series = ActivitySeries(clock)

    returned = series.record("w", 10)

    assert returned == Sample(at=1_700_000_000.0, size=10)
    assert series.samples("w") == [Sample(at=1_700_000_000.0, size=10)]


def test_only_observed_samples_are_kept() -> None:
    clock = FakeClock()
    series = ActivitySeries(clock)

    series.record("w", 10)
    clock.advance(600)
    series.record("w", 25)

    # Exactly the two observations, in order: no sample invented at t=300.
    assert series.samples("w") == [Sample(at=0.0, size=10), Sample(at=600, size=25)]


def test_reading_never_fabricates_a_sample() -> None:
    clock = FakeClock()
    series = ActivitySeries(clock)
    series.record("w", 4)

    clock.advance(120)
    assert series.samples("w") == [Sample(at=0.0, size=4)]

    clock.advance(120)
    assert series.samples("w") == [Sample(at=0.0, size=4)]


def test_repeated_equal_sizes_are_separate_observations() -> None:
    clock = FakeClock()
    series = ActivitySeries(clock)

    series.record("w", 7)
    clock.advance(5)
    series.record("w", 7)

    assert series.log_size_ring("w") == [7, 7]
    assert [sample.at for sample in series.samples("w")] == [0.0, 5.0]


def test_log_size_ring_mirrors_the_samples() -> None:
    clock = FakeClock()
    series = ActivitySeries(clock)
    for size in (3, 9, 27):
        series.record("w", size)
        clock.advance(1)

    assert series.log_size_ring("w") == [sample.size for sample in series.samples("w")]
    assert series.log_size_ring("w") == [3, 9, 27]


def test_samples_are_isolated_per_key() -> None:
    clock = FakeClock()
    series = ActivitySeries(clock)
    series.record("w", 1)
    series.record("other", 100)

    assert series.log_size_ring("w") == [1]
    assert series.log_size_ring("other") == [100]
    assert sorted(series.keys()) == ["other", "w"]
    assert series.samples("ghost") == []


def test_window_keeps_samples_up_to_thirty_minutes_old() -> None:
    clock = FakeClock()
    series = ActivitySeries(clock)
    series.record("w", 1)

    clock.advance(WINDOW_SECONDS)  # exactly 30 minutes later
    assert series.samples("w") == [Sample(at=0.0, size=1)]

    clock.advance(0.5)  # just past 30 minutes
    assert series.samples("w") == []
    assert series.log_size_ring("w") == []


def test_window_is_pruned_even_without_a_new_record() -> None:
    clock = FakeClock()
    series = ActivitySeries(clock)
    series.record("w", 1)
    series.record("w", 2)

    assert len(series.samples("w")) == 2
    clock.advance(WINDOW_SECONDS + 1)
    assert series.samples("w") == []


def test_window_never_spans_more_than_thirty_minutes() -> None:
    clock = FakeClock()
    series = ActivitySeries(clock)
    for step in range(10):
        series.record("w", step)
        clock.advance(600)  # one sample every 10 minutes for 90 minutes

    samples = series.samples("w")
    assert samples, "recent samples should remain"
    assert samples[-1].at - samples[0].at <= WINDOW_SECONDS
    assert samples[0].at >= clock.now - WINDOW_SECONDS


def test_a_new_instance_starts_empty_after_a_restart() -> None:
    clock = FakeClock()
    first = ActivitySeries(clock)
    first.record("w", 42)
    assert first.log_size_ring("w") == [42]

    restarted = ActivitySeries(clock)
    assert restarted.samples("w") == []
    assert restarted.log_size_ring("w") == []
    assert restarted.keys() == []


def test_returned_lists_do_not_expose_internal_state() -> None:
    series = ActivitySeries(FakeClock())
    series.record("w", 5)

    returned = series.samples("w")
    returned.append(Sample(at=999.0, size=999))
    series.log_size_ring("w").append(999)

    assert series.samples("w") == [Sample(at=0.0, size=5)]
    assert series.log_size_ring("w") == [5]


def test_clear_forgets_everything() -> None:
    series = ActivitySeries(FakeClock())
    series.record("w", 5)
    series.record("other", 6)

    series.clear()

    assert series.keys() == []
    assert series.samples("w") == []


def test_session_series_without_samples_is_ten_zero_buckets() -> None:
    series = session_series([], now=1000.0)

    assert len(series) == 10
    assert [sample.count for sample in series] == [0] * 10
    assert series[0].at == 1000.0 - 10 * 180.0
    assert series[-1].at == 1000.0 - 180.0


def test_session_series_puts_bytes_in_the_bucket_of_the_later_sample() -> None:
    now = 1800.0
    # Bucket 0 starts at 0 and bucket 9 at 1620. b.at=100 lands in 0, b.at=1700 in 9.
    worker = [(50.0, 10), (100.0, 25), (1700.0, 40)]

    series = session_series([worker], now=now)

    assert series[0].count == 15
    assert series[9].count == 15
    assert sum(sample.count for sample in series) == 30


def test_session_series_counts_a_shrinking_log_as_zero() -> None:
    worker = [(100.0, 500), (200.0, 120), (300.0, 150)]

    series = session_series([worker], now=1800.0)

    assert sum(sample.count for sample in series) == 30


def test_session_series_ignores_samples_outside_the_window() -> None:
    # The window is [0, 1800]. Only the pair ending at 50.0 counts, adding 5.
    worker = [(-500.0, 0), (-10.0, 100), (50.0, 105), (1900.0, 999)]

    series = session_series([worker], now=1800.0)

    assert sum(sample.count for sample in series) == 5


def test_session_series_sums_two_workers() -> None:
    first = [(100.0, 0), (110.0, 7)]
    second = [(100.0, 100), (120.0, 103)]

    series = session_series([first, second], now=1800.0)

    assert series[0].count == 10


def test_session_series_needs_two_samples_per_worker() -> None:
    series = session_series([[(100.0, 50)]], now=1800.0)

    assert [sample.count for sample in series] == [0] * 10
