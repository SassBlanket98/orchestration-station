import json
from pathlib import Path

import pytest

from stagewatch.activity_feed import ActivityFeed


def _line(kind: str, content: list[dict], timestamp: str = "2026-10-24T10:00:00Z") -> str:
    return json.dumps({"type": kind, "timestamp": timestamp, "message": {"content": content}})


def _stage(path: str, kind: str, sid: str, cwd: str) -> dict:
    return {
        "path": path,
        "lead_state": "live",
        "lead_kind": kind,
        "session_id": sid,
        "native_id": sid,
        "cwd": cwd,
    }


def test_reads_newest_meaningful_claude_action_and_filters_secrets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = "/work/project"
    encoded = "-work-project"
    transcript = home / ".claude" / "projects" / encoded / "claude-id.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            [
                _line(
                    "assistant",
                    [
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {"file_path": "/work/project/parse.ts"},
                        }
                    ],
                ),
                _line(
                    "assistant",
                    [{"type": "tool_use", "name": "Bash", "input": {"command": "pnpm test"}}],
                ),
                _line(
                    "assistant",
                    [
                        {
                            "type": "tool_use",
                            "name": "AskUserQuestion",
                            "input": {"question": "TOKEN=secret"},
                        }
                    ],
                ),
                _line("assistant", [{"type": "text", "text": "KEY=secret"}]),
            ]
        )
        + "\n"
    )
    feed = ActivityFeed(home=home)

    result = feed.get([_stage("/stage/a", "claude", "claude-id", cwd)])

    assert result == {
        "/stage/a": {"text": "asked the user a question", "ts": 1792836000.0, "kind": "ask"}
    }


def test_falls_back_to_last_assistant_sentence_and_bounds_read(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    cwd = "/work/project"
    transcript = home / ".claude-extra" / "projects" / "-work-project" / "say-id.jsonl"
    monkeypatch.setenv("STATION_CLAUDE_CONFIG_DIRS", ".claude-extra")
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(
        (b"x" * (300 * 1024))
        + (
            "\n"
            + _line("assistant", [{"type": "text", "text": "Finished the parser.\nMore details"}])
            + "\n"
        ).encode()
    )
    reads: list[int] = []
    original = Path.open

    class LimitedReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def seek(self, *args):
            return self.handle.seek(*args)

        def read(self, size=-1):
            reads.append(size)
            return self.handle.read(size)

    def tracked_open(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        return LimitedReader(handle) if Path(path) == transcript else handle

    monkeypatch.setattr(Path, "open", tracked_open)
    result = ActivityFeed(home=home).get([_stage("/stage/say", "claude", "say-id", cwd)])

    assert result["/stage/say"]["text"] == "Finished the parser."
    assert result["/stage/say"]["kind"] == "say"
    assert reads == [256 * 1024]


def test_finds_codex_rollout_by_thread_id_and_ignores_non_live_or_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    rollout = home / ".codex" / "sessions" / "2026" / "rollout-2026-10-24T10-00-00-thread-123.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-10-24T10:00:00Z",
                "payload": {
                    "type": "function_call",
                    "name": "Edit",
                    "arguments": {"file_path": "/tmp/main.py"},
                },
            }
        )
        + "\n"
    )
    live = _stage("/stage/codex", "codex", "thread-123", "/work")
    not_live = _stage("/stage/dead", "claude", "missing", "/work") | {"lead_state": "gone"}
    missing = _stage("/stage/missing", "claude", "missing", "/work")

    result = ActivityFeed(home=home).get([live, not_live, missing])

    assert list(result) == ["/stage/codex"]
    assert result["/stage/codex"]["text"] == "Edit main.py"


def test_drops_secret_looking_events_and_limits_stage_count(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = "/project"
    transcript = home / ".claude" / "projects" / "-project" / "secret-id.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        _line(
            "assistant",
            [{"type": "tool_use", "name": "Bash", "input": {"command": "export API_KEY=abc"}}],
        )
        + "\n"
    )
    stages = [_stage(f"/stage/{i}", "claude", "secret-id", cwd) for i in range(45)]

    result = ActivityFeed(home=home).get(stages)
    assert len(result) == 40
    assert all(item["text"] == "Bash command" for item in result.values())
    assert "abc" not in repr(result) and "API_KEY" not in repr(result)


def test_command_words_and_transcript_outside_project_folder(tmp_path: Path) -> None:
    from stagewatch.activity_feed import _command_words

    assert _command_words('cd x && git commit -m "fix"') == "git commit"
    assert _command_words("FOO=1 pnpm build --strict") == "pnpm build"
    assert _command_words("curl -H 'Authorization: x' https://a") == "curl"
    assert _command_words("(cd a; ls -d) | head") == "ls"
    home = tmp_path / "home"
    stray = home / ".claude" / "projects" / "-other" / "sid.jsonl"
    stray.parent.mkdir(parents=True)
    stray.write_text(_line("assistant", [{"type": "text", "text": "hello"}]) + "\n")
    assert ActivityFeed(home=home).get([_stage("/stage/x", "claude", "sid", "/work/project")]) == {}


def test_caps_results_at_forty_live_stages(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = "/project"
    transcript = home / ".claude" / "projects" / "-project" / "valid-id.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(_line("assistant", [{"type": "text", "text": "Working"}]) + "\n")
    stages = [_stage(f"/stage/{i}", "claude", "valid-id", cwd) for i in range(45)]

    result = ActivityFeed(home=home).get(stages)

    assert len(result) == 40


def test_codex_index_builds_right_after_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # monotonic() starts near 0 on a fresh machine; the first lookup must still scan.
    home = tmp_path / "home"
    rollout = home / ".codex" / "sessions" / "rollout-2026-10-24T10-00-00-thread-9.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("")
    monkeypatch.setattr("stagewatch.activity_feed.time.monotonic", lambda: 5.0)
    assert ActivityFeed(home=home)._find_codex("thread-9") == rollout
