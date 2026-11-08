"""Message protocol: parse node -> base lines, validate base -> node commands,
and turn messages into plain-English timeline text.

Radio links are noisy and the receiver Feather also prints its own debug text,
so the parser NEVER raises: it returns (msg, None) or (None, "why it was rejected").
"""
import json

STATUSES = ("NO SIGNS", "POSSIBLE", "LIKELY", "CONFIRMED", "UNRESPONSIVE")
EVENTS = ("BTN", "TAP", "VOICE", "STUCK", "NORESP", "SHIFT", "ANS", "SOUND", "CO2",
          "CALL", "RESP", "BEEP", "DANGER", "BOOT", "REBOOTING")
BOOT_PARTS = ("sound", "co2", "gps", "button", "speaker")
CO2_LEVELS = ("LEARNING", "NORMAL", "ELEVATED", "SUDDEN_RISE", "BREATHING", "SLOW_BUILDUP")
CO2_EVIDENCE = ("SUDDEN_RISE", "BREATHING", "SLOW_BUILDUP")   # ELEVATED/NORMAL are info only
PROFILES = ("DEMO", "FIELD")
SOUND_KINDS = ("TAP", "VOICE")   # SOUND event (older nodes): knock vs. sustained voice
BTN_STAGES = ("first", "confirmed")
QUESTIONS = ("injured", "count")
CALL_STATES = ("IDLE", "CALLING", "LISTENING")
BEEPS = ("CALL", "PRESS_ME", "AGAIN", "CONFIRM", "MESSAGE", "DANGER")
MSG_MAX = 60  # node screen fits ~60 characters

# Optional numeric fields: name -> type. Bad values are dropped, not fatal.
_NUMERIC = {"co2": float, "base": float, "bat": float, "rssi": float,
            "dg": int, "taps": int, "calls": int,
            "snd": int,   # loudest sound level since the last heartbeat (0..65535)
            "rh": float, "rh_base": float,          # humidity % now / baseline
            "rise": float, "secs": int,             # CO2 event: sudden rise (ppm) in N seconds
            "rh_jump": float,                       # CO2 event: humidity jump (points)
            "hold": int,                            # seconds of CO2 evidence left
            "listen_left": int, "next_call_in": int, # call cycle (HB)
            "calls_unanswered": int}
# Optional text fields: name -> allowed values. Unknown values are dropped.
_CHOICES = {"lvl": CO2_LEVELS, "prev": CO2_LEVELS, "prof": PROFILES,
            "kind": SOUND_KINDS, "p": BEEPS, "call_state": CALL_STATES, "mic": ("on", "muted")}
_BOOLS = ("on", "danger")                           # true / false fields
# Short field names the node uses to keep LoRa packets small -> the names used here.
# (Older nodes send the long names; both work.)
_ALIASES = {"cs": "call_state", "ll": "listen_left", "nc": "next_call_in", "rhb": "rh_base"}
# Evidence snapshot "ev" in ST / HB: why the node chose its status
EV_CHOICES = {"btn": ("none", "first", "confirmed"), "snd": ("none", "TAP", "VOICE"),
              "co2": CO2_LEVELS + ("NONE",)}
EV_NUMBERS = ("resp", "miss", "dg")
MSG_TYPES = ("HB", "ST", "EV", "SN", "LOC")
LOC_SOURCES = ("LIVE", "LAST", "MANUAL", "NONE")


def _num(value, kind):
    if isinstance(value, bool):
        return None
    try:
        out = kind(value)
    except (TypeError, ValueError):
        return None
    if kind is float and out == int(out):
        out = int(out)  # keep 812 as 812, not 812.0
    return out


