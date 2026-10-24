from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build

SIBLING_IMPORT = re.compile(r"^from (\w+) import .*$", re.MULTILINE)
SIBLING_START = re.compile(r"^from (\w+) import ")


def _join_statement(line: str, rest) -> str:
    """Normalise one import the way build.py does: keep appending lines until parentheses balance."""
    stmt = line.rstrip()
    while stmt.count("(") > stmt.count(")"):
        nxt = next(rest, None)
        if nxt is None:
            break
        stmt += " " + nxt.strip()
    return stmt


def aliased_sibling_imports(text: str, siblings: set[str]) -> list[str]:
    """Return joined `from <sibling> import ...` statements that alias a name with ` as `."""
    found: list[str] = []
    rest = iter(text.split("\n"))
    for line in rest:
        match = SIBLING_START.match(line)
        if not match or match.group(1) not in siblings:
            continue
        stmt = _join_statement(line, rest)
        if " as " in stmt:
            found.append(stmt)
    return found


class JoinedBuildImports(unittest.TestCase):
    """station drops sibling imports and joins modules in ORDER, so they must resolve by plain name."""

    def modules(self) -> set[str]:
        return {path.stem for path in (ROOT / "src").glob("*.py")}

    def test_every_sibling_imported_by_a_joined_module_is_in_order(self) -> None:
        mods = self.modules()
        for name in build.ORDER:
            text = (ROOT / "src" / f"{name}.py").read_text()
            for dep in SIBLING_IMPORT.findall(text):
                if dep in mods:
                    self.assertIn(dep, build.ORDER, f"{name}.py imports {dep}, which build.ORDER omits")
                    self.assertLess(build.ORDER.index(dep), build.ORDER.index(name) + 1, f"{dep} must be joined before {name}")

    def test_joined_modules_do_not_alias_sibling_imports(self) -> None:
        mods = self.modules()
        for name in build.ORDER:
            text = (ROOT / "src" / f"{name}.py").read_text()
            aliased = aliased_sibling_imports(text, mods)
            self.assertEqual(aliased, [], f"{name}.py aliases a sibling import; the joined file loses the alias")

    def test_guard_detects_parenthesised_aliased_sibling_import(self) -> None:
        source = "from registry import (\n    a,\n    b as c,\n)\n"
        self.assertEqual(aliased_sibling_imports(source, {"registry"}),
                         ["from registry import ( a, b as c, )"])

    def test_guard_ignores_clean_and_non_sibling_aliases(self) -> None:
        source = ("from registry import (\n    a,\n    b,\n)\n"
                  "from os import path as p\n"
                  "from registry import x\n")
        self.assertEqual(aliased_sibling_imports(source, {"registry"}), [])


if __name__ == "__main__":
    unittest.main()
