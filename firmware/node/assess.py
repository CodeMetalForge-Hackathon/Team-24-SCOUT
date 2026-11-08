NO_SIGNS     = "NO SIGNS"
POSSIBLE     = "POSSIBLE"
LIKELY       = "LIKELY"
CONFIRMED    = "CONFIRMED"
UNRESPONSIVE = "UNRESPONSIVE"

# what each sound kind is called on screen / dashboard
SOUND_REASON = {"TAP": "heard knocking", "VOICE": "heard voice"}
SOUND_WORD = {"TAP": "knocking", "VOICE": "voice"}
# CO2 levels that count as evidence (same as co2logic.EVIDENCE).
# ELEVATED (high but flat: room air) and NORMAL never change the status.
CO2_EVIDENCE = ("SUDDEN_RISE", "BREATHING", "SLOW_BUILDUP")


class Assessor:
    RESPONSE_WINDOW = 300   # a tap response stays "recent" for 5 min
    ACTIVITY_WINDOW = 15    # random taps/noise stay "recent" this long
    CONFIRM_WINDOW = 20     # 2nd button press must come within this many seconds
    COMBINE_WINDOW = 120    # CO2 + knock/voice within this many seconds = LIKELY

    def __init__(self, activity_window=None, response_window=None, confirm_window=None,
                 combine_window=None):
        if activity_window is not None:
            self.ACTIVITY_WINDOW = activity_window
        if response_window is not None:
            self.RESPONSE_WINDOW = response_window
        if confirm_window is not None:
            self.CONFIRM_WINDOW = confirm_window
        if combine_window is not None:
            self.COMBINE_WINDOW = combine_window
        self.reset()

    def reset(self):
        """Forget all evidence. Also called when the base sends RESET (false alarm).
        (The CO2 sensor keeps its baseline; only the evidence here is cleared.)"""
        self.confirmed_by = None        # what confirmed the survivor (button, answer)
        self.first_press = None         # time of a 1st button press waiting for a 2nd
        self.last_response = None       # time of last tap-back after a call
        self.last_activity = None       # time of last unprompted tap/noise
        self.activity_kind = None       # "TAP" / "VOICE" / None
        self.co2_level = None           # latest CO2 level from co2logic
        self.co2_rise = 0               # ppm
        self.co2_rise_secs = 0          # over this many seconds (minutes*60 for SLOW_BUILDUP)
        self.co2_rh_jump = 0            # humidity points
        self.co2_seen = None            # last time CO2 was evidence (SUDDEN_RISE/BREATHING/SLOW)
        self.unanswered_calls = 0
        self.danger = False

    # ---- evidence inputs: sensors call these ----
    def confirmed(self, source):
        self.confirmed_by = source

    def button_press(self, now):
        """A survivor must press TWICE to confirm, so one bump from falling
        debris can't create a false CONFIRMED. Returns "first" or "confirmed"."""
        if self.confirmed_by:
            return "confirmed"                       # already latched
        if self.awaiting_second(now):
            self.confirmed_by = "pressed button twice"
            return "confirmed"
        self.first_press = now
        return "first"

    def awaiting_second(self, now):
        return (self.confirmed_by is None and self.first_press is not None
                and now - self.first_press < self.CONFIRM_WINDOW)

    def tap_response(self, now):
        self.last_response = now
        self.unanswered_calls = 0

    def no_response(self):
        self.unanswered_calls += 1

    def activity(self, now, kind=None):
        self.last_activity = now
        self.activity_kind = kind

    def set_co2(self, level, rise, rise_secs, rh_jump, now):
        """Latest CO2 level. CO2 is supporting evidence only: it can make a
        node POSSIBLE (alone) or LIKELY (with a sound), never CONFIRMED."""
        self.co2_level = level
        self.co2_rise = rise
        self.co2_rise_secs = rise_secs
        self.co2_rh_jump = rh_jump
        if level in CO2_EVIDENCE:
            self.co2_seen = now

    def set_co2_rising(self, rising):          # older API, kept for compatibility
        self.set_co2("SUDDEN_RISE" if rising else "NORMAL", 0, 0, 0, 0)

    def set_danger(self, on):
        self.danger = on

    # ---- helpers ----
    def co2_up(self):
        return self.co2_level in CO2_EVIDENCE

    def _recent(self, t, now, window):
        return t is not None and now - t < window

    def _co2_reason(self):
        d, rh, secs = int(self.co2_rise), int(self.co2_rh_jump), int(self.co2_rise_secs)
        if self.co2_level == "BREATHING":
            return "breathing detected: CO2 +%d ppm, humidity +%d%%" % (d, rh)
        if self.co2_level == "SLOW_BUILDUP":
            return "slow CO2 build-up +%d ppm over %d min" % (d, int(secs / 60 + 0.5))
        return "sudden CO2 rise +%d ppm in %ds" % (d, secs)

    # ---- the rules, checked strongest first ----
    def evaluate(self, now):
        # 1. CONFIRMED: only a person can press twice. Latched.
        if self.confirmed_by:
            return CONFIRMED, self.confirmed_by

        # 2. LIKELY
        if self.awaiting_second(now):
            left = int(self.CONFIRM_WINDOW - (now - self.first_press) + 0.999)   # round up
            return LIKELY, "pressed once - press again (%ds)" % left
        if self._recent(self.last_response, now, self.RESPONSE_WINDOW):
            return LIKELY, "tapped back after call"
        # CO2 and a sound, both within COMBINE_WINDOW, and at least one still happening now
        if (self._recent(self.co2_seen, now, self.COMBINE_WINDOW)
                and self._recent(self.last_activity, now, self.COMBINE_WINDOW)
                and (self.co2_up() or self._recent(self.last_activity, now, self.ACTIVITY_WINDOW))):
            return LIKELY, "breathing + " + SOUND_WORD.get(self.activity_kind, "noise")

        # 3. UNRESPONSIVE: signs of a person, but they don't answer calls
        if self.co2_up() and self.unanswered_calls >= 2:
            return UNRESPONSIVE, "CO2 up, %d calls unanswered" % self.unanswered_calls

        # 4. POSSIBLE
        if self.co2_up():
            return POSSIBLE, self._co2_reason()
        if (self.first_press is not None
                and now - self.first_press < self.CONFIRM_WINDOW + self.ACTIVITY_WINDOW):
            return POSSIBLE, "single press, not confirmed"
        if self._recent(self.last_activity, now, self.ACTIVITY_WINDOW):
            return POSSIBLE, SOUND_REASON.get(self.activity_kind, "unprompted taps/noise")

        # 5.
        return NO_SIGNS, "no evidence yet"