def parse_line(line):
    """Parse one serial line. Returns (msg, None) on success or (None, error)."""
    if isinstance(line, (bytes, bytearray)):
        line = line.decode("utf-8", errors="replace")
    if not isinstance(line, str):
        return None, "not text"
    line = line.strip()
    if not line:
        return None, "empty line"

    # The receiver may prefix lines ("RX: {...}"); take the JSON object part.
    start, end = line.find("{"), line.rfind("}")
    if start == -1 or end <= start:
        return None, "no JSON object"
    try:
        raw = json.loads(line[start:end + 1])
    except ValueError:
        return None, "bad JSON"
    if not isinstance(raw, dict):
        return None, "JSON is not an object"

    t = raw.get("t")
    if t not in MSG_TYPES:
        return None, f"unknown type {t!r}"
    for short, long in _ALIASES.items():
        if short in raw and long not in raw:
            raw[long] = raw[short]
    n = _num(raw.get("n"), int)
    if n is None or n < 0:
        return None, "missing/bad node id 'n'"

    msg = {"t": t, "n": n}
    for key, kind in _NUMERIC.items():
        if key in raw:
            val = _num(raw[key], kind)
            if val is not None:
                msg[key] = val
    for key, allowed in _CHOICES.items():
        if raw.get(key) in allowed:
            msg[key] = raw[key]
    for key in _BOOLS:
        if isinstance(raw.get(key), bool):
            msg[key] = raw[key]
    if isinstance(raw.get("ev"), dict):
        ev = {}
        for key, allowed in EV_CHOICES.items():
            if raw["ev"].get(key) in allowed:
                ev[key] = raw["ev"][key]
        for key in EV_NUMBERS:
            val = _num(raw["ev"].get(key), int)
            if val is not None:
                ev[key] = val
        msg["ev"] = ev
        if "miss" in ev:
            msg.setdefault("calls_unanswered", ev["miss"])
        if "dg" in ev:
            msg.setdefault("danger", bool(ev["dg"]))

    st = raw.get("st")
    if st is not None:
        if st not in STATUSES:
            return None, f"unknown status {st!r}"
        msg["st"] = st
    elif t == "ST":
        return None, "ST message without 'st'"
    if isinstance(raw.get("why"), str):
        msg["why"] = raw["why"][:120]

    if t == "LOC":
        src = raw.get("src")
        if src not in LOC_SOURCES:
            return None, "LOC needs src (LIVE/LAST/MANUAL/NONE)"
        msg["src"] = src
        if src != "NONE":
            lat, lon = _num(raw.get("lat"), float), _num(raw.get("lon"), float)
            if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return None, "LOC needs a valid lat/lon"
            msg["lat"], msg["lon"] = lat, lon
            for key in ("acc", "age", "sats"):
                val = _num(raw.get(key), int)
                if val is not None:
                    msg[key] = val
            if isinstance(raw.get("label"), str):
                msg["label"] = raw["label"][:30]

    if t == "EV":
        e = raw.get("e")
        if e not in EVENTS:
            return None, f"unknown event {e!r}"
        msg["e"] = e
        if e == "ANS":
            q, a = raw.get("q"), raw.get("a")
            if q not in QUESTIONS or a is None:
                return None, "ANS needs q (injured/count) and a"
            msg["q"] = q
            msg["a"] = str(a)[:20]
        elif e == "SOUND":
            k = raw.get("k")
            if k not in SOUND_KINDS:
                return None, "SOUND needs k (TAP/VOICE)"
            msg["k"] = k
        elif e == "BTN" and raw.get("stage") in BTN_STAGES:
            msg["stage"] = raw["stage"]
        elif e == "CO2" and "lvl" not in msg:
            return None, "CO2 event needs a known lvl"
        elif e == "RESP" and "kind" not in msg:
            return None, "RESP needs kind (TAP/VOICE)"
        elif e == "DANGER" and "on" not in msg:
            return None, "DANGER needs on (true/false)"
        elif e == "BOOT":
            parts = raw.get("sensors") if isinstance(raw.get("sensors"), dict) else {}
            msg["sensors"] = {k: bool(parts[k]) for k in BOOT_PARTS if k in parts}
    return msg, None


