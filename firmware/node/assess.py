NO_SIGNS     = "NO SIGNS"
POSSIBLE     = "POSSIBLE"
LIKELY       = "LIKELY"
CONFIRMED    = "CONFIRMED"
UNRESPONSIVE = "UNRESPONSIVE"

# what each sound kind is called on screen / dashboard
SOUND_REASON = {"TAP": "heard knocking", "VOICE": "heard voice"}


class Assessor:
    RESPONSE_WINDOW = 300   # a tap response stays "recent" for 5 min
    ACTIVITY_WINDOW = 15    # random taps/noise stay "recent" this long
    CONFIRM_WINDOW = 20     # 2nd button press must come within this many seconds

    def __init__(self, activity_window=None, response_window=None, confirm_window=None):
        if activity_window is not None:
            self.ACTIVITY_WINDOW = activity_window
        if response_window is not None:
            self.RESPONSE_WINDOW = response_window
        if confirm_window is not None:
            self.CONFIRM_WINDOW = confirm_window
        self.reset()

    def reset(self):
        """Forget all evidence. Also called when the base sends RESET (false alarm)."""
        self.confirmed_by = None        # what confirmed the survivor (button, answer)
        self.first_press = None         # time of a 1st button press waiting for a 2nd
        self.last_response = None       # time of last tap-back after a call
        self.last_activity = None       # time of last unprompted tap/noise
        self.activity_kind = None       # "TAP" / "VOICE" / None
        self.co2_rising = False
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

    def set_co2_rising(self, rising):
        self.co2_rising = rising

    def set_danger(self, on):
        self.danger = on

    # ---- the rules, checked strongest first ----
    def evaluate(self, now):
        if self.confirmed_by:
            return CONFIRMED, self.confirmed_by
        if self.awaiting_second(now):
            left = int(self.CONFIRM_WINDOW - (now - self.first_press) + 0.999)   # round up
            return LIKELY, "pressed once - press again (%ds)" % left
        if self.last_response is not None and now - self.last_response < self.RESPONSE_WINDOW:
            return LIKELY, "tapped back after call"
        if self.co2_rising and self.unanswered_calls >= 2:
            return UNRESPONSIVE, "CO2 up, %d calls unanswered" % self.unanswered_calls
        if self.co2_rising:
            return POSSIBLE, "CO2 rising"
        if (self.first_press is not None
                and now - self.first_press < self.CONFIRM_WINDOW + self.ACTIVITY_WINDOW):
            return POSSIBLE, "single press, not confirmed"
        if self.last_activity is not None and now - self.last_activity < self.ACTIVITY_WINDOW:
            return POSSIBLE, SOUND_REASON.get(self.activity_kind, "unprompted taps/noise")
        return NO_SIGNS, "no evidence yet"
