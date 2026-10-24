"""build.py joins src/*.py into one namespace, so a top-level def or class name used twice silently replaces the first."""
from __future__ import annotations

import ast
import collections
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class BuildNameTests(unittest.TestCase):
    def test_top_level_names_are_unique_across_modules(self) -> None:
        order = re.findall(r'"(\w+)"', re.search(r"ORDER = \[(.*?)\]", (ROOT / "build.py").read_text(), re.DOTALL).group(1))
        seen: dict[str, list[str]] = collections.defaultdict(list)
        for module in order:
            for node in ast.parse((ROOT / "src" / f"{module}.py").read_text()).body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    seen[node.name].append(module)
        self.assertEqual({k: v for k, v in seen.items() if len(v) > 1}, {})


if __name__ == "__main__":
    unittest.main()
