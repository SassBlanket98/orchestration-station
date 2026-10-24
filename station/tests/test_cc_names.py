"""The built station joins src/*.py into one namespace: top-level names must not collide between modules."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _top_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


class NameCollisionTests(unittest.TestCase):
    def test_no_private_name_defined_in_two_modules(self) -> None:
        order = re.search(r"^ORDER = \[(.*?)\]", (SRC.parent / "build.py").read_text(), re.MULTILINE).group(1)   # the modules build.py joins
        all_modules = re.findall(r"\"(\w+)\"", order)
        seen: dict[str, str] = {}
        clashes: list[str] = []
        for module in all_modules:
            for name in _top_names(SRC / f"{module}.py"):
                if name.startswith("_") and name in seen and name != "__all__":
                    clashes.append(f"{name}: {seen[name]} and {module}")
                seen[name] = module
        self.assertEqual(clashes, [])


if __name__ == "__main__":
    unittest.main()
