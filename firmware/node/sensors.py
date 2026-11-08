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
            if now - self.last_tap > 0.15:
                self.last_tap = now
                return "TAP"
            return None
        if length >= self.voice_min:
            return "VOICE"
        return None                               # short medium blip: ignore