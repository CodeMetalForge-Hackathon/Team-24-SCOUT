"""Fake nodes for demos and development without hardware.

Each fake node runs the REAL node logic: firmware/node/assess.py (decision table),
co2logic.py (CO2 levels) and caller.py (call-and-listen), DEMO profile. So
statuses, reasons, calls and countdowns are exactly what the board would send;
only the raw sensor values and the "survivor" are invented.

Node 1's story (~6 min loop) walks through every rule:
    ~20 s  calls start (every 20 s after the previous one ends); nobody answers yet
     36 s  breathing on the CO2 sensor          -> POSSIBLE "breathing detected"
     50 s  unprompted knock while breathing     -> LIKELY  "breathing + knocking"
    175 s  breathing again; earlier calls missed -> UNRESPONSIVE "breathing but no answer"
   ~218 s  knocks back during the next call     -> LIKELY  "tapped back 2 times after call"
    290 s  unprompted knock                      -> POSSIBLE "heard knocking"
    305 s  1st press                             -> LIKELY countdown; 312 s 2nd press -> CONFIRMED
    330 s  danger alarm ON (siren every 10 s), 345 s off; 350 s stuck button
Node 2 (optional) stays NO SIGNS and never answers its calls.

It reacts to dashboard commands like a real node:
  CALL -> calls as soon as allowed    MSG -> beeps    DANGER -> siren on/off
  RESET -> forgets all evidence (node 1 also restarts its story)    ASK -> answers (node 1)
Every line goes through the same parser as real serial data.
"""
import heapq
import itertools
import json
import logging
import random
import threading
import time

from base.node_brain import load_assess, load_caller, load_co2logic
from base.sources import Source

log = logging.getLogger("base.sim")
assess = load_assess()
co2logic = load_co2logic()
caller_mod = load_caller()

# = firmware/node/config.py, DEMO profile
PROFILE = "DEMO"
CONFIRM_WINDOW, ACTIVITY_WINDOW, RESPONSE_WINDOW, COMBINE_WINDOW = 20, 15, 60, 120
ARMING_SECS, CALL_INTERVAL, LISTEN_SECS, RESPONSE_MIN_TAPS = 10, 20, 10, 2
CALL_AUDIO = 1.75 + 0.6          # CALL pattern + MIC_TAIL: mic muted, then listening starts
SIREN_AUDIO = 3.6 + 0.6          # DANGER pattern + MIC_TAIL
DANGER_REPEAT = 10
ST_MIN_INTERVAL = 5              # reason-only ST lines at most every 5 s
CO2_EVERY = 5                    # the SCD41 makes a reading every ~5 s
DEMO_CO2 = dict(baseline_secs=30, rate_window=15, sudden_ppm=120, event_hold=60,
                rh_rise_points=3, follow=0.02, slow_enabled=False)


class SimNode:
    def __init__(self, n, co2, rh, bat, rssi):
        self.n = n
        self.brain = assess.Assessor(activity_window=ACTIVITY_WINDOW, response_window=RESPONSE_WINDOW,
                                     confirm_window=CONFIRM_WINDOW, combine_window=COMBINE_WINDOW)
        self.co2 = co2logic.CO2Levels(start=time.monotonic(), **DEMO_CO2)
        self.room_ppm, self.room_rh = co2, rh
        self.ppm, self.rh = co2, rh             # what the "air" is doing right now
        self.ppm_target, self.rh_target = co2, rh
        self.k = 0.05                           # how fast ppm/rh approach their targets (1/s)
        self.last_lvl = None
        self.bat, self.rssi_mid = bat, rssi
        self.last_st = (None, None)
        self.last_st_time = -100
        self.power_on(time.monotonic())

    def power_on(self, now):
        self.arm_until = now + ARMING_SECS
        self.caller = caller_mod.Caller(CALL_INTERVAL, LISTEN_SECS, RESPONSE_MIN_TAPS,
                                        first_call_at=now + CALL_INTERVAL)
        self.call_audio_end = 0                 # mic muted until then (call beeps)
        self.danger_cmd = False
        self.brain.set_danger(False)
        self.next_siren = 0
        self.siren_until = 0
        self.answer_next = None                 # survivor will answer the next call: "TAP"/"VOICE"

    def muted(self, now):
        return now < self.call_audio_end or now < self.siren_until

    def drift(self, dt):
        f = min(1.0, self.k * dt)
        self.ppm += (self.ppm_target - self.ppm) * f
        self.rh += (self.rh_target - self.rh) * f
        self.bat = max(5, self.bat - 0.002 * dt)

    def back_to_room(self, k=0.04):
        self.ppm_target, self.rh_target, self.k = self.room_ppm, self.room_rh, k

    def rssi(self):
        return int(self.rssi_mid + random.uniform(-4, 4))


