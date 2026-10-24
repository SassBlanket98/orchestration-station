"""Bounded reads of log files. No code path here reads a whole file.

Lines are split on raw bytes before decoding, so a multi-byte UTF-8 sequence
can never be cut in half by a window edge. Decoding uses errors="replace".
"""

import os
from pathlib import Path
from typing import NamedTuple

OVERSIZED_LINE = "(record larger than 1 MB)"
MAX_WINDOW = 1024 * 1024
_CHUNK = 64 * 1024


class TailChunk(NamedTuple):
    lines: list[str]
    offset: int  # byte offset just after the last returned line
    size: int  # file size at read time
    truncated: bool  # True when bytes exist outside what was returned
    inode: int


def split_lines(raw: bytes) -> list[str]:
    """Split a byte block into decoded lines, with CRs removed."""
    if not raw:
        return []
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    return [part.decode("utf-8", errors="replace").replace("\r", "") for part in raw.split(b"\n")]


def read_tail(path: Path, max_bytes: int = 65536) -> TailChunk:
    """Return the last complete lines within the final `max_bytes` of the file.

    A line counts as complete when it is newline-terminated, or ends at EOF
    after a known record boundary (start of file, or the byte before `start`
    is a newline). The first partial line in the window is dropped. If the
    window holds no complete line, the window doubles up to 1 MB; past that a
    single OVERSIZED_LINE placeholder is returned.
    """
    window = min(max(1, max_bytes), MAX_WINDOW)
    with path.open("rb") as fh:
        st = os.fstat(fh.fileno())
        size, inode = st.st_size, st.st_ino
        while True:
            start = max(0, size - window)
            fh.seek(start)
            data = fh.read(size - start)
            if start == 0:
                return TailChunk(split_lines(data), size, size, False, inode)
            # Is `start` on a record boundary? Peek at the byte before it: a
            # cut record whose tail is all we hold must not pose as a line.
            fh.seek(start - 1)
            on_boundary = fh.read(1) == b"\n"
            if on_boundary:
                return TailChunk(split_lines(data), size, size, True, inode)
            nl = data.find(b"\n")
            if nl != -1 and nl + 1 < len(data):
                return TailChunk(split_lines(data[nl + 1 :]), size, size, True, inode)
            if window >= MAX_WINDOW:
                return TailChunk([OVERSIZED_LINE], size, size, True, inode)
            window = min(window * 2, MAX_WINDOW)


def _skip_record(fh, pos: int, size: int) -> int:
    """Return the offset just after the next newline at or past `pos`, or `size`."""
    fh.seek(pos)
    while pos < size:
        chunk = fh.read(min(_CHUNK, size - pos))
        if not chunk:
            break
        idx = chunk.find(b"\n")
        if idx != -1:
            return pos + idx + 1
        pos += len(chunk)
    return size


def read_from(path: Path, offset: int, max_bytes: int = 262144) -> TailChunk:
    """Return complete lines starting at `offset`, reading at most about `max_bytes`.

    Only newline-terminated lines are returned, so `offset` on the result is
    always a record boundary. An unterminated fragment at EOF is left for a
    later call. If `offset` is past EOF (the file shrank), no lines are returned
    and `offset` on the result is the current size.
    """
    with path.open("rb") as fh:
        st = os.fstat(fh.fileno())
        size, inode = st.st_size, st.st_ino
        if offset < 0 or offset > size:
            return TailChunk([], size, size, False, inode)
        window = min(max(1, max_bytes), MAX_WINDOW)
        while True:
            fh.seek(offset)
            data = fh.read(min(window, size - offset))
            last_nl = data.rfind(b"\n")
            if last_nl != -1:
                body = data[: last_nl + 1]
                end = offset + len(body)
                return TailChunk(split_lines(body), end, size, end < size, inode)
            if offset + len(data) >= size:
                return TailChunk([], offset, size, False, inode)
            if window >= MAX_WINDOW:
                end = _skip_record(fh, offset + len(data), size)
                return TailChunk([OVERSIZED_LINE], end, size, end < size, inode)
            window *= 2
