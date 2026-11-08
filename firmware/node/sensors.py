import time
import digitalio
from buttonlogic import PressLogic


class Button:
    """PRESS ME button + its LED, with accidental-press protection.

    update(now) returns:
      "PRESS" once, when the button is RELEASED after a normal press
      "STUCK" once, if it is held >= stuck_secs (rubble resting on it, not a
              person); that press is then ignored
      None    otherwise
    Presses that start before arm_until (just after power-on) are ignored.
    """
    def __init__(self, btn_pin, pull, led_pin=None, arm_until=0, stuck_secs=5, debounce=0.05):
        self.btn = digitalio.DigitalInOut(btn_pin)
        self.btn.direction = digitalio.Direction.INPUT
        self.btn.pull = pull
        # Idle level comes from the pull resistor, not a startup reading, so a
        # button that is already held at boot is still seen as "down" (-> STUCK).
        self.idle = pull == digitalio.Pull.UP

        self.led = None
        if led_pin is not None:
            self.led = digitalio.DigitalInOut(led_pin)
            self.led.direction = digitalio.Direction.OUTPUT

        # the press / stuck / arming rules live in buttonlogic.py (tested on the laptop)
        self.logic = PressLogic(arm_until, stuck_secs, debounce)

    def update(self, now):
        return self.logic.update(now, self.btn.value != self.idle)

    def set_led(self, now, mode):
        """mode: 'blink' = slow (waiting), 'fast' = press again!, 'on' = confirmed."""
        if self.led is None:
            return
        if mode == "on":
            self.led.value = True
        elif mode == "fast":
            self.led.value = int(now * 8) % 2 == 0    # 4 blinks per second
        else:
            self.led.value = int(now * 2) % 2 == 0    # 1 blink per second


class Vibration:
    """SW-420 vibration sensor. Counts one tap each time the signal CHANGES
    away from its normal 'still' value."""
    def __init__(self, pin, debounce=0.15):
        self.pin = digitalio.DigitalInOut(pin)
        self.pin.direction = digitalio.Direction.INPUT
        self.pin.pull = digitalio.Pull.UP

        # learn the 'still' value: sample for 1 second, take the majority
        highs = lows = 0
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            if self.pin.value:
                highs += 1
            else:
                lows += 1
        self.idle = highs > lows
        print("Vibration still-value =", self.idle, "(highs", highs, "lows", lows, ")")

        self.last_value = self.idle
        self.debounce = debounce
        self.last_tap = None

    def update(self, now):
        v = self.pin.value
        tapped = False
        if v != self.last_value:
            if v != self.idle and (self.last_tap is None or now - self.last_tap > self.debounce):
                tapped = True
                self.last_tap = now
            self.last_value = v
        return tapped


