"""Fake nodes for demos and development without hardware.

Node 1 plays a rescue story on a loop (~110 s):
  NO SIGNS -> POSSIBLE (CO2 rising 650 -> 1100) -> 2 unanswered calls -> UNRESPONSIVE
  -> taps back (4) -> LIKELY -> button -> CONFIRMED -> answers (injured=yes, count=2)
  -> pile shift (DANGER for ~12 s) -> restart.
Node 2 (optional) sits at NO SIGNS, to show several nodes at once.

It also reacts to dashboard commands like a real node would:
  CALL -> 3 s later taps back (node 1) or 10 s later "no answer" (node 2)
  ASK  -> 3 s later answers (node 1 only)
Every line goes through the same parser as real serial data.
"""
import heapq
import itertools
import json
import logging
import random
import threading
import time

from base.sources import Source

log = logging.getLogger("base.sim")


class SimNode:
    def __init__(self, n, co2, base, bat, rssi):
        self.n = n
        self.st, self.why = "NO SIGNS", "no evidence yet"
        self.co2, self.co2_target, self.base = co2, co2, base
        self.bat, self.rssi_mid = bat, rssi
        self.dg = 0
        self.calls = 0

    def drift(self, dt):
        # CO2 ramps ~15 ppm/s toward its target, with a little sensor noise
        step = 15 * dt
        if abs(self.co2_target - self.co2) <= step:
            self.co2 = self.co2_target
        else:
            self.co2 += step if self.co2_target > self.co2 else -step
        self.bat = max(5, self.bat - 0.002 * dt)

    def rssi(self):
        return int(self.rssi_mid + random.uniform(-4, 4))


class SimSource(Source):
    name = "sim"
    CYCLE = 110

    def __init__(self, second_node=True):
        super().__init__()
        self.n1 = SimNode(1, co2=650, base=640, bat=87, rssi=-48)
        self.n2 = SimNode(2, co2=600, base=590, bat=64, rssi=-71) if second_node else None
        self._heap = []
        self._seq = itertools.count()
        self._lock = threading.Lock()

    # ---------- helpers ----------
    def later(self, delay, fn):
        with self._lock:
            heapq.heappush(self._heap, (time.monotonic() + delay, next(self._seq), fn))

    def emit(self, node, **msg):
        msg = {"n": node.n, **msg, "rssi": node.rssi()}
        msg = {"t": msg.pop("t"), **msg}          # keep "t" first, like the real nodes
        self.on_line(json.dumps(msg, separators=(",", ":")))

    def status(self, node, st, why):
        node.st, node.why = st, why
        self.emit(node, t="ST", st=st, why=why)

    def heartbeat(self, node):
        self.emit(node, t="HB", st=node.st, why=node.why, co2=round(node.co2 + random.gauss(0, 4)),
                  base=node.base, bat=round(node.bat), dg=node.dg)

    # ---------- node 1's story ----------
    def script(self):
        n = self.n1

        def reset():
            n.co2_target, n.dg, n.calls = 650, 0, 0
            self.status(n, "NO SIGNS", "demo restarted")

        def co2_rise():
            n.co2_target = 1100
            self.status(n, "POSSIBLE", "CO2 rising")

        def noresp():
            n.calls += 1
            self.emit(n, t="EV", e="NORESP", calls=n.calls)
            if n.calls >= 2:
                self.status(n, "UNRESPONSIVE", f"CO2 up, {n.calls} calls unanswered")

        def taps():
            n.calls = 0
            self.emit(n, t="EV", e="TAP", taps=4)
            self.status(n, "LIKELY", "tapped back after call")

        def button():
            self.emit(n, t="EV", e="BTN")
            self.status(n, "CONFIRMED", "pressed PRESS ME")

        def shift():
            n.dg = 1
            self.emit(n, t="EV", e="SHIFT")
            self.heartbeat(n)

        def settle():
            n.dg = 0
            self.heartbeat(n)

        return [
            (0, reset),
            (10, co2_rise),
            (20, noresp),
            (30, noresp),
            (42, taps),
            (55, button),
            (62, lambda: self.emit(n, t="EV", e="ANS", q="injured", a="yes")),
            (68, lambda: self.emit(n, t="EV", e="ANS", q="count", a=2)),
            (78, shift),
            (90, settle),
            (100, lambda: self.on_line("RX: crc error, packet dropped")),  # real receivers do this
        ]

    # ---------- dashboard commands ----------
    def send(self, cmd):
        node = {1: self.n1, 2: self.n2}.get(cmd["n"])
        if node is None:
            raise ConnectionError(f"no simulated node {cmd['n']}")
        log.info("sim node %s got %s", node.n, cmd)
        if cmd["t"] == "CALL":
            if node is self.n1:
                def answer():
                    self.emit(node, t="EV", e="TAP", taps=3)
                    if node.st != "CONFIRMED":
                        node.calls = 0
                        self.status(node, "LIKELY", "tapped back after call")
                self.later(3, answer)
            else:
                def silent():
                    node.calls += 1
                    self.emit(node, t="EV", e="NORESP", calls=node.calls)
                self.later(10, silent)    # node listens ~10 s before giving up
        elif cmd["t"] == "ASK" and node is self.n1:
            a = "yes" if cmd["q"] == "injured" else 2
            self.later(3, lambda: self.emit(node, t="EV", e="ANS", q=cmd["q"], a=a))

    # ---------- main loop ----------
    def run(self):
        self._set_status(True, "simulator (fake nodes)")
        steps = self.script()
        start = last = time.monotonic()
        idx = 0
        next_hb = {1: start + 1, 2: start + 2}
        while not self._stop.is_set():
            now = time.monotonic()
            dt, last = now - last, now
            try:
                pos = now - start
                if pos >= self.CYCLE:                 # loop the story
                    start, pos, idx = now, 0, 0
                while idx < len(steps) and pos >= steps[idx][0]:
                    steps[idx][1]()
                    idx += 1

                due = []
                with self._lock:
                    while self._heap and self._heap[0][0] <= now:
                        due.append(heapq.heappop(self._heap)[2])
                for fn in due:
                    fn()

                for node, every in ((self.n1, 3), (self.n2, 5)):
                    if node is None:
                        continue
                    node.drift(dt)
                    if now >= next_hb[node.n]:
                        self.heartbeat(node)
                        next_hb[node.n] = now + every
            except Exception:
                log.exception("simulator step failed")   # never kill the thread
            self._stop.wait(0.1)
