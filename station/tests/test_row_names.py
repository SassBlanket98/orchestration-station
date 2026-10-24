"""Terminal overview row names: a project root's name ignores the collector's disambiguation suffixes, and a session's
own name is unique within its project root."""
from __future__ import annotations

import hashlib
import unittest

from model import Stage
from render import _rd_g_session_names, _rd_g_short_labels


def stage(path: str, label: str, *, dir: str = "", session_name: str = "", session_id: str = "",
          goal: str = "", unlinked: bool = False) -> Stage:
    return Stage(path=path, label=label, dir=dir, session_name=session_name, session_id=session_id,
                 goal=goal, unlinked=unlinked)


class RootNameIgnoresSuffixes(unittest.TestCase):
    def test_suffixed_labels_of_one_root_all_show_the_root_name(self) -> None:
        root = "/home/u/Projects/sample-project"
        stages = [
            stage(f"{root}#a", "sample-project", dir=root),
            stage(f"{root}#b", "sample-project · claude bg", dir=root),
            stage(f"{root}#c", "Projects/sample-project", dir=root),
        ]
        self.assertEqual(_rd_g_short_labels(stages), {root: "sample-project"})

    def test_hash_number_suffix_is_stripped_too(self) -> None:
        root = "/home/u/Projects/openclaw"
        stages = [stage(f"{root}#a", "openclaw #2", dir=root), stage(f"{root}#b", "openclaw · opencode", dir=root)]
        self.assertEqual(_rd_g_short_labels(stages), {root: "openclaw"})

    def test_two_roots_with_the_same_basename_keep_path_parts(self) -> None:
        a = "/home/u/Projects/openclaw"
        b = "/mnt/work/openclaw"
        stages = [
            stage(f"{a}#x", "Projects/openclaw · claude bg", dir=a),
            stage(f"{b}#y", "work/openclaw · opencode", dir=b),
        ]
        self.assertEqual(_rd_g_short_labels(stages), {a: "Projects/openclaw", b: "work/openclaw"})


class SessionNamesUnique(unittest.TestCase):
    def test_same_name_in_one_root_gets_session_id_suffix_and_unique_name_stays(self) -> None:
        root = "/home/u/Projects/app"
        stages = [
            stage(f"{root}#a", "app", dir=root, session_name="Fix tests", session_id="abcd1234"),
            stage(f"{root}#b", "app", dir=root, session_name="fix TESTS", session_id="ef567890"),
            stage(f"{root}#c", "app", dir=root, session_name="Docs", session_id="0123aaaa"),
        ]
        names = _rd_g_session_names(stages)
        self.assertEqual(names[f"{root}#a"], "Fix tests #abcd")
        self.assertEqual(names[f"{root}#b"], "fix TESTS #ef56")
        self.assertEqual(names[f"{root}#c"], "Docs")

    def test_same_name_in_different_roots_is_not_a_clash(self) -> None:
        a = stage("/home/u/one#a", "one", dir="/home/u/one", session_name="Plan", session_id="aaaa1111")
        b = stage("/home/u/two#b", "two", dir="/home/u/two", session_name="Plan", session_id="bbbb2222")
        self.assertEqual(_rd_g_session_names([a, b]), {a.path: "Plan", b.path: "Plan"})

    def test_missing_session_id_uses_a_stable_hash_of_the_path(self) -> None:
        root = "/home/u/Projects/app"
        a = stage(f"{root}#a", "app", dir=root, session_name="Plan")
        b = stage(f"{root}#b", "app", dir=root, session_name="Plan")
        first = _rd_g_session_names([a, b])
        self.assertEqual(first, _rd_g_session_names([a, b]))
        self.assertEqual(first[a.path], "Plan #" + hashlib.sha1(a.path.encode("utf-8")).hexdigest()[:4])
        self.assertNotEqual(first[a.path], first[b.path])


class SessionNameFallback(unittest.TestCase):
    def test_empty_name_falls_back_to_the_first_four_goal_words(self) -> None:
        root = "/home/u/Projects/app"
        s = stage(f"{root}#a", "app", dir=root, goal="Refactor the big legacy parser module")
        self.assertEqual(_rd_g_session_names([s]), {s.path: "Refactor the big legacy"})

    def test_empty_name_and_empty_goal_fall_back_to_session(self) -> None:
        root = "/home/u/Projects/app"
        s = stage(f"{root}#a", "app", dir=root, goal="   ")
        self.assertEqual(_rd_g_session_names([s]), {s.path: ""})

    def test_two_nameless_goalless_rows_keep_an_empty_name(self) -> None:
        root = "/home/u/Projects/app"
        a = stage(f"{root}#a", "app", dir=root, session_id="1111aaaa")
        b = stage(f"{root}#b", "app", dir=root, session_id="2222bbbb")
        self.assertEqual(_rd_g_session_names([a, b]), {a.path: "", b.path: ""})

    def test_unlinked_group_keeps_an_empty_name(self) -> None:
        root = "/home/u/Projects/app"
        s = stage(f"{root}#unlinked", "app", dir=root, unlinked=True)
        self.assertEqual(_rd_g_session_names([s]), {s.path: ""})


if __name__ == "__main__":
    unittest.main()