class CO2Sensor:
    """SCD41 CO2 + humidity sensor (on the SparkFun SEN-25200, I2C 0x62).
    The SEN55 on the same board is deliberately NOT started: its fan adds
    noise right next to the sound sensor.

    update(now) is non-blocking: it checks for data at most once a second
    (the SCD41 makes a new reading every ~5 s) and returns True only when a
    new reading arrived. The level logic lives in co2logic.py.
    If the sensor is missing, .ok is False and the node runs without CO2."""

    def __init__(self, i2c, cfg):
        from co2logic import CO2Levels
        self.ok = False
        self.temp = None
        self.last_check = 0
        self.errors = 0
        self.levels = CO2Levels(cfg.CO2_BASELINE_SECS, cfg.CO2_RATE_WINDOW, cfg.CO2_SUDDEN_PPM,
                                cfg.CO2_EVENT_HOLD, cfg.RH_RISE_POINTS, cfg.CO2_BASELINE_FOLLOW,
                                cfg.SLOW_BUILDUP_ENABLED, cfg.CO2_TREND_MINUTES,
                                cfg.CO2_TREND_PPM, start=time.monotonic())
        try:
            import adafruit_scd4x  # type: ignore
            self.scd = adafruit_scd4x.SCD4X(i2c)
            self.scd.stop_periodic_measurement()   # in case it was left running
            self.scd.start_periodic_measurement()
            self.ok = True
            print("CO2 sensor started; learning baseline for", cfg.CO2_BASELINE_SECS, "s")
        except Exception as e:                      # not plugged in / wrong wiring
            print("CO2 sensor NOT found (", e, ") - continuing without CO2")

    def update(self, now):
        if not self.ok or now - self.last_check < 1:
            return False
        self.last_check = now
        try:
            if not self.scd.data_ready:
                return False
            ppm = self.scd.CO2
            rh = self.scd.relative_humidity
            self.temp = self.scd.temperature
        except Exception as e:                      # cable pulled while running
            self.errors += 1
            if self.errors == 1 or self.errors % 60 == 0:
                print("CO2 read failed (", e, ") - will keep trying")
            return False
        self.errors = 0
        self.levels.feed(now, ppm, rh)
        return True

    # shortcuts so code.py can say co2.level, co2.rise, ...
    @property
    def ppm(self): return self.levels.ppm
    @property
    def rh(self): return self.levels.rh
    @property
    def level(self): return self.levels.level
    @property
    def rise(self): return self.levels.rise
    @property
    def rise_secs(self): return self.levels.rise_secs
    @property
    def rh_jump(self): return self.levels.rh_jump
    @property
    def baseline(self): return self.levels.baseline
    @property
    def rh_baseline(self): return self.levels.rh_baseline


class Battery:
    """MAX17048 fuel gauge on the Feather (I2C 0x36). Read at most every 30 s.
    .percent is None if there's no gauge or no battery."""
    def __init__(self, i2c):
        self.percent = None
        self.last_read = None
        self.gauge = None
        try:
            import adafruit_max1704x  # type: ignore
            self.gauge = adafruit_max1704x.MAX17048(i2c)
        except Exception as e:
            print("Battery gauge not available (", e, ")")

    def update(self, now):
        if self.gauge is None or (self.last_read is not None and now - self.last_read < 30):
            return
        self.last_read = now
        try:
            p = self.gauge.cell_percent
            self.percent = int(max(0, min(100, p))) if p is not None else None
        except Exception:
            self.percent = None


class SoundSensor:
    """Grove Sound Sensor on an analog pin.
    'TAP'   = short AND very loud (knock)
    'VOICE' = only moderately loud but LASTS (talking/'HELP!', even from farther away)
    Short medium blips are ignored."""

    def __init__(self, pin, tap_threshold, voice_threshold,
                 window=0.03, tap_max_windows=4, voice_min_windows=6):
        import analogio
        self.mic = analogio.AnalogIn(pin)
        self.tap_threshold = tap_threshold
        self.voice_threshold = voice_threshold
        self.window = window
        self.tap_max = tap_max_windows
        self.voice_min = voice_min_windows
        self.run = 0          # loud slices in a row
        self.peak = 0         # loudest slice during this sound
        self.last_tap = None  # None = never (not "at time 0")
        self.level = 0

    def reset_run(self):
        """Forget a sound in progress. Called when the mic is un-muted after a
        beep, so a sound that started during the beep can't become an event."""
        self.run = 0
        self.peak = 0

    def _swing(self):
        lo, hi = 65535, 0
        end = time.monotonic() + self.window
        while time.monotonic() < end:
            v = self.mic.value
            if v < lo: lo = v
            if v > hi: hi = v
        return hi - lo

    def update(self, now):
        """Returns 'TAP', 'VOICE', or None."""
        self.level = self._swing()

        if self.level > self.voice_threshold:     # louder than background: track it
            self.run += 1
            if self.level > self.peak:
                self.peak = self.level
            return None
        if self.run == 0:
            return None

        # the sound just ENDED: decide what it was
        length, peak = self.run, self.peak
        self.run, self.peak = 0, 0

        if length <= self.tap_max and peak > self.tap_threshold:
            if self.last_tap is None or now - self.last_tap > 0.15:
                self.last_tap = now
                return "TAP"
            return None
        if length >= self.voice_min:
            return "VOICE"
        return None                               # short medium blip: ignore