import time
import json
import sys
import supervisor  # type: ignore
import config
from ui import UI
from assess import Assessor, CONFIRMED
from sensors import Button, Vibration, SoundSensor

arm_until = time.monotonic() + config.ARMING_SECS   # presses before this are ignored

ui = UI(config.NODE_ID, config.PIN_SPEAKER)
brain = Assessor(activity_window=config.ACTIVITY_WINDOW, confirm_window=config.CONFIRM_WINDOW)

# Both buttons feed the SAME logic: Grove LED button (main) + built-in D1 (backup)
buttons = [
    Button(config.PIN_GROVE_BUTTON, config.GROVE_BUTTON_PULL, config.PIN_GROVE_LED,
           arm_until, config.STUCK_SECS),
    Button(config.PIN_BUTTON, config.BUTTON_PULL, config.PIN_BUTTON_LED,
           arm_until, config.STUCK_SECS),
]

vibration = Vibration(config.PIN_VIBRATION) if config.ENABLE_VIBRATION else None

sound = None
if config.ENABLE_SOUND:
    sound = SoundSensor(config.PIN_SOUND,
                        config.SOUND_TAP_THRESHOLD, config.SOUND_VOICE_THRESHOLD,
                        config.SOUND_WINDOW, config.TAP_MAX_WINDOWS,
                        config.VOICE_MIN_WINDOWS)


def report(**msg):
    """One JSON line for the base station. Over USB the dashboard reads these
    directly; later the radio sends the exact same text over LoRa.
    Normal debug prints have no { } so the dashboard ignores them."""
    msg["n"] = config.NODE_ID
    print(json.dumps(msg))


# ---------- commands from the base (USB serial now, LoRa later) ----------
cmd_buf = ""

def handle_command(line, now):
    start = line.find("{")
    if start < 0:
        return                                   # someone typing in the console
    try:
        cmd = json.loads(line[start:])
    except ValueError:
        print("bad command:", line)
        return
    if not isinstance(cmd, dict) or cmd.get("n", config.NODE_ID) != config.NODE_ID:
        return                                   # not for this node
    t = cmd.get("t")
    if t == "RESET":
        print("COMMAND: reset by base (false alarm)")
        brain.reset()                            # LED goes back to slow blink below
    elif t == "MSG":
        txt = str(cmd.get("txt", ""))[:60]
        print("COMMAND: message:", txt)
        ui.set_message(txt, now)
        ui.beep()
    else:
        print("COMMAND: not supported yet:", t)


def read_commands(now):
    """Non-blocking: only reads bytes that have already arrived, so the loop
    never waits. Ctrl-C is handled by CircuitPython before it reaches us."""
    global cmd_buf
    while supervisor.runtime.serial_bytes_available:
        ch = sys.stdin.read(1)
        if not ch:
            break
        if ch in "\r\n":
            line, cmd_buf = cmd_buf.strip(), ""
            if line:
                handle_command(line, now)
        elif len(cmd_buf) < 200:                 # ignore absurdly long junk
            cmd_buf += ch


print("Node", config.NODE_ID, "starting. Demo mode:", config.DEMO_MODE,
      "| presses ignored for", config.ARMING_SECS, "s")
last_shown = None
last_st = (None, None)
last_st_time = -100
next_hb = 0
stuck_until = 0
loudest = 0          # loudest sound level since the last heartbeat (shown on dashboard)

while True:
    now = time.monotonic()
    read_commands(now)

    # ---------- 1. SENSE ----------
    for b in buttons:
        ev = b.update(now)
        if ev == "STUCK":
            print("EVENT: button held >=", config.STUCK_SECS, "s - ignored (debris?)")
            report(t="EV", e="STUCK")
            stuck_until = now + 15
        elif ev == "PRESS":
            stage = brain.button_press(now)
            print("EVENT: PRESS ME", stage)
            report(t="EV", e="BTN", stage=stage)
            if stage == "first":
                ui.beep()
                ui.beep()
            else:
                ui.beep(dur=0.6)                 # long beep = confirmed

    if vibration and vibration.update(now):
        print("EVENT: vibration tap")
        brain.activity(now)

    if sound:
        ev = sound.update(now)
        if sound.level > loudest:
            loudest = sound.level
        if ev:
            print("EVENT: sound", ev)
            report(t="EV", e=ev)                 # "TAP" (knocking) or "VOICE"
            brain.activity(now, ev)              # unprompted sound = weak evidence -> POSSIBLE

    # ---------- 2. DECIDE ----------
    status, reason = brain.evaluate(now)
    waiting = brain.awaiting_second(now)

    # ---------- 3. SHOW (screen + LEDs) ----------
    led_mode = "on" if status == CONFIRMED else "fast" if waiting else "blink"
    for b in buttons:
        b.set_led(now, led_mode)

    if now < arm_until:
        note = "ARMING... %ds" % int(arm_until - now + 0.999)
    elif now < stuck_until:
        note = "button stuck?"
    else:
        note = ""
    prompt = "PRESS AGAIN!" if waiting else ""
    message = ui.message(now)
    shown = (status, reason, brain.danger, prompt, note, message)
    if shown != last_shown:
        ui.show(status, reason, brain.danger, prompt, note, message)
        last_shown = shown

    # ---------- 4. REPORT to base ----------
    # Status changes go out at once; reason-only changes (the countdown ticking)
    # at most every ST_MIN_INTERVAL s, to save radio airtime.
    if status != last_st[0] or (reason != last_st[1]
                                and now - last_st_time >= config.ST_MIN_INTERVAL):
        print("STATUS:", status, "|", reason)
        report(t="ST", st=status, why=reason)
        last_st = (status, reason)
        last_st_time = now

    if now >= next_hb:                           # "I'm alive" + latest readings
        hb = {"t": "HB", "st": status, "why": reason, "dg": int(brain.danger)}
        if sound:
            hb["snd"] = loudest
        report(**hb)
        loudest = 0
        next_hb = now + config.HEARTBEAT_INTERVAL

    time.sleep(0.005)    # tiny pause; button presses last far longer than this
