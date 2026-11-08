"""In-memory picture of every node, built from incoming messages.

The dashboard only ever reads this; it never talks to nodes directly.
"""
import time
from collections import deque

from base.protocol import describe

LOST_AFTER = 90          # seconds without any message -> node shown as LOST
CO2_HISTORY_SECS = 600   # keep ~10 min of CO2 points for the chart
RECENT_EVENTS = 200      # global timeline length kept in memory
NODE_EVENTS = 20


def make_event(msg, ts, outgoing=False, ev_id=None):
    """Timeline entry. HB is not interesting enough to show, so callers skip it."""
    return {"id": ev_id, "ts": ts, "n": msg.get("n"), "t": msg.get("t"),
            "e": msg.get("e"), "out": outgoing, "desc": describe(msg, outgoing)}


class Node:
    def __init__(self, n, ts):
        self.n = n
        self.first_seen = ts
        self.last_seen = ts
        self.status = "NO SIGNS"
        self.why = ""
        self.rssi = None
        self.bat = None
        self.co2 = None
        self.base = None
        self.danger = False
        self.answers = {"injured": None, "count": None}
        self.snd = None             # loudest sound level in the last heartbeat period
        self.last_sound = None      # {"k": "TAP"/"VOICE", "ts": ...}
        self.co2_hist = deque()     # (ts, co2, base)
        self.events = deque(maxlen=NODE_EVENTS)

    def to_dict(self, now):
        lost = now - self.last_seen > LOST_AFTER
        return {
            "n": self.n,
            "status": "LOST" if lost else self.status,
            "last_status": self.status,     # what it said before going quiet
            "why": f"no message for {int(now - self.last_seen)} s" if lost else self.why,
            "lost": lost,
            "last_seen": self.last_seen,
            "rssi": self.rssi, "bat": self.bat, "co2": self.co2, "base": self.base,
            "danger": self.danger,
            "answers": dict(self.answers),
            "snd": self.snd, "last_sound": self.last_sound,
            "co2_hist": [[round(t, 1), c, b] for t, c, b in self.co2_hist],
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
        for key in ("rssi", "bat", "co2", "base", "snd"):
            if key in msg:
                setattr(nd, key, msg[key])
        if "st" in msg:
            nd.status = msg["st"]
            # a heartbeat may omit 'why'; keep the previous reason in that case
            if "why" in msg or msg["t"] == "ST":
                nd.why = msg.get("why", "")
        if "dg" in msg:
            nd.danger = bool(msg["dg"])

        if "co2" in msg:
            nd.co2_hist.append((ts, msg["co2"], msg.get("base", nd.base)))
            while nd.co2_hist and ts - nd.co2_hist[0][0] > CO2_HISTORY_SECS:
                nd.co2_hist.popleft()

        if msg["t"] == "EV":
            if msg["e"] == "SHIFT":
                nd.danger = True        # cleared by the next heartbeat with dg=0
            elif msg["e"] == "ANS":
                nd.answers[msg["q"]] = msg["a"]
            elif msg["e"] == "SOUND":
                nd.last_sound = {"k": msg["k"], "ts": ts}
        if msg["t"] == "HB":
            return None
        ev = make_event(msg, ts, ev_id=ev_id)
        nd.events.appendleft(ev)
        self.events.appendleft(ev)
        return ev

    def outgoing(self, cmd, ts, ev_id=None):
        """Log a rescuer command on the timeline."""
        ev = make_event(cmd, ts, outgoing=True, ev_id=ev_id)
        if cmd["n"] in self.nodes:
            self.nodes[cmd["n"]].events.appendleft(ev)
        self.events.appendleft(ev)
        return ev

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        return {n: nd.to_dict(now) for n, nd in sorted(self.nodes.items())}

    def lost_flags(self, now):
        return {n: now - nd.last_seen > LOST_AFTER for n, nd in self.nodes.items()}
