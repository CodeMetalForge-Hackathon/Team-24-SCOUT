"""Tests the REAL node call cycle (caller.py) and beep player (beeper.py) on the laptop.
On a laptop there's no speaker, so BeepPlayer runs silently but its timing logic is real."""
import unittest

from base.node_brain import load_beeper, load_caller

caller_mod = load_caller()
beeper_mod = load_beeper()

BEEPS = {
    "CALL": [("tone", 3000, 0.35), ("rest", 0.35), ("tone", 3000, 0.35), ("rest", 0.35),
             ("tone", 3000, 0.35)],
    "PRESS_ME": [("tone", 3000, 0.9)],
    "CONFIRM": [("tone", 2300, 0.2), ("tone", 2800, 0.2), ("tone", 3300, 0.45)],
    "DANGER": [("slide", 1500, 3800, 0.6), ("slide", 3800, 1500, 0.6)] * 3,
}
PRIO = {"DANGER": 6, "CONFIRM": 5, "AGAIN": 4, "PRESS_ME": 3, "MESSAGE": 2, "CALL": 1}


def run(b, start, end, step=0.01):
    t = start
    while t < end:
        b.update(t)
        t += step


class Beeper(unittest.TestCase):
    def test_pattern_plays_then_mic_tail(self):
        b = beeper_mod.BeepPlayer(None, 3000, BEEPS, PRIO, mic_tail=0.6)
        self.assertTrue(b.play("CALL", 0))
        self.assertAlmostEqual(b.quiet_until, 1.75 + 0.6)
        run(b, 0, 1.0)
        self.assertTrue(b.playing)
        run(b, 1.0, 1.9)
        self.assertFalse(b.playing)                    # 5 steps, 1.75 s
        self.assertGreaterEqual(b.quiet_until, 2.3)    # mic stays off for the echo

    def test_priorities(self):
        b = beeper_mod.BeepPlayer(None, 3000, BEEPS, PRIO)
        b.play("DANGER", 0)
        self.assertFalse(b.play("CALL", 0.1))          # lower never interrupts higher
        self.assertEqual(b.name, "DANGER")
        b2 = beeper_mod.BeepPlayer(None, 3000, BEEPS, PRIO)
        b2.play("CALL", 0)
        self.assertTrue(b2.play("CONFIRM", 0.1))       # higher replaces lower
        self.assertEqual(b2.name, "CONFIRM")

    def test_unknown_pattern_and_stuck_loop(self):
        b = beeper_mod.BeepPlayer(None, 3000, BEEPS, PRIO)
        self.assertFalse(b.play("NOPE", 0))
        b.play("PRESS_ME", 0)
        b.update(5.0)                                  # loop was blocked for 5 s
        self.assertFalse(b.playing)


class CallCycle(unittest.TestCase):
    def new(self):
        return caller_mod.Caller(interval=20, listen_secs=10, min_taps=2, first_call_at=20)

    def test_full_cycle_no_answer(self):
        c = self.new()
        self.assertIsNone(c.update(10, True, True))     # not time yet
        self.assertEqual(c.update(20, True, False), "CALL")
        self.assertIsNone(c.update(21, True, False))    # beeps still playing
        self.assertEqual(c.update(22.4, True, True), "LISTEN")
        self.assertEqual(c.state, "LISTENING")
        self.assertIsNone(c.update(30, True, True))
        self.assertEqual(c.update(32.5, True, True), "NORESP")
        self.assertEqual(c.state, "IDLE")
        self.assertAlmostEqual(c.next_call_in(32.5), 20)

    def test_two_taps_or_a_voice_is_an_answer(self):
        c = self.new()
        c.update(20, True, True); c.update(20.1, True, True)
        self.assertIsNone(c.heard("TAP", 22))
        self.assertEqual(c.heard("TAP", 23), "RESP")
        self.assertEqual(c.state, "IDLE")
        c2 = self.new()
        c2.update(20, True, True); c2.update(20.1, True, True)
        self.assertEqual(c2.heard("VOICE", 22), "RESP")

    def test_sounds_outside_listening_are_not_answers(self):
        c = self.new()
        self.assertIsNone(c.heard("TAP", 5))
        self.assertIsNone(c.heard("VOICE", 5))

    def test_not_allowed_waits_and_request_calls_early(self):
        c = self.new()
        self.assertIsNone(c.update(25, False, True))    # e.g. CONFIRMED / countdown / siren
        self.assertEqual(c.update(26, True, True), "CALL")
        c.cancel(27)                                    # button pressed during the call
        self.assertEqual(c.state, "IDLE")
        c.request()                                     # rescuer: "Call now"
        self.assertEqual(c.update(28, True, True), "CALL")


if __name__ == "__main__":
    unittest.main()
