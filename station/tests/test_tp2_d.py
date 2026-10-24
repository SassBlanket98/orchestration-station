"""TP2-D: K on the stop question moves to kill when ccactions allows it."""
from __future__ import annotations

import unittest
from unittest import mock

import cckeys
from cckeys import cc_key
from test_cc_keys import SEL_WORKER, _actions, _gs, _ui


class StopToKillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.arm_s = cckeys._CXK_ARM_S
        cckeys._CXK_ARM_S = 0.35
        cckeys._CXK_ARMED.update(value=None, t=0.0)
        cckeys._CXK_TARGET = None

    def tearDown(self) -> None:
        cckeys._CXK_ARM_S = self.arm_s

    def _armed_stop(self) -> tuple[object, object]:
        ui = _ui()
        with _actions():
            cc_key(ui, "s", _gs(), 40, SEL_WORKER)
        return ui, cckeys._CXK_ARMED["t"]

    def test_k_after_arm_rearms_kill_and_sends_nothing(self) -> None:
        ui, armed_at = self._armed_stop()
        assert ui.cc_confirm is not None
        self.assertEqual(ui.cc_confirm[0], "stop")
        with mock.patch.object(cckeys, "can", return_value=(True, "")) as can, \
                mock.patch.object(cckeys, "confirm_text", return_value="Kill w1? (y/n)") as confirm_text, \
                mock.patch.object(cckeys, "run") as run, \
                mock.patch.object(cckeys.time, "monotonic", return_value=armed_at + 0.36):
            self.assertEqual(cc_key(ui, "K", _gs(), 40, None), "")
        can.assert_called_once()
        self.assertEqual(can.call_args.args[0], "kill")
        confirm_text.assert_called_once_with("kill", can.call_args.args[1])
        self.assertEqual(ui.cc_confirm, ("kill", "/stage/one|w1", "Kill w1? (y/n)"))
        self.assertEqual(ui.note, "")
        self.assertEqual(run.call_count, 0)

    def test_k_when_kill_refused_cancels_with_prefixed_note(self) -> None:
        ui, armed_at = self._armed_stop()
        with mock.patch.object(cckeys, "can", return_value=(False, "send stop first")), \
                mock.patch.object(cckeys, "run") as run, \
                mock.patch.object(cckeys.time, "monotonic", return_value=armed_at + 0.36):
            self.assertEqual(cc_key(ui, "K", _gs(), 40, None), "")
        self.assertIsNone(ui.cc_confirm)
        self.assertEqual(ui.note, "kill: send stop first")
        self.assertEqual(run.call_count, 0)

    def test_k_before_arm_does_not_cancel(self) -> None:
        ui, armed_at = self._armed_stop()
        with mock.patch.object(cckeys, "can") as can, mock.patch.object(cckeys, "run") as run, \
                mock.patch.object(cckeys.time, "monotonic", return_value=armed_at + 0.01):
            self.assertEqual(cc_key(ui, "K", _gs(), 40, None), "")
        self.assertIsNotNone(ui.cc_confirm)
        self.assertEqual(ui.cc_confirm[0], "stop")
        self.assertEqual(ui.note, "")
        can.assert_not_called()
        self.assertEqual(run.call_count, 0)

    def test_other_keys_still_cancel_stop(self) -> None:
        ui, _armed_at = self._armed_stop()
        with _actions() as run:
            self.assertEqual(cc_key(ui, "x", _gs(), 40, None), "")
        self.assertEqual(ui.note, "cancelled")
        self.assertIsNone(ui.cc_confirm)
        self.assertEqual(run.call_count, 0)

    def test_k_on_kill_question_cancels(self) -> None:
        ui = _ui(cc_confirm=("kill", "/stage/one|w1", "Kill w1? (y/n)"))
        cckeys._CXK_ARMED.update(value=ui.cc_confirm, t=100.0)
        with _actions() as run, mock.patch.object(cckeys.time, "monotonic", return_value=200.0):
            self.assertEqual(cc_key(ui, "K", _gs(), 40, None), "")
        self.assertEqual(ui.note, "cancelled")
        self.assertIsNone(ui.cc_confirm)
        self.assertEqual(run.call_count, 0)


if __name__ == "__main__":
    unittest.main()
