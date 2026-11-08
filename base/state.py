"""In-memory picture of every node, built from incoming messages.

The dashboard only ever reads this; it never talks to nodes directly.
"""
import re
import time
from collections import deque

from base.protocol import CO2_EVIDENCE, describe, sound_kind

LOST_AFTER = 90          # seconds without any message -> node shown as LOST
CO2_HISTORY_SECS = 600   # keep ~10 min of CO2 points for the chart
RECENT_EVENTS = 200      # global timeline length kept in memory
NODE_EVENTS = 20
CALL_AUDIO = 2.4           # 3 call beeps (1.75 s) + mic tail, before listening starts
LISTEN_SECS = 10
CALL_INTERVAL = {"DEMO": 20, "FIELD": 60}   # must match firmware/node/config.py
ELEVATED_LOG_EVERY = 300  # log "CO2 high but steady" at most every 5 min
NUMBERS = re.compile(r"[+-]?\d+")


def same_words(a, b):
    """True if two reasons differ only in their numbers: "press again (15s)" vs
    "(10s)", or "CO2 +380 ppm" vs "+410 ppm". Those update the card, not the timeline."""
    return NUMBERS.sub("#", a or "") == NUMBERS.sub("#", b or "")


def make_event(msg, ts, outgoing=False, ev_id=None):
    """Timeline entry. HB is not interesting enough to show, so callers skip it."""
    return {"id": ev_id, "ts": ts, "n": msg.get("n"), "t": msg.get("t"),
            "e": msg.get("e"), "stage": msg.get("stage"),
            "lvl": msg.get("lvl") if msg.get("e") == "CO2" else None, "out": outgoing,
            "desc": describe(msg, outgoing)}


class Node:
    def __init__(self, n, ts):
        self.n = n
        self.first_seen = ts
        self.last_seen = ts
        self.status = "NO SIGNS"
        self.why = ""
        self.why_ts = ts            # when 'why' arrived (lets the page tick a countdown)
        self.rssi = None
        self.bat = None
        self.co2 = None
        self.base = None
        self.rh = None              # humidity % now / baseline
        self.rh_base = None
        self.lvl = None             # CO2 level: LEARNING / NORMAL / ... / BREATHING
        self.prof = None            # node profile: DEMO / FIELD
        self.danger = False
        self.answers = {"injured": None, "count": None}
        self.snd = None             # loudest sound level in the last heartbeat period
        self.last_sound = None      # {"k": "TAP"/"VOICE", "ts": ...}
        self.co2_hist = deque()     # (ts, co2, base)
        self.hold_until = None      # CO2 evidence expires at this time (node's hold timer)
        self.co2_marks = deque()    # (start_ts, end_ts) of each sudden rise, for the chart
        self.elevated_logged = -1e9
        # call-and-listen cycle (exact state from heartbeats, estimated in between)
        self.call_state = None      # IDLE / CALLING / LISTENING
        self.call_ts = None         # when the last call started
        self.listen_end = None      # when the current listening window ends
        self.next_call_at = None    # when the next call is due
        self.calls_unanswered = 0
        self.mic = None             # "on" / "muted" (from the last heartbeat)
        self.parts = None           # BOOT self-check: {"sound": True, "co2": False, ...}
        self.events = deque(maxlen=NODE_EVENTS)

    def to_dict(self, now):
        lost = now - self.last_seen > LOST_AFTER
        return {
            "n": self.n,
            "status": "LOST" if lost else self.status,
            "last_status": self.status,     # what it said before going quiet
            "why": f"no message for {int(now - self.last_seen)} s" if lost else self.why,
            "why_ts": self.why_ts,
            "lost": lost,
            "last_seen": self.last_seen,
            "rssi": self.rssi, "bat": self.bat, "co2": self.co2, "base": self.base,
            "rh": self.rh, "rh_base": self.rh_base, "lvl": self.lvl, "prof": self.prof,
            "danger": self.danger,
            "answers": dict(self.answers),
            "snd": self.snd, "last_sound": self.last_sound,
            "co2_hist": [[round(t, 1), c, b] for t, c, b in self.co2_hist],
            "hold_until": self.hold_until if self.lvl in CO2_EVIDENCE else None,
            "co2_marks": [[round(a, 1), round(b, 1)] for a, b in self.co2_marks],
            "call_state": self.call_state, "call_ts": self.call_ts,
            "listen_end": self.listen_end, "next_call_at": self.next_call_at,
            "calls_unanswered": self.calls_unanswered, "mic": self.mic, "parts": self.parts,
            "events": list(self.events),
        }


