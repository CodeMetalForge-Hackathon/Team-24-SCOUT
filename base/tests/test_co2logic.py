"""Tests the REAL node CO2 logic (firmware/node/co2logic.py), using curves shaped
like the measurements in tests/RESULTS.md. Readings every 5 s."""
import unittest

from base.node_brain import load_co2logic

co2logic = load_co2logic()

DEMO = dict(baseline_secs=30, rate_window=15, sudden_ppm=120, event_hold=60,
            rh_rise_points=3, follow=0.02, slow_enabled=False)
FIELD = dict(baseline_secs=120, rate_window=60, sudden_ppm=40, event_hold=600,
             rh_rise_points=3, follow=0.001, slow_enabled=True, trend_minutes=15, trend_ppm=60)


def learned(profile=DEMO, room=810, rh=48.0):
    """A sensor that has finished learning a steady room."""
    c = co2logic.CO2Levels(start=0, **profile)
    t = 0
    while t <= profile["baseline_secs"]:
        c.feed(t, room + (t % 10) / 5, rh)     # tiny noise
        t += 5
    return c, t


def breathe(c, t, room=810, rh=48.0):
    """Measured breath: +150 in 5 s, +380 in 10 s, peak +460 at 30 s; RH 48 -> 54."""
    levels = []
    for d, drh in [(150, 2), (380, 4), (420, 5), (440, 6), (455, 6), (460, 6)]:
        c.feed(t, room + d, rh + drh)
        levels.append(c.level)
        t += 5
    return levels, t


class Learning(unittest.TestCase):
    def test_learning_then_normal(self):
        c = co2logic.CO2Levels(start=0, **DEMO)
        c.feed(5, 810, 48)
        self.assertEqual(c.level, "LEARNING")
        c, _ = learned()
        self.assertEqual(c.level, "NORMAL")
        self.assertAlmostEqual(c.baseline, 810, delta=3)

    def test_breath_during_learning_does_not_inflate_baseline(self):
        c = co2logic.CO2Levels(start=0, **DEMO)
        for t, ppm in [(0, 810), (5, 812), (10, 1100), (15, 1250), (20, 815), (25, 811), (30, 809)]:
            c.feed(t, ppm, 48)
        self.assertLess(c.baseline, 815)


class SuddenRise(unittest.TestCase):
    def test_breath_is_breathing_within_10_s(self):
        c, t = learned()
        levels, t = breathe(c, t)
        self.assertNotIn(levels[0], co2logic.EVIDENCE)   # 1 fast reading isn't enough (noise)
        self.assertEqual(levels[1], "BREATHING")     # 2nd in a row, humidity +4
        self.assertGreaterEqual(c.rise, 380)
        self.assertGreaterEqual(c.rh_jump, 3)

    def test_rise_without_humidity_is_sudden_rise(self):
        c, t = learned()
        for d in (150, 380):
            c.feed(t, 810 + d, 48.3)
            t += 5
        self.assertEqual(c.level, "SUDDEN_RISE")
        self.assertEqual(c.rise_secs, 10)

    def test_evidence_expires_after_hold_even_if_co2_stays_high(self):
        c, t = learned()
        _, t = breathe(c, t)
        last_trigger = c.last_sudden_time
        while t - last_trigger < 60:                 # CO2 stays high but flat
            c.feed(t, 1270, 54)
            self.assertIn(c.level, ("BREATHING", "SUDDEN_RISE"))
            t += 5
        c.feed(t, 1270, 54)                          # first reading after the hold
        self.assertEqual(c.level, "ELEVATED")        # high but flat = room air, not evidence

    def test_continuing_rise_extends_the_hold(self):
        c, t = learned()
        ppm = 810
        for _ in range(30):                          # keeps climbing 70 ppm per reading
            ppm += 70
            c.feed(t, ppm, 48)
            t += 5
        self.assertEqual(c.level, "SUDDEN_RISE")
        self.assertLess(t - c.last_sudden_time, 6)

    def test_slow_room_drift_is_only_elevated(self):
        c, t = learned()
        ppm, seen = 810.0, set()
        for _ in range(60):                          # +2 ppm/s for 5 min: people filling the room
            ppm += 10
            c.feed(t, ppm, 48)
            seen.add(c.level)
            t += 5
        self.assertNotIn("SUDDEN_RISE", seen)
        self.assertNotIn("BREATHING", seen)
        self.assertIn("ELEVATED", seen)

    def test_baseline_frozen_during_event(self):
        c, t = learned()
        base = c.baseline
        breathe(c, t)
        self.assertEqual(c.baseline, base)


class SlowBuildup(unittest.TestCase):
    def test_field_steady_climb(self):
        c, t = learned(FIELD)
        ppm, seen = 810.0, set()
        for _ in range(12 * 15):                     # 15 min, +0.1 ppm/s (6 ppm/min)
            ppm += 0.5
            c.feed(t, ppm, 48)
            seen.add(c.level)
            t += 5
        self.assertIn("SLOW_BUILDUP", seen)
        self.assertNotIn("SUDDEN_RISE", seen)

    def test_demo_has_no_slow_rule(self):
        c, t = learned()
        ppm, seen = 810.0, set()
        for _ in range(12 * 5):
            ppm += 2
            c.feed(t, ppm, 48)
            seen.add(c.level)
            t += 5
        self.assertNotIn("SLOW_BUILDUP", seen)


if __name__ == "__main__":
    unittest.main()
