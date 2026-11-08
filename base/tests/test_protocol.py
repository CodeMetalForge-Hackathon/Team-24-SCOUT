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

    def test_sound_event(self):
        msg, err = parse_line('{"t":"EV","n":1,"e":"SOUND","k":"VOICE"}')
        self.assertIsNone(err)
        self.assertEqual(describe(msg), "heard a voice")
        self.assertIsNone(parse_line('{"t":"EV","n":1,"e":"SOUND","k":"SING"}')[0])

    def test_button_stages_stuck_and_sounds(self):
        cases = [('{"t":"EV","n":1,"e":"BTN","stage":"first"}', "Button pressed once — waiting for confirmation"),
                 ('{"t":"EV","n":1,"e":"BTN","stage":"confirmed"}', "SURVIVOR CONFIRMED: pressed twice"),
                 ('{"t":"EV","n":1,"e":"STUCK"}', "Button held down — possible debris"),
                 ('{"t":"EV","n":1,"e":"TAP"}', "heard knocking"),
                 ('{"t":"EV","n":1,"e":"VOICE"}', "heard a voice"),
                 ('{"t":"EV","n":1,"e":"TAP","taps":3}', "tapped back 3 times after call")]
        for line, text in cases:
            msg, err = parse_line(line)
            self.assertIsNone(err, line)
            self.assertEqual(describe(msg), text)

    def test_co2_events_and_heartbeat(self):
        cases = [('{"t":"EV","n":1,"e":"CO2","lvl":"SUDDEN_RISE","rise":240,"secs":10,"prev":"NORMAL"}',
                  "Sudden CO2 rise +240 ppm in 10 s"),
                 ('{"t":"EV","n":1,"e":"CO2","lvl":"BREATHING","rise":380,"secs":10,"rh_jump":6}',
                  "Breathing detected: CO2 +380 ppm, humidity +6%"),
                 ('{"t":"EV","n":1,"e":"CO2","lvl":"SLOW_BUILDUP","rise":70,"secs":720}',
                  "Slow CO2 build-up +70 ppm over 12 min"),
                 ('{"t":"EV","n":1,"e":"CO2","lvl":"ELEVATED","prev":"BREATHING"}', "CO2 evidence expired"),
                 ('{"t":"EV","n":1,"e":"CO2","lvl":"NORMAL","prev":"SUDDEN_RISE"}', "CO2 evidence expired"),
                 ('{"t":"EV","n":1,"e":"CO2","lvl":"ELEVATED","prev":"NORMAL"}',
                  "CO2 high but steady (room air?)"),
                 ('{"t":"EV","n":1,"e":"CO2","lvl":"NORMAL","prev":null,"base":810}',
                  "CO2 baseline learned: 810 ppm")]
        for line, text in cases:
            msg, err = parse_line(line)
            self.assertIsNone(err, line)
            self.assertEqual(describe(msg), text)
        self.assertIsNone(parse_line('{"t":"EV","n":1,"e":"CO2","lvl":"RISING"}')[0])   # old level name
        msg, _ = parse_line('{"t":"HB","n":1,"co2":1012,"base":810,"rh":53.5,"rh_base":48,'
                            '"lvl":"BREATHING","prof":"DEMO","hold":42}')
        self.assertEqual((msg["lvl"], msg["prof"], msg["rh"], msg["hold"]), ("BREATHING", "DEMO", 53.5, 42))
        msg, _ = parse_line('{"t":"HB","n":1,"lvl":"WEIRD","prof":"PARTY"}')
        self.assertNotIn("lvl", msg)
        self.assertNotIn("prof", msg)

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
        self.assertEqual(validate_command({"t": "RESET", "n": 1}), {"t": "RESET", "n": 1})

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


class CallMessages(unittest.TestCase):
    def test_call_cycle_events(self):
        cases = [('{"t":"EV","n":1,"e":"CALL"}', "Node called: 'tap 3 times'"),
                 ('{"t":"EV","n":1,"e":"RESP","kind":"TAP","taps":3}', "Survivor tapped back 3 times after call"),
                 ('{"t":"EV","n":1,"e":"RESP","kind":"VOICE"}', "Survivor answered by voice"),
                 ('{"t":"EV","n":1,"e":"NORESP","calls":2}', "No answer to call (2 in a row)"),
                 ('{"t":"EV","n":1,"e":"DANGER","on":true}', "Danger alarm ON"),
                 ('{"t":"EV","n":1,"e":"DANGER","on":false}', "Danger alarm off"),
                 ('{"t":"EV","n":1,"e":"BEEP","p":"CONFIRM"}', "Node played the confirm chime"),
                 ('{"t":"ST","n":1,"st":"UNRESPONSIVE","why":"breathing but no answer to 2 calls"}',
                  "UNRESPONSIVE: Breathing but no answer to 2 calls: possibly unconscious")]
        for line, text in cases:
            msg, err = parse_line(line)
            self.assertIsNone(err, line)
            self.assertEqual(describe(msg), text)
        self.assertIsNone(parse_line('{"t":"EV","n":1,"e":"RESP"}')[0])           # needs kind
        self.assertIsNone(parse_line('{"t":"EV","n":1,"e":"DANGER","on":"yes"}')[0])

    def test_heartbeat_call_fields(self):
        msg, err = parse_line('{"t":"HB","n":1,"call_state":"LISTENING","listen_left":7,"next_call_in":0,'
                              '"calls_unanswered":2,"mic":"muted","danger":true}')
        self.assertIsNone(err)
        self.assertEqual((msg["call_state"], msg["listen_left"], msg["calls_unanswered"], msg["mic"],
                          msg["danger"]), ("LISTENING", 7, 2, "muted", True))

    def test_danger_command(self):
        self.assertEqual(validate_command({"t": "DANGER", "n": 1, "on": True}),
                         {"t": "DANGER", "n": 1, "on": True})
        with self.assertRaises(ValueError):
            validate_command({"t": "DANGER", "n": 1})
        self.assertIn("ON (test)", describe({"t": "DANGER", "n": 1, "on": True}, outgoing=True))


class BootMessage(unittest.TestCase):
    def test_boot_self_check(self):
        msg, err = parse_line('{"t":"EV","n":1,"e":"BOOT","sensors":{"sound":true,"co2":true,"gps":false,'
                              '"button":true,"speaker":true,"laser":true}}')
        self.assertIsNone(err)
        self.assertNotIn("laser", msg["sensors"])          # unknown parts ignored
        self.assertEqual(describe(msg), "Node started (missing: gps)")
