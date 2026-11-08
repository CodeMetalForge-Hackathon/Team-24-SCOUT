"""Wireless link (ESP-NOW via the LilyGO receiver): only our network, no duplicates,
receiver boot noise ignored, commands carry an id + network ID."""
import asyncio
import os
import tempfile
import unittest

from base.app import Hub
from base.db import EventDB


class FakeSource:
    name, connected, detail = "serial", True, ""

    def __init__(self):
        self.sent = []

    def send(self, cmd):
        self.sent.append(cmd)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = FakeSource()
        self.hub = Hub(self.src, EventDB(os.path.join(self.tmp.name, "t.db")))   # net_id T24

    def tearDown(self):
        self.hub.db.conn.close()
        self.tmp.cleanup()

    def stored(self):
        return [r for r in self.hub.db.all_for(None)]

    def test_other_networks_and_boot_noise_never_appear(self):
        for line in ["ets Jun  8 2016 00:22:57", "rst:0x1 (POWERON_RESET),boot:0x13", "LilyGO receiver ready",
                     '{"t":"EV","n":1,"e":"BTN","stage":"confirmed","net":"T07","id":3,"rssi":-40}',   # other team
                     '{"t":"ST","n":1,"st":"CONFIRMED","why":"x","id":4,"rssi":-40}']:              # no network ID
            self.hub.handle_line(line, 1)
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.hub.state.nodes, {})
        self.assertEqual(self.hub.db.alerts(), [])
        self.assertEqual(self.hub.foreign, 2)

    def test_urgent_copies_counted_once(self):
        line = '{"t":"EV","n":1,"e":"BTN","stage":"first","net":"T24","id":15,"rssi":-45}'
        for i in range(3):
            self.hub.handle_line(line, 10 + i * 0.1)
        self.assertEqual(len(self.stored()), 1)
        self.assertEqual(self.hub.duplicates, 2)
        self.hub.handle_line(line, 25)                        # same id 15 s later: a new message
        self.assertEqual(len(self.stored()), 2)
        self.hub.handle_line('{"t":"EV","n":2,"e":"BTN","stage":"first","net":"T24","id":15,"rssi":-60}', 25.1)
        self.assertEqual(len(self.stored()), 3)               # same id from another node is different

    def test_link_type_and_rssi(self):
        self.hub.handle_line('{"t":"HB","n":1,"st":"NO SIGNS","net":"T24","id":1,"rssi":-63}', 1)
        self.assertEqual(self.hub.link_type, "ESP-NOW")
        self.assertEqual(self.hub.state.snapshot(2)[1]["rssi"], -63)
        self.hub.handle_line('{"t":"HB","n":1,"st":"NO SIGNS","net":"T24","id":2}', 3)
        self.assertEqual(self.hub.link_type, "USB")

    def test_commands_carry_id_and_network(self):
        asyncio.run(self.hub.command({"t": "MSG", "n": 1, "txt": "Help is coming"}))
        asyncio.run(self.hub.command({"t": "CALL", "n": 1}))
        a, b = self.src.sent
        self.assertEqual((a["t"], a["net"], b["net"]), ("MSG", "T24", "T24"))
        self.assertNotEqual(a["id"], b["id"])


if __name__ == "__main__":
    unittest.main()
