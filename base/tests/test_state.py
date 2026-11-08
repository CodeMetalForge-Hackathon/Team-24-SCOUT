import unittest

from base.protocol import parse_line
from base.state import State


def apply(state, line, ts):
    msg, err = parse_line(line)
    assert err is None, err
    return state.apply(msg, ts)


class StateTests(unittest.TestCase):
    def test_lost_after_90s_of_silence(self):
        s = State()
        apply(s, '{"t":"ST","n":1,"st":"LIKELY","why":"tapped back"}', 1000)
        self.assertEqual(s.snapshot(1080)[1]["status"], "LIKELY")
        snap = s.snapshot(1091)[1]
        self.assertEqual(snap["status"], "LOST")
        self.assertEqual(snap["last_status"], "LIKELY")

    def test_answers_danger_and_heartbeat(self):
        s = State()
        self.assertIsNone(apply(s, '{"t":"HB","n":1,"st":"POSSIBLE","co2":800,"base":640,"dg":0}', 1))
        apply(s, '{"t":"EV","n":1,"e":"ANS","q":"injured","a":"yes"}', 2)
        apply(s, '{"t":"EV","n":1,"e":"SHIFT"}', 3)
        nd = s.snapshot(4)[1]
        self.assertEqual(nd["answers"]["injured"], "yes")
        self.assertTrue(nd["danger"])
        self.assertEqual(nd["co2_hist"], [[1, 800, 640]])
        apply(s, '{"t":"HB","n":1,"dg":0}', 5)
        self.assertFalse(s.snapshot(6)[1]["danger"])
        self.assertEqual(len(s.events), 2)   # heartbeats are not timeline events


class ReasonTicks(unittest.TestCase):
    def test_number_only_changes_update_card_not_timeline(self):
        s = State()
        apply(s, '{"t":"ST","n":1,"st":"POSSIBLE","why":"breathing detected: CO2 +380 ppm, humidity +6%"}', 1)
        n_events = len(s.events)
        self.assertIsNone(apply(s, '{"t":"ST","n":1,"st":"POSSIBLE",'
                                   '"why":"breathing detected: CO2 +410 ppm, humidity +6%"}', 6))
        self.assertEqual(len(s.events), n_events)
        self.assertIn("+410", s.snapshot(7)[1]["why"])
        # different words = a real change -> timeline
        self.assertIsNotNone(apply(s, '{"t":"ST","n":1,"st":"POSSIBLE","why":"heard voice"}', 8))

    def test_co2_heartbeat_fields_and_hold(self):
        s = State()
        apply(s, '{"t":"HB","n":1,"co2":1012,"base":810,"rh":53.5,"rh_base":48,"lvl":"BREATHING",'
                 '"prof":"DEMO","hold":42}', 100)
        nd = s.snapshot(101)[1]
        self.assertEqual((nd["lvl"], nd["prof"], nd["rh"], nd["rh_base"]), ("BREATHING", "DEMO", 53.5, 48))
        self.assertEqual(nd["hold_until"], 142)

    def test_sudden_rise_marks_chart_and_quiet_room_air(self):
        s = State()
        ev = apply(s, '{"t":"EV","n":1,"e":"CO2","lvl":"SUDDEN_RISE","prev":"NORMAL","rise":200,"secs":10,'
                      '"rh_jump":1,"hold":60}', 100)
        self.assertIsNotNone(ev)
        self.assertEqual(s.snapshot(101)[1]["co2_marks"], [[90, 100]])
        # expired -> logged
        self.assertIsNotNone(apply(s, '{"t":"EV","n":1,"e":"CO2","lvl":"ELEVATED","prev":"SUDDEN_RISE"}', 160))
        self.assertIsNone(s.snapshot(161)[1]["hold_until"])
        # room air flips: NORMAL never logged, ELEVATED at most every 5 min
        self.assertIsNone(apply(s, '{"t":"EV","n":1,"e":"CO2","lvl":"NORMAL","prev":"ELEVATED"}', 170))
        self.assertIsNotNone(apply(s, '{"t":"EV","n":1,"e":"CO2","lvl":"ELEVATED","prev":"NORMAL"}', 180))
        self.assertIsNone(apply(s, '{"t":"EV","n":1,"e":"CO2","lvl":"NORMAL","prev":"ELEVATED"}', 190))
        self.assertIsNone(apply(s, '{"t":"EV","n":1,"e":"CO2","lvl":"ELEVATED","prev":"NORMAL"}', 200))
        self.assertIsNotNone(apply(s, '{"t":"EV","n":1,"e":"CO2","lvl":"ELEVATED","prev":"NORMAL"}', 490))


if __name__ == "__main__":
    unittest.main()
