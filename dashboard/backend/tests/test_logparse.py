import json
from pathlib import Path

from stagewatch.logparse import detect_format, model_from_log, now_line, parse_lines
from stagewatch.models import LogLine


def _jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def _text(text: str) -> dict:
    return {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": text}}


def _tool(part_extra: dict, input_: dict | None = None, title: str | None = None) -> dict:
    state: dict = {"status": "completed"}
    if input_ is not None:
        state["input"] = input_
    if title is not None:
        state["title"] = title
    return {
        "type": "tool_use",
        "sessionID": "ses_1",
        "part": {"type": "tool", "tool": "read", **part_extra, "state": state},
    }


def test_detect_missing_empty_and_directory(tmp_path: Path) -> None:
    empty = tmp_path / "e.log"
    empty.write_bytes(b"")

    assert detect_format(tmp_path / "nope.log") == "missing"
    assert detect_format(empty) == "empty"
    assert detect_format(tmp_path) == "missing"


def test_detect_opencode_by_type_or_session_id(tmp_path: Path) -> None:
    by_type = tmp_path / "a.log"
    by_type.write_text(_jsonl({"type": "step_start"}), encoding="utf-8")
    by_session = tmp_path / "b.log"
    by_session.write_text(_jsonl({"sessionID": "ses_9", "other": 1}), encoding="utf-8")

    assert detect_format(by_type) == "opencode-jsonl"
    assert detect_format(by_session) == "opencode-jsonl"


def test_detect_codex_by_header(tmp_path: Path) -> None:
    by_banner = tmp_path / "a.log"
    by_banner.write_text("OpenAI Codex v1\nhello\n", encoding="utf-8")
    by_model = tmp_path / "b.log"
    by_model.write_text("intro\nmodel: gpt-x\n", encoding="utf-8")

    assert detect_format(by_banner) == "codex-text"
    assert detect_format(by_model) == "codex-text"


def test_detect_plain_prose_is_text(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("Just some prose.\nMore prose.\n", encoding="utf-8")

    assert detect_format(path) == "text"


def test_opencode_mapping_for_each_record_type() -> None:
    lines = [
        json.dumps({"type": "step_start", "part": {}}),
        json.dumps(_text("hello there")),
        json.dumps(_tool({"tool": "bash"}, input_={"command": "make test"}, title="x")),
        json.dumps(_tool({"tool": "edit"}, input_={"filePath": "/a/b.py"})),
        json.dumps(_tool({"tool": "read"}, input_={"path": "/c.txt"})),
        json.dumps(_tool({"tool": "grep"}, input_={}, title="grep TODO")),
        json.dumps({"type": "step_finish", "part": {}}),
    ]

    out = parse_lines(lines, "opencode-jsonl")

    assert out[0] == LogLine(kind="step", text="step_start", tool=None)
    assert out[1] == LogLine(kind="text", text="hello there", tool=None)
    assert out[2] == LogLine(kind="tool", text="make test", tool="bash")
    assert out[3] == LogLine(kind="tool", text="/a/b.py", tool="edit")
    assert out[4] == LogLine(kind="tool", text="/c.txt", tool="read")
    assert out[5] == LogLine(kind="tool", text="grep TODO", tool="grep")
    assert out[6] == LogLine(kind="step", text="step_finish", tool=None)


def test_command_wins_over_other_fallbacks() -> None:
    line = json.dumps(
        _tool({"tool": "bash"}, input_={"command": "a", "filePath": "b", "path": "c"}, title="d")
    )

    assert parse_lines([line], "opencode-jsonl")[0].text == "a"


def test_empty_command_is_kept_as_nullish_fallback() -> None:
    line = json.dumps(_tool({"tool": "bash"}, input_={"command": "", "filePath": "b"}))

    assert parse_lines([line], "opencode-jsonl")[0].text == ""


def test_unknown_and_unparseable_lines_are_raw() -> None:
    lines = [json.dumps({"type": "mystery", "x": 1}), "not json at all", "[1, 2]"]

    out = parse_lines(lines, "opencode-jsonl")

    assert [item.kind for item in out] == ["raw", "raw", "raw"]
    assert out[1].text == "not json at all"


def test_non_jsonl_formats_are_all_raw() -> None:
    lines = ["OpenAI Codex v1", "model: gpt-x", '{"type": "text", "part": {"text": "hi"}}']

    for fmt in ("codex-text", "text"):
        out = parse_lines(lines, fmt)  # type: ignore[arg-type]
        assert [item.kind for item in out] == ["raw", "raw", "raw"]
        assert out[2].text == lines[2]


def test_line_text_is_capped_at_2000_chars() -> None:
    long = "q" * 5000
    line = json.dumps(_text(long))

    assert len(parse_lines([line], "opencode-jsonl")[0].text) == 2000
    assert len(parse_lines([long], "text")[0].text) == 2000


def test_now_line_prefers_last_text_or_tool_record(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text(
        _jsonl(
            {"type": "step_start", "part": {}},
            _text("working on it"),
            _tool({"tool": "bash"}, input_={"command": "pytest -q"}),
            {"type": "step_finish", "part": {}},
        ),
        encoding="utf-8",
    )

    line = now_line(path)

    assert line is not None
    assert (line.kind, line.text, line.tool) == ("tool", "pytest -q", "bash")


def test_now_line_falls_back_to_last_nonblank_line(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("first\nsecond\n\n   \n", encoding="utf-8")

    line = now_line(path)

    assert line is not None
    assert (line.kind, line.text, line.tool) == ("raw", "second", None)


def test_now_line_flattens_newlines_and_caps_length(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    text = "line one\nline two\r\n" + "z" * 900
    path.write_text(_jsonl(_text(text)), encoding="utf-8")

    line = now_line(path)

    assert line is not None
    assert line.kind == "text"
    assert "\n" not in line.text and "\r" not in line.text
    assert line.text.startswith("line one line two")
    assert len(line.text) == 500


def test_now_line_none_for_missing_or_empty(tmp_path: Path) -> None:
    empty = tmp_path / "e.log"
    empty.write_bytes(b"")

    assert now_line(tmp_path / "missing.log") is None
    assert now_line(empty) is None


def test_model_from_header_within_first_40_lines(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("OpenAI Codex\nmodel: gpt-6-luna\n", encoding="utf-8")

    assert model_from_log(path) == "gpt-6-luna"


def test_model_header_past_line_40_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("filler\n" * 40 + "model: late-model\n", encoding="utf-8")

    assert model_from_log(path) is None


def test_model_from_json_value_in_first_64_kb(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text(
        _jsonl({"type": "step_start", "info": {"modelID": "claude-test-1"}}),
        encoding="utf-8",
    )

    assert model_from_log(path) == "claude-test-1"


def test_model_json_value_past_64_kb_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("x" * (70 * 1024) + '\n{"model": "far-away"}\n', encoding="utf-8")

    assert model_from_log(path) is None


def test_model_none_for_missing_file(tmp_path: Path) -> None:
    assert model_from_log(tmp_path / "missing.log") is None


# --------------------------------------------------------------------------- #
# Claude stream-json
# --------------------------------------------------------------------------- #
def _claude_init() -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "model": "claude-x",
        "tools": ["Bash", "Read"],
        "session_id": "s",
    }


def _claude_text(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "model": "claude-x",
            "content": [{"type": "text", "text": text}],
        },
        "session_id": "s",
    }


def _claude_tool(name: str, input_: dict) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": "msg_2",
            "model": "claude-x",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": name, "input": input_}],
        },
        "session_id": "s",
    }


def _cursor_init() -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "apiKeySource": "login",
        "session_id": "s",
        "model": "Composer",
    }


def _cursor_tool(call: dict) -> dict:
    return {
        "type": "tool_call",
        "subtype": "started",
        "call_id": "call_1",
        "tool_call": call,
        "session_id": "s",
    }


def test_detect_claude_stream_json(tmp_path: Path) -> None:
    path = tmp_path / "claude.log"
    path.write_text(_jsonl(_claude_init(), _claude_text("hi")), encoding="utf-8")

    assert detect_format(path) == "claude-stream-json"


def test_detect_cursor_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "cursor.log"
    path.write_text(
        _jsonl(_cursor_init(), _cursor_tool({"readToolCall": {"args": {"path": "a"}}})),
        encoding="utf-8",
    )

    assert detect_format(path) == "cursor-jsonl"


def test_detect_cursor_text_by_banner(tmp_path: Path) -> None:
    path = tmp_path / "cursor.txt"
    path.write_text("Cursor Agent\nReviewing the diff.\n", encoding="utf-8")

    assert detect_format(path) == "cursor-text"


def test_claude_records_map_to_line_kinds() -> None:
    lines = [
        json.dumps(_claude_init()),
        json.dumps(_claude_text("hello there")),
        json.dumps(_claude_tool("Bash", {"command": "pytest -q"})),
        json.dumps(_claude_tool("Read", {"file_path": "/a/b.py"})),
        json.dumps({"type": "result", "subtype": "success", "num_turns": 2}),
    ]

    out = parse_lines(lines, "claude-stream-json")

    assert out[0] == LogLine(kind="step", text="init", tool=None)
    assert out[1] == LogLine(kind="text", text="hello there", tool=None)
    assert out[2] == LogLine(kind="tool", text="pytest -q", tool="Bash")
    assert out[3] == LogLine(kind="tool", text="/a/b.py", tool="Read")
    assert out[4] == LogLine(kind="step", text="success", tool=None)


def test_cursor_records_map_to_line_kinds() -> None:
    lines = [
        json.dumps(_cursor_init()),
        json.dumps(_cursor_tool({"readToolCall": {"args": {"path": "src/a.ts"}}})),
        json.dumps(_cursor_tool({"writeToolCall": {"args": {"path": "out.txt"}}})),
        json.dumps(_cursor_tool({"function": {"name": "grep", "arguments": '{"query": "TODO"}'}})),
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}
        ),
    ]

    out = parse_lines(lines, "cursor-jsonl")

    assert out[0] == LogLine(kind="step", text="init", tool=None)
    assert out[1] == LogLine(kind="tool", text="src/a.ts", tool="read")
    assert out[2] == LogLine(kind="tool", text="out.txt", tool="write")
    assert out[3] == LogLine(kind="tool", text="TODO", tool="grep")
    assert out[4] == LogLine(kind="text", text="done", tool=None)


