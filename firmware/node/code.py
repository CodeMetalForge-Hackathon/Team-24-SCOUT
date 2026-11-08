import time
import json
import sys
import supervisor  # type: ignore
import config
from ui import UI
from assess import Assessor, CONFIRMED
from sensors import Button, Vibration, SoundSensor, CO2Sensor

arm_until = time.monotonic() + config.ARMING_SECS   # presses before this are ignored

ui = UI(config.NODE_ID, config.PIN_SPEAKER)
brain = Assessor(activity_window=config.ACTIVITY_WINDOW, confirm_window=config.CONFIRM_WINDOW,
                 combine_window=config.COMBINE_WINDOW)

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

co2 = None
if config.ENABLE_CO2:
    try:
        import board  # type: ignore
        co2 = CO2Sensor(board.I2C(), config)
    except Exception as e:           # board.I2C() fails if nothing is on the I2C wires
        print("I2C not available (", e, ") - continuing without CO2")
    if co2 is not None and not co2.ok:
        co2 = None


def signed(x):
    """+12 / -3 (written out, so we don't rely on '%+d' support)."""
    x = int(x)
    return ("+%d" if x >= 0 else "%d") % x


def co2_note(now):
    """Short CO2 text for the screen header."""
    if co2 is None:
        return ""
    if co2.level == "LEARNING":
        return "CO2 learning %ds" % int(co2.levels.learning_left(now) + 0.999)
    watch = " watch" if co2.level == "SLIGHT_RISE" else ""
    return "CO2 %d (%s) RH%s%s" % (co2.ppm, signed(co2.delta), signed(co2.rh_delta), watch)


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


print("Node", config.NODE_ID, "starting. Profile:", config.PROFILE,
      "| presses ignored for", config.ARMING_SECS, "s")
last_co2_level = None
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

    if co2 and co2.update(now):                  # True only when a new reading (~every 5 s)
        print("CO2: %d ppm (%s)  RH %.0f%% (%s)  %s"
              % (co2.ppm, signed(co2.delta), co2.rh, signed(co2.rh_delta), co2.level))
        brain.set_co2(co2.level, co2.delta, co2.rh_delta, now, co2.levels.trend_min)
        if co2.level != last_co2_level:          # tell the base only when the level changes
            print("EVENT: CO2", last_co2_level, "->", co2.level)
            ev = {"t": "EV", "e": "CO2", "lvl": co2.level, "prev": last_co2_level,
                  "delta": int(co2.delta), "rh_delta": int(co2.rh_delta)}
            if co2.baseline is not None:
                ev["base"] = int(co2.baseline)
            if co2.level == "SLOW_BUILDUP":
                ev["min"] = co2.levels.trend_min
            report(**ev)
            last_co2_level = co2.level

    # ---------- 2. DECIDE ----------
    status, reason = brain.evaluate(now)
    waiting = brain.awaiting_second(now)

    # ---------- 3. SHOW (screen + LEDs) ----------
    led_mode = "on" if status == CONFIRMED else "fast" if waiting else "blink"
    for b in buttons:
        b.set_led(now, led_mode)

    if now < arm_until:
        note = "ARMING %ds " % int(arm_until - now + 0.999)
    elif now < stuck_until:
        note = "STUCK? "
    else:
        note = ""
    note += co2_note(now)
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
        hb["prof"] = config.PROFILE
        if co2 and co2.ppm is not None:
            hb["co2"] = co2.ppm
            hb["rh"] = round(co2.rh, 1)
            hb["lvl"] = co2.level
            if co2.baseline is not None:
                hb["base"] = int(co2.baseline)
                hb["rh_base"] = round(co2.rh_baseline, 1)
        report(**hb)
        loudest = 0
        next_hb = now + config.HEARTBEAT_INTERVAL

    time.sleep(0.005)    # tiny pause; button presses last far longer than this
