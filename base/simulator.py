"""Fake nodes for demos and development without hardware.

Each fake node runs the REAL node logic from firmware/node/: assess.py (decision
table, incl. the arming phase), co2logic.py (CO2 levels), caller.py
(call-and-listen) and location.py (GPS / last known / manual), DEMO profile, and
sends exactly the messages the board sends (BOOT, ST + evidence, short HB, SN, LOC).
Only the raw sensor values and the "survivors" are invented.

The story (~7 min loop; every node boots at the start: BOOT -> NO SIGNS "starting up"):
  Node 1 (GPS LIVE, ~40 m NE of base) walks through every status:
     36 s  breathing on the CO2 sensor          -> POSSIBLE "breathing detected"
     50 s  unprompted knock while breathing     -> LIKELY  "breathing + knocking"
    175 s  breathing again, calls missed         -> UNRESPONSIVE (red)
   ~218 s  knocks back during the next call      -> LIKELY  "tapped back 2 times after call"
    290 s  unprompted knock                      -> POSSIBLE "heard knocking"
    305 s  1st press -> LIKELY countdown; 312 s 2nd press -> CONFIRMED
    330-345 s  danger alarm (siren)              -> DANGER flag + alert
  Node 2 (~145 m SW): GPS LIVE from 15 s, fix lost at 150 s -> LAST KNOWN, age growing;
         a voice at 105 s -> POSSIBLE "heard voice"
  Node 3: no GPS -> NO POSITION until you "Set location manually" on the dashboard
         (-> MANUAL); goes silent at 240 s -> LOST after 90 s

It reacts to dashboard commands like a real node: CALL, MSG, DANGER, SETLOC, ASK (node 1
answers), RESET (clear status) and REBOOT (the node restarts: REBOOTING, BOOT, arming;
rebooting node 1 replays its story). A silent (LOST) node doesn't answer anything.
"""
import heapq
import itertools
import json
import logging
import math
import random
import threading
import time

from base.node_brain import load_assess, load_caller, load_co2logic, load_node_module
from base.sources import Source

log = logging.getLogger("base.sim")
assess = load_assess()
co2logic = load_co2logic()
caller_mod = load_caller()
location = load_node_module("location")

SIM_BASE = {"lat": 42.36010, "lon": -71.05890}   # pretend command post (sim mode default)

# = firmware/node/config.py, DEMO profile
PROFILE = "DEMO"
CONFIRM_WINDOW, ACTIVITY_WINDOW, RESPONSE_WINDOW, COMBINE_WINDOW = 20, 15, 60, 120
ARMING_SECS, CALL_INTERVAL, LISTEN_SECS, RESPONSE_MIN_TAPS = 10, 20, 10, 2
CALL_AUDIO = 1.75 + 0.6          # CALL pattern + MIC_TAIL: mic muted, then listening starts
SIREN_AUDIO = 3.6 + 0.6          # DANGER pattern + MIC_TAIL
DANGER_REPEAT = 10
ST_MIN_INTERVAL = 5              # reason-only ST lines at most every 5 s
HB_EVERY = 10                    # heartbeat + sensor message (DEMO)
LOC_EVERY = 30
CO2_EVERY = 5                    # the SCD41 makes a reading every ~5 s
DEMO_CO2 = dict(baseline_secs=30, rate_window=15, sudden_ppm=120, event_hold=60,
                rh_rise_points=3, follow=0.02, slow_enabled=False)


def offset(north_m, east_m):
    """A position this many metres north / east of the base."""
    lat = SIM_BASE["lat"] + north_m / 111320
    lon = SIM_BASE["lon"] + east_m / (111320 * math.cos(math.radians(SIM_BASE["lat"])))
    return lat, lon


