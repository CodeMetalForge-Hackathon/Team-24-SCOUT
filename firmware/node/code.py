import time
import json
import sys
import supervisor  # type: ignore
import config
from ui import UI
from assess import Assessor, CONFIRMED, CO2_EVIDENCE
from sensors import Button, Vibration, SoundSensor, CO2Sensor, Battery
from beeper import BeepPlayer
from caller import Caller, CALLING, LISTENING
from location import Location, GPSReader, header_tag

boot = time.monotonic()
arm_until = boot + config.ARMING_SECS   # presses before this are ignored

ui = UI(config.NODE_ID)
# arm_until: for the first ARMING_SECS the brain ignores every input (sensors settle)
brain = Assessor(activity_window=config.ACTIVITY_WINDOW, response_window=config.RESPONSE_WINDOW,
                 confirm_window=config.CONFIRM_WINDOW, combine_window=config.COMBINE_WINDOW,
                 arm_until=arm_until)
beeper = BeepPlayer(config.PIN_SPEAKER, config.BASE_FREQ, config.BEEPS, config.BEEP_PRIORITY,
                    config.MIC_TAIL)
caller = Caller(config.CALL_INTERVAL, config.LISTEN_SECS, config.RESPONSE_MIN_TAPS,
                first_call_at=max(arm_until, boot + config.CALL_INTERVAL))

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
    try:
        sound = SoundSensor(config.PIN_SOUND,
                            config.SOUND_TAP_THRESHOLD, config.SOUND_VOICE_THRESHOLD,
                            config.SOUND_WINDOW, config.TAP_MAX_WINDOWS,
                            config.VOICE_MIN_WINDOWS)
    except Exception as e:
        print("Sound sensor not available (", e, ") - continuing without it")

# I2C bus (STEMMA QT): CO2 sensor, battery gauge, GPS. board.I2C() fails if
# nothing is plugged in (no pull-up resistors), so everything on it is optional.
i2c = None
try:
    import board  # type: ignore
    i2c = board.I2C()
except Exception as e:
    print("I2C not available (", e, ") - continuing without CO2 / GPS / battery")

co2 = None
if config.ENABLE_CO2 and i2c is not None:
    co2 = CO2Sensor(i2c, config)
    if not co2.ok:
        co2 = None

battery = Battery(i2c) if i2c is not None else None

# ---------- location: GPS on the same I2C bus, last fix saved in flash ----------
loc = Location(live_secs=config.GPS_LIVE_SECS)
gps = GPSReader(i2c) if (config.ENABLE_GPS and i2c is not None) else None
nvm = None
microcontroller = None
try:
    import microcontroller  # type: ignore
    nvm = microcontroller.nvm
    if loc.load_saved(nvm):
        print("Last known location from flash:", loc.fix["lat"], loc.fix["lon"], "(age unknown)")
except Exception as e:
    print("No flash storage for location (", e, ")")

tilt = None
if config.ENABLE_TILT:
    try:
        import digitalio  # type: ignore
        tilt = digitalio.DigitalInOut(config.PIN_TILT)
        tilt.direction = digitalio.Direction.INPUT
        tilt.pull = digitalio.Pull.UP
    except Exception as e:
        print("Tilt switch not available (", e, ")")
        tilt = None


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
    tag = {"SUDDEN_RISE": " JUMP", "BREATHING": " BREATH", "SLOW_BUILDUP": " SLOW",
           "ELEVATED": " high"}.get(co2.level, "")
    return "CO2 %d (%s)%s" % (co2.ppm, signed(co2.ppm - co2.baseline), tag)


MAX_MSG = 200   # bytes: keep every message small enough for one LoRa packet


def report(**msg):
    """One JSON line for the base station. Over USB the dashboard reads these
    directly; later the radio sends the exact same text over LoRa.
    Normal debug prints have no { } so the dashboard ignores them."""
    msg["n"] = config.NODE_ID
    try:
        line = json.dumps(msg, separators=(",", ":"))   # no spaces: fewer bytes
    except TypeError:                                   # older json without separators=
        line = json.dumps(msg)
    if len(line) > MAX_MSG:
        print("WARNING: message is", len(line), "bytes (> %d)" % MAX_MSG)
    print(line)


def beep(name, now):
    """Start a beep pattern; tell the base about the meaningful ones."""
    if beeper.play(name, now) and name not in ("CALL", "DANGER"):
        report(t="EV", e="BEEP", p=name)       # CALL / DANGER have their own events


