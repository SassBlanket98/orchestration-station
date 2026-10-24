import builtins
import os
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from stagewatch.config import Config
from stagewatch.discovery import run_id
from stagewatch.snapshot import LOG_SERIES_LIMIT, SnapshotStore

DEAD_PID = "999999999"


def _make_stage(
    root: Path, project: str, *, pid: str | None = DEAD_PID, log: str = "hello\n"
) -> Path:
    stage = root / project / ".stage"
    stage.mkdir(parents=True)
    if pid is not None:
        (stage / f"{project}.pid").write_text(pid)
    (stage / f"{project}.log").write_text(log)
    return stage


def _make_run(root: Path, project: str, run: str, *, log: str = "hello\n") -> Path:
    """A qualifying nested run inside the project's `.stage`, with one worker."""
    rundir = root / project / ".stage" / run
    rundir.mkdir(parents=True)
    (rundir / "status.sh").write_text("#!/bin/sh\n")
    (rundir / f"{run}.pid").write_text(DEAD_PID)
    (rundir / f"{run}.log").write_text(log)
    return rundir


def _store(root: Path, clock: Callable[[], float] = time.time) -> SnapshotStore:
    """A store whose fake Monitor endpoint lists the fixture stages."""
    def fetch_state():
        paths = sorted(root.rglob(".stage"))
        paths.extend(
            path
            for path in sorted(root.rglob("*"))
            if path.is_dir() and path.parent.name == ".stage"
        )
        return {"stages": [{"path": str(path)} for path in paths]}

    store = SnapshotStore(Config(), clock=clock, state_fetcher=fetch_state)
    store.discover()
    return store