class SimNode:
    def __init__(self, n, co2, rh, bat, rssi, pos=None, has_gps=True):
        self.n = n
        self.room_ppm, self.room_rh = co2, rh
        self.bat, self.rssi_mid = bat, rssi
        self.pos = pos                           # true position (for the fake GPS)
        self.has_gps = has_gps
        self.loc = location.Location(live_secs=10)
        self.power_on(time.monotonic())

    def power_on(self, now):
        """Like a real restart: fresh brain (arming), call cycle and sensors.
        The location's last fix survives (it's in flash on the real node)."""
        self.alive = True
        self.arm_until = now + ARMING_SECS
        self.brain = assess.Assessor(activity_window=ACTIVITY_WINDOW, response_window=RESPONSE_WINDOW,
                                     confirm_window=CONFIRM_WINDOW, combine_window=COMBINE_WINDOW,
                                     arm_until=self.arm_until)
        self.co2 = co2logic.CO2Levels(start=now, **DEMO_CO2)
        self.ppm, self.rh = self.room_ppm, self.room_rh
        self.ppm_target, self.rh_target, self.k = self.room_ppm, self.room_rh, 0.05
        self.last_lvl = None
        self.caller = caller_mod.Caller(CALL_INTERVAL, LISTEN_SECS, RESPONSE_MIN_TAPS,
                                        first_call_at=now + CALL_INTERVAL)
        self.call_audio_end = 0
        self.danger_cmd = False
        self.next_siren = 0
        self.siren_until = 0
        self.answer_next = None
        self.gps_live = False
        self.last_st = (None, None)
        self.last_st_time = -100
        self.next_hb = now + 1
        self.next_co2 = now + CO2_EVERY
        self.next_loc = now
        self.next_fix = now
        self.last_loc_src = None
        self.booted = False                      # BOOT message still to send
        self.paused = False                      # demo pause from the dashboard
        self.loudest = 0                         # loudest sound since the last heartbeat
        self.floor = random.uniform(12000, 13500)   # the room's quiet level (like the Grove sensor)

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
    CYCLE = 420

    def __init__(self, second_node=True):
        super().__init__()
        self.n1 = SimNode(1, co2=810, rh=48.0, bat=87, rssi=-48, pos=offset(35, 20))
        extra = [SimNode(2, co2=600, rh=45.0, bat=64, rssi=-71, pos=offset(-120, -80)),
                 SimNode(3, co2=700, rh=50.0, bat=23, rssi=-92, pos=None, has_gps=False)] if second_node else []
        self.all = [self.n1] + extra
        self._heap = []
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._restart = False
        self._story_restart = False

    def node(self, n):
        return next((nd for nd in self.all if nd.n == n), None)

    # ---------- sending, exactly like code.py ----------
    def later(self, delay, fn):
        with self._lock:
            heapq.heappush(self._heap, (time.monotonic() + delay, next(self._seq), fn))

    def emit(self, node, **msg):
        if not node.alive:
            return
        msg = {"n": node.n, **msg, "rssi": node.rssi()}
        msg = {"t": msg.pop("t"), **msg}          # keep "t" first, like the real nodes
        self.on_line(json.dumps(msg, separators=(",", ":")))

    def heartbeat(self, node, now):
        st, _ = node.brain.evaluate(now)
        self.emit(node, t="HB", st=st, ev=node.brain.evidence(now), cs=node.caller.state,
                  ll=int(node.caller.listen_left(now)), nc=int(node.caller.next_call_in(now)),
                  mic="muted" if node.muted(now) else "on", prof=PROFILE)
        # sound: the room's background plus any knock / voice since the last heartbeat
        node.floor += random.uniform(-150, 150)
        loud = max(node.loudest, int(node.floor + random.uniform(1500, 3000)))
        node.loudest = 0
        sn = {"t": "SN", "bat": round(node.bat), "snd": loud, "nf": int(node.floor),
              "vt": int(max(14000, node.floor + 4000)), "tt": int(max(22000, node.floor + 8000))}
        c = node.co2
        if c.ppm is not None:
            sn.update(co2=round(c.ppm), rh=round(c.rh, 1), lvl=c.level)
            if c.hold_left(now):
                sn["hold"] = int(c.hold_left(now))
            if c.baseline is not None:
                sn.update(base=int(c.baseline), rhb=round(c.rh_baseline, 1))
        self.emit(node, **sn)

    def send_loc(self, node, now):
        where = node.loc.best(now)
        if now >= node.next_loc or where["src"] != node.last_loc_src:
            msg = {"t": "LOC", "src": where["src"]}
            if where["src"] != "NONE":
                msg.update(lat=round(where["lat"], 5), lon=round(where["lon"], 5), age=where["age"])
                for k in ("acc", "sats", "label"):
                    if where.get(k) not in (None, ""):
                        msg[k] = where[k]
            self.emit(node, **msg)
            node.last_loc_src = where["src"]
            node.next_loc = now + LOC_EVERY

    def co2_reading(self, node, now):
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
        st, why = node.brain.evaluate(now)
        if st != node.last_st[0] or (why != node.last_st[1]
                                     and now - node.last_st_time >= ST_MIN_INTERVAL):
            self.emit(node, t="ST", st=st, why=why, ev=node.brain.evidence(now))
            node.last_st, node.last_st_time = (st, why), now

    # ---------- survivor actions ----------
    def press(self, node):
        now = time.monotonic()
        node.caller.cancel(now)
        stage = node.brain.button_press(now)
        if stage:
            self.emit(node, t="EV", e="BTN", stage=stage)
            self.emit(node, t="EV", e="BEEP", p="AGAIN" if stage == "first" else "CONFIRM")

    def sound(self, node, kind):
        """The mic heard a knock / voice: an answer while listening, else unprompted."""
        now = time.monotonic()
        if node.paused:
            return
        node.loudest = max(node.loudest, 62000 if kind == "TAP" else random.randint(26000, 34000))
        if node.muted(now) or now < node.arm_until:
            return                               # own beeps / still arming: ignored, like the board
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

    def fake_gps(self, node, now):
        if node.has_gps and node.gps_live and node.pos and now >= node.next_fix:
            lat, lon = node.pos
            jitter = 2 / 111320                  # ~2 m
            node.loc.on_fix(now, lat + random.uniform(-jitter, jitter), lon + random.uniform(-jitter, jitter),
                            round(random.uniform(1.2, 1.8), 1), random.randint(7, 10))
            node.next_fix = now + 1

    # ---------- the story ----------
    def script(self):
        n1, more = self.n1, len(self.all) > 1
        n2, n3 = (self.all[1], self.all[2]) if more else (None, None)

        def breathe():                          # measured: +150 in 5 s, +380 in 10 s, RH +6
            n1.ppm_target, n1.rh_target, n1.k = n1.room_ppm + 460, n1.room_rh + 6, 0.15

        def answer_next_call():
            n1.answer_next = "TAP"

        def gps(node, on):
            def f():
                node.gps_live = on
            return f

        steps = [
            (0, gps(n1, True)),
            (36, breathe),                         # -> POSSIBLE "breathing detected"
            (50, lambda: self.sound(n1, "TAP")),   # -> LIKELY "breathing + knocking"
            (62, n1.back_to_room),
            (175, breathe),                        # missed calls by now -> UNRESPONSIVE
            (190, answer_next_call),               # misses this call, knocks back in the next
            (200, n1.back_to_room),
            (290, lambda: self.sound(n1, "TAP")),  # -> POSSIBLE "heard knocking"
            (305, lambda: self.press(n1)),         # 1st press -> LIKELY countdown
            (312, lambda: self.press(n1)),         # 2nd press -> CONFIRMED
            (318, lambda: self.emit(n1, t="EV", e="ANS", q="injured", a="yes")),
            (323, lambda: self.emit(n1, t="EV", e="ANS", q="count", a=2)),
            (330, lambda: self.set_danger(n1, True)),
            (345, lambda: self.set_danger(n1, False)),
            (350, lambda: self.emit(n1, t="EV", e="STUCK")),
            (355, lambda: self.on_line("RX: crc error, packet dropped")),  # real receivers do this
        ]
        if more:
            steps += [
                (15, gps(n2, True)),               # node 2: GPS fix...
                (105, lambda: self.sound(n2, "VOICE")),   # between its calls (mic not muted)
                (150, gps(n2, False)),             # ...lost: LAST KNOWN with a growing age
                (240, lambda: setattr(n3, "alive", False)),   # node 3 goes silent -> LOST at ~330 s
            ]
        return sorted(steps, key=lambda s: s[0])

    # ---------- dashboard commands ----------
    def send(self, cmd):
        node = self.node(cmd["n"])
        if node is None:
            raise ConnectionError(f"no simulated node {cmd['n']}")
        if not node.alive:
            return                               # radio: sent, but nobody is listening
        log.info("sim node %s got %s", node.n, cmd)
        t = cmd["t"]
        if t == "RESET":                         # clear status: evidence + counters, keep baselines
            def do_reset():
                node.brain.reset("status cleared by base")
                node.caller.reset(time.monotonic())
                node.answer_next = None
                node.call_audio_end = node.siren_until = 0
            self.later(0.3, do_reset)
        elif t == "REBOOT":                      # full reset: the node restarts
            self.emit(node, t="EV", e="REBOOTING")

            def do_reboot():
                if cmd.get("keep_gps", True):
                    node.loc.manual = None       # manual location was only in RAM
                else:
                    node.loc = location.Location(live_secs=10)   # saved fix cleared too
                node.power_on(time.monotonic())  # -> BOOT, arming, CO2 baseline re-learned
                if node is self.n1:
                    self._story_restart = True   # replay node 1's walk-through
            self.later(1.5, do_reboot)
        elif t == "CALL":
            node.caller.request()
        elif t == "MSG":
            self.later(0.3, lambda: self.emit(node, t="EV", e="BEEP", p="MESSAGE"))
        elif t == "DANGER":
            self.later(0.3, lambda: self.set_danger(node, cmd["on"]))
        elif t == "PAUSE":
            def do_pause():
                now = time.monotonic()
                if cmd["on"] != node.paused:
                    node.paused = cmd["on"]
                    node.caller.reset(now)
                    node.call_audio_end = node.siren_until = 0
                    node.next_hb = now                    # paused HB / normal HB right away
                    node.last_st = (None, None)
                self.emit(node, t="EV", e="PAUSE", on=node.paused)
            self.later(0.3, do_pause)
        elif t == "SETLOC":
            self.later(0.3, lambda: node.loc.set_manual(time.monotonic(), cmd["lat"], cmd["lon"], cmd.get("label", "")))
        elif t == "ASK" and node is self.n1:
            a = "yes" if cmd["q"] == "injured" else 2
            self.later(3, lambda: self.emit(node, t="EV", e="ANS", q=cmd["q"], a=a))

    # ---------- main loop ----------
    def run(self):
        self._set_status(True, "simulator (fake nodes)")
        steps = self.script()
        start = last = time.monotonic()
        idx = 0
        while not self._stop.is_set():
            now = time.monotonic()
            dt, last = now - last, now
            try:
                pos = now - start
                if pos >= self.CYCLE or self._restart:      # loop the story: every node reboots
                    for nd in self.all:
                        nd.power_on(now)
                    start, pos, idx, self._restart = now, 0, 0, False
                if self._story_restart:                     # node 1 was rebooted from the dashboard
                    start, pos, idx, self._story_restart = now, 0, 0, False
                while idx < len(steps) and pos >= steps[idx][0]:
                    steps[idx][1]()
                    idx += 1

                due = []
                with self._lock:
                    while self._heap and self._heap[0][0] <= now:
                        due.append(heapq.heappop(self._heap)[2])
                for fn in due:
                    fn()

                for node in self.all:
                    if not node.alive:
                        continue
                    if not node.booted:
                        node.booted = True
                        self.emit(node, t="EV", e="BOOT", sensors={"sound": True, "co2": True, "gps": node.has_gps,
                                                                   "button": True, "speaker": True})
                    if node.paused:                     # demo pause: only "I'm paused" every 30 s
                        if now >= node.next_hb:
                            self.emit(node, t="HB", st="NO SIGNS", paused=1)
                            node.next_hb = now + 30
                        continue
                    node.drift(dt)
                    self.fake_gps(node, now)
                    if now >= node.next_co2:
                        self.co2_reading(node, now)
                        node.next_co2 = now + CO2_EVERY
                    self.call_cycle(node, now)
                    self.report_status(node, now)
                    self.send_loc(node, now)
                    if now >= node.next_hb:
                        self.heartbeat(node, now)
                        node.next_hb = now + HB_EVERY
            except Exception:
                log.exception("simulator step failed")   # never kill the thread
            self._stop.wait(0.1)