class State:
    def __init__(self):
        self.nodes = {}
        self.events = deque(maxlen=RECENT_EVENTS)

    def node(self, n, ts):
        if n not in self.nodes:
            self.nodes[n] = Node(n, ts)
        return self.nodes[n]

    def apply(self, msg, ts, ev_id=None):
        """Update a node from one parsed message. Returns a timeline event or None."""
        nd = self.node(msg["n"], ts)
        nd.last_seen = max(nd.last_seen, ts)
        # same status, only the numbers in the reason changed (countdown, CO2 delta):
        # update the card, not the timeline
        tick = (msg["t"] == "ST" and msg.get("st") == nd.status
                and same_words(msg.get("why"), nd.why))
        for key in ("rssi", "bat", "co2", "base", "snd", "rh", "rh_base", "lvl", "prof"):
            if key in msg:
                setattr(nd, key, msg[key])
        if "st" in msg:
            nd.status = msg["st"]
            # a heartbeat may omit 'why'; keep the previous reason in that case
            if "why" in msg or msg["t"] == "ST":
                nd.why = msg.get("why", "")
                nd.why_ts = ts
        if "dg" in msg:
            nd.danger = bool(msg["dg"])
        if "danger" in msg:
            nd.danger = msg["danger"]
        if "mic" in msg:
            nd.mic = msg["mic"]
        if "calls_unanswered" in msg:
            nd.calls_unanswered = msg["calls_unanswered"]
        if "call_state" in msg:                 # heartbeat: exact call-cycle state
            nd.call_state = msg["call_state"]
            if nd.call_state == "LISTENING" and "listen_left" in msg:
                nd.listen_end = ts + msg["listen_left"]
            if nd.call_state == "IDLE" and "next_call_in" in msg:
                nd.next_call_at = ts + msg["next_call_in"]

        if "co2" in msg:
            nd.co2_hist.append((ts, msg["co2"], msg.get("base", nd.base)))
            while nd.co2_hist and ts - nd.co2_hist[0][0] > CO2_HISTORY_SECS:
                nd.co2_hist.popleft()
        if "hold" in msg:                       # node says how long its CO2 evidence lasts
            nd.hold_until = ts + msg["hold"]

        quiet_co2 = False                       # CO2 level change too boring for the timeline?
        if msg["t"] == "EV" and msg["e"] == "CO2":
            lvl, prev = msg["lvl"], msg.get("prev")
            if lvl in CO2_EVIDENCE and prev not in CO2_EVIDENCE:
                # shade the chart from where the rise started to where it was detected
                nd.co2_marks.append((ts - msg.get("secs", 10), ts))
            while nd.co2_marks and ts - nd.co2_marks[0][1] > CO2_HISTORY_SECS:
                nd.co2_marks.popleft()
            if prev not in CO2_EVIDENCE:
                if lvl == "ELEVATED":            # room air drifting: at most every 5 min
                    quiet_co2 = ts - nd.elevated_logged < ELEVATED_LOG_EVERY
                    if not quiet_co2:
                        nd.elevated_logged = ts
                elif lvl == "NORMAL" and prev not in (None, "LEARNING"):
                    quiet_co2 = True             # ELEVATED -> NORMAL flip

        if msg["t"] == "EV":
            if msg["e"] == "SHIFT":
                nd.danger = True        # cleared by the next heartbeat with dg=0
            elif msg["e"] == "ANS":
                nd.answers[msg["q"]] = msg["a"]
            elif msg["e"] == "DANGER":
                nd.danger = msg["on"]
            elif msg["e"] == "BOOT":
                nd.parts = msg.get("sensors")
            elif msg["e"] == "CALL":
                nd.call_state, nd.call_ts = "CALLING", ts
                nd.listen_end = ts + CALL_AUDIO + LISTEN_SECS       # estimate until the next HB
            elif msg["e"] in ("RESP", "NORESP") or (msg["e"] == "BTN" and nd.call_state != "IDLE"):
                nd.call_state = "IDLE"
                nd.next_call_at = ts + CALL_INTERVAL.get(nd.prof, 20)
            if msg["e"] in ("RESP", "BTN"):
                nd.calls_unanswered = 0
            elif msg["e"] == "NORESP" and "calls" in msg:
                nd.calls_unanswered = msg["calls"]
            kind = sound_kind(msg)
            if kind:
                nd.last_sound = {"k": kind, "ts": ts}
        quiet_beep = msg["t"] == "EV" and msg["e"] == "BEEP"   # always duplicates another entry
        if msg["t"] == "HB" or tick or quiet_co2 or quiet_beep:
            return None
        ev = make_event(msg, ts, ev_id=ev_id)
        nd.events.appendleft(ev)
        self.events.appendleft(ev)
        return ev

    def outgoing(self, cmd, ts, ev_id=None):
        """Log a rescuer command on the timeline."""
        ev = make_event(cmd, ts, outgoing=True, ev_id=ev_id)
        if cmd["n"] in self.nodes:
            nd = self.nodes[cmd["n"]]
            nd.events.appendleft(ev)
            if cmd["t"] == "RESET":   # false alarm: forget what the survivor "said"
                nd.answers = {"injured": None, "count": None}
                nd.last_sound = None
                nd.danger = False
        self.events.appendleft(ev)
        return ev

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        return {n: nd.to_dict(now) for n, nd in sorted(self.nodes.items())}

    def lost_flags(self, now):
        return {n: now - nd.last_seen > LOST_AFTER for n, nd in self.nodes.items()}
