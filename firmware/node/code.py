import time
import json
import config
from ui import UI
from assess import Assessor
from sensors import Button, Vibration, SoundSensor

ui = UI(config.NODE_ID, config.PIN_SPEAKER)
brain = Assessor(activity_window=config.ACTIVITY_WINDOW)
button = Button(config.PIN_BUTTON, config.PIN_BUTTON_LED, config.BUTTON_PULL)

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


print("Node", config.NODE_ID, "starting. Demo mode:", config.DEMO_MODE)
last_shown = None
next_hb = 0
loudest = 0          # loudest sound level since the last heartbeat (shown on dashboard)

while True:
    now = time.monotonic()

    # ---------- 1. SENSE ----------
    if button.update(now):
        print("EVENT: PRESS ME")
        report(t="EV", e="BTN")
        brain.confirmed("pressed PRESS ME")
        button.mode = "on"
        ui.beep()
        ui.beep()

    if vibration and vibration.update(now):
        print("EVENT: vibration tap")
        brain.activity(now)

    if sound:
        ev = sound.update(now)
        if sound.level > loudest:
            loudest = sound.level
        if ev:
            print("EVENT: sound", ev)
            report(t="EV", e="SOUND", k=ev)   # k = "TAP" (knock) or "VOICE"
            brain.activity(now)            # unprompted tap/voice = weak evidence -> POSSIBLE

    # ---------- 2. DECIDE ----------
    status, reason = brain.evaluate(now)

    # ---------- 3. SHOW ----------
    if (status, reason, brain.danger) != last_shown:
        ui.show(status, reason, brain.danger)
        print("STATUS:", status, "|", reason)
        report(t="ST", st=status, why=reason)
        last_shown = (status, reason, brain.danger)

    # ---------- 4. HEARTBEAT: "I'm alive" + latest readings ----------
    if now >= next_hb:
        report(t="HB", st=status, why=reason, dg=int(brain.danger), snd=loudest)
        loudest = 0
        next_hb = now + config.HEARTBEAT_INTERVAL