def test_cursor_text_strips_control_sequences() -> None:
    lines = ["\x1b[31mred\x1b[0m and \x07bell", "plain"]

    out = parse_lines(lines, "cursor-text")

    assert [item.kind for item in out] == ["text", "text"]
    assert out[0].text == "red and bell"
    assert out[1].text == "plain"


def test_malformed_records_fall_back_to_raw_for_new_formats() -> None:
    bad = ["not json at all", "[1, 2]", json.dumps({"type": "mystery"})]

    for fmt in ("claude-stream-json", "cursor-jsonl"):
        out = parse_lines(bad, fmt)  # type: ignore[arg-type]
        assert [item.kind for item in out] == ["raw", "raw", "raw"]
        assert out[0].text == "not json at all"


def test_claude_line_text_is_capped() -> None:
    line = json.dumps(_claude_text("q" * 5000))

    assert len(parse_lines([line], "claude-stream-json")[0].text) == 2000


def test_now_line_prefers_the_last_claude_text_or_tool(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text(
        _jsonl(
            _claude_init(),
            _claude_text("thinking"),
            _claude_tool("Bash", {"command": "make test"}),
            {"type": "result", "subtype": "success", "num_turns": 1},
        ),
        encoding="utf-8",
    )

    line = now_line(path)

    assert line is not None
    assert (line.kind, line.text, line.tool) == ("tool", "make test", "Bash")


def test_now_line_cursor_text_is_cleaned(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("Cursor Agent\n\x1b[32mstill working\x1b[0m\n", encoding="utf-8")

    line = now_line(path)

    assert line is not None
    assert (line.kind, line.text) == ("text", "still working")



def test_ansi_is_stripped_from_raw_text_and_now_line(tmp_path: Path) -> None:
    lines = ["\x1b[0m\x1b[1mbuild\x1b[0m · model", "\x1b[0m$ \x1b[0mls"]

    for fmt in ("text", "codex-text"):
        out = parse_lines(lines, fmt)  # type: ignore[arg-type]
        assert [item.text for item in out] == ["build · model", "$ ls"]
    log = tmp_path / "w.log"
    log.write_text("\n".join(lines) + "\n\x1b[0m\n")
    now = now_line(log)
    assert now is not None
    assert now.text == "$ ls"
