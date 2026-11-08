"""Tests the REAL node brain (firmware/node/assess.py) on the laptop: the decision table."""
import unittest

from base.node_brain import load_assess

assess = load_assess()


def brain():
    return assess.Assessor(activity_window=15, response_window=60, confirm_window=20,
                           combine_window=120)


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
        b.activity(112, "VOICE")                  # sounds / CO2 never downgrade CONFIRMED
        b.set_co2("NORMAL", 0, 0, 0, 113)
        b.no_response(114); b.no_response(115)
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

    def test_press_resets_unanswered_calls(self):
        b = brain()
        b.no_response(1); b.no_response(2)
        b.button_press(3)
        self.assertEqual(b.unanswered_calls, 0)

    def test_reset_clears_everything(self):
        b = brain()
        b.button_press(14); b.button_press(15)
        b.no_response(16)
        b.set_co2("BREATHING", 380, 10, 6, 17)
        b.reset()
        self.assertEqual(b.evaluate(18), ("NO SIGNS", "no evidence yet"))
        self.assertEqual(b.unanswered_calls, 0)


class CallRules(unittest.TestCase):
    def test_tap_back_is_likely_for_response_window(self):
        b = brain()
        b.call_response(10, "TAP", 3)
        self.assertEqual(b.evaluate(11), ("LIKELY", "tapped back 3 times after call"))
        self.assertEqual(b.evaluate(69)[0], "LIKELY")
        self.assertEqual(b.evaluate(71)[0], "NO SIGNS")    # 60 s window over

    def test_voice_answer(self):
        b = brain()
        b.call_response(10, "VOICE")
        self.assertEqual(b.evaluate(11), ("LIKELY", "answered by voice after call"))

    def test_response_resets_unanswered(self):
        b = brain()
        b.no_response(1); b.no_response(2)
        b.call_response(3, "TAP", 2)
        self.assertEqual(b.unanswered_calls, 0)


class SoundRules(unittest.TestCase):
    def test_unprompted_sounds(self):
        b = brain()
        b.activity(10, "TAP")
        self.assertEqual(b.evaluate(11), ("POSSIBLE", "heard knocking"))
        b.activity(12, "VOICE")
        self.assertEqual(b.evaluate(13), ("POSSIBLE", "heard voice"))
        self.assertEqual(b.evaluate(28)[0], "NO SIGNS")

    def test_sound_beats_single_press_reason(self):
        b = brain()
        b.button_press(100)
        b.activity(121, "TAP")                    # after the countdown expired
        self.assertEqual(b.evaluate(122), ("POSSIBLE", "heard knocking"))


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

    def test_elevated_normal_learning_never_change_status(self):
        b = brain()
        for lvl in ("ELEVATED", "NORMAL", "LEARNING"):
            b.set_co2(lvl, 0, 0, 0, 1)
            self.assertEqual(b.evaluate(2)[0], "NO SIGNS", lvl)

    def test_co2_plus_unprompted_sound_is_likely(self):
        b = brain()
        b.activity(10, "TAP")
        b.set_co2("BREATHING", 380, 10, 6, 100)            # knock 90 s earlier
        self.assertEqual(b.evaluate(101), ("LIKELY", "breathing + knocking"))
        b.set_co2("SUDDEN_RISE", 200, 10, 1, 102)
        self.assertEqual(b.evaluate(103), ("LIKELY", "CO2 rise + knocking"))
        b.activity(104, "VOICE")
        self.assertEqual(b.evaluate(105), ("LIKELY", "CO2 rise + voice"))

    def test_sound_too_old_does_not_combine(self):
        b = brain()
        b.activity(0, "TAP")
        b.set_co2("BREATHING", 250, 10, 4, 200)            # 200 s later > COMBINE_WINDOW
        self.assertEqual(b.evaluate(200)[0], "POSSIBLE")

    def test_co2_never_confirms(self):
        b = brain()
        b.set_co2("BREATHING", 900, 10, 10, 1)
        b.activity(2, "VOICE")
        self.assertNotEqual(b.evaluate(3)[0], "CONFIRMED")


class UnresponsiveRules(unittest.TestCase):
    def test_breathing_and_two_missed_calls(self):
        b = brain()
        b.set_co2("BREATHING", 380, 10, 6, 1)
        b.no_response(10)
        self.assertEqual(b.evaluate(11)[0], "POSSIBLE")    # only 1 missed
        b.no_response(40)
        self.assertEqual(b.evaluate(41), ("UNRESPONSIVE", "breathing but no answer to 2 calls"))

    def test_falls_back_when_co2_evidence_expires_counter_kept(self):
        b = brain()
        b.set_co2("BREATHING", 380, 10, 6, 1)
        b.no_response(10); b.no_response(40)
        b.set_co2("ELEVATED", 0, 0, 0, 70)                 # hold expired
        self.assertEqual(b.evaluate(71)[0], "NO SIGNS")
        self.assertEqual(b.unanswered_calls, 2)
        b.set_co2("BREATHING", 300, 10, 5, 80)             # breathing again -> straight back
        self.assertEqual(b.evaluate(81)[0], "UNRESPONSIVE")

    def test_answer_after_unresponsive_is_likely(self):
        b = brain()
        b.set_co2("BREATHING", 380, 10, 6, 1)
        b.no_response(10); b.no_response(40)
        b.call_response(60, "TAP", 3)
        self.assertEqual(b.evaluate(61), ("LIKELY", "tapped back 3 times after call"))

    def test_danger_does_not_change_status(self):
        b = brain()
        b.set_danger(True)
        self.assertEqual(b.evaluate(1)[0], "NO SIGNS")
        b.reset()
        self.assertTrue(b.danger)                          # reset clears evidence, not DANGER


if __name__ == "__main__":
    unittest.main()
