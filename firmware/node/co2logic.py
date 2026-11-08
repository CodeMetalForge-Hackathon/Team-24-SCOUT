"""CO2 level logic: turns raw CO2 / humidity readings into a level.

Plain Python with no hardware imports, so the base-station simulator and the
laptop tests run exactly this file too.

Levels:
  LEARNING      first BASELINE_SECS: measuring the room
  NORMAL        close to the baseline
  SLIGHT_RISE   a bit up ("watch")
  RISING        clearly up for 2 readings in a row (stays until well back down)
  BREATHING     RISING and humidity up too (exhaled breath is warm and wet)
  SLOW_BUILDUP  small but steady climb over several minutes (person in a closed void)
"""

ELEVATED = ("RISING", "BREATHING", "SLOW_BUILDUP")


def _low_median(values):
    """Median of the LOWER half: a breath during learning can't inflate the baseline."""
    s = sorted(values)
    low = s[:max(1, len(s) // 2)]
    return low[len(low) // 2]


class CO2Levels:
    def __init__(self, baseline_secs, rise_ppm, slight_ppm, follow,
                 trend_minutes, trend_ppm, rh_rise_points, start=0):
        self.baseline_secs = baseline_secs
        self.rise_ppm = rise_ppm
        self.slight_ppm = slight_ppm
        self.follow = follow
        self.trend_secs = trend_minutes * 60
        self.trend_ppm = trend_ppm
        self.rh_rise = rh_rise_points
        self.start = start

        self.level = "LEARNING"
        self.ppm = self.rh = None
        self.baseline = self.rh_baseline = None
        self.delta = self.rh_delta = 0
        self.trend_rise = 0          # ppm gained over the trend window
        self.trend_min = 0           # how many minutes that window actually covers
        self._learn_ppm = []
        self._learn_rh = []
        self._rising = False
        self._above = 0              # readings in a row above rise_ppm
        self._below = 0              # readings in a row below rise_ppm / 2
        # ring buffer of (time, ppm), one point per trend_secs/30, so ~31 points max
        self._trend = []
        self._trend_step = self.trend_secs / 30

    def learning_left(self, now):
        return max(0, self.baseline_secs - (now - self.start))

    def feed(self, now, ppm, rh):
        """Give it one new reading. Updates .level, .delta, .rh_delta."""
        self.ppm, self.rh = ppm, rh

        if self.baseline is None:
            self._learn_ppm.append(ppm)
            self._learn_rh.append(rh)
            if now - self.start < self.baseline_secs or len(self._learn_ppm) < 2:
                return
            self.baseline = _low_median(self._learn_ppm)
            self.rh_baseline = _low_median(self._learn_rh)
            self._learn_ppm = self._learn_rh = None      # free the memory

        self.delta = ppm - self.baseline
        self.rh_delta = rh - self.rh_baseline

        # RISING with hysteresis: 2 readings above to start, 2 well below to stop,
        # so one noisy reading can't flip it either way.
        if self._rising:
            self._below = self._below + 1 if self.delta < self.rise_ppm / 2 else 0
            if self._below >= 2:
                self._rising, self._below = False, 0
        else:
            self._above = self._above + 1 if self.delta >= self.rise_ppm else 0
            if self._above >= 2:
                self._rising, self._above = True, 0

        slow = self._update_trend(now, ppm)

        if self._rising:
            self.level = "BREATHING" if self.rh_delta >= self.rh_rise else "RISING"
        elif slow:
            self.level = "SLOW_BUILDUP"
        elif self.delta >= self.slight_ppm:
            self.level = "SLIGHT_RISE"
        else:
            self.level = "NORMAL"

        # The baseline follows slow room changes, but ONLY while nothing is
        # happening, so a survivor's CO2 never gets "learned away".
        if self.level == "NORMAL":
            f = self.follow
            self.baseline = (1 - f) * self.baseline + f * ppm
            self.rh_baseline = (1 - f) * self.rh_baseline + f * rh

    def _update_trend(self, now, ppm):
        t = self._trend
        if not t or now - t[-1][0] >= self._trend_step:
            t.append((now, ppm))
        while t and now - t[0][0] > self.trend_secs:
            t.pop(0)
        span = t[-1][0] - t[0][0] if t else 0
        self.trend_min = int(span / 60 + 0.5)
        if len(t) < 4 or span < self.trend_secs * 0.66:    # not enough history yet
            self.trend_rise = 0
            return False
        self.trend_rise = t[-1][1] - t[0][1]
        ups = sum(1 for i in range(1, len(t)) if t[i][1] >= t[i - 1][1])
        return self.trend_rise >= self.trend_ppm and ups >= 0.7 * (len(t) - 1)
