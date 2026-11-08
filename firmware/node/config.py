import board
import digitalio

NODE_ID = 1
DEMO_MODE = True
CALL_INTERVAL = 20 if DEMO_MODE else 60   # seconds between "calls"
ACTIVITY_WINDOW = 15 if DEMO_MODE else 120
HEARTBEAT_INTERVAL = 10 if DEMO_MODE else 30  # seconds between {"t":"HB"} report lines
ST_MIN_INTERVAL = 5          # while counting down, send an ST line at most every 5 s

# ---- accidental-press protection ----
ARMING_SECS = 10 if DEMO_MODE else 30   # ignore presses right after power-on (handling, placing)
CONFIRM_WINDOW = 20          # 2nd press within this many seconds = CONFIRMED
STUCK_SECS = 5               # held this long = debris on the button, not a person

# ---- turn sensors on/off ----
ENABLE_VIBRATION = False     # set True once the vibration sensor is wired to 3V/GND/5
ENABLE_SOUND = False

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

# ---- CO2 (used later) ----
CO2_BASELINE_SECS = 30 if DEMO_MODE else 120
CO2_RISE_PPM = 200