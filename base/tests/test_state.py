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


if __name__ == "__main__":
    unittest.main()
