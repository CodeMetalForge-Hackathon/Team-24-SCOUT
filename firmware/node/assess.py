NO_SIGNS     = "NO SIGNS"
POSSIBLE     = "POSSIBLE"
LIKELY       = "LIKELY"
CONFIRMED    = "CONFIRMED"
UNRESPONSIVE = "UNRESPONSIVE"

class Assessor:
    RESPONSE_WINDOW = 300   # a tap response stays "recent" for 5 min
    ACTIVITY_WINDOW = 15   # random taps/noise stay "recent" for 2 min

    def __init__(self, activity_window=None, response_window=None):
        if activity_window is not None:
            self.ACTIVITY_WINDOW = activity_window
        if response_window is not None:
            self.RESPONSE_WINDOW = response_window
        self.reset()

    def reset(self):
        self.confirmed_by = None        # what confirmed the survivor (button, answer)
        self.last_response = None       # time of last tap-back after a call
        self.last_activity = None       # time of last unprompted tap/noise
        self.co2_rising = False
        self.unanswered_calls = 0
        self.danger = False

    # ---- evidence inputs: sensors call these ----
    def confirmed(self, source):
        self.confirmed_by = source

    def tap_response(self, now):
        self.last_response = now
        self.unanswered_calls = 0

    def no_response(self):
        self.unanswered_calls += 1

    def activity(self, now):
        self.last_activity = now

    def set_co2_rising(self, rising):
        self.co2_rising = rising

    def set_danger(self, on):
        self.danger = on

    # ---- the rules, checked strongest first ----
    def evaluate(self, now):
        if self.confirmed_by:
            return CONFIRMED, self.confirmed_by
        if self.last_response is not None and now - self.last_response < self.RESPONSE_WINDOW:
            return LIKELY, "tapped back after call"
        if self.co2_rising and self.unanswered_calls >= 2:
            return UNRESPONSIVE, f"CO2 up, {self.unanswered_calls} calls unanswered"
        if self.co2_rising:
            return POSSIBLE, "CO2 rising"
        if self.last_activity is not None and now - self.last_activity < self.ACTIVITY_WINDOW:
            return POSSIBLE, "unprompted taps/noise"
        return NO_SIGNS, "no evidence yet"