from __future__ import annotations

import unittest

from ccrow import command_lines
from model import LogState, Step, Worker
from widgets import _wd_width


class CommandLineTests(unittest.TestCase):
    def test_long_command_wraps_when_expanded(self) -> None:
        worker = Worker("w", "running", goal="run a very long command " * 8,
                        log=LogState(steps=[Step(1, "tool", "bash", "python3 " + "argument " * 15)]))
        lines = command_lines(worker, 24, True)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(_wd_width(line) == 24 for line in lines))

    def test_cjk_and_emoji_measure_in_cells(self) -> None:
        worker = Worker("w", "running", goal="界🙂界🙂")
        lines = command_lines(worker, 20, True)
        self.assertEqual(_wd_width(lines[0]), 20)

    def test_empty_command_has_no_subline(self) -> None:
        self.assertEqual(command_lines(Worker("w", "done"), 20, False), [])

    def test_narrow_width_is_still_exact(self) -> None:
        worker = Worker("w", "running", goal="abcdefghij")
        for expanded in (False, True):
            lines = command_lines(worker, 20, expanded)
            self.assertTrue(lines)
            self.assertTrue(all(_wd_width(line) == 20 for line in lines))


if __name__ == "__main__":
    unittest.main()
