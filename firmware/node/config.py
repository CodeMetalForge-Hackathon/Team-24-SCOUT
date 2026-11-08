import board
import digitalio

NODE_ID = 1
DEMO_MODE = True
CALL_INTERVAL = 20 if DEMO_MODE else 60   # seconds between "calls"
ACTIVITY_WINDOW = 15 if DEMO_MODE else 120

# ---- turn sensors on/off ----
ENABLE_VIBRATION = False     # set True once the vibration sensor is wired to 3V/GND/5
ENABLE_SOUND = True

# ---- PRESS ME: built-in D1 button + built-in red LED ----
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