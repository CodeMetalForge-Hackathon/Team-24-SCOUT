import time
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

print("Node", config.NODE_ID, "starting. Demo mode:", config.DEMO_MODE)
last_shown = None

while True:
    now = time.monotonic()

    # ---------- 1. SENSE ----------
    if button.update(now):
        print("EVENT: PRESS ME")
        brain.confirmed("pressed PRESS ME")
        button.mode = "on"
        ui.beep()
        ui.beep()

    if vibration and vibration.update(now):
        print("EVENT: vibration tap")
        brain.activity(now)

    if sound:
        ev = sound.update(now)
        if ev:
            print("EVENT: sound", ev)
            brain.activity(now)            # unprompted tap/voice = weak evidence -> POSSIBLE

    # ---------- 2. DECIDE ----------
    status, reason = brain.evaluate(now)

    # ---------- 3. SHOW ----------
    if (status, reason, brain.danger) != last_shown:
        ui.show(status, reason, brain.danger)
        print("STATUS:", status, "|", reason)
        last_shown = (status, reason, brain.danger)