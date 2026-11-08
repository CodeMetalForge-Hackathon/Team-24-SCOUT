"""Server-side team features: alerts on the right transitions, acknowledgements,
incident settings, team assignment. Uses the real Hub with a temporary database."""
import os
import tempfile
import unittest

from base.app import Hub
from base.db import EventDB


class FakeSource:
    name, connected, detail = "fake", True, ""

    def send(self, cmd):
        pass


class HubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub(FakeSource(), EventDB(os.path.join(self.tmp.name, "t.db")), net_id="")

    def tearDown(self):
        self.hub.db.conn.close()
        self.tmp.cleanup()

    def line(self, text, ts):
        self.hub.handle_line(text, ts)

    def open_alerts(self):
        return [a for a in self.hub.db.alerts() if a["acked_at"] is None]

    def test_alerts_only_on_urgent_transitions_and_not_repeated(self):
        self.line('{"t":"ST","n":1,"st":"POSSIBLE","why":"heard knocking"}', 1)
        self.line('{"t":"ST","n":1,"st":"LIKELY","why":"breathing + knocking"}', 2)
        self.assertEqual(self.open_alerts(), [])
        self.line('{"t":"ST","n":1,"st":"UNRESPONSIVE","why":"breathing but no answer to 2 calls"}', 3)
        self.line('{"t":"ST","n":1,"st":"UNRESPONSIVE","why":"breathing but no answer to 3 calls"}', 4)
        self.assertEqual([a["kind"] for a in self.open_alerts()], ["UNRESPONSIVE"])     # once
        self.line('{"t":"ST","n":1,"st":"CONFIRMED","why":"pressed button twice"}', 5)
        self.line('{"t":"EV","n":2,"e":"DANGER","on":true}', 6)
        self.assertEqual(sorted(a["kind"] for a in self.open_alerts()), ["CONFIRMED", "DANGER", "UNRESPONSIVE"])

    def test_acknowledge_records_who_and_when(self):
        self.line('{"t":"ST","n":1,"st":"UNRESPONSIVE","why":"x"}', 1)
        self.line('{"t":"ST","n":1,"st":"CONFIRMED","why":"y"}', 2)
        self.assertEqual(self.hub.ack("Ana", node=1), 2)
        acked = self.hub.db.alerts()
        self.assertTrue(all(a["acked_by"] == "Ana" and a["acked_at"] for a in acked))
        self.assertIn("Alert acknowledged by Ana", self.hub.state.events[0]["desc"])
        # after an ack, a NEW transition raises a new alert
        self.line('{"t":"ST","n":1,"st":"LIKELY","why":"z"}', 3)
        self.line('{"t":"ST","n":1,"st":"UNRESPONSIVE","why":"again"}', 4)
        self.assertEqual(len(self.open_alerts()), 1)

    def test_incident_and_base_location(self):
        self.hub.set_meta({"incident": "Collapse - Main St", "base": {"lat": 42.36, "lon": -71.05}, "by": "Ana"})
        self.assertEqual(self.hub.db.get_kv(), {"incident": "Collapse - Main St", "base": {"lat": 42.36, "lon": -71.05}})
        with self.assertRaises(ValueError):
            self.hub.set_meta({"base": {"lat": 95, "lon": 0}})

    def test_team_tag_notes_shared(self):
        self.hub.set_node_meta({"n": 2, "team": "Team Alpha", "tag": "Sector B", "by": "Ana"})
        self.hub.set_node_meta({"n": 2, "notes": "voice heard near the stairwell", "by": "Ben"})
        self.assertEqual(self.hub.db.get_node_meta()[2],
                         {"team": "Team Alpha", "notes": "voice heard near the stairwell", "tag": "Sector B"})
        descs = [e["desc"] for e in self.hub.state.events]
        self.assertIn("Assigned to Team Alpha by Ana", descs)
        self.assertIn("Notes updated by Ben", descs)

    def test_notes_survive_restart(self):
        self.hub.set_node_meta({"n": 2, "team": "Team Alpha", "by": "Ana"})
        hub2 = Hub(FakeSource(), self.hub.db, net_id="")
        hub2.replay()
        self.assertIn("Assigned to Team Alpha by Ana", [e["desc"] for e in hub2.state.events])


if __name__ == "__main__":
    unittest.main()


class PauseAll(unittest.TestCase):
    def test_pause_all_sends_to_every_node(self):
        import asyncio
        tmp = tempfile.TemporaryDirectory()
        sent = []
        src = FakeSource()
        src.send = sent.append
        hub = Hub(src, EventDB(os.path.join(tmp.name, "t.db")), net_id="")
        for n in (1, 2):
            hub.handle_line(f'{{"t":"HB","n":{n},"st":"NO SIGNS"}}', 1)
        asyncio.run(hub.pause_all(True, "Ana"))
        self.assertEqual([{k: v for k, v in c.items() if k not in ("id", "net")} for c in sent],
                         [{"t": "PAUSE", "n": 1, "on": True}, {"t": "PAUSE", "n": 2, "on": True}])
        self.assertTrue(all(c["net"] == "T24" and isinstance(c["id"], int) for c in sent))
        self.assertTrue(hub.db.get_kv()["paused"])
        self.assertIn("All nodes paused (demo) by Ana", hub.state.events[0]["desc"])
        hub.db.conn.close()
        tmp.cleanup()
