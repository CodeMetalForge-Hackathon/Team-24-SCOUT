"""Non-blocking beep player for the Grove Speaker (PWM on one pin).

play(name) starts a pattern; update(now) must be called every loop and moves
through the steps by looking at the clock: no time.sleep, so buttons, sound
and CO2 keep working while it beeps.

The speaker is driven ONLY during a tone/slide step (duty_cycle 32768).
During rests and when idle duty_cycle is 0, so there is no hum or whine.
"""
import time

ON = 32768          # 50 % duty = loudest
SLIDE_STEP = 0.015  # update the siren pitch every ~15 ms


def pattern_length(steps):
    return sum(s[-1] for s in steps)


class BeepPlayer:
    def __init__(self, pin, base_freq, patterns, priority, mic_tail=0.6):
        self.patterns = patterns
        self.priority = priority
        self.mic_tail = mic_tail
        self.name = None           # pattern playing now, or None
        self.steps = []
        self.i = 0
        self.step_start = 0
        self.last_slide = 0
        self.quiet_until = 0       # microphone must ignore sounds until this time
        self.pwm = None
        try:
            import pwmio  # type: ignore
            self.pwm = pwmio.PWMOut(pin, frequency=base_freq, duty_cycle=0, variable_frequency=True)
        except Exception as e:    # speaker not wired / pin busy: run silently
            print("Speaker not available (", e, ") - beeps will be silent")

    @property
    def playing(self):
        return self.name is not None

    def _set(self, freq=None, on=False):
        if self.pwm is None:
            return
        try:
            if freq:
                self.pwm.frequency = int(freq)
            self.pwm.duty_cycle = ON if on else 0
        except Exception:
            pass

    def play(self, name, now=None):
        """Start a pattern. Returns False if a higher-priority one is playing."""
        if name not in self.patterns:
            return False
        if self.name and self.priority.get(name, 0) < self.priority.get(self.name, 0):
            return False                                  # don't interrupt e.g. the siren
        now = time.monotonic() if now is None else now
        self.name, self.steps, self.i = name, self.patterns[name], 0
        self.quiet_until = now + pattern_length(self.steps) + self.mic_tail
        self._start_step(now)
        return True

    def stop(self, now=None):
        self._set(on=False)
        if self.name:
            now = time.monotonic() if now is None else now
            self.quiet_until = max(self.quiet_until, now + self.mic_tail)
        self.name = None

    def _start_step(self, now):
        if self.i >= len(self.steps):                     # pattern finished
            self.stop(now)
            return
        step = self.steps[self.i]
        self.step_start = self.last_slide = now
        if step[0] == "tone":
            self._set(step[1], True)
        elif step[0] == "slide":
            self._set(step[1], True)
        else:                                             # rest
            self._set(on=False)

    def update(self, now):
        if self.name is None:
            return
        step = self.steps[self.i]
        dur = step[-1]
        t = now - self.step_start
        if t >= dur:
            if t > dur + 0.5:                             # loop was stuck: never leave it on
                self._set(on=False)
            self.i += 1
            self._start_step(now)
            return
        if step[0] == "slide" and now - self.last_slide >= SLIDE_STEP:
            self.last_slide = now
            self._set(step[1] + (step[2] - step[1]) * t / dur, True)
