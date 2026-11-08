"""ESP-NOW wireless link: the node's messages go out by radio as well as USB.

ESP-NOW = direct ESP32-to-ESP32 radio (no router, no WiFi network), up to 250
bytes per packet. We broadcast (peer ff:ff:ff:ff:ff:ff); the receiver (a LilyGO
ESP32 on the base station's USB) keeps only packets with our network ID.

Other teams broadcast ESP-NOW nearby too, so NOTHING received over the air may
crash the node: bad packets are skipped, non-text / non-JSON payloads ignored.

send() never waits: urgent messages are repeated 2 more times ~100 ms apart
from update(), which the main loop calls every pass.
"""

BROADCAST = b"\xff\xff\xff\xff\xff\xff"
MAX_BYTES = 240          # ESP-NOW limit is 250; keep a margin
REPEAT_GAP = 0.1         # seconds between the copies of an urgent message
REPEATS = 2              # extra copies (3 sends in total)


class EspNowLink:
    def __init__(self, buffer_size=1024):
        self.ok = False
        self.queue = []          # [due_time, line, copies_left] for urgent repeats
        self.sent = 0
        self.failed = 0
        self.bad_packets = 0
        try:
            import espnow  # type: ignore
            self.e = espnow.ESPNow(buffer_size=buffer_size)
            self.peer = espnow.Peer(mac=BROADCAST)
            self.e.peers.append(self.peer)
            self.ok = True
            print("ESP-NOW link ready (broadcast)")
        except Exception as e:                  # no radio / wrong firmware: USB only
            print("ESP-NOW not available (", e, ") - continuing USB-only")

    def _tx(self, line):
        try:
            self.e.send(line, self.peer)
            self.sent += 1
        except Exception as e:                  # radio busy: lose this copy, never crash
            self.failed += 1
            if self.failed == 1 or self.failed % 50 == 0:
                print("ESP-NOW send failed (", e, ")")

    def send(self, line, now, urgent=False):
        """Broadcast one JSON line. Urgent ones go out 3 times (same id)."""
        if not self.ok:
            return
        if len(line) > MAX_BYTES:
            print("WARNING: not sent by radio, message is", len(line), "bytes")
            return
        self._tx(line)
        if urgent:
            self.queue.append([now + REPEAT_GAP, line, REPEATS])

    def update(self, now):
        """Send the urgent repeats that are due. Call every loop pass."""
        if not self.queue:
            return
        keep = []
        for item in self.queue:
            if now >= item[0]:
                self._tx(item[1])
                item[2] -= 1
                item[0] = now + REPEAT_GAP
            if item[2] > 0:
                keep.append(item)
        self.queue = keep

    def poll(self, max_packets=8):
        """Packets received since the last call: a list of (text, rssi).
        Never blocks, never raises."""
        out = []
        if not self.ok:
            return out
        for _ in range(max_packets):
            try:
                packet = self.e.read()
            except ValueError as e:              # malformed packet (another team): skip it
                self.bad_packets += 1
                print("skipped bad packet:", e)
                continue
            except Exception:
                break
            if not packet:
                break
            try:
                text = bytes(packet.msg).decode("utf-8")
            except Exception:
                continue                         # not text: not ours
            if text.startswith("{") and len(text) <= MAX_BYTES + 10:
                out.append((text, packet.rssi))
        return out
