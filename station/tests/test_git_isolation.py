"""run.sh under a pre-push hook's environment: the git-using tests must not touch the repo GIT_DIR points at."""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GIT_MODULES = ["test_worker_changes", "test_pages", "test_gitstate", "test_ops_retention"]


def git(repo: str, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", repo, "-c", "user.name=s", "-c", "user.email=s@s", *args], check=True,
                          capture_output=True, text=True, env=env | {"GIT_CONFIG_GLOBAL": "/dev/null"}).stdout


class HookEnvironment(unittest.TestCase):
    def test_hook_git_env_does_not_reach_the_repo(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q")
            git(repo, "commit", "-q", "--allow-empty", "-m", "sentinel")
            config = Path(repo, ".git/config")
            before = (hashlib.sha256(config.read_bytes()).hexdigest(), git(repo, "for-each-ref"))
            hook_env = os.environ | {"GIT_DIR": f"{repo}/.git", "GIT_WORK_TREE": repo,
                                     "GIT_INDEX_FILE": f"{repo}/.git/index"}
            done = subprocess.run(["bash", str(HERE / "run.sh"), *GIT_MODULES], check=False, env=hook_env,
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            self.assertEqual((hashlib.sha256(config.read_bytes()).hexdigest(), git(repo, "for-each-ref")), before)


if __name__ == "__main__":
    unittest.main()