class SimSource(Source):
    name = "sim"
    CYCLE = 365

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
        danger = node.brain.danger
        hb = dict(t="HB", st=st, why=why, bat=round(node.bat), dg=int(danger), danger=danger,
                  prof=PROFILE, call_state=node.caller.state,
                  listen_left=int(node.caller.listen_left(now)),
                  next_call_in=int(node.caller.next_call_in(now)),
                  calls_unanswered=node.brain.unanswered_calls,
                  mic="muted" if node.muted(now) else "on")
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
        (countdown ticking, CO2 numbers) -> at most every ST_MIN_INTERVAL s."""
        st, why = node.brain.evaluate(now)
        if st != node.last_st[0] or (why != node.last_st[1]
                                     and now - node.last_st_time >= ST_MIN_INTERVAL):
            self.emit(node, t="ST", st=st, why=why)
            node.last_st, node.last_st_time = (st, why), now

    def press(self, node):
        now = time.monotonic()
        node.caller.cancel(now)                  # a press during a call cancels it
        stage = node.brain.button_press(now)
        self.emit(node, t="EV", e="BTN", stage=stage)
        self.emit(node, t="EV", e="BEEP", p="AGAIN" if stage == "first" else "CONFIRM")

    def sound(self, node, kind):
        """The mic heard a knock / voice: an answer while listening, else unprompted."""
        now = time.monotonic()
        if node.muted(now):
            return                               # node's own beeps: discarded, like the board
        if node.caller.state == caller_mod.LISTENING:
            if node.caller.heard(kind, now) == "RESP":
                taps = node.caller.taps
                node.brain.call_response(now, kind, taps)
                self.emit(node, t="EV", e="RESP", kind=kind, **({"taps": taps} if kind == "TAP" else {}))
                self.emit(node, t="EV", e="BEEP", p="PRESS_ME")
            return
        node.brain.activity(now, kind)
        self.emit(node, t="EV", e=kind)

    def set_danger(self, node, on):
        now = time.monotonic()
        node.danger_cmd = on
        node.brain.set_danger(on)
        node.next_siren = now
        if not on:
            node.siren_until = min(node.siren_until, now + 0.6)
        self.emit(node, t="EV", e="DANGER", on=on)

    def call_cycle(self, node, now):
        """Same as code.py: run the real Caller and report its steps."""
        if node.danger_cmd and now >= node.next_siren:
            node.siren_until = now + SIREN_AUDIO
            node.next_siren = now + DANGER_REPEAT
        st, _ = node.brain.evaluate(now)
        allowed = (now >= node.arm_until and st != "CONFIRMED"
                   and not node.brain.awaiting_second(now) and now >= node.siren_until)
        step = node.caller.update(now, allowed, now >= node.call_audio_end)
        if step == "CALL":
            node.call_audio_end = now + CALL_AUDIO
            self.emit(node, t="EV", e="CALL")
        elif step == "LISTEN" and node.answer_next:
            kind, node.answer_next = node.answer_next, None
            if kind == "TAP":                    # survivor knocks back twice
                self.later(2.0, lambda: self.sound(node, "TAP"))
                self.later(2.6, lambda: self.sound(node, "TAP"))
            else:
                self.later(2.0, lambda: self.sound(node, "VOICE"))
        elif step == "NORESP":
            node.brain.no_response(now)
            self.emit(node, t="EV", e="NORESP", calls=node.brain.unanswered_calls)

    # ---------- node 1's story ----------
    def script(self):
        n = self.n1

        def reset():
            n.brain.reset()                     # CO2 baseline is kept, like the real node
            n.back_to_room(k=0.1)

        def breathe():                          # measured: +150 in 5 s, +380 in 10 s, RH +6
            n.ppm_target, n.rh_target, n.k = n.room_ppm + 460, n.room_rh + 6, 0.15

        def answer_next_call():
            n.answer_next = "TAP"

        return [
            (0, reset),
            (36, breathe),                         # -> POSSIBLE "breathing detected"
            (50, lambda: self.sound(n, "TAP")),    # -> LIKELY "breathing + knocking"
            (62, n.back_to_room),
            (175, breathe),                        # missed calls by now -> UNRESPONSIVE
            (190, answer_next_call),               # misses this call, knocks back in the next
            (200, n.back_to_room),
            (290, lambda: self.sound(n, "TAP")),   # -> POSSIBLE "heard knocking"
            (305, lambda: self.press(n)),          # 1st press -> LIKELY countdown
            (312, lambda: self.press(n)),          # 2nd press -> CONFIRMED
            (318, lambda: self.emit(n, t="EV", e="ANS", q="injured", a="yes")),
            (323, lambda: self.emit(n, t="EV", e="ANS", q="count", a=2)),
            (330, lambda: self.set_danger(n, True)),
            (345, lambda: self.set_danger(n, False)),
            (350, lambda: self.emit(n, t="EV", e="STUCK")),
            (355, lambda: self.on_line("RX: crc error, packet dropped")),  # real receivers do this
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
                node.caller.reset(time.monotonic())
                if node is self.n1:
                    self._restart = True        # replay the story from the start
            self.later(0.5, do_reset)
        elif t == "CALL":
            node.caller.request()               # calls as soon as it's allowed
        elif t == "MSG":
            self.later(0.3, lambda: self.emit(node, t="EV", e="BEEP", p="MESSAGE"))
        elif t == "DANGER":
            self.later(0.3, lambda: self.set_danger(node, cmd["on"]))
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
                    if pos >= self.CYCLE or self._restart:
                        self.n1.power_on(now)
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
                    self.call_cycle(node, now)
                    self.report_status(node, now)
                    if now >= next_hb[node.n]:
                        self.heartbeat(node, now)
                        next_hb[node.n] = now + 5
            except Exception:
                log.exception("simulator step failed")   # never kill the thread
            self._stop.wait(0.1)
