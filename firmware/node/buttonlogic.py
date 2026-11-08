"""Button press logic, without any hardware: feed it "is the button down?"
and the time, and it says when a real press happened.

Plain Python so the laptop tests can check it (sensors.Button wraps it).
"""


class PressLogic:
    """update(now, down) returns:
      "PRESS" once, when the button is RELEASED after a normal press
      "STUCK" once, if it is held >= stuck_secs (rubble resting on it, not a
              person); that press is then ignored
      None    otherwise
    Presses that START before arm_until (just after power-on) are ignored."""

    def __init__(self, arm_until=0, stuck_secs=5, debounce=0.05):
        self.arm_until = arm_until
        self.stuck_secs = stuck_secs
        self.debounce = debounce
        self.down = False
        self.last_change = None     # None = never changed (not "at time 0")
        self.press_start = None
        self.stuck = False

    def update(self, now, down):
        settled = self.last_change is None or now - self.last_change > self.debounce
        if down != self.down and settled:
            self.down = down
            self.last_change = now
            if down:                                  # just pressed: start timing
                self.press_start = now
                self.stuck = False
                return None
            # just released: count it only if it was a normal, armed press
            if self.stuck or self.press_start is None or self.press_start < self.arm_until:
                return None
            return "PRESS"
        if (self.down and not self.stuck and self.press_start is not None
                and now - self.press_start >= self.stuck_secs):
            self.stuck = True
            return "STUCK"
        return None