# ---------- screen prompts for the survivor ----------
prompt_text = ""        # temporary prompt (e.g. "WE HEAR YOU...")
prompt_until = 0
message_text = ""       # rescuer message
message_until = 0

# ---------- DANGER (pile moving): command from the base or tilt switch ----------
danger_cmd = False      # rescuer switched the alarm on
tilt_until = 0          # tilt switch moved recently
tilt_last = None
next_siren = 0


def set_danger_cmd(on, now):
    global danger_cmd, next_siren
    danger_cmd = on
    next_siren = now                            # siren right away when switched on


# ---------- commands from the base (USB serial now, LoRa later) ----------
cmd_buf = ""


def handle_command(line, now):
    global message_text, message_until, prompt_until, mic_was_ignored
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
        # "Clear status": forget all evidence and counters, keep sensor baselines
        print("COMMAND: status cleared by base (false alarm)")
        brain.reset("status cleared by base")   # NO SIGNS; LEDs go back to slow blink below
        caller.reset(now)                        # IDLE, next call one interval away
        beeper.silence(now)                      # stop any beep and un-mute the mic now
        if sound:
            sound.reset_run()
        mic_was_ignored = False
        prompt_until = 0
        message_until = 0
    elif t == "REBOOT":
        # "Full reset": a clean restart (arming, CO2 baseline re-learned, BOOT sent)
        keep_gps = cmd.get("keep_gps", True) is not False
        print("COMMAND: reboot by base, keep GPS fix:", keep_gps)
        report(t="EV", e="REBOOTING")
        time.sleep(0.3)                          # let the reply get out before we restart
        if not keep_gps and nvm is not None:
            try:
                nvm[0] = 0                       # mark the saved last fix invalid
                print("Saved GPS fix cleared")
            except Exception as e:
                print("Could not clear the saved fix (", e, ")")
        beeper.silence(now)
        if microcontroller is not None:
            microcontroller.reset()              # never returns
        print("REBOOT not possible here (no microcontroller module)")
    elif t == "MSG":
        message_text = str(cmd.get("txt", ""))[:60]
        message_until = now + config.MESSAGE_SECS
        print("COMMAND: message:", message_text)
        beep("MESSAGE", now)
    elif t == "CALL":
        print("COMMAND: call now")
        caller.request()                         # starts as soon as it's allowed
    elif t == "DANGER":
        on = bool(cmd.get("on"))
        print("COMMAND: danger alarm", "ON" if on else "off")
        set_danger_cmd(on, now)
    elif t == "SETLOC":
        try:
            lat, lon = float(cmd.get("lat")), float(cmd.get("lon"))
        except (TypeError, ValueError):
            print("COMMAND: bad SETLOC", cmd)
            return
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            loc.set_manual(now, lat, lon, str(cmd.get("label", ""))[:20])
            print("COMMAND: manual location", lat, lon, cmd.get("label", ""))
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


# ---------- startup self-check: which parts answered? ----------
sensors_ok = {"sound": sound is not None, "co2": co2 is not None, "gps": bool(gps and gps.ok),
              "button": True, "speaker": beeper.pwm is not None}
print("SELF-CHECK:", " ".join("%s=%s" % (k, "ok" if v else "MISSING") for k, v in sensors_ok.items()))
report(t="EV", e="BOOT", sensors=sensors_ok)

print("Node", config.NODE_ID, "starting. Profile:", config.PROFILE,
      "| presses ignored for", config.ARMING_SECS, "s | first call in",
      int(caller.next_call - boot), "s")
last_co2_level = None
last_shown = None
last_st = (None, None)
last_st_time = -100
last_danger = False
next_hb = 0
next_loc = 0
last_loc_src = None
stuck_until = 0
mic_was_ignored = False
loudest = 0          # loudest sound level since the last heartbeat (shown on dashboard)

