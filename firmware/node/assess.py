"""The node's decision logic: evidence in, one STATUS + reason out.

While ARMING (the first ARMING_SECS after power-on) the status is NO SIGNS
"starting up (arming Ns)" and every input is ignored: sensors settle, nothing
is recorded. Times are None for "never" (not 0: monotonic() starts near 0).

Rules are checked strongest first; the first one that matches wins:
  1. CONFIRMED     button pressed twice (latched until reset)
  2. LIKELY        a. 1st press, waiting for the 2nd (countdown)
                   b. answered a call (knocked back / voice) within RESPONSE_WINDOW
                   c. CO2 evidence + an unprompted knock/voice within COMBINE_WINDOW
  3. UNRESPONSIVE  CO2 evidence + >= 2 calls in a row unanswered
  4. POSSIBLE      a/b/c. CO2 breathing / sudden rise / slow build-up
                   d. unprompted knock/voice within ACTIVITY_WINDOW
                   e. a single press that was never confirmed (for ACTIVITY_WINDOW)
  5. NO SIGNS
CO2 alone can never make CONFIRMED. DANGER (pile moving) is a separate flag
shown on top of any status; it never changes the status.

Plain Python, no hardware imports: the base-station simulator and tests run it too.
"""
NO_SIGNS     = "NO SIGNS"
POSSIBLE     = "POSSIBLE"
LIKELY       = "LIKELY"
CONFIRMED    = "CONFIRMED"
UNRESPONSIVE = "UNRESPONSIVE"

# what each sound kind is called on screen / dashboard
SOUND_REASON = {"TAP": "heard knocking", "VOICE": "heard voice"}
SOUND_WORD = {"TAP": "knocking", "VOICE": "voice"}
# CO2 levels that count as evidence (same as co2logic.EVIDENCE).
# ELEVATED (high but flat: room air), NORMAL and LEARNING never change the status.
CO2_EVIDENCE = ("SUDDEN_RISE", "BREATHING", "SLOW_BUILDUP")
CO2_WORD = {"BREATHING": "breathing", "SUDDEN_RISE": "CO2 rise", "SLOW_BUILDUP": "CO2 build-up"}


