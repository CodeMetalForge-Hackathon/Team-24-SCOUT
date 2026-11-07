"""Run:  python -m unittest discover base/tests      (from the repo root)"""
import unittest

from base.protocol import parse_line, validate_command, describe


class ParseValid(unittest.TestCase):
    def test_heartbeat(self):
        msg, err = parse_line('{"t":"HB","n":1,"st":"POSSIBLE","why":"CO2 rising",'
                              '"co2":812,"base":640,"bat":87,"dg":0,"rssi":-48}\n')
        self.assertIsNone(err)
        self.assertEqual(msg["t"], "HB")
        self.assertEqual(msg["co2"], 812)
        self.assertEqual(msg["rssi"], -48)
        self.assertEqual(msg["st"], "POSSIBLE")

    def test_status(self):
        msg, err = parse_line('{"t":"ST","n":1,"st":"LIKELY","why":"tapped back after call","rssi":-47}')
        self.assertIsNone(err)
        self.assertEqual(msg["st"], "LIKELY")

    def test_events(self):
        for line, e in [('{"t":"EV","n":1,"e":"BTN"}', "BTN"),
                        ('{"t":"EV","n":1,"e":"TAP","taps":4}', "TAP"),
                        ('{"t":"EV","n":1,"e":"NORESP","calls":2}', "NORESP"),
                        ('{"t":"EV","n":1,"e":"SHIFT"}', "SHIFT"),
                        ('{"t":"EV","n":1,"e":"ANS","q":"injured","a":"yes"}', "ANS")]:
            msg, err = parse_line(line)
            self.assertIsNone(err, line)
            self.assertEqual(msg["e"], e)

    def test_bytes_and_prefix(self):
        msg, err = parse_line(b'RX: {"t":"EV","n":2,"e":"BTN"}\r\n')
        self.assertIsNone(err)
        self.assertEqual(msg["n"], 2)

    def test_count_answer_number(self):
        msg, err = parse_line('{"t":"EV","n":1,"e":"ANS","q":"count","a":2}')
        self.assertIsNone(err)
        self.assertEqual(msg["a"], "2")


class ParseJunk(unittest.TestCase):
    def test_junk_lines_never_raise(self):
        for line in ["", "   ", "\n", "Adafruit CircuitPython 10.3.1", "{", "}{", "{bad json}",
                     "[1,2,3]", '"just a string"', "null", b"\xff\xfe\x00garbage", None, 42,
                     '{"t":"HB","n":1', "{}" * 1000]:
            msg, err = parse_line(line)
            self.assertIsNone(msg, repr(line))
            self.assertIsInstance(err, str)


class ParseMissingFields(unittest.TestCase):
    def test_missing_type(self):
        self.assertIsNone(parse_line('{"n":1,"st":"LIKELY"}')[0])

    def test_unknown_type(self):
        self.assertIsNone(parse_line('{"t":"XX","n":1}')[0])

    def test_missing_node(self):
        self.assertIsNone(parse_line('{"t":"HB","st":"LIKELY"}')[0])
        self.assertIsNone(parse_line('{"t":"HB","n":"abc"}')[0])

    def test_status_message_needs_status(self):
        self.assertIsNone(parse_line('{"t":"ST","n":1}')[0])
        self.assertIsNone(parse_line('{"t":"ST","n":1,"st":"HAPPY"}')[0])

    def test_event_needs_known_event(self):
        self.assertIsNone(parse_line('{"t":"EV","n":1}')[0])
        self.assertIsNone(parse_line('{"t":"EV","n":1,"e":"DANCE"}')[0])

    def test_answer_needs_question_and_answer(self):
        self.assertIsNone(parse_line('{"t":"EV","n":1,"e":"ANS","q":"injured"}')[0])
        self.assertIsNone(parse_line('{"t":"EV","n":1,"e":"ANS","a":"yes"}')[0])

    def test_heartbeat_with_only_node_is_ok(self):
        msg, err = parse_line('{"t":"HB","n":3}')
        self.assertIsNone(err)
        self.assertNotIn("co2", msg)

    def test_bad_optional_number_is_dropped(self):
        msg, err = parse_line('{"t":"HB","n":1,"co2":"lots","bat":true,"rssi":-50}')
        self.assertIsNone(err)
        self.assertNotIn("co2", msg)
        self.assertNotIn("bat", msg)
        self.assertEqual(msg["rssi"], -50)


class Commands(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_command({"t": "CALL", "n": 1}), {"t": "CALL", "n": 1})
        self.assertEqual(validate_command({"t": "ASK", "n": 1, "q": "count"})["q"], "count")
        self.assertEqual(validate_command({"t": "MSG", "n": 1, "txt": " Hi "})["txt"], "Hi")

    def test_invalid(self):
        for cmd in [{"t": "MSG", "n": 1, "txt": ""}, {"t": "MSG", "n": 1, "txt": "x" * 61},
                    {"t": "ASK", "n": 1, "q": "name"}, {"t": "BOOM", "n": 1}, {"t": "CALL"}, "CALL"]:
            with self.assertRaises(ValueError):
                validate_command(cmd)

    def test_describe(self):
        self.assertEqual(describe({"t": "EV", "n": 1, "e": "TAP", "taps": 4}),
                         "tapped back 4 times after call")
        self.assertIn("Help is coming", describe({"t": "MSG", "n": 1, "txt": "Help is coming"}, outgoing=True))


if __name__ == "__main__":
    unittest.main()
