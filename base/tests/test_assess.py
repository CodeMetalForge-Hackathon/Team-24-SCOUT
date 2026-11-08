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


class CO2Rules(unittest.TestCase):
    # set_co2(level, rise, rise_secs, rh_jump, now)
    def test_co2_alone_is_possible_with_reasons(self):
        b = brain()
        b.set_co2("BREATHING", 380, 10, 6, 10)
        self.assertEqual(b.evaluate(11), ("POSSIBLE", "breathing detected: CO2 +380 ppm, humidity +6%"))
        b.set_co2("SUDDEN_RISE", 240, 10, 1, 12)
        self.assertEqual(b.evaluate(12), ("POSSIBLE", "sudden CO2 rise +240 ppm in 10s"))
        b.set_co2("SLOW_BUILDUP", 70, 720, 0, 13)
        self.assertEqual(b.evaluate(13), ("POSSIBLE", "slow CO2 build-up +70 ppm over 12 min"))

    def test_elevated_and_normal_never_change_status(self):
        b = brain()
        for lvl in ("ELEVATED", "NORMAL", "LEARNING"):
            b.set_co2(lvl, 0, 0, 0, 1)
            self.assertEqual(b.evaluate(2)[0], "NO SIGNS", lvl)

    def test_breathing_plus_sound_is_likely_in_either_order(self):
        b = brain()
        b.set_co2("BREATHING", 380, 10, 6, 10)
        b.activity(40, "TAP")                             # knock 30 s later (still held)
        self.assertEqual(b.evaluate(41), ("LIKELY", "breathing + knocking"))
        b2 = brain()
        b2.activity(10, "VOICE")                          # voice first...
        b2.set_co2("BREATHING", 300, 10, 5, 60)           # ...then breathing
        self.assertEqual(b2.evaluate(61), ("LIKELY", "breathing + voice"))

    def test_combination_ends_when_both_are_over(self):
        b = brain()
        b.set_co2("BREATHING", 380, 10, 6, 10)
        b.activity(20, "TAP")
        b.set_co2("ELEVATED", 0, 0, 0, 75)                # hold expired, knock long ago
        self.assertEqual(b.evaluate(76), ("NO SIGNS", "no evidence yet"))

    def test_sound_too_old_does_not_combine(self):
        b = brain()
        b.activity(0, "TAP")
        b.set_co2("SUDDEN_RISE", 250, 10, 0, 200)         # 200 s later > COMBINE_WINDOW
        self.assertEqual(b.evaluate(200)[0], "POSSIBLE")

    def test_co2_never_confirms_or_downgrades_confirmed(self):
        b = brain()
        b.set_co2("BREATHING", 900, 10, 10, 1)
        b.activity(2, "VOICE")
        self.assertNotEqual(b.evaluate(3)[0], "CONFIRMED")
        b.button_press(4); b.button_press(5)
        b.set_co2("NORMAL", 0, 0, 0, 6)
        self.assertEqual(b.evaluate(7)[0], "CONFIRMED")

    def test_unresponsive_needs_co2_and_two_missed_calls(self):
        b = brain()
        b.set_co2("SUDDEN_RISE", 250, 10, 0, 1)
        b.no_response(); b.no_response()
        self.assertEqual(b.evaluate(2), ("UNRESPONSIVE", "CO2 up, 2 calls unanswered"))

    def test_reset_clears_co2_evidence(self):
        b = brain()
        b.set_co2("BREATHING", 380, 10, 6, 10)
        b.reset()
        self.assertEqual(b.evaluate(11), ("NO SIGNS", "no evidence yet"))


if __name__ == "__main__":
    unittest.main()
