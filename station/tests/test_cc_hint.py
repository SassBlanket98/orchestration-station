"""CC hint row: the confirm question, the status note, the spawn prompt or the shortened key hint."""
from __future__ import annotations

import unittest

from cchint import HINT, hint_line
from model import UIState
from widgets import _wd_C_AMBER, _wd_SGR_RE, _wd_width

WIDTHS = (20, 60, 100, 140, 200)


def plain(s: str) -> str:
    """The visible text of a styled row."""
    return _wd_SGR_RE.sub("", s)


def modes() -> dict[str, UIState]:
    return {
        "confirm": UIState(cc_confirm=("stop", "/p/alpha|c1-codexread", "stop c1-codexread?")),
        "note": UIState(note="stopped c1-codexread"),
        "spawn": UIState(cc_spawn=True),
        "hint": UIState(),
    }


class HintLineTests(unittest.TestCase):
    def test_actions_are_not_advertised_when_disabled(self) -> None:
        hint = plain(hint_line(UIState(cc=True, allow_actions=False), 200))
        for key in ("s stop", "n spawn", "r resume", "K kill"):
            self.assertNotIn(key, hint)

    def test_actions_are_advertised_when_enabled(self) -> None:
        hint = plain(hint_line(UIState(cc=True, allow_actions=True), 200))
        for key in ("s stop", "n spawn", "r resume", "K kill"):
            self.assertIn(key, hint)

    def test_exact_width_in_every_mode(self) -> None:
        for name, ui in modes().items():
            for w in WIDTHS:
                with self.subTest(mode=name, width=w):
                    line = hint_line(ui, w)
                    self.assertEqual(_wd_width(line), w)
                    self.assertNotIn("\n", line)

    def test_confirm_beats_note_and_spawn(self) -> None:
        ui = UIState(cc_confirm=("stop", "/p/alpha|c1", "stop c1?"), note="note text", cc_spawn=True)
        self.assertEqual(plain(hint_line(ui, 120)).strip(), "stop c1?")

    def test_confirm_is_amber_and_bold(self) -> None:
        line = hint_line(UIState(cc_confirm=("kill", "/p/alpha|c1", "kill c1?")), 100)
        self.assertIn(f"38;2;{_wd_C_AMBER[0]};{_wd_C_AMBER[1]};{_wd_C_AMBER[2]}", line)
        self.assertIn("\x1b[1;", line)
        self.assertEqual(plain(line).strip(), "kill c1?")

    def test_confirm_is_verbatim_and_truncated_with_ellipsis(self) -> None:
        question = "spawn c1-codexread with model luna on the monitor stream?"
        self.assertEqual(plain(hint_line(UIState(cc_confirm=("spawn", "k", question)), 200)).strip(), question)
        short = plain(hint_line(UIState(cc_confirm=("spawn", "k", question)), 20))
        self.assertTrue(short.rstrip().endswith("…"))
        self.assertTrue(question.startswith(short.rstrip()[:-1]))

    def test_note_wins_over_hint_and_spawn(self) -> None:
        ui = UIState(note="resumed c2-events", cc_spawn=True)
        self.assertEqual(plain(hint_line(ui, 120)).strip(), "resumed c2-events")

    def test_note_newlines_become_spaces(self) -> None:
        ui = UIState(note="first line\nsecond\tline")
        out = plain(hint_line(ui, 100))
        self.assertNotIn("\n", out)
        self.assertNotIn("\t", out)
        self.assertIn("first line second line", out)

    def test_long_note_is_truncated_with_ellipsis(self) -> None:
        ui = UIState(note="x" * 500)
        out = plain(hint_line(ui, 40))
        self.assertEqual(len(out), 40)
        self.assertTrue(out.endswith("…"))

    def test_spawn_hint(self) -> None:
        self.assertEqual(plain(hint_line(UIState(cc_spawn=True), 100)).strip(), "spawn: pick a model")

    def test_hint_drops_whole_items_only(self) -> None:
        items = HINT.split(" · ")
        for w in (20, 40, 60, 100):
            with self.subTest(width=w):
                out = plain(hint_line(UIState(), w)).rstrip()
                self.assertTrue(HINT.startswith(out))
                self.assertEqual(out, " · ".join(items[:len(out.split(" · "))]))
                self.assertLessEqual(_wd_width(out), _wd_width(HINT))

    def test_hint_full_when_it_fits(self) -> None:
        self.assertEqual(plain(hint_line(UIState(), 200)).strip(), HINT)

    def test_hint_names_details_and_expand_keys(self) -> None:
        hint = plain(hint_line(UIState(), 200))
        self.assertIn("d details", hint)
        self.assertIn("←→ pane tabs", hint)
        self.assertIn("1-6 pane tab", hint)
        self.assertNotIn("Tab pane", hint)

    def test_hint_styled_dim(self) -> None:
        self.assertIn("\x1b[", hint_line(UIState(), 100))

    def test_tiny_widths_do_not_raise(self) -> None:
        for name, ui in modes().items():
            for w in (0, 1, 2, 5):
                with self.subTest(mode=name, width=w):
                    line = hint_line(ui, w)
                    self.assertEqual(_wd_width(line), w)
                    self.assertNotIn("\n", line)

    def test_width_one_truncates_to_ellipsis(self) -> None:
        self.assertEqual(plain(hint_line(UIState(), 1)), "…")
        self.assertEqual(plain(hint_line(UIState(note="a long note"), 1)), "…")


if __name__ == "__main__":
    unittest.main()
