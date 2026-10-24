import json
import time
from pathlib import Path

from stagewatch.logparse import now_line
from stagewatch.tail import MAX_WINDOW, OVERSIZED_LINE, read_from, read_tail

FIVE_MB = 5 * 1024 * 1024
TWO_MB = 2 * 1024 * 1024


def _write_jsonl_of_size(path: Path, min_bytes: int) -> None:
    pad = "x" * 200
    with path.open("w", encoding="utf-8") as fh:
        written = 0
        n = 0
        while written < min_bytes:
            line = json.dumps({"type": "text", "n": n, "part": {"text": pad}}) + "\n"
            fh.write(line)
            written += len(line)
            n += 1


def test_small_file_returns_all_lines(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_bytes(b"one\ntwo\nthree\n")

    chunk = read_tail(path)

    assert chunk.lines == ["one", "two", "three"]
    assert chunk.offset == chunk.size == 14
    assert chunk.truncated is False
    assert chunk.inode == path.stat().st_ino


def test_window_drops_first_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("".join(f"line{i:05d}\n" for i in range(10_000)), encoding="utf-8")

    chunk = read_tail(path, max_bytes=100)

    assert chunk.truncated is True
    assert chunk.lines[-1] == "line09999"
    assert all(len(line) == 9 and line.startswith("line") for line in chunk.lines)


def test_five_mb_jsonl_tail_under_50_ms(tmp_path: Path) -> None:
    path = tmp_path / "big.jsonl"
    _write_jsonl_of_size(path, FIVE_MB)
    assert path.stat().st_size >= FIVE_MB

    timings = []
    for _ in range(3):
        start = time.perf_counter()
        chunk = read_tail(path, max_bytes=FIVE_MB)
        timings.append(time.perf_counter() - start)
    assert chunk.lines and json.loads(chunk.lines[-1])["type"] == "text"
    assert min(timings) < 0.05

    start = time.perf_counter()
    line = now_line(path)
    assert time.perf_counter() - start < 0.05
    assert line is not None and line.kind == "text"


def test_window_doubles_until_a_complete_line_fits(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    big = "a" * 300_000
    path.write_text(f"head\n{big}\n", encoding="utf-8")

    chunk = read_tail(path)

    assert chunk.lines == ["head", big]
    assert chunk.truncated is False


def test_two_mb_line_gives_oversized_fallback(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("first\n" + "b" * TWO_MB + "\n", encoding="utf-8")

    chunk = read_tail(path)

    assert chunk.lines == [OVERSIZED_LINE]
    assert chunk.offset == chunk.size
    assert MAX_WINDOW == 1024 * 1024


def test_read_from_handles_two_mb_line(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_text("first\n" + "b" * TWO_MB + "\nafter\n", encoding="utf-8")

    first = read_from(path, 0)
    assert first.lines == ["first"]
    assert first.offset == 6

    oversized = read_from(path, first.offset)
    assert oversized.lines == [OVERSIZED_LINE]
    assert oversized.offset == first.offset + TWO_MB + 1
    assert oversized.truncated is True

    rest = read_from(path, oversized.offset)
    assert rest.lines == ["after"]


def test_read_from_doubles_for_a_300_kb_line(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    big = "c" * 300_000
    path.write_text(f"{big}\nafter\n", encoding="utf-8")

    chunk = read_from(path, 0, max_bytes=4096)

    assert chunk.lines == [big, "after"]
    assert chunk.offset == path.stat().st_size
    assert chunk.truncated is False


def test_invalid_utf8_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_bytes(b"ok \xff\xfe bad\ncaf\xc3\xa9\n")

    chunk = read_tail(path)

    assert chunk.lines == ["ok �� bad", "café"]


def test_carriage_returns_are_stripped(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_bytes(b"alpha\r\nbeta\r\n")

    assert read_tail(path).lines == ["alpha", "beta"]
    assert read_from(path, 0).lines == ["alpha", "beta"]


def test_read_from_offsets_do_not_depend_on_chunk_size(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    lines = [f"{i} " + "é" * (i % 37) + "z" * (i % 11) for i in range(2000)]
    data = "".join(line + "\n" for line in lines).encode("utf-8")
    path.write_bytes(data)

    for max_bytes in (1, 7, 64, 1000, 262_144):
        collected: list[str] = []
        offset = 0
        steps = 0
        while offset < len(data):
            chunk = read_from(path, offset, max_bytes=max_bytes)
            assert chunk.offset > offset, f"no progress at {offset} with {max_bytes}"
            assert data[chunk.offset - 1 : chunk.offset] == b"\n"
            collected.extend(chunk.lines)
            offset = chunk.offset
            steps += 1
            assert steps < 100_000
        assert collected == lines, f"mismatch with max_bytes={max_bytes}"


def test_read_from_leaves_unterminated_fragment_for_later(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_bytes(b"a\nb")

    first = read_from(path, 0)
    assert first.lines == ["a"]
    assert first.offset == 2

    partial = read_from(path, first.offset)
    assert partial.lines == []
    assert partial.offset == 2

    with path.open("ab") as fh:
        fh.write(b"\n")
    done = read_from(path, first.offset)
    assert done.lines == ["b"]
    assert done.offset == 4


def test_read_from_past_eof_returns_nothing(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    path.write_bytes(b"short\n")

    chunk = read_from(path, 999)

    assert chunk.lines == []
    assert chunk.offset == chunk.size == 6


def test_read_tail_drops_straddling_record(tmp_path: Path) -> None:
    # The window cuts into the middle of the big A record: its tail must not
    # pose as a complete line; the last real record is returned instead.
    p = tmp_path / "straddle.log"
    p.write_bytes(b"A" * 3000 + b"\n" + b"B" * 40 + b"\n")
    chunk = read_tail(p, max_bytes=64)
    assert chunk.lines == ["B" * 40]


def test_read_tail_returns_eof_line_when_start_is_on_boundary(tmp_path: Path) -> None:
    # 151 bytes: "x" * 50, one newline (byte 50), then "y" * 100. With a
    # 100-byte window, start = 51 sits right after that newline, so the final
    # unterminated "y" line is a real record and must be returned as-is.
    p = tmp_path / "boundary.log"
    p.write_bytes(b"x" * 50 + b"\n" + b"y" * 100)
    chunk = read_tail(p, max_bytes=100)
    assert chunk.lines == ["y" * 100]


def test_read_tail_doubles_when_window_has_no_boundary(tmp_path: Path) -> None:
    # One unterminated record, no newline anywhere: doubling must recover the
    # whole file once start reaches 0.
    p = tmp_path / "oneline.log"
    p.write_bytes(b"z" * 3000)
    chunk = read_tail(p, max_bytes=32)
    assert chunk.lines == ["z" * 3000]