def describe_co2(msg):
    lvl, prev = msg["lvl"], msg.get("prev")
    rise, secs, rh = int(msg.get("rise", 0)), int(msg.get("secs", 0)), int(msg.get("rh_jump", 0))
    if lvl == "BREATHING":
        return f"Breathing detected: CO2 +{rise} ppm, humidity +{rh}%"
    if lvl == "SUDDEN_RISE":
        return f"Sudden CO2 rise +{rise} ppm in {secs} s"
    if lvl == "SLOW_BUILDUP":
        return f"Slow CO2 build-up +{rise} ppm over {round(secs / 60)} min"
    if prev in CO2_EVIDENCE:                       # ELEVATED / NORMAL after an event
        return "CO2 evidence expired"
    if lvl == "ELEVATED":
        return "CO2 high but steady (room air?)"
    if lvl == "NORMAL":
        if prev in (None, "LEARNING"):
            return "CO2 baseline learned" + (f": {msg['base']} ppm" if "base" in msg else "")
        return "CO2 back to normal"
    return "CO2 sensor learning the room's baseline"


def sound_kind(msg):
    """'TAP' / 'VOICE' if this message is a sound the mic heard, else None.
    A TAP *with* a count is a tap-back after a call, not an unprompted knock."""
    e = msg.get("e")
    if e == "VOICE" or (e == "TAP" and "taps" not in msg):
        return e
    if e == "SOUND":
        return msg.get("k")
    return None


def validate_command(cmd):
    """Check a base -> node command from the dashboard. Returns a clean dict
    or raises ValueError with a message safe to show the user."""
    if not isinstance(cmd, dict):
        raise ValueError("command must be a JSON object")
    t = cmd.get("t")
    n = _num(cmd.get("n"), int)
    if n is None or n < 0:
        raise ValueError("missing/bad node id 'n'")
    if t == "CALL":
        return {"t": "CALL", "n": n}
    if t == "MSG":
        txt = cmd.get("txt")
        txt = txt.strip() if isinstance(txt, str) else ""
        if not txt:
            raise ValueError("message is empty")
        if len(txt) > MSG_MAX:
            raise ValueError(f"message longer than {MSG_MAX} characters")
        return {"t": "MSG", "n": n, "txt": txt}
    if t == "ASK":
        q = cmd.get("q")
        if q not in QUESTIONS:
            raise ValueError("question must be 'injured' or 'count'")
        return {"t": "ASK", "n": n, "q": q}
    if t == "RESET":                       # "clear status": evidence + counters, keeps baselines
        return {"t": "RESET", "n": n}
    if t == "REBOOT":                      # "full reset": the node restarts cleanly
        return {"t": "REBOOT", "n": n, "keep_gps": cmd.get("keep_gps", True) is not False}
    if t == "DANGER":
        if not isinstance(cmd.get("on"), bool):
            raise ValueError("danger needs on: true or false")
        return {"t": "DANGER", "n": n, "on": cmd["on"]}
    if t == "SETLOC":
        lat, lon = _num(cmd.get("lat"), float), _num(cmd.get("lon"), float)
        if lat is None or not -90 <= lat <= 90:
            raise ValueError("latitude must be a number from -90 to 90")
        if lon is None or not -180 <= lon <= 180:
            raise ValueError("longitude must be a number from -180 to 180")
        label = cmd.get("label") if isinstance(cmd.get("label"), str) else ""
        return {"t": "SETLOC", "n": n, "lat": lat, "lon": lon, "label": label.strip()[:20]}
    raise ValueError("t must be CALL, MSG, ASK, DANGER, SETLOC, RESET or REBOOT")


