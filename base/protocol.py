"""Message protocol: parse node -> base lines, validate base -> node commands,
and turn messages into plain-English timeline text.

Radio links are noisy and the receiver Feather also prints its own debug text,
so the parser NEVER raises: it returns (msg, None) or (None, "why it was rejected").
"""
import json

STATUSES = ("NO SIGNS", "POSSIBLE", "LIKELY", "CONFIRMED", "UNRESPONSIVE")
EVENTS = ("BTN", "TAP", "VOICE", "STUCK", "NORESP", "SHIFT", "ANS", "SOUND", "CO2")
CO2_LEVELS = ("LEARNING", "NORMAL", "ELEVATED", "SUDDEN_RISE", "BREATHING", "SLOW_BUILDUP")
CO2_EVIDENCE = ("SUDDEN_RISE", "BREATHING", "SLOW_BUILDUP")   # ELEVATED/NORMAL are info only
PROFILES = ("DEMO", "FIELD")
SOUND_KINDS = ("TAP", "VOICE")   # SOUND event (older nodes): knock vs. sustained voice
BTN_STAGES = ("first", "confirmed")
QUESTIONS = ("injured", "count")
MSG_MAX = 60  # node screen fits ~60 characters

# Optional numeric fields: name -> type. Bad values are dropped, not fatal.
_NUMERIC = {"co2": float, "base": float, "bat": float, "rssi": float,
            "dg": int, "taps": int, "calls": int,
            "snd": int,   # loudest sound level since the last heartbeat (0..65535)
            "rh": float, "rh_base": float,          # humidity % now / baseline
            "rise": float, "secs": int,             # CO2 event: sudden rise (ppm) in N seconds
            "rh_jump": float,                       # CO2 event: humidity jump (points)
            "hold": int}                            # seconds of CO2 evidence left
# Optional text fields: name -> allowed values. Unknown values are dropped.
_CHOICES = {"lvl": CO2_LEVELS, "prev": CO2_LEVELS, "prof": PROFILES}


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
    if t not in ("HB", "ST", "EV"):
        return None, f"unknown type {t!r}"
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

    st = raw.get("st")
    if st is not None:
        if st not in STATUSES:
            return None, f"unknown status {st!r}"
        msg["st"] = st
    elif t == "ST":
        return None, "ST message without 'st'"
    if isinstance(raw.get("why"), str):
        msg["why"] = raw["why"][:120]

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
    if t == "RESET":
        return {"t": "RESET", "n": n}
    raise ValueError("t must be CALL, MSG, ASK or RESET")


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
            return "Rescuer reset the node (false alarm)"
        return f"Rescuer sent {t}"
    if t == "HB":
        return f"heartbeat ({msg.get('st', '?')})"
    if t == "ST":
        why = msg.get("why")
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
    if e == "NORESP":
        calls = msg.get("calls")
        return f"no answer to call ({calls} unanswered)" if calls else "no answer to call"
    if e == "SHIFT":
        return "PILE MOVEMENT detected: clear the area"
    if e == "ANS":
        q, a = msg.get("q"), msg.get("a")
        if q == "injured":
            return f"survivor answered: injured = {a}"
        return f"survivor answered: {a} people"
    return f"event {e}"
