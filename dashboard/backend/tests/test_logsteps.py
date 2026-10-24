import json

from stagewatch.logsteps import parse_log_steps


def test_codex_exec_and_exit_tokens(tmp_path):
    path = tmp_path / "codex.log"
    path.write_text(
        "OpenAI Codex v0.161.0\nexec\nprintf hello\n succeeded in 1,234ms:\n"
        "hello\ntokens used\n12,345\n"
    )
    result = parse_log_steps(path)
    assert [(s.kind, s.text, s.duration_ms, s.ok, s.at) for s in result.steps] == [
        ("exec", "printf hello", 1234, True, None)
    ]
    assert (result.tokens, result.tokens_source) == (12345, "codex_exit")


def test_codex_stdin_hang(tmp_path):
    path = tmp_path / "hang.log"
    path.write_text("Reading additional input from stdin...\n")
    result = parse_log_steps(path)
    assert result.steps == []
    assert result.tokens is None
    assert [(e.kind, e.at) for e in result.events] == [("stdin_hang", None)]


def test_opencode_timestamp_tool_and_tokens(tmp_path):
    path = tmp_path / "opencode.jsonl"
    tool = {
        "type": "tool_use",
        "timestamp": 1791495539874,
        "part": {
            "tool": "shell",
            "state": {"status": "completed", "input": {"command": "pwd"}},
        },
    }
    finish = {"type": "step_finish", "tokens": {"input": 10, "output": 3}}
    path.write_text(json.dumps(tool) + "\n" + json.dumps(finish) + "\n")
    result = parse_log_steps(path)
    assert [(s.kind, s.text, s.at, s.ok) for s in result.steps] == [
        ("shell", "pwd", 1791495539874, True)
    ]
    assert (result.tokens, result.tokens_source) == (13, "opencode_step_finish")


def test_opencode_permission_reject(tmp_path):
    path = tmp_path / "permission.log"
    path.write_text(
        '{"type":"step_start"}\n'
        "! permission requested: external_directory (/tmp/x); auto-rejecting\n"
    )
    event, = parse_log_steps(path).events
    assert (event.kind, event.at) == ("permission_reject", None)


def test_claude_prose_has_no_derived_steps_or_tokens(tmp_path):
    path = tmp_path / "claude.log"
    path.write_text("Implemented the change. Tests were not run.\n")
    result = parse_log_steps(path)
    assert result.steps == []
    assert result.tokens is None
    assert result.tokens_source is None
    assert result.events == []


def test_marker_past_read_cap_is_not_parsed(tmp_path):
    path = tmp_path / "large.log"
    path.write_bytes(b"x" * (256 * 1024) + b"\nOpenAI Codex v1\nexec\ncmd\n succeeded in 1ms:\n")

    result = parse_log_steps(path)

    assert result.steps == []
    assert result.tokens is None


def test_opencode_cost_sums_step_finish_parts(tmp_path):
    path = tmp_path / "opencode.jsonl"
    first = {"type": "step_finish", "part": {"cost": 0.25}}
    second = {"type": "step_finish", "part": {"cost": 0.5}}
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
    assert parse_log_steps(path).cost == 0.75


def test_opencode_cost_is_none_without_cost_fields(tmp_path):
    path = tmp_path / "opencode.jsonl"
    path.write_text(json.dumps({"type": "step_finish", "part": {}}) + "\n")
    assert parse_log_steps(path).cost is None


def test_claude_result_cost_is_read_from_the_tail(tmp_path):
    path = tmp_path / "claude.jsonl"
    # The result record sits past the head read, so only the tail read finds it.
    filler = json.dumps({"type": "assistant", "message": {"content": "x" * 1000}}) + "\n"
    result = {"type": "result", "subtype": "success", "total_cost_usd": 1.5, "result": "y" * 4000}
    path.write_text(filler * 400 + json.dumps(result) + "\n")

    assert path.stat().st_size > 256 * 1024
    assert parse_log_steps(path).cost == 1.5


def test_claude_result_without_cost_is_none(tmp_path):
    path = tmp_path / "claude.jsonl"
    path.write_text(json.dumps({"type": "result", "subtype": "success"}) + "\n")
    assert parse_log_steps(path).cost is None
