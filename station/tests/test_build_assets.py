import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build


class BuildOutputTests(unittest.TestCase):
    def test_built_output_has_no_embedded_web_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            (src / "web").mkdir(parents=True)
            (src / "web" / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
            (src / "header.txt").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (src / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
            old_root, old_order = build.ROOT, build.ORDER
            build.ROOT, build.ORDER = root, ["model"]
            try:
                build.main()
            finally:
                build.ROOT, build.ORDER = old_root, old_order
            out = (root / "station").read_text(encoding="utf-8")
        self.assertIn("VALUE = 1", out)
        self.assertNotIn("_EMBEDDED_ASSETS", out)
        self.assertNotIn("index.html", out)


if __name__ == "__main__":
    unittest.main()
