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
NODE_EVENTS = 60
CALL_AUDIO = 2.4           # 3 call beeps (1.75 s) + mic tail, before listening starts
LISTEN_SECS = 10
CALL_INTERVAL = {"DEMO": 20, "FIELD": 60}   # must match firmware/node/config.py
ELEVATED_LOG_EVERY = 300  # log "CO2 high but steady" at most every 5 min
NUMBERS = re.compile(r"[+-]?\d+")


def same_words(a, b):
    """True if two reasons differ only in their numbers: "press again (15s)" vs
    "(10s)", or "CO2 +380 ppm" vs "+410 ppm". Those update the card, not the timeline."""
    return NUMBERS.sub("#", a or "") == NUMBERS.sub("#", b or "")


def category(msg, outgoing=False):
    """Event-log filter group: alerts / comms (survivor communication) / sensors / system."""
    t, e = msg.get("t"), msg.get("e")
    if outgoing:
        return {"CALL": "comms", "MSG": "comms", "ASK": "comms", "DANGER": "alerts"}.get(t, "system")
    if t == "ST":                       # only the status changes a responder must act on
        return "alerts" if msg.get("st") in ("UNRESPONSIVE", "CONFIRMED", "LIKELY") else "system"
    if e in ("DANGER", "SHIFT"):
        return "alerts"
    if e == "BTN":
        return "alerts" if msg.get("stage") == "confirmed" else "comms"
    if e in ("CALL", "RESP", "NORESP", "ANS", "BEEP"):
        return "comms"
    if e == "BOOT":
        return "system"
    return "sensors"                    # CO2, TAP, VOICE, STUCK, LOC ...


def make_event(msg, ts, outgoing=False, ev_id=None):
    """Timeline entry. HB is not interesting enough to show, so callers skip it."""
    return {"id": ev_id, "ts": ts, "n": msg.get("n"), "t": msg.get("t"), "cat": category(msg, outgoing),
            "e": msg.get("e"), "stage": msg.get("stage"), "st": msg.get("st"),
            "lvl": msg.get("lvl") if msg.get("e") == "CO2" else None, "out": outgoing,
            "desc": describe(msg, outgoing)}


class Node:
    def __init__(self, n, ts):
        self.n = n
        self.first_seen = ts
        self.last_seen = ts
        self.status = "NO SIGNS"
        self.pending_reboot = None  # time a full reset was sent; cleared by the node's BOOT
        self.status_since = ts      # when the status last changed (triage: longest waiting first)
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
        self.snd_hist = deque()     # (ts, loudest level, noise floor, voice threshold, knock threshold)
        self.snd_marks = deque()    # (ts, "TAP"/"VOICE") sounds the node reported, for the chart
        self.paused = False         # paused for a demo (from the base)
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
        self.ev = None              # evidence snapshot from the last ST/HB (why this status)
        self.loc = None             # {"src","lat","lon","acc","age","sats","label"}
        self.loc_ts = None          # when that LOC arrived (age grows from there)
        self.events = deque(maxlen=NODE_EVENTS)

    def to_dict(self, now):
        lost = now - self.last_seen > LOST_AFTER
        return {
            "n": self.n,
            "status": "LOST" if lost else self.status,
            "last_status": self.status,     # what it said before going quiet
            "status_since": self.status_since,
            "pending_reboot": self.pending_reboot,
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
            "snd_hist": [[round(t, 1), *rest] for t, *rest in self.snd_hist],
            "snd_marks": [[round(t, 1), k] for t, k in self.snd_marks],
            "paused": self.paused,
            "hold_until": self.hold_until if self.lvl in CO2_EVIDENCE else None,
            "co2_marks": [[round(a, 1), round(b, 1)] for a, b in self.co2_marks],
            "call_state": self.call_state, "call_ts": self.call_ts,
            "listen_end": self.listen_end, "next_call_at": self.next_call_at,
            "calls_unanswered": self.calls_unanswered, "mic": self.mic, "parts": self.parts,
            "ev": self.ev, "loc": self.loc, "loc_ts": self.loc_ts,
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
            if msg["st"] != nd.status:
                nd.status_since = ts
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
        if "ev" in msg:
            nd.ev = msg["ev"]
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
        if "snd" in msg:
            nd.snd_hist.append((ts, msg["snd"], msg.get("nf"), msg.get("vt"), msg.get("tt")))
            while nd.snd_hist and ts - nd.snd_hist[0][0] > CO2_HISTORY_SECS:
                nd.snd_hist.popleft()
        if msg["t"] == "HB":                    # a paused node says so in every heartbeat
            nd.paused = bool(msg.get("paused"))
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
                nd.pending_reboot = None            # the node is back after a full reset
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
            if msg["e"] == "RESP":
                kind = msg["kind"]
            if kind:
                nd.last_sound = {"k": kind, "ts": ts}
                nd.snd_marks.append((ts, kind))
                while nd.snd_marks and ts - nd.snd_marks[0][0] > CO2_HISTORY_SECS:
                    nd.snd_marks.popleft()
            if msg["e"] == "PAUSE":
                nd.paused = msg["on"]
        quiet_loc = False
        if msg["t"] == "LOC":                   # timeline only when the source changes
            prev = nd.loc["src"] if nd.loc else None
            nd.loc = {k: msg[k] for k in ("src", "lat", "lon", "acc", "age", "sats", "label") if k in msg}
            nd.loc_ts = ts
            quiet_loc = msg["src"] == prev
        quiet_beep = msg["t"] == "EV" and msg["e"] == "BEEP"   # always duplicates another entry
        if msg["t"] in ("HB", "SN") or tick or quiet_co2 or quiet_beep or quiet_loc:
            return None
        ev = make_event(msg, ts, ev_id=ev_id)
        nd.events.appendleft(ev)
        self.events.appendleft(ev)
        return ev

    def reset_node(self, n, ts):
        """Full reset: forget everything about node n; show it as 'awaiting boot'."""
        nd = Node(n, ts)
        nd.pending_reboot = ts
        self.nodes[n] = nd
        kept = [e for e in self.events if e["n"] != n]
        self.events.clear()
        self.events.extend(kept)

    def reset_all(self, ts):
        """New incident: every known node starts over ('awaiting boot'), timeline empty."""
        for n in list(self.nodes):
            self.nodes[n] = Node(n, ts)
            self.nodes[n].pending_reboot = ts
        self.events.clear()

    def add_note(self, n, desc, cat, ts, ev_id=None):
        """A base-side timeline line (alert, acknowledgement, assignment, setting)."""
        ev = {"id": ev_id, "ts": ts, "n": n, "t": "NOTE", "cat": cat, "e": None, "stage": None,
              "st": None, "lvl": None, "out": False, "sys": True, "desc": desc}
        if n in self.nodes:
            self.nodes[n].events.appendleft(ev)
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
