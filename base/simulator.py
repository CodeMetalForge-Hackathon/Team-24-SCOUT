"""Fake nodes for demos and development without hardware.

Each fake node runs the REAL node logic (firmware/node/assess.py and co2logic.py,
DEMO profile), so statuses, reasons, CO2 levels and countdowns are exactly what
the board would send. Only the raw sensor values are invented.

Node 1 plays a story on a loop (~6.5 min); the first 30 s it also learns its CO2 baseline:
    6 s  heard a voice -> POSSIBLE
   25 s  ACCIDENTAL single press -> LIKELY countdown -> expires -> "single press, not confirmed"
   40 s  room CO2 drifts up (+2 ppm/s, people arriving) -> ELEVATED only, NO status change
  175 s  breathing burst on the sensor -> BREATHING -> POSSIBLE -> expires after the 60 s hold
  270 s  breathing again + knocking at 285 s -> LIKELY "breathing + knocking"
  345 s  press, 353 s press again -> CONFIRMED (latched) -> answers -> pile shift -> stuck button
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

from base.node_brain import load_assess, load_co2logic
from base.sources import Source

log = logging.getLogger("base.sim")
assess = load_assess()
co2logic = load_co2logic()

PROFILE = "DEMO"
CONFIRM_WINDOW = 20
ACTIVITY_WINDOW = 15
COMBINE_WINDOW = 120
ST_MIN_INTERVAL = 5      # same throttle as the node: reason-only ST lines every 5 s
CO2_EVERY = 5            # the SCD41 makes a reading every ~5 s
DEMO_CO2 = dict(baseline_secs=30, rate_window=15, sudden_ppm=120, event_hold=60,
                rh_rise_points=3, follow=0.02, slow_enabled=False)   # = config.py DEMO


class SimNode:
    def __init__(self, n, co2, rh, bat, rssi):
        self.n = n
        self.brain = assess.Assessor(activity_window=ACTIVITY_WINDOW, confirm_window=CONFIRM_WINDOW,
                                     combine_window=COMBINE_WINDOW)
        self.co2 = co2logic.CO2Levels(start=time.monotonic(), **DEMO_CO2)
        self.room_ppm, self.room_rh = co2, rh
        self.ppm, self.rh = co2, rh             # what the "air" is doing right now
        self.ppm_target, self.rh_target = co2, rh
        self.k = 0.05                           # how fast ppm/rh approach their targets (1/s)
        self.ramp = 0.0                         # ppm/s added to the target (slow build-up)
        self.last_lvl = None
        self.bat, self.rssi_mid = bat, rssi
        self.calls = 0
        self.last_st = (None, None)
        self.last_st_time = -100

    def drift(self, dt):
        self.ppm_target += self.ramp * dt
        f = min(1.0, self.k * dt)
        self.ppm += (self.ppm_target - self.ppm) * f
        self.rh += (self.rh_target - self.rh) * f
        self.bat = max(5, self.bat - 0.002 * dt)

    def back_to_room(self, k=0.04):
        self.ppm_target, self.rh_target, self.ramp, self.k = self.room_ppm, self.room_rh, 0.0, k

    def rssi(self):
        return int(self.rssi_mid + random.uniform(-4, 4))


class SimSource(Source):
    name = "sim"
    CYCLE = 400

    def __init__(self, second_node=True):
        super().__init__()
        self.n1 = SimNode(1, co2=810, rh=48.0, bat=87, rssi=-48)
        self.n2 = SimNode(2, co2=600, rh=45.0, bat=64, rssi=-71) if second_node else None
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
        hb = dict(t="HB", st=st, why=why, bat=round(node.bat), dg=int(node.brain.danger), prof=PROFILE)
        c = node.co2
        if c.ppm is not None:
            hb.update(co2=round(c.ppm), rh=round(c.rh, 1), lvl=c.level)
            if c.hold_left(now):
                hb["hold"] = int(c.hold_left(now))
            if c.baseline is not None:
                hb.update(base=int(c.baseline), rh_base=round(c.rh_baseline, 1))
        self.emit(node, **hb)

    def co2_reading(self, node, now):
        """Like code.py: feed a reading, tell the brain, send EV CO2 on a level change."""
        c = node.co2
        c.feed(now, round(node.ppm + random.gauss(0, 4)), round(node.rh + random.gauss(0, 0.2), 1))
        node.brain.set_co2(c.level, c.rise, c.rise_secs, c.rh_jump, now)
        if c.level != node.last_lvl:
            ev = dict(t="EV", e="CO2", lvl=c.level, prev=node.last_lvl)
            if c.level in co2logic.EVIDENCE:
                ev.update(rise=int(c.rise), secs=c.rise_secs, rh_jump=int(c.rh_jump),
                          hold=int(c.hold_left(now)))
            if c.baseline is not None:
                ev["base"] = int(c.baseline)
            self.emit(node, **ev)
            node.last_lvl = c.level

    def report_status(self, node, now):
        """Same rule as code.py: status change -> send now; reason-only change
        (countdown ticking, CO2 delta moving) -> at most every ST_MIN_INTERVAL s."""
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
            n.brain.reset()                     # CO2 baseline is kept, like the real node
            n.back_to_room(k=0.1)
            n.calls = 0

        def breathe():                          # measured: +150 in 5 s, peak +460, RH +6
            n.ppm_target, n.rh_target, n.k = n.room_ppm + 460, n.room_rh + 6, 0.15

        def room_drift():
            n.k, n.ramp = 0.2, 2.0               # +2 ppm/s: people filling the room, not a breath

        def drift_stops():
            n.ramp = 0.0                         # room stays high (ELEVATED), flat

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
            (40, room_drift),                      # (b) slow room drift -> ELEVATED only
            (130, drift_stops),
            (150, lambda: n.back_to_room(k=0.03)), # room aired out
            (175, breathe),                        # (a) breathing burst -> BREATHING ~10 s later
            (200, n.back_to_room),                 #     stops; evidence expires after the hold
            (270, breathe),                        # (c) breathing again...
            (285, lambda: self.sound(n, "TAP")),   #     ...+ knocking -> LIKELY
            (295, n.back_to_room),
            (345, lambda: self.press(n)),          # real survivor: 1st press...
            (353, lambda: self.press(n)),          # ...2nd press -> CONFIRMED
            (359, lambda: self.emit(n, t="EV", e="ANS", q="injured", a="yes")),
            (364, lambda: self.emit(n, t="EV", e="ANS", q="count", a=2)),
            (370, shift),
            (376, lambda: self.emit(n, t="EV", e="STUCK")),
            (382, settle),
            (390, lambda: self.on_line("RX: crc error, packet dropped")),  # real receivers do this
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
        next_co2 = {1: start + CO2_EVERY, 2: start + CO2_EVERY + 1}
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
                    if now >= next_co2[node.n]:
                        self.co2_reading(node, now)
                        next_co2[node.n] = now + CO2_EVERY
                    self.report_status(node, now)
                    if now >= next_hb[node.n]:
                        self.heartbeat(node, now)
                        next_hb[node.n] = now + 5
            except Exception:
                log.exception("simulator step failed")   # never kill the thread
            self._stop.wait(0.1)
