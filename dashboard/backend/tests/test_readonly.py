"""The package must not kill processes, spawn subprocesses, or open files for writing."""

import re
from pathlib import Path

import stagewatch

PACKAGE = Path(stagewatch.__file__).parent
# Real call sites only: a docstring may say "subprocess" without calling one.
PROCESS_CALLS = re.compile(r"\bos\.kill\(|\bsubprocess\.|\bimport subprocess\b|\bfrom subprocess\b")
# An open() call whose mode literal contains a write, append, create or update flag.
OPEN_CALL = re.compile(r"""\bopen\([^()]*?["'](?P<mode>[rwxabt+]{1,3})["']""")
PATH_OPEN_CALL = re.compile(r"""\.open\(\s*["'](?P<mode>[rwxabt+]{1,3})["']""")


def _sources() -> list[tuple[Path, str]]:
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, "no package sources found"
    return [(path, path.read_text(encoding="utf-8")) for path in files]


def test_package_never_kills_or_spawns() -> None:
    for path, text in _sources():
        match = PROCESS_CALLS.search(text)
        assert match is None, f"process call {match.group(0)!r} in {path.name}"


def test_package_opens_files_read_only() -> None:
    for path, text in _sources():
        for pattern in (OPEN_CALL, PATH_OPEN_CALL):
            for match in pattern.finditer(text):
                mode = match.group("mode")
                assert not set(mode) & set("wxa+"), f"write-mode open in {path.name}: {mode!r}"


def test_readonly_patterns_see_the_mode() -> None:
    assert OPEN_CALL.search('with open(path, "w") as fh:').group("mode") == "w"
    assert OPEN_CALL.search("open(path, 'ab')").group("mode") == "ab"
    assert PATH_OPEN_CALL.search('path.open("r+b")').group("mode") == "r+b"
    assert OPEN_CALL.search('open(path, "rb")').group("mode") == "rb"
    assert PATH_OPEN_CALL.search('path.open("rb")').group("mode") == "rb"
    assert PROCESS_CALLS.search("import subprocess")
    assert PROCESS_CALLS.search("os.kill(pid, 15)")
    assert PROCESS_CALLS.search("from subprocess import run")
    assert PROCESS_CALLS.search("subprocess.run(['ls'])")
    assert PROCESS_CALLS.search('"""no subprocesses here"""') is None
