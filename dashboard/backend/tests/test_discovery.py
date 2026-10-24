"""Stable ids for stage paths supplied by Monitor."""

from pathlib import Path

from stagewatch.discovery import assign_ids, run_id


def test_assign_ids_uses_parent_basename_for_one_top_level_stage(tmp_path: Path) -> None:
    stage = tmp_path / "proj" / ".stage"

    assert assign_ids([stage]) == {"proj": stage}


def test_assign_ids_disambiguates_colliding_parent_basenames(tmp_path: Path) -> None:
    first = tmp_path / "a" / "proj" / ".stage"
    second = tmp_path / "b" / "proj" / ".stage"

    ids = assign_ids([first, second])

    assert len(ids) == 2
    assert all(key.startswith("proj-") for key in ids)
    assert set(ids.values()) == {first, second}


def test_nested_run_id_uses_absolute_path(tmp_path: Path) -> None:
    run = tmp_path / "proj" / ".stage" / "attempt-a"

    assert assign_ids([run]) == {run_id(run): run}
