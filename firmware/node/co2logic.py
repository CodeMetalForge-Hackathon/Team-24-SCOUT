"""CO2 level logic: turns raw CO2 / humidity readings into a level.

Plain Python with no hardware imports, so the base-station simulator and the
laptop tests run exactly this file too.

Design: CO2 counts as evidence only when it JUMPS quickly (someone breathing
close to the node), and that evidence lasts a fixed hold time. "X ppm above
baseline" is NOT evidence: room CO2 drifts as people come and go, which caused
false alarms that lasted minutes after a breath.

Levels (each new reading):
  LEARNING     first BASELINE_SECS: measuring the room
  BREATHING    sudden rise held, and humidity jumped too (breath is warm and wet)
  SUDDEN_RISE  sudden rise held, humidity didn't jump
  SLOW_BUILDUP (FIELD profile only) steady climb over many minutes: a closed void filling up
  ELEVATED     well above the baseline but flat: room air, info only (not evidence)
  NORMAL       otherwise
"""

EVIDENCE = ("SUDDEN_RISE", "BREATHING", "SLOW_BUILDUP")


def _low_median(values):
    """Median of the LOWER half: a breath during learning can't inflate the baseline."""
    s = sorted(values)
    low = s[:max(1, len(s) // 2)]
    return low[len(low) // 2]


class CO2Levels:
    def __init__(self, baseline_secs, rate_window, sudden_ppm, event_hold, rh_rise_points,
                 follow, slow_enabled=False, trend_minutes=15, trend_ppm=60, start=0):
        self.baseline_secs = baseline_secs
        self.rate_window = rate_window
        self.sudden_ppm = sudden_ppm
        self.event_hold = event_hold
        self.rh_rise = rh_rise_points
        self.follow = follow
        self.slow_enabled = slow_enabled
        self.trend_secs = trend_minutes * 60
        self.trend_ppm = trend_ppm
        self.start = start

        self.level = "LEARNING"
        self.ppm = self.rh = None
        self.baseline = self.rh_baseline = None
        self.last_sudden_time = None   # last reading that (re)triggered a sudden rise
        self.rise = 0                  # biggest rise (ppm) seen in the current event
        self.rise_secs = 0             # ...and over how many seconds
        self.rh_jump = 0               # biggest humidity jump (points) in the current event
        self.trend_min = 0             # SLOW_BUILDUP: minutes the trend window covers
        self._learn_ppm = []
        self._learn_rh = []
        self._recent = []              # (t, ppm, rh) for the last rate_window seconds
        self._fast = 0                 # readings in a row with a fast rise
        self._trend = []               # FIELD only: (t, ppm), one point per trend_secs/30 (~31 max)

    def learning_left(self, now):
        return max(0, self.baseline_secs - (now - self.start))

    def held(self, now):
        """True while a sudden rise is still counted as evidence."""
        return self.last_sudden_time is not None and now - self.last_sudden_time < self.event_hold

    def hold_left(self, now):
        return max(0, self.event_hold - (now - self.last_sudden_time)) if self.held(now) else 0

    def feed(self, now, ppm, rh):
        """Give it one new reading. Updates .level and the event numbers."""
        self.ppm, self.rh = ppm, rh

        # short history for the rise check (only the last rate_window seconds)
        self._recent.append((now, ppm, rh))
        while now - self._recent[0][0] > self.rate_window:
            self._recent.pop(0)

        if self.baseline is None:
            self._learn_ppm.append(ppm)
            self._learn_rh.append(rh)
            if now - self.start < self.baseline_secs or len(self._learn_ppm) < 2:
                return
            self.baseline = _low_median(self._learn_ppm)
            self.rh_baseline = _low_median(self._learn_rh)
            self._learn_ppm = self._learn_rh = None      # free the memory

        # ---- sudden rise: compare with the LOWEST value in the last rate_window s ----
        low_t, low_ppm, _ = min(self._recent, key=lambda r: r[1])
        low_rh = min(r[2] for r in self._recent)
        rise = ppm - low_ppm
        self._fast = self._fast + 1 if rise >= self.sudden_ppm else 0
        if self._fast >= 2:                              # 2 readings in a row: not noise
            if not self.held(now):                       # a new event: start fresh numbers
                self.rise = self.rise_secs = self.rh_jump = 0
            self.last_sudden_time = now                  # a continuing rise extends the hold
            if rise > self.rise:
                self.rise, self.rise_secs = rise, int(now - low_t)
            self.rh_jump = max(self.rh_jump, rh - low_rh)

        slow = self.slow_enabled and self._update_trend(now, ppm)

        if self.held(now):
            self.level = "BREATHING" if self.rh_jump >= self.rh_rise else "SUDDEN_RISE"
        elif slow:
            self.level = "SLOW_BUILDUP"
        elif ppm - self.baseline >= self.sudden_ppm:
            self.level = "ELEVATED"                      # high but flat: room air
        else:
            self.level = "NORMAL"

        # The baseline (for display / ELEVATED only) follows the room, but not
        # during a sudden rise (or a fast reading that may start one), so a
        # breath doesn't drag it up.
        if not self.held(now) and self._fast == 0:
            f = self.follow
            self.baseline = (1 - f) * self.baseline + f * ppm
            self.rh_baseline = (1 - f) * self.rh_baseline + f * rh

    def _update_trend(self, now, ppm):
        """FIELD only: steady climb of trend_ppm over trend_secs, >= 70% of steps up."""
        t = self._trend
        if not t or now - t[-1][0] >= self.trend_secs / 30:
            t.append((now, ppm))
        while now - t[0][0] > self.trend_secs:
            t.pop(0)
        span = t[-1][0] - t[0][0]
        self.trend_min = int(span / 60 + 0.5)
        if len(t) < 4 or span < self.trend_secs * 0.66:  # not enough history yet
            return False
        climb = t[-1][1] - t[0][1]
        ups = sum(1 for i in range(1, len(t)) if t[i][1] >= t[i - 1][1])
        if climb >= self.trend_ppm and ups >= 0.7 * (len(t) - 1):
            self.rise, self.rise_secs = climb, int(span)
            return True
        return False