def describe(msg, outgoing=False):
    """One plain-English line for the timeline (without time/node prefix)."""
    t = msg.get("t")
    if outgoing:
        if t == "CALL":
            return "Rescuer started a call (beep + listen for taps)"
        if t == "MSG":
            return f'Rescuer sent: "{msg.get("txt", "")}"'
        if t == "ASK":
            return {"injured": "Rescuer asked: are you injured?",
                    "count": "Rescuer asked: how many people?"}.get(msg.get("q"), "Rescuer asked a question")
        if t == "RESET":
            return "Rescuer cleared the node's status (false alarm)"
        if t == "REBOOT":
            return "Rescuer restarted the node" + ("" if msg.get("keep_gps", True) else " and cleared its GPS fix")
        if t == "DANGER":
            return "Rescuer turned the danger alarm " + ("ON (test)" if msg.get("on") else "off")
        if t == "SETLOC":
            label = f' "{msg["label"]}"' if msg.get("label") else ""
            return f"Rescuer set the location manually: {msg['lat']:.5f}, {msg['lon']:.5f}{label}"
        return f"Rescuer sent {t}"
    if t == "HB":
        return f"heartbeat ({msg.get('st', '?')})"
    if t == "SN":
        return "sensor readings"
    if t == "LOC":
        src = msg["src"]
        if src == "LIVE":
            return "Location: GPS fix" + (f" (±{msg['acc']} m est.)" if "acc" in msg else "")
        if src == "LAST":
            return "Location: GPS fix lost, using last known position"
        if src == "MANUAL":
            return "Location: set manually" + (f" ({msg['label']})" if msg.get("label") else "")
        return "Location: unknown (no GPS fix)"
    if t == "ST":
        why = msg.get("why")
        if msg["st"] == "UNRESPONSIVE" and why:
            return f"UNRESPONSIVE: {why[0].upper() + why[1:]}: possibly unconscious"
        return f"status is now {msg['st']}" + (f": {why}" if why else "")
    e = msg.get("e")
    if e == "BTN":
        stage = msg.get("stage")
        if stage == "first":
            return "Button pressed once — waiting for confirmation"
        if stage == "confirmed":
            return "SURVIVOR CONFIRMED: pressed twice"
        return "survivor pressed the PRESS ME button"
    if e == "STUCK":
        return "Button held down — possible debris"
    if e == "CO2":
        return describe_co2(msg)
    kind = sound_kind(msg)
    if kind:
        return "heard knocking" if kind == "TAP" else "heard a voice"
    if e == "TAP":
        taps = msg.get("taps")
        return f"tapped back {taps} times after call" if taps else "tapped back after call"
    if e == "CALL":
        return "Node called: 'tap 3 times'"
    if e == "RESP":
        if msg["kind"] == "VOICE":
            return "Survivor answered by voice"
        return f"Survivor tapped back {msg.get('taps', '')} times after call".replace("  ", " ")
    if e == "NORESP":
        calls = msg.get("calls")
        return f"No answer to call ({calls} in a row)" if calls else "No answer to call"
    if e == "BEEP":
        return {"PRESS_ME": "Node beeped: 'press the red button'", "AGAIN": "Node beeped: 'press again'",
                "CONFIRM": "Node played the confirm chime", "MESSAGE": "Node beeped for a message",
                }.get(msg.get("p"), f"Node beeped ({msg.get('p')})")
    if e == "DANGER":
        return "Danger alarm ON" if msg["on"] else "Danger alarm off"
    if e == "REBOOTING":
        return "Node restarting…"
    if e == "BOOT":
        parts = msg.get("sensors", {})
        missing = [k for k, ok in parts.items() if not ok]
        return "Node started" + (f" (missing: {', '.join(missing)})" if missing else " (all parts OK)")
    if e == "SHIFT":
        return "PILE MOVEMENT detected: clear the area"
    if e == "ANS":
        q, a = msg.get("q"), msg.get("a")
        if q == "injured":
            return f"survivor answered: injured = {a}"
        return f"survivor answered: {a} people"
    return f"event {e}"
