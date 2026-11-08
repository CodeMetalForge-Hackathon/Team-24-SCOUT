import board
import digitalio

NODE_ID = 1

# "DEMO": tuned for breathing on the sensor at close range (live demo)
# "FIELD": tuned for real rubble: smaller, slower CO2 changes (see docs/co2.md)
PROFILE = "DEMO"
DEMO_MODE = PROFILE == "DEMO"
ACTIVITY_WINDOW = 15 if DEMO_MODE else 120   # unprompted knock/voice stays "recent" this long
RESPONSE_WINDOW = 60 if DEMO_MODE else 300   # a tap-back/voice after a call stays "recent" this long
HEARTBEAT_INTERVAL = 10 if DEMO_MODE else 30  # seconds between {"t":"HB"} report lines
ST_MIN_INTERVAL = 5          # while counting down, send an ST line at most every 5 s

# ---- accidental-press protection ----
ARMING_SECS = 10 if DEMO_MODE else 30   # ignore presses right after power-on (handling, placing)
CONFIRM_WINDOW = 20          # 2nd press within this many seconds = CONFIRMED
STUCK_SECS = 5               # held this long = debris on the button, not a person

# ---- turn sensors on/off ----
ENABLE_VIBRATION = False     # set True once the vibration sensor is wired to 3V/GND/5
ENABLE_SOUND = True
ENABLE_CO2 = True            # SCD41 on the SEN-25200 via STEMMA QT / I2C
ENABLE_TILT = False          # tilt switch on D6 -> DANGER siren (not wired yet)

# ---- call-and-listen ----
CALL_INTERVAL = 20 if DEMO_MODE else 60   # seconds from the end of one call to the next
LISTEN_SECS = 10             # listen this long after the 3 call beeps
RESPONSE_MIN_TAPS = 2        # this many knocks (or any voice) while listening = an answer
PROMPT_SECS = 10             # "WE HEAR YOU. PRESS THE RED BUTTON" stays this long
MESSAGE_SECS = 15            # rescuer message stays on screen this long
DANGER_REPEAT = 10           # replay the siren every N s while DANGER is on
TILT_HOLD = 30               # tilt switch moved -> DANGER for this long

# ---- speaker (Grove Speaker on D9, powered from USB 5V; loudest ~3000 Hz) ----
BASE_FREQ = 3000
MIC_TAIL = 0.6               # keep the microphone muted this long after any beep (echo)
# Each pattern is a list of steps: ("tone", Hz, s), ("rest", s), ("slide", from_Hz, to_Hz, s)
BEEPS = {
    "CALL":     [("tone", 3000, 0.35), ("rest", 0.35), ("tone", 3000, 0.35), ("rest", 0.35),
                 ("tone", 3000, 0.35)],                                   # 3 beeps: "knock 3x"
    "PRESS_ME": [("tone", 3000, 0.9)],                                    # long: press the button
    "AGAIN":    [("tone", 3300, 0.12), ("rest", 0.1), ("tone", 3300, 0.12)],   # quick double
    "CONFIRM":  [("tone", 2300, 0.2), ("tone", 2800, 0.2), ("tone", 3300, 0.45)],  # rising chime
    "MESSAGE":  [("tone", 3000, 0.6)],
    "DANGER":   [("slide", 1500, 3800, 0.6), ("slide", 3800, 1500, 0.6)] * 3,  # wailing siren
}
# a lower-priority pattern never interrupts a higher one
BEEP_PRIORITY = {"DANGER": 6, "CONFIRM": 5, "AGAIN": 4, "PRESS_ME": 3, "MESSAGE": 2, "CALL": 1}

# ---- PRESS ME (main): Grove Red LED Button ----
PIN_GROVE_BUTTON  = board.A3
GROVE_BUTTON_PULL = digitalio.Pull.UP      # reads True when idle, False when pressed
PIN_GROVE_LED     = board.A2               # True = LED on

# ---- PRESS ME (backup): built-in D1 button + built-in red LED ----
PIN_BUTTON     = board.D1
BUTTON_PULL    = digitalio.Pull.DOWN       # D1 reads True when pressed
PIN_BUTTON_LED = board.LED

# ---- sensor pins ----
PIN_VIBRATION  = board.D5
PIN_SPEAKER    = board.D9
PIN_SOUND      = board.A0
PIN_TILT       = board.D6

# ---- sound ----
SOUND_TAP_THRESHOLD = 22000     # very loud + short = TAP (knock)
SOUND_VOICE_THRESHOLD = 14000   # lower: moderately loud but sustained = VOICE
                                # set just ABOVE your quiet-room "loudest slice" value
SOUND_WINDOW = 0.03             # 30 ms slices
TAP_MAX_WINDOWS = 4             # TAP lasts <= 4 slices (~0.12 s)
VOICE_MIN_WINDOWS = 6           # VOICE lasts >= 6 slices (~0.2 s)

# ---- CO2 (SCD41) ----
# Measured (tests/RESULTS.md): room ~810 ppm (drifts as the room fills); breath at
# close range +150 ppm within 5 s, +380 within 10 s, peak +460 after 30 s, slow
# decay; humidity 48% -> 54%.
# Only a SUDDEN rise counts as evidence (see firmware/node/co2logic.py, docs/co2.md).
if PROFILE == "DEMO":
    CO2_BASELINE_SECS = 30       # learn the room for this long (for display / ELEVATED)
    CO2_RATE_WINDOW = 15         # sudden rise = CO2 now minus the lowest value in the last 15 s...
    CO2_SUDDEN_PPM = 120         # ...is at least this much, on 2 readings in a row
    CO2_EVENT_HOLD = 60          # a sudden rise stays evidence for this many seconds
    RH_RISE_POINTS = 3           # humidity jumped this much too = BREATHING
    CO2_BASELINE_FOLLOW = 0.02   # how fast the baseline follows the room
    SLOW_BUILDUP_ENABLED = False # the demo room drifts; no slow-trend rule
    CO2_TREND_MINUTES = 15       # (unused in DEMO)
    CO2_TREND_PPM = 60
else:  # FIELD
    CO2_BASELINE_SECS = 120
    CO2_RATE_WINDOW = 60
    CO2_SUDDEN_PPM = 40
    CO2_EVENT_HOLD = 600
    RH_RISE_POINTS = 3
    CO2_BASELINE_FOLLOW = 0.001  # nearly fixed baseline
    SLOW_BUILDUP_ENABLED = True  # real voids fill slowly
    CO2_TREND_MINUTES = 15
    CO2_TREND_PPM = 60

COMBINE_WINDOW = 120   # CO2 + a knock/voice within this many seconds = LIKELY