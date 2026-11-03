"""The generated single-file program must preserve source-tree behavior."""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _build_in_temp(directory: Path) -> Path:
    directory.mkdir(parents=True)
    shutil.copy2(ROOT / "build.py", directory / "build.py")
    shutil.copytree(ROOT / "src", directory / "src")
    subprocess.run([sys.executable, str(directory / "build.py")], cwd=directory, check=True,
                   capture_output=True, text=True)
    return directory / "station"


def _load_script(path: Path):
    loader = SourceFileLoader("status_built_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError("could not create a module spec for built station")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _codex_fixture(home: Path) -> None:
    rollout = home / ".codex/sessions/2026/10/11/rollout-built.jsonl"
    rollout.parent.mkdir(parents=True)
    events = [
        {"type": "session_meta", "payload": {"id": "built-thread", "cwd": "/tmp/task", "source": "cli"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "# Keep the built Codex row\nDo the task"}]}},
        {"type": "event_msg", "timestamp": "2026-10-11T20:00:00Z", "payload": {
         "type": "task_complete", "last_agent_message": "verified result"}},
    ]
    rollout.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


class BuiltRuntimeTests(unittest.TestCase):
    def test_built_native_reader_keeps_codex_row_and_human_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            built = _build_in_temp(temp_path / "build")
            module = _load_script(built)
            home = temp_path / "home"
            home.mkdir()
            _codex_fixture(home)

            with mock.patch.dict(sys.modules, {"codexread": None}):
                records = module.read_native_executions("codex", str(home), 1791749000)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].record.title, "Keep the built Codex row")

    def test_function_level_sibling_imports_are_guarded_for_joined_build(self) -> None:
        order_match = re.search(r"ORDER = \[(.*?)\]", (ROOT / "build.py").read_text(), re.DOTALL)
        self.assertIsNotNone(order_match)
        siblings = set(re.findall(r'"(\w+)"', order_match.group(1)))
        violations: list[str] = []

        for source in sorted((ROOT / "src").glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            parents: dict[ast.AST, ast.AST] = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent

            for node in ast.walk(tree):
                imported = []
                if isinstance(node, ast.Import):
                    imported.extend(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module.split(".")[0])
                if not siblings.intersection(imported):
                    continue

                child = node
                ancestor = parents.get(child)
                inside_function = False
                guarded = False
                while ancestor is not None:
                    if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        inside_function = True
                    if isinstance(ancestor, ast.Try):
                        names = {name for handler in ancestor.handlers for name in _exception_names(handler.type)}
                        scope = parents.get(ancestor)
                        while scope is not None and not isinstance(scope, ast.Module):
                            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                                inside_function = True
                                break
                            scope = parents.get(scope)
                        if (inside_function and child in ancestor.body
                                and names.intersection({"ImportError", "ModuleNotFoundError"})):
                            guarded = True
                    child = ancestor
                    ancestor = parents.get(ancestor)
                if inside_function and not guarded:
                    violations.append(f"{source.name}:{node.lineno}")

        self.assertEqual(violations, [], "function-level sibling imports need an ImportError fallback")


def _exception_names(node: ast.expr | None) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Tuple):
        return set().union(*(_exception_names(item) for item in node.elts))
    return set()


if __name__ == "__main__":
    unittest.main()
