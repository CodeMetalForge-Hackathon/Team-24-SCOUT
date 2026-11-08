"""Tests the REAL node brain (firmware/node/assess.py) on the laptop."""
import unittest

from base.node_brain import load_assess

assess = load_assess()


def brain():
    return assess.Assessor(activity_window=15, confirm_window=20)


class ButtonRules(unittest.TestCase):
    def test_first_press_counts_down(self):
        b = brain()
        self.assertEqual(b.button_press(100), "first")
        self.assertEqual(b.evaluate(100), ("LIKELY", "pressed once - press again (20s)"))
        self.assertEqual(b.evaluate(106.5), ("LIKELY", "pressed once - press again (14s)"))

    def test_second_press_confirms_and_latches(self):
        b = brain()
        b.button_press(100)
        self.assertEqual(b.button_press(110), "confirmed")
        self.assertEqual(b.evaluate(111), ("CONFIRMED", "pressed button twice"))
        b.activity(112, "VOICE")                  # sounds never downgrade CONFIRMED
        self.assertEqual(b.evaluate(500)[0], "CONFIRMED")

    def test_single_press_expires(self):
        b = brain()
        b.button_press(100)
        self.assertEqual(b.evaluate(120.1), ("POSSIBLE", "single press, not confirmed"))
        self.assertEqual(b.evaluate(135.1), ("NO SIGNS", "no evidence yet"))

    def test_late_second_press_starts_over(self):
        b = brain()
        b.button_press(100)
        self.assertEqual(b.button_press(125), "first")   # outside the 20 s window
        self.assertEqual(b.evaluate(125)[0], "LIKELY")

    def test_sound_reasons_and_reset(self):
        b = brain()
        b.activity(10, "TAP")
        self.assertEqual(b.evaluate(11), ("POSSIBLE", "heard knocking"))
        b.activity(12, "VOICE")
        self.assertEqual(b.evaluate(13), ("POSSIBLE", "heard voice"))
        b.button_press(14)
        b.button_press(15)
        b.reset()
        self.assertEqual(b.evaluate(16), ("NO SIGNS", "no evidence yet"))


if __name__ == "__main__":
    unittest.main()
