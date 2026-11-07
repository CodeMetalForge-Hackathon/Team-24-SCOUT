import time
import digitalio


class Button:
    """PRESS ME button + LED. LED blinks to get attention, solid once pressed."""
    def __init__(self, btn_pin, led_pin, pull, debounce=0.05):
        self.btn = digitalio.DigitalInOut(btn_pin)
        self.btn.direction = digitalio.Direction.INPUT
        self.btn.pull = pull
        self.idle = self.btn.value            # value when NOT pressed

        self.led = digitalio.DigitalInOut(led_pin)
        self.led.direction = digitalio.Direction.OUTPUT

        self.debounce = debounce
        self.was_down = False
        self.last_change = 0
        self.mode = "blink"

    def update(self, now):
        """Returns True ONCE per press."""
        down = self.btn.value != self.idle
        pressed = False
        if down != self.was_down and now - self.last_change > self.debounce:
            self.last_change = now
            self.was_down = down
            pressed = down
        self.led.value = True if self.mode == "on" else int(now * 2) % 2 == 0
        return pressed


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
        self.last_tap = 0

    def update(self, now):
        v = self.pin.value
        tapped = False
        if v != self.last_value:
            if v != self.idle and now - self.last_tap > self.debounce:
                tapped = True
                self.last_tap = now
            self.last_value = v
        return tapped


class SoundSensor:
    """Grove Sound Sensor on an analog pin.
    'TAP' = short loud spike, 'VOICE' = loud sound that lasts longer."""
    def __init__(self, pin, threshold, window=0.03, tap_max_windows=4):
        import analogio
        self.mic = analogio.AnalogIn(pin)
        self.threshold = threshold
        self.window = window
        self.tap_max = tap_max_windows
        self.run = 0
        self.last_tap = 0
        self.level = 0

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
        if self.level > self.threshold:
            self.run += 1
            return None
        if self.run == 0:
            return None
        length, self.run = self.run, 0
        if length <= self.tap_max:
            if now - self.last_tap > 0.15:
                self.last_tap = now
                return "TAP"
            return None
        return "VOICE"