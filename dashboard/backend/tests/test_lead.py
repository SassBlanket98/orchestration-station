import json
from pathlib import Path

from stagewatch.lead import parse_lead


def _stage(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_stream_heading_proves_phase_cycle_and_timestamp(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.md").write_text("## Build — cycle 2 — 2026-10-09 12:30 UTC\n")

    result = parse_lead(stage, "stream.md")

    assert result["phase"] == "build"
    assert result["cycle"] == 2
    assert result["phase_at"] == [{"phase": "build", "cycle": 2, "at": "2026-10-09 12:30 UTC"}]
    assert result["lead_source"] == "stream"


def test_progress_explicit_phase_and_cycle(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "PROGRESS.md").write_text("10:00 plan cycle 1 complete\n10:30 build cycle 1 started\n")

    result = parse_lead(stage, "missing.md")

    assert result["phase"] == "build"
    assert result["cycle"] == 1
    assert result["phase_at"] == []
    assert result["lead_source"] == "stream"


def test_conflicting_phase_evidence_is_unknown(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.md").write_text("## Build cycle 1\n")
    (stage / "PROGRESS.md").write_text("10:00 review cycle 1 started\n")

    result = parse_lead(stage, "stream.md")

    assert result["phase"] is None
    assert result["cycle"] is None
    assert result["lead_source"] == "unknown"


def test_nested_run_wording_is_ambiguous(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.md").write_text("## Build cycle 1\nNested run: plan cycle 2\n")

    result = parse_lead(stage, "stream.md")

    assert result["phase"] is None
    assert result["cycle"] is None
    assert result["lead_label"] is None
    assert result["lead_source"] == "unknown"


def test_cycle_is_clamped_and_over_policy_set(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.md").write_text("## Review cycle 5\n")

    result = parse_lead(stage, "stream.md")

    assert result["phase"] == "review"
    assert result["cycle"] == 3
    assert result["over_policy"] is True


def test_symlink_lead_json_is_rejected(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    target = stage / "target.json"
    target.write_text(json.dumps({"schema": 1, "phase": "review", "cycle": 1}))
    (stage / "lead.json").symlink_to(target)
    (stage / "stream.md").write_text("## Build cycle 1\n")

    result = parse_lead(stage, "stream.md")

    assert result["phase"] == "build"
    assert result["lead_source"] == "stream"


def test_oversized_lead_json_is_rejected(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "lead.json").write_text(" " * (64 * 1024 + 1))
    (stage / "stream.md").write_text("## Build cycle 1\n")

    result = parse_lead(stage, "stream.md")

    assert result["phase"] == "build"
    assert result["lead_source"] == "stream"


def test_untrusted_stream_files_are_ignored(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    target = stage / "real.md"
    target.write_text("## Build cycle 1\n")
    (stage / "stream.md").symlink_to(target)

    symlink_result = parse_lead(stage, "stream.md")
    assert symlink_result["phase"] is None
    assert symlink_result["lead_source"] == "unknown"

    (stage / "stream.md").unlink()
    (stage / "stream.md").write_text(" " * (64 * 1024 + 1) + "\n## Build cycle 1\n")
    oversized_result = parse_lead(stage, "stream.md")
    assert oversized_result["phase"] is None
    assert oversized_result["lead_source"] == "unknown"


def test_small_regular_stream_file_still_parses(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "stream.md").write_text("## Build cycle 1\n")

    result = parse_lead(stage, "stream.md")

    assert result["phase"] == "build"
    assert result["lead_source"] == "stream"


def test_schema_one_lead_json_wins_over_prose(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    (stage / "lead.json").write_text(json.dumps({
        "schema": 1, "lead_model": "m", "lead_label": "Lead", "harness": "codex",
        "account": "a", "pid": 42, "started": "start", "phase": "plan", "cycle": 2,
        "stream": "docs/stream.md", "updated_at": "update", "picks": [],
    }))
    (stage / "stream.md").write_text("## Review cycle 3\n")

    result = parse_lead(stage, "stream.md")

    assert result["phase"] == "plan"
    assert result["cycle"] == 2
    assert result["lead_model"] == "m"
    assert result["lead_source"] == "lead.json"
    assert result["phase_at"] == [{"phase": "plan", "cycle": 2, "at": "update"}]
