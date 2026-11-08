"""Full reset / new incident: archive first, then clear exactly the right data.
Real Hub with a temporary database and archive folder."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from base.app import Hub
from base.db import EventDB


class FakeSource:
    name, connected, detail = "fake", True, ""

    def __init__(self):
        self.sent = []

    def send(self, cmd):
        self.sent.append(cmd)


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = FakeSource()
        self.hub = Hub(self.src, EventDB(str(self.dir / "t.db")), archive_dir=self.dir / "archive")
        self.hub.set_meta({"incident": "Collapse Main St", "base": {"lat": 42.36, "lon": -71.05}})
        for n in (1, 2):
            self.hub.handle_line(f'{{"t":"EV","n":{n},"e":"BOOT","sensors":{{"sound":true}}}}', 1)
            self.hub.handle_line(f'{{"t":"ST","n":{n},"st":"UNRESPONSIVE","why":"breathing but no answer to 2 calls"}}', 2)
            self.hub.handle_line(f'{{"t":"SN","n":{n},"co2":1200,"base":810}}', 3)
            self.hub.handle_line(f'{{"t":"LOC","n":{n},"src":"MANUAL","lat":42.36,"lon":-71.05,"label":"x"}}', 4)
            self.hub.set_node_meta({"n": n, "team": "Team Alpha", "notes": "voice heard", "tag": "Sector B"})
        self.hub.ack("Ana", node=1)

    def tearDown(self):
        self.hub.db.conn.close()
        self.tmp.cleanup()

    def run_async(self, coro):
        return asyncio.run(coro)

    def node_rows(self, n):
        return [r for r in self.hub.db.all_for(n)]

    def test_full_reset_archives_then_clears_only_that_node(self):
        before = len(self.node_rows(1))
        archived = self.run_async(self.hub.full_reset(1, {"gps": True}, "Ana"))
        self.assertEqual(self.src.sent[-1], {"t": "REBOOT", "n": 1, "keep_gps": True})
        # archive: JSON + CSV with node 1's history, alerts and state
        data = json.loads((self.dir / "archive" / archived).read_text())
        self.assertTrue(archived.startswith("Collapse-Main-St_node1_"))
        self.assertGreaterEqual(len(data["events"]), before)
        self.assertEqual({e["node"] for e in data["events"]}, {1})
        self.assertEqual(data["alerts"][0]["acked_by"], "Ana")
        self.assertTrue((self.dir / "archive" / archived.replace(".json", ".csv")).exists())
        # live: node 1 left with ONE audit entry; node 2 untouched
        rows = self.node_rows(1)
        self.assertEqual(len(rows), 1)
        self.assertIn("Node 1 fully reset by Ana at", json.loads(rows[0][5])["desc"])
        self.assertIn(f"(archived: {archived})", json.loads(rows[0][5])["desc"])
        self.assertGreater(len(self.node_rows(2)), 3)
        self.assertEqual([a["n"] for a in self.hub.db.alerts()], [2])
        # in memory: chart, location, evidence gone; awaiting boot
        nd = self.hub.state.snapshot(10)[1]
        self.assertEqual((nd["co2_hist"], nd["loc"], nd["status"]), ([], None, "NO SIGNS"))
        self.assertIsNotNone(nd["pending_reboot"])
        self.assertEqual([e["n"] for e in self.hub.state.events if e["n"] == 1], [1])     # only the audit line
        self.assertEqual(self.hub.state.snapshot(10)[2]["status"], "UNRESPONSIVE")
        # the node's BOOT ends "awaiting boot"
        self.hub.handle_line('{"t":"EV","n":1,"e":"BOOT","sensors":{"sound":true}}', 12)
        self.assertIsNone(self.hub.state.snapshot(13)[1]["pending_reboot"])

    def test_keep_checkboxes(self):
        self.run_async(self.hub.full_reset(1, {"gps": False, "team": False, "notes": False, "tag": False}, "Ana"))
        self.assertEqual(self.src.sent[-1]["keep_gps"], False)
        self.assertNotIn(1, self.hub.db.get_node_meta())
        self.run_async(self.hub.full_reset(2, {"gps": True, "team": True, "notes": False, "tag": True}, "Ana"))
        self.assertEqual(self.hub.db.get_node_meta()[2], {"team": "Team Alpha", "notes": "", "tag": "Sector B"})

    def test_new_incident_clears_all_nodes(self):
        archived, not_reached = self.run_async(self.hub.new_incident("Second collapse", True, "Ben"))
        self.assertEqual(not_reached, [])
        self.assertEqual(sorted(c["n"] for c in self.src.sent if c["t"] == "REBOOT"), [1, 2])
        data = json.loads((self.dir / "archive" / archived).read_text())
        self.assertTrue(archived.startswith("Collapse-Main-St_all_"))        # named after the OLD incident
        self.assertEqual({e["node"] for e in data["events"] if e["node"]}, {1, 2})
        rows = self.hub.db.all_for(None)
        self.assertEqual(len(rows), 1)
        self.assertIn("New incident 'Second collapse' started by Ben", json.loads(rows[0][5])["desc"])
        self.assertEqual(self.hub.db.alerts(), [])
        self.assertEqual(self.hub.db.get_node_meta(), {})
        self.assertEqual(self.hub.db.get_kv()["incident"], "Second collapse")
        self.assertEqual(self.hub.db.get_kv()["base"], {"lat": 42.36, "lon": -71.05})   # command post kept
        self.assertTrue(all(nd["pending_reboot"] for nd in self.hub.state.snapshot(10).values()))

    def test_unreachable_node_is_not_cleared(self):
        def fail(cmd):
            raise ConnectionError("receiver not connected")
        self.src.send = fail
        with self.assertRaises(ConnectionError):
            self.run_async(self.hub.full_reset(1, {}, "Ana"))
        self.assertGreater(len(self.node_rows(1)), 3)
        self.assertEqual(list((self.dir).glob("archive/*")), [])

    def test_reset_survives_restart_of_the_base(self):
        self.run_async(self.hub.full_reset(1, {}, "Ana"))
        hub2 = Hub(FakeSource(), self.hub.db, archive_dir=self.dir / "archive")
        hub2.replay()
        n1 = [e for e in hub2.state.events if e["n"] == 1]
        self.assertEqual(len(n1), 1)
        self.assertIn("fully reset", n1[0]["desc"])


if __name__ == "__main__":
    unittest.main()