class Assessor:
    RESPONSE_WINDOW = 300   # an answer to a call stays "recent" this long
    ACTIVITY_WINDOW = 15    # unprompted knock/voice stays "recent" this long
    CONFIRM_WINDOW = 20     # 2nd button press must come within this many seconds
    COMBINE_WINDOW = 120    # CO2 + knock/voice within this many seconds = LIKELY

    def __init__(self, activity_window=None, response_window=None, confirm_window=None,
                 combine_window=None, arm_until=None):
        if activity_window is not None:
            self.ACTIVITY_WINDOW = activity_window
        if response_window is not None:
            self.RESPONSE_WINDOW = response_window
        if confirm_window is not None:
            self.CONFIRM_WINDOW = confirm_window
        if combine_window is not None:
            self.COMBINE_WINDOW = combine_window
        self.arm_until = arm_until      # None = no arming phase
        self.danger = False
        self.reset()

    def arming(self, now):
        return self.arm_until is not None and now < self.arm_until

    def reset(self):
        """Forget all evidence and counters (false alarm). Sensor baselines and
        the DANGER flag are not evidence and are kept."""
        self.confirmed_by = None        # what confirmed the survivor
        self.first_press = None         # time of a 1st button press waiting for a 2nd
        self.last_response = None       # time the survivor answered a call
        self.response_kind = None       # "TAP" / "VOICE"
        self.response_taps = 0
        self.last_activity = None       # time of the last UNPROMPTED knock/voice
        self.activity_kind = None
        self.co2_level = None           # latest CO2 level from co2logic
        self.co2_rise = 0               # ppm
        self.co2_rise_secs = 0          # over this many seconds
        self.co2_rh_jump = 0            # humidity points
        self.unanswered_calls = 0       # calls in a row with no answer

    # ---- evidence inputs ----
    def button_press(self, now):
        """A survivor must press TWICE to confirm, so one bump from falling
        debris can't create a false CONFIRMED. Returns "first", "confirmed",
        or None (ignored while arming)."""
        if self.arming(now):
            return None
        self.unanswered_calls = 0                    # a press is an answer too
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

    def call_response(self, now, kind, taps=0):
        """The survivor answered a call (knocked back or spoke while listening)."""
        if self.arming(now):
            return
        self.last_response = now
        self.response_kind = kind
        self.response_taps = taps
        self.unanswered_calls = 0

    def tap_response(self, now):                     # older API, kept for compatibility
        self.call_response(now, "TAP", 0)

    def no_response(self, now):
        if self.arming(now):
            return
        self.unanswered_calls += 1

    def activity(self, now, kind=None):
        """An UNPROMPTED knock/voice (not during a listening window)."""
        if self.arming(now):
            return
        self.last_activity = now
        self.activity_kind = kind

    def set_co2(self, level, rise, rise_secs, rh_jump, now):
        """Latest CO2 level from co2logic. Supporting evidence only: it can make
        a node POSSIBLE / LIKELY / UNRESPONSIVE, never CONFIRMED. (co2logic
        reports LEARNING until its baseline exists, which is never evidence.)"""
        if self.arming(now):
            return
        self.co2_level = level
        self.co2_rise = rise
        self.co2_rise_secs = rise_secs
        self.co2_rh_jump = rh_jump

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

    def evidence(self, now):
        """Compact snapshot of WHY the status is what it is (sent in ST / HB):
          btn  none | first (waiting for 2nd press) | confirmed
          snd  none | TAP | VOICE   (unprompted, within COMBINE_WINDOW)
          co2  latest CO2 level, or NONE (no sensor / not yet)
          resp 1 if the survivor answered a call within RESPONSE_WINDOW
          miss calls in a row with no answer
          dg   1 if the danger alarm is on"""
        if self.confirmed_by:
            btn = "confirmed"
        elif self.awaiting_second(now):
            btn = "first"
        else:
            btn = "none"
        snd = self.activity_kind if self._recent(self.last_activity, now, self.COMBINE_WINDOW) else None
        return {"btn": btn, "snd": snd or "none", "co2": self.co2_level or "NONE",
                "resp": int(self._recent(self.last_response, now, self.RESPONSE_WINDOW)),
                "miss": self.unanswered_calls, "dg": int(self.danger)}

    # ---- the rules, checked strongest first ----
    def evaluate(self, now):
        # 0. ARMING: just powered on, sensors still settling
        if self.arming(now):
            return NO_SIGNS, "starting up (arming %ds)" % int(self.arm_until - now + 0.999)

        # 1. CONFIRMED: only a person can press twice. Latched.
        if self.confirmed_by:
            return CONFIRMED, self.confirmed_by

        # 2. LIKELY
        if self.awaiting_second(now):
            left = int(self.CONFIRM_WINDOW - (now - self.first_press) + 0.999)   # round up
            return LIKELY, "pressed once - press again (%ds)" % left
        if self._recent(self.last_response, now, self.RESPONSE_WINDOW):
            if self.response_kind == "VOICE":
                return LIKELY, "answered by voice after call"
            return LIKELY, "tapped back %d times after call" % self.response_taps
        if self.co2_up() and self._recent(self.last_activity, now, self.COMBINE_WINDOW):
            return LIKELY, "%s + %s" % (CO2_WORD[self.co2_level],
                                        SOUND_WORD.get(self.activity_kind, "noise"))

        # 3. UNRESPONSIVE: signs of breathing, but no answer to the calls
        if self.co2_up() and self.unanswered_calls >= 2:
            return UNRESPONSIVE, "breathing but no answer to %d calls" % self.unanswered_calls

        # 4. POSSIBLE
        if self.co2_up():
            return POSSIBLE, self._co2_reason()
        if self._recent(self.last_activity, now, self.ACTIVITY_WINDOW):
            return POSSIBLE, SOUND_REASON.get(self.activity_kind, "unprompted taps/noise")
        if (self.first_press is not None
                and now - self.first_press < self.CONFIRM_WINDOW + self.ACTIVITY_WINDOW):
            return POSSIBLE, "single press, not confirmed"

        # 5.
        return NO_SIGNS, "no evidence yet"
