from __future__ import annotations

import os
import tempfile
import unittest

from collect import find_stages


def touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


class NestedStage(unittest.TestCase):
    def test_a_dispatch_review_folder_inside_a_stage_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            touch(f"{root}/proj/.stage/station")
            touch(f"{root}/proj/.stage/a.pid")
            # The review stage is discoverable from the contract markers alone.
            touch(f"{root}/proj/.stage/review/review-muse.pid")
            touch(f"{root}/proj/.stage/plain/notes.txt")   # no stage markers
            found = dict(find_stages([root]))
        paths = {os.path.relpath(p, root) for p in found}
        self.assertEqual(paths, {"proj/.stage", "proj/.stage/review"})
        self.assertEqual(found[f"{root}/proj/.stage/review"].endswith("(review)"), True)

    def test_stage_markers_do_not_require_status_script(self) -> None:
        import collect

        with tempfile.TemporaryDirectory() as root:
            for marker in ("briefs", "PROGRESS.md", "worker.pid", "lead.json", "worker/REPORT.md"):
                path = os.path.join(root, marker)
                if marker == "briefs":
                    os.mkdir(path)
                else:
                    touch(path)
                self.assertTrue(collect._co_is_stage_dir(root), marker)
                if marker != "briefs":
                    os.unlink(path)
                else:
                    os.rmdir(path)

            touch(os.path.join(root, "unrelated.txt"))
            self.assertFalse(collect._co_is_stage_dir(root))


if __name__ == "__main__":
    unittest.main()