while True:
    now = time.monotonic()
    read_commands(now)
    beeper.update(now)                           # advance the beep pattern (never blocks)

    # ---------- 1. SENSE ----------
    for b in buttons:
        ev = b.update(now)
        if ev == "STUCK":
            print("EVENT: button held >=", config.STUCK_SECS, "s - ignored (debris?)")
            report(t="EV", e="STUCK")
            stuck_until = now + 15
        elif ev == "PRESS":
            caller.cancel(now)                   # a press during a call: button logic takes over
            stage = brain.button_press(now)      # also resets the unanswered-call counter
            if stage is None:                    # (can't happen: Button ignores arming presses)
                continue
            print("EVENT: PRESS ME", stage)
            report(t="EV", e="BTN", stage=stage)
            beep("AGAIN" if stage == "first" else "CONFIRM", now)

    if vibration and vibration.update(now):
        print("EVENT: vibration tap")
        brain.activity(now, "TAP")

    # Microphone: muted while the speaker beeps (+ MIC_TAIL), so our own beeps
    # never count as knocks, and ignored while ARMING (the input is still settling
    # and the display/speaker start-up clicks look like knocks). We still sample it
    # to keep its state consistent.
    muted = beeper.playing or now < beeper.quiet_until
    ignore_mic = muted or now < arm_until
    if sound:
        if mic_was_ignored and not ignore_mic:
            sound.reset_run()                    # a sound that began while ignored doesn't count
        ev = sound.update(now)
        if ignore_mic:
            ev = None
        elif sound.level > loudest:
            loudest = sound.level
        if ev:
            if caller.state == LISTENING:        # an answer to our call, not unprompted activity
                print("EVENT: sound", ev, "while listening")
                if caller.heard(ev, now) == "RESP":
                    taps = caller.taps
                    print("EVENT: call answered by", ev, "(taps:", taps, ")")
                    resp = {"t": "EV", "e": "RESP", "kind": ev}
                    if ev == "TAP":
                        resp["taps"] = taps
                    report(**resp)
                    brain.call_response(now, ev, taps)
                    beep("PRESS_ME", now)
                    prompt_text = "WE HEAR YOU. PRESS THE RED BUTTON"
                    prompt_until = now + config.PROMPT_SECS
            else:
                print("EVENT: sound", ev)
                report(t="EV", e=ev)             # "TAP" (knocking) or "VOICE"
                brain.activity(now, ev)          # unprompted sound = weak evidence -> POSSIBLE
    mic_was_ignored = ignore_mic

    if gps:
        gps.update(now, loc)                     # at most one NMEA sentence, 4x per second
        if nvm is not None and loc.should_save(now):
            try:
                nvm[0:len(loc.to_saved())] = loc.to_saved()
                loc.mark_saved(now)
                print("Location saved to flash")
            except Exception as e:
                print("Saving location failed (", e, ")")

    if co2 and co2.update(now):                  # True only when a new reading (~every 5 s)
        base = " base %d" % co2.baseline if co2.baseline is not None else ""
        print("CO2: %d ppm  RH %.0f%%%s  %s" % (co2.ppm, co2.rh, base, co2.level))
        brain.set_co2(co2.level, co2.rise, co2.rise_secs, co2.rh_jump, now)
        if co2.level != last_co2_level:          # tell the base only when the level changes
            print("EVENT: CO2", last_co2_level, "->", co2.level)
            ev = {"t": "EV", "e": "CO2", "lvl": co2.level, "prev": last_co2_level}
            if co2.level in CO2_EVIDENCE:
                ev["rise"] = int(co2.rise)
                ev["secs"] = co2.rise_secs
                ev["rh_jump"] = int(co2.rh_jump)
                ev["hold"] = int(co2.levels.hold_left(now))   # seconds of evidence left
            if co2.baseline is not None:
                ev["base"] = int(co2.baseline)
            report(**ev)
            last_co2_level = co2.level

    if tilt is not None:
        v = tilt.value
        if tilt_last is not None and v != tilt_last:   # the switch moved: the pile shifted
            print("EVENT: tilt switch moved")
            if now >= tilt_until:
                next_siren = now
            tilt_until = now + config.TILT_HOLD
        tilt_last = v

    # ---------- DANGER: siren on top of everything, replayed every DANGER_REPEAT s ----------
    danger = danger_cmd or now < tilt_until
    if danger != last_danger:
        brain.set_danger(danger)
        report(t="EV", e="DANGER", on=danger)
        print("EVENT: DANGER", "ON" if danger else "off")
        if not danger and beeper.name == "DANGER":
            beeper.stop(now)
        last_danger = danger
    if danger and now >= next_siren:
        beeper.play("DANGER", now)
        next_siren = now + config.DANGER_REPEAT

    # ---------- 2. DECIDE ----------
    status, reason = brain.evaluate(now)
    waiting = brain.awaiting_second(now)

    # ---------- call-and-listen ----------
    allowed = (now >= arm_until and status != CONFIRMED and not waiting
               and beeper.name != "DANGER")
    audio_done = beeper.name != "CALL" and now >= beeper.quiet_until
    step = caller.update(now, allowed, audio_done)
    if step == "CALL":
        print("EVENT: calling (3 beeps)")
        report(t="EV", e="CALL")
        beep("CALL", now)
    elif step == "LISTEN":
        print("EVENT: listening for", config.LISTEN_SECS, "s")
    elif step == "NORESP":
        brain.no_response(now)
        print("EVENT: no answer to call (%d in a row)" % brain.unanswered_calls)
        report(t="EV", e="NORESP", calls=brain.unanswered_calls)
    status, reason = brain.evaluate(now)         # again: the call step may have changed it

    # ---------- 3. SHOW (screen + LEDs) ----------
    listening = caller.state == LISTENING
    led_mode = "on" if status == CONFIRMED else "fast" if (waiting or listening) else "blink"
    for b in buttons:
        b.set_led(now, led_mode)

    # what to tell the survivor, most important first
    if danger:
        prompt = "!! DANGER !! CLEAR THE AREA"
    elif now < message_until:
        prompt = message_text
    elif status == CONFIRMED:
        prompt = "RESCUERS NOTIFIED. HELP IS COMING"
    elif waiting:
        prompt = "PRESS AGAIN TO CONFIRM (%ds)" % int(brain.CONFIRM_WINDOW - (now - brain.first_press) + 0.999)
    elif now < prompt_until:
        prompt = prompt_text
    elif caller.state == CALLING:
        prompt = "TAP 3 TIMES IF YOU HEAR THIS"
    elif listening:
        prompt = "TAP 3 TIMES NOW LISTENING %ds" % int(caller.listen_left(now) + 0.999)
    else:
        prompt = ""

    where = loc.best(now)
    note = "MIC OFF " if (sound and muted) else ""
    if now < arm_until:
        note += "ARMING %ds " % int(arm_until - now + 0.999)
    elif now < stuck_until:
        note += "STUCK? "
    note += header_tag(where) + " " + co2_note(now)
    shown = (status, reason, danger, prompt, note)
    if shown != last_shown:
        ui.show(status, reason, danger, prompt, note)
        last_shown = shown

    # ---------- 4. REPORT to base ----------
    # Status changes go out at once; reason-only changes (countdowns, CO2 numbers)
    # at most every ST_MIN_INTERVAL s, to save radio airtime.
    if status != last_st[0] or (reason != last_st[1]
                                and now - last_st_time >= config.ST_MIN_INTERVAL):
        print("STATUS:", status, "|", reason)
        report(t="ST", st=status, why=reason, ev=brain.evidence(now))
        last_st = (status, reason)
        last_st_time = now

    # location in its own small message: every LOC_INTERVAL s and when the source changes
    if now >= next_loc or where["src"] != last_loc_src:
        msg = {"t": "LOC", "src": where["src"]}
        if where["src"] != "NONE":
            msg["lat"] = round(where["lat"], 5)          # 5 decimals = ~1 m
            msg["lon"] = round(where["lon"], 5)
            msg["age"] = where["age"]
            for k in ("acc", "sats", "label"):
                if where.get(k) is not None and where.get(k) != "":
                    msg[k] = where[k]
        report(**msg)
        if where["src"] != last_loc_src:
            print("LOCATION:", header_tag(where))
        last_loc_src = where["src"]
        next_loc = now + config.LOC_INTERVAL

    if battery:
        battery.update(now)
    if now >= next_hb:
        # "I'm alive": status + evidence + call cycle (short keys: one LoRa packet)
        report(t="HB", st=status, ev=brain.evidence(now), cs=caller.state,
               ll=int(caller.listen_left(now)), nc=int(caller.next_call_in(now)),
               mic="muted" if muted else "on", prof=config.PROFILE)
        # latest sensor readings, in their own small message
        sn = {"t": "SN"}
        if sound:
            sn["snd"] = loudest
        if battery and battery.percent is not None:
            sn["bat"] = battery.percent
        if co2 and co2.ppm is not None:
            sn["co2"] = co2.ppm
            sn["rh"] = round(co2.rh, 1)
            sn["lvl"] = co2.level
            hold = int(co2.levels.hold_left(now))
            if hold:
                sn["hold"] = hold                  # lets the dashboard count down the evidence
            if co2.baseline is not None:
                sn["base"] = int(co2.baseline)
                sn["rhb"] = round(co2.rh_baseline, 1)
        report(**sn)
        loudest = 0
        next_hb = now + config.HEARTBEAT_INTERVAL

    time.sleep(0.005)    # tiny pause; button presses last far longer than this
