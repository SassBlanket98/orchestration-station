import json

from stagewatch.picks import read_picks


def test_lead_picks_win_over_prose(tmp_path):
    stage = tmp_path / ".stage"
    stage.mkdir()
    (stage / "lead.json").write_text(
        json.dumps({"schema": 1, "picks": [{"job": "build", "tier": "small"}]})
    )
    (stage / "stream.log").write_text("job=prose tierUsed=large\n")

    records, reason = read_picks(stage)

    assert reason is None
    assert len(records) == 1
    assert (records[0].job, records[0].tier, records[0].source) == ("build", "small", "lead.json")


def test_parses_stream_block(tmp_path):
    stage = tmp_path / ".stage"
    stage.mkdir()
    (stage / "stream.log").write_text(
        "job=build tierUsed=small\n"
        "model-a@high ok group: fast\n"
        "model-b@low runs-out group: budget\n"
        "assign: api=codex, ui=claude\n"
        "skipped: docs — no capacity\n"
    )

    records, reason = read_picks(stage)

    assert reason is None
    record = records[0]
    assert record.job == "build"
    assert record.tier == "small"
    assert [(c.model, c.effort, c.group, c.reason) for c in record.candidates] == [
        ("model-a", "high", "fast", "ok"),
        ("model-b", "low", "budget", "runs-out"),
    ]
    assert record.assigned == ["api=codex", "ui=claude"]
    assert record.skipped == ["docs — no capacity"]


def test_rejects_symlink_escape(tmp_path):
    stage = tmp_path / ".stage"
    stage.mkdir()
    outside = tmp_path / "outside"
    outside.write_text(json.dumps({"schema": 1, "picks": [{"job": "secret"}]}))
    (stage / "lead.json").symlink_to(outside)

    records, reason = read_picks(stage)

    assert records == []
    assert reason and "escapes" in reason


def test_rejects_oversized_stream(tmp_path):
    stage = tmp_path / ".stage"
    stage.mkdir()
    (stage / "stream.log").write_bytes(b"x" * (1024 * 1024 + 1))

    records, reason = read_picks(stage)

    assert records == []
    assert reason and "exceeds" in reason


def test_does_not_invent_score(tmp_path):
    stage = tmp_path / ".stage"
    stage.mkdir()
    (stage / "stream.log").write_text("job=build tierUsed=small\nmodel-a@high ok\n")

    records, _ = read_picks(stage)

    assert records[0].candidates[0].model == "model-a"
    assert not hasattr(records[0].candidates[0], "score")