def test_refresh_reports_sessions_and_starts_rev_at_one(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    _make_stage(root, "beta")
    store = _store(root)

    assert store.refresh() == {"alpha", "beta"}
    assert store.detail("alpha").rev == 1
    assert store.detail("beta").rev == 1
    assert store.detail("ghost") is None
    assert store.detail("alpha").lat is None


def test_second_refresh_without_changes_opens_no_files(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    _make_stage(root, "beta")
    store = _store(root)
    assert store.refresh() == {"alpha", "beta"}

    opened: list[object] = []
    real_open = builtins.open

    def spy(file, *args, **kwargs):
        opened.append(file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)

    assert store.refresh() == set()
    assert opened == []


def test_touching_one_log_bumps_only_that_sessions_rev(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    alpha_stage = _make_stage(root, "alpha")
    _make_stage(root, "beta")
    store = _store(root)
    store.refresh()

    (alpha_stage / "alpha.log").write_text("changed content\n")

    assert store.refresh() == {"alpha"}
    assert store.detail("alpha").rev == 2
    assert store.detail("beta").rev == 1


def test_session_appears_and_disappears(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    store = _store(root)
    store.refresh()

    _make_stage(root, "gamma")
    store.discover()
    assert store.refresh() == {"gamma"}
    assert store.detail("gamma").rev == 1

    shutil.rmtree(root / "alpha")
    store.discover()
    assert store.refresh() == {"alpha"}
    assert store.detail("alpha") is None


def test_resolve_log_rejects_traversal_and_unknown(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    store = _store(root)
    store.refresh()

    resolved = store.resolve_log("alpha", "alpha")
    assert resolved is not None
    assert resolved.name == "alpha.log"

    assert store.resolve_log("alpha", "../x") is None
    assert store.resolve_log("alpha", "nope") is None
    assert store.resolve_log("ghost", "alpha") is None


def test_resolve_log_refuses_symlink_outside_stage(tmp_path: Path) -> None:
    stage = _make_stage(tmp_path, "alpha")
    (stage / "alpha.log").unlink()
    outside = tmp_path / "outside.log"
    outside.write_text("must not be read")
    (stage / "alpha.log").symlink_to(outside)
    store = _store(tmp_path)
    store.refresh()

    assert store.resolve_log("alpha", "alpha") is None


def test_resolve_log_finds_txt_worker(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    stage = root / "txtworker" / ".stage"
    stage.mkdir(parents=True)
    (stage / "txtworker.pid").write_text(DEAD_PID)
    (stage / "txtworker.txt").write_text("plain log\n")
    store = _store(root)
    store.refresh()

    resolved = store.resolve_log("txtworker", "txtworker")

    assert resolved is not None
    assert resolved.name == "txtworker.txt"


def test_worker_detail_reads_and_caps_brief_and_report(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    stage = root / "alpha" / ".stage"
    (stage / "alpha").mkdir(parents=True)
    (stage / "briefs").mkdir()
    (stage / "alpha.pid").write_text(DEAD_PID)
    (stage / "alpha.log").write_text("done\n")
    (stage / "briefs" / "alpha.md").write_text("x" * (300 * 1024))
    (stage / "alpha" / "REPORT.md").write_text("y" * (300 * 1024))
    store = _store(root)
    store.refresh()

    detail = store.worker_detail("alpha", "alpha")

    assert detail is not None
    assert detail.brief_md is not None
    assert detail.report_md is not None
    assert len(detail.brief_md) == 256 * 1024
    assert len(detail.report_md) == 256 * 1024
    assert store.worker_detail("alpha", "review-x") is None
    assert store.worker_detail("alpha", "bad name") is None
    assert store.worker_detail("alpha", "ghost") is None


def test_worker_detail_without_report(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    store = _store(root)
    store.refresh()

    detail = store.worker_detail("alpha", "alpha")

    assert detail is not None
    assert detail.brief_md is None
    assert detail.report_md is None


def test_summaries_put_active_sessions_first(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    _make_stage(root, "beta", pid=str(os.getpid()))
    store = _store(root)
    store.refresh()

    summaries = store.summaries()

    assert [summary.id for summary in summaries] == ["beta", "alpha"]
    assert summaries[0].status == "active"
    assert summaries[0].counts.running == 1
    assert summaries[1].status == "idle"


def _total(counts) -> int:
    return counts.running + counts.finished + counts.exited + counts.unknown


def test_concurrent_refresh_and_reads_see_whole_snapshots(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    stage = _make_stage(root, "alpha", pid=None)
    for index in range(4):
        (stage / f"w{index}.pid").write_text(DEAD_PID)
        (stage / f"w{index}.log").write_text("hello\n")
    store = _store(root)
    store.refresh()
    worker_total = len(store.detail("alpha").workers)
    errors: list[BaseException] = []
    done = threading.Event()

    def writer() -> None:
        try:
            for step in range(200):
                # Flip one worker between dead and live, and touch another log, so
                # every refresh changes the published maps.
                (stage / "w0.pid").write_text(str(os.getpid()) if step % 2 else DEAD_PID)
                (stage / "w1.log").write_text(f"step {step}\n")
                store.refresh()
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    def reader() -> None:
        try:
            while not done.is_set():
                for summary in store.summaries():
                    assert _total(summary.counts) == worker_total
                detail = store.detail("alpha")
                assert detail is not None
                assert _total(detail.counts) == len(detail.workers) == worker_total
                assert store.worker_detail("alpha", "w1") is not None
                assert store.resolve_log("alpha", "w1") is not None
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert store.detail("alpha").rev > 1


def test_refresh_with_unconfigured_monitor_returns_empty(tmp_path: Path) -> None:
    store = SnapshotStore(Config(monitor_url=None))
    store.discover()

    assert store.refresh() == set()
    assert store.summaries() == []
    assert store.monitor_online is False


def test_discovery_uses_monitor_and_drops_non_stage_paths(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha" / ".stage"
    beta = tmp_path / "beta" / ".stage"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    state = {"stages": [
        {"path": str(alpha), "section": "active", "lead_state": "live", "lead_kind": "codex",
         "level_tag": "LEAD", "project": "Alpha", "parent_session": None,
         "root_session": "root-a", "launch_depth": 0, "session_id": "sess-a",
         "session_name": "alpha", "lead_model": "gpt-6-sol", "effort": "high",
         "counts": {"live": 2, "failed": 1, "noreport": 3, "done": 4, "total": 10},
         "tokens_total": 123, "cost_total": 0.25, "goal": "ship"},
        {"path": str(beta)},
        {"path": str(tmp_path / "missing" / ".stage")},
        {"path": str(tmp_path / ".." / "escape")},
    ]}
    store = SnapshotStore(Config(), state_fetcher=lambda: state)

    store.discover()
    changed = store.refresh()

    assert changed == {"alpha", "beta"}
    assert {summary.id for summary in store.summaries()} == {"alpha", "beta"}
    facts = store.detail("alpha").lat
    assert facts is not None
    assert facts.section == "active"
    assert facts.lead_state == "live"
    assert facts.counts == {"live": 2, "failed": 1, "noreport": 3, "done": 4, "total": 10}
    assert facts.goal == "ship"
    assert store.monitor_online is True


def test_failed_monitor_fetch_keeps_last_good_stage_list(tmp_path: Path) -> None:
    stage = _make_stage(tmp_path, "alpha")
    responses = iter([{"stages": [{"path": str(stage)}]}, None])
    store = SnapshotStore(Config(), state_fetcher=lambda: next(responses))
    store.discover()
    store.refresh()

    store.discover()

    assert store.monitor_online is False
    assert store.refresh() == set()
    assert store.detail("alpha") is not None


def test_garbage_monitor_payload_keeps_last_good_stage_list(tmp_path: Path) -> None:
    stage = _make_stage(tmp_path, "alpha")
    responses = iter([{"stages": [{"path": str(stage)}]}, {"stages": "invalid"}])
    store = SnapshotStore(Config(), state_fetcher=lambda: next(responses))
    store.discover()
    store.refresh()

    store.discover()

    assert store.monitor_online is False
    assert store.refresh() == set()
    assert store.detail("alpha") is not None


def test_stage_ids_remain_stable_when_monitor_order_changes(tmp_path: Path) -> None:
    alpha = _make_stage(tmp_path, "alpha")
    beta = _make_stage(tmp_path, "beta")
    payloads = iter([
        {"stages": [{"path": str(alpha)}, {"path": str(beta)}]},
        {"stages": [{"path": str(beta)}, {"path": str(alpha)}]},
    ])
    store = SnapshotStore(Config(), state_fetcher=lambda: next(payloads))
    store.discover()
    store.refresh()
    before = {summary.id: summary.path for summary in store.summaries()}

    store.discover()
    store.refresh()

    assert {summary.id: summary.path for summary in store.summaries()} == before


def test_worker_detail_refuses_report_symlink_outside_stage(tmp_path: Path) -> None:
    stage = tmp_path / "alpha" / ".stage"
    (stage / "worker").mkdir(parents=True)
    (stage / "worker.pid").write_text(DEAD_PID)
    outside = tmp_path / "outside.md"
    outside.write_text("must not be read")
    (stage / "worker" / "REPORT.md").symlink_to(outside)
    store = _store(tmp_path)
    store.refresh()

    detail = store.worker_detail("alpha", "worker")

    assert detail is not None
    assert detail.report_md is None


# --------------------------------------------------------------------------- #
# Discovery cadence
# --------------------------------------------------------------------------- #
def test_refresh_does_not_fetch_state(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    calls = []

    def fetch_state():
        calls.append(1)
        return {"stages": [{"path": str(root / "alpha" / ".stage")}]}

    store = SnapshotStore(Config(), state_fetcher=fetch_state)
    store.discover()
    assert len(calls) == 1

    assert store.refresh() == {"alpha"}
    assert store.refresh() == set()
    assert store.refresh() == set()
    assert len(calls) == 1


def test_additions_and_removals_appear_after_the_next_discover(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    store = _store(root)
    assert store.refresh() == {"alpha"}

    _make_stage(root, "gamma")
    assert store.refresh() == set()  # not discovered yet
    assert store.detail("gamma") is None
    store.discover()
    assert store.refresh() == {"gamma"}
    assert store.detail("gamma").rev == 1

    shutil.rmtree(root / "alpha")
    assert store.refresh() == set()  # cached until the next discover
    assert store.detail("alpha") is not None
    store.discover()
    assert store.refresh() == {"alpha"}
    assert store.detail("alpha") is None


# --------------------------------------------------------------------------- #
# Nested runs
# --------------------------------------------------------------------------- #
def test_nested_run_is_a_distinct_station_with_project_and_run(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    rundir = _make_run(root, "alpha", "ram")
    store = _store(root)

    assert store.refresh() == {"alpha", run_id(rundir)}

    nested = store.detail(run_id(rundir))
    assert nested is not None
    assert (nested.project, nested.run, nested.path) == ("alpha", "ram", str(rundir))
    top = store.detail("alpha")
    assert top is not None
    assert (top.project, top.run) == ("alpha", None)


def test_summaries_carry_the_run_field(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    rundir = _make_run(root, "alpha", "ram")
    store = _store(root)
    store.refresh()

    by_id = {summary.id: summary for summary in store.summaries()}

    assert by_id["alpha"].run is None
    assert by_id[run_id(rundir)].run == "ram"
    assert by_id[run_id(rundir)].project == "alpha"


def test_nested_run_revisions_and_removal_are_independent(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    rundir = _make_run(root, "alpha", "ram")
    store = _store(root)
    store.refresh()

    (rundir / "ram.log").write_text("changed\n")
    assert store.refresh() == {run_id(rundir)}
    assert store.detail(run_id(rundir)).rev == 2
    assert store.detail("alpha").rev == 1

    shutil.rmtree(rundir)
    store.discover()
    assert store.refresh() == {run_id(rundir)}
    assert store.detail(run_id(rundir)) is None
    assert store.detail("alpha") is not None
    assert store.detail("alpha").rev == 1


def test_nested_worker_detail_and_log_resolution(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    rundir = _make_run(root, "alpha", "ram")
    (rundir / "w.pid").write_text(DEAD_PID)
    (rundir / "w.log").write_text("worker output\n")
    (rundir / "briefs").mkdir()
    (rundir / "briefs" / "w.md").write_text("# W\n\nGoal: nested work\n")
    (rundir / "w").mkdir()
    (rundir / "w" / "REPORT.md").write_text("## Summary\n- nested done\n")
    store = _store(root)
    store.refresh()
    sid = run_id(rundir)

    detail = store.worker_detail(sid, "w")

    assert detail is not None
    assert detail.worker.name == "w"
    assert detail.report_md == "## Summary\n- nested done\n"

    resolved = store.resolve_log(sid, "w")
    assert resolved is not None
    assert resolved.name == "w.log"
    assert resolved.parent == rundir


def test_nested_refresh_does_not_write_to_the_tree(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha")
    _make_run(root, "alpha", "ram")
    store = _store(root)

    def tree() -> dict[str, tuple[int, int]]:
        return {
            str(path.relative_to(root)): (path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(root.rglob("*"))
        }

    before = tree()
    store.refresh()
    store.refresh()

    assert tree() == before


class _Clock:
    """A hand-moved epoch clock so sample times and the window are exact."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _clocked_store(root: Path, clock: _Clock) -> SnapshotStore:
    return _store(root, clock)


def test_log_series_records_every_scan_even_when_nothing_changed(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha", pid=DEAD_PID, log="hello\n")
    clock = _Clock()
    store = _clocked_store(root, clock)
    assert store.refresh() == {"alpha"}

    clock.now += 10
    assert store.refresh() == set()

    detail = store.detail("alpha")
    assert detail is not None
    assert detail.rev == 1
    worker = detail.workers[0]
    assert [sample.size for sample in worker.log_series] == [6, 6]
    assert [sample.at for sample in worker.log_series] == [1000.0, 1010.0]


def test_summaries_carry_a_ten_bucket_activity_series(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha", pid=DEAD_PID, log="hello\n")
    clock = _Clock()
    store = _clocked_store(root, clock)
    store.refresh()
    clock.now += 10
    store.refresh()

    (summary,) = store.summaries()

    assert len(summary.activity_series) == 10
    assert [sample.count for sample in summary.activity_series] == [0] * 10


def test_log_series_follows_a_size_change_without_bumping_for_samples(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    stage = _make_stage(root, "alpha", pid=DEAD_PID, log="hello\n")
    store = _clocked_store(root, _Clock())
    store.refresh()

    (stage / "alpha.log").write_text("hello, longer now\n")

    assert store.refresh() == {"alpha"}
    detail = store.detail("alpha")
    assert detail is not None
    assert detail.rev == 2
    assert [sample.size for sample in detail.workers[0].log_series] == [6, 18]


def test_log_series_is_dropped_when_the_worker_disappears(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    stage = _make_stage(root, "alpha", pid=DEAD_PID, log="hello\n")
    clock = _Clock()
    store = _clocked_store(root, clock)
    store.refresh()
    clock.now += 5
    store.refresh()

    (stage / "alpha.pid").unlink()
    (stage / "alpha.log").unlink()
    assert store.refresh() == {"alpha"}
    detail = store.detail("alpha")
    assert detail is not None
    assert detail.workers == []

    (stage / "alpha.pid").write_text(DEAD_PID)
    (stage / "alpha.log").write_text("back again, longer\n")
    clock.now += 5
    store.refresh()

    detail = store.detail("alpha")
    assert detail is not None
    assert [sample.size for sample in detail.workers[0].log_series] == [len("back again, longer\n")]


def test_log_series_starts_empty_after_a_restart(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha", pid=DEAD_PID, log="hello\n")
    clock = _Clock()
    _clocked_store(root, clock).refresh()
    clock.now += 5

    restarted = _clocked_store(root, clock)
    restarted.refresh()

    detail = restarted.detail("alpha")
    assert detail is not None
    assert [sample.size for sample in detail.workers[0].log_series] == [6]


def test_log_series_drops_samples_older_than_the_window(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha", pid=DEAD_PID, log="hello\n")
    clock = _Clock()
    store = _clocked_store(root, clock)
    store.refresh()
    clock.now += 31 * 60

    store.refresh()

    detail = store.detail("alpha")
    assert detail is not None
    assert [sample.at for sample in detail.workers[0].log_series] == [1000.0 + 31 * 60]


def test_log_series_is_capped_at_the_newest_samples(tmp_path: Path) -> None:
    root = tmp_path / "Projects"
    _make_stage(root, "alpha", pid=DEAD_PID, log="hello\n")
    clock = _Clock()
    store = _clocked_store(root, clock)
    for _ in range(LOG_SERIES_LIMIT + 10):
        store.refresh()
        clock.now += 1

    detail = store.detail("alpha")
    assert detail is not None
    series = detail.workers[0].log_series
    assert len(series) == LOG_SERIES_LIMIT
    assert series[-1].at == clock.now - 1



def test_discover_strips_session_suffix_and_skips_execution_ids(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha" / ".stage"
    alpha.mkdir(parents=True)
    state = {"stages": [
        {"path": f"{alpha}#old", "section": "closed", "session_id": "old"},
        {"path": f"{alpha}#new", "section": "active", "session_id": "new"},
        {"path": "execution:abc123", "section": "active"},
    ]}
    store = SnapshotStore(Config(), state_fetcher=lambda: state)

    store.discover()
    store.refresh()

    assert [summary.id for summary in store.summaries()] == ["alpha"]
    facts = store.detail("alpha").lat
    assert facts is not None and facts.session_id == "new"


def test_discover_rejects_dirs_that_are_not_stages(tmp_path: Path) -> None:
    plain = tmp_path / "secrets"
    plain.mkdir()
    hashed = tmp_path / "foo#bar" / ".stage"
    hashed.mkdir(parents=True)
    state = {"stages": [{"path": str(plain)}, {"path": "/etc"}, {"path": str(hashed)}]}
    store = SnapshotStore(Config(), state_fetcher=lambda: state)

    store.discover()
    store.refresh()

    assert [summary.path for summary in store.summaries()] == [str(hashed)]
