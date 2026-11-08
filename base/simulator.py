"""Fake nodes for demos and development without hardware.

Each fake node runs the REAL node brain (firmware/node/assess.py), so statuses,
reasons and countdowns are exactly what the board would send.

Node 1 plays a story on a loop (~120 s):
  heard a voice -> POSSIBLE
  ACCIDENTAL single press -> LIKELY countdown -> expires -> POSSIBLE "single press, not confirmed"
  knocking -> POSSIBLE, CO2 creeps up
  press -> LIKELY countdown -> 2nd press -> CONFIRMED (latched)
  answers injured=yes, count=2 -> pile shift (DANGER) -> stuck button -> restart
Node 2 (optional) sits at NO SIGNS, to show several nodes at once.

It reacts to dashboard commands like a real node:
  CALL  -> 3 s later taps back (node 1) or 10 s later "no answer" (node 2)
  ASK   -> 3 s later answers (node 1 only)
  RESET -> forgets all evidence (node 1 also restarts its story)
Every line goes through the same parser as real serial data.
"""
import heapq
import itertools
import json
import logging
import random
import threading
import time

from base.node_brain import load_assess
from base.sources import Source

log = logging.getLogger("base.sim")
assess = load_assess()

CONFIRM_WINDOW = 20
ACTIVITY_WINDOW = 15
ST_MIN_INTERVAL = 5      # same throttle as the node: countdown ST lines every 5 s


class SimNode:
    def __init__(self, n, co2, base, bat, rssi):
        self.n = n
        self.brain = assess.Assessor(activity_window=ACTIVITY_WINDOW, confirm_window=CONFIRM_WINDOW)
        self.co2, self.co2_target, self.base = co2, co2, base
        self.bat, self.rssi_mid = bat, rssi
        self.calls = 0
        self.last_st = (None, None)
        self.last_st_time = -100

    def drift(self, dt):
        # CO2 ramps ~10 ppm/s toward its target, with a little sensor noise
        step = 10 * dt
        if abs(self.co2_target - self.co2) <= step:
            self.co2 = self.co2_target
        else:
            self.co2 += step if self.co2_target > self.co2 else -step
        self.bat = max(5, self.bat - 0.002 * dt)

    def rssi(self):
        return int(self.rssi_mid + random.uniform(-4, 4))


class SimSource(Source):
    name = "sim"
    CYCLE = 120

    def __init__(self, second_node=True):
        super().__init__()
        self.n1 = SimNode(1, co2=650, base=640, bat=87, rssi=-48)
        self.n2 = SimNode(2, co2=600, base=590, bat=64, rssi=-71) if second_node else None
        self._heap = []
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._restart = False

    # ---------- helpers ----------
    def nodes(self):
        return [nd for nd in (self.n1, self.n2) if nd is not None]

    def later(self, delay, fn):
        with self._lock:
            heapq.heappush(self._heap, (time.monotonic() + delay, next(self._seq), fn))

    def emit(self, node, **msg):
        msg = {"n": node.n, **msg, "rssi": node.rssi()}
        msg = {"t": msg.pop("t"), **msg}          # keep "t" first, like the real nodes
        self.on_line(json.dumps(msg, separators=(",", ":")))

    def heartbeat(self, node, now):
        st, why = node.brain.evaluate(now)
        self.emit(node, t="HB", st=st, why=why, co2=round(node.co2 + random.gauss(0, 4)),
                  base=node.base, bat=round(node.bat), dg=int(node.brain.danger))

    def report_status(self, node, now):
        """Same rule as code.py: status change -> send now; reason-only change
        (the countdown ticking) -> at most every ST_MIN_INTERVAL s."""
        st, why = node.brain.evaluate(now)
        if st != node.last_st[0] or (why != node.last_st[1]
                                     and now - node.last_st_time >= ST_MIN_INTERVAL):
            self.emit(node, t="ST", st=st, why=why)
            node.last_st, node.last_st_time = (st, why), now

    def press(self, node):
        stage = node.brain.button_press(time.monotonic())
        self.emit(node, t="EV", e="BTN", stage=stage)

    def sound(self, node, kind):
        node.brain.activity(time.monotonic(), kind)
        self.emit(node, t="EV", e=kind)

    # ---------- node 1's story ----------
    def script(self):
        n = self.n1

        def reset():
            n.brain.reset()
            n.co2_target, n.calls = 650, 0

        def knock():
            n.co2_target = 1000
            self.sound(n, "TAP")

        def shift():
            n.brain.set_danger(True)
            self.emit(n, t="EV", e="SHIFT")
            self.heartbeat(n, time.monotonic())

        def settle():
            n.brain.set_danger(False)
            self.heartbeat(n, time.monotonic())

        return [
            (0, reset),
            (6, lambda: self.sound(n, "VOICE")),
            (25, lambda: self.press(n)),           # accidental single press -> expires at 45
            (63, knock),
            (70, lambda: self.press(n)),           # real survivor: 1st press...
            (78, lambda: self.press(n)),           # ...2nd press -> CONFIRMED
            (84, lambda: self.emit(n, t="EV", e="ANS", q="injured", a="yes")),
            (90, lambda: self.emit(n, t="EV", e="ANS", q="count", a=2)),
            (96, shift),
            (102, lambda: self.emit(n, t="EV", e="STUCK")),
            (108, settle),
            (115, lambda: self.on_line("RX: crc error, packet dropped")),  # real receivers do this
        ]

    # ---------- dashboard commands ----------
    def send(self, cmd):
        node = {1: self.n1, 2: self.n2}.get(cmd["n"])
        if node is None:
            raise ConnectionError(f"no simulated node {cmd['n']}")
        log.info("sim node %s got %s", node.n, cmd)
        t = cmd["t"]
        if t == "RESET":
            def do_reset():
                node.brain.reset()
                node.calls = 0
                if node is self.n1:
                    self._restart = True        # replay the story from the start
            self.later(0.5, do_reset)
        elif t == "CALL":
            if node is self.n1:
                def answer():
                    node.calls = 0
                    node.brain.tap_response(time.monotonic())
                    self.emit(node, t="EV", e="TAP", taps=3)
                self.later(3, answer)
            else:
                def silent():
                    node.calls += 1
                    node.brain.no_response()
                    self.emit(node, t="EV", e="NORESP", calls=node.calls)
                self.later(10, silent)          # node listens ~10 s before giving up
        elif t == "ASK" and node is self.n1:
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
                if pos >= self.CYCLE or self._restart:      # loop the story
                    start, pos, idx, self._restart = now, 0, 0, False
                while idx < len(steps) and pos >= steps[idx][0]:
                    steps[idx][1]()
                    idx += 1

                due = []
                with self._lock:
                    while self._heap and self._heap[0][0] <= now:
                        due.append(heapq.heappop(self._heap)[2])
                for fn in due:
                    fn()

                for node in self.nodes():
                    node.drift(dt)
                    self.report_status(node, now)
                    if now >= next_hb[node.n]:
                        self.heartbeat(node, now)
                        next_hb[node.n] = now + 5
            except Exception:
                log.exception("simulator step failed")   # never kill the thread
            self._stop.wait(0.1)
