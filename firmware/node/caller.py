"""Call-and-listen cycle: IDLE -> CALLING -> LISTENING -> IDLE.

The node beeps 3 times ("tap 3 times if you hear this"), then listens for
LISTEN_SECS. A survivor who can hear it knocks back.

Plain Python, no hardware imports: the base-station simulator and the laptop
tests run this exact file. The beeps themselves are played by code.py; this
class only needs to be told when the call audio (+ mic tail) is over.
"""

IDLE, CALLING, LISTENING = "IDLE", "CALLING", "LISTENING"


class Caller:
    def __init__(self, interval, listen_secs, min_taps, first_call_at):
        self.interval = interval
        self.listen_secs = listen_secs
        self.min_taps = min_taps
        self.state = IDLE
        self.next_call = first_call_at
        self.listen_until = 0
        self.taps = 0
        self.requested = False      # rescuer pressed "Call now"

    def request(self):
        self.requested = True

    def reset(self, now):
        self.state = IDLE
        self.next_call = now + self.interval
        self.requested = False

    def _finish(self, now):
        self.state = IDLE
        self.next_call = now + self.interval

    def cancel(self, now):
        """A button press during a call: the button logic takes over."""
        if self.state != IDLE:
            self._finish(now)

    def update(self, now, allowed, audio_done):
        """Move the cycle along. Returns "CALL" (start the beeps now),
        "LISTEN" (beeps over, listening), "NORESP" (window ended, no answer) or None.
          allowed:    False during arming, CONFIRMED, the 1st-press countdown or the siren
          audio_done: True once the CALL beeps and the mic tail are over"""
        if self.state == IDLE:
            if (now >= self.next_call or self.requested) and allowed:
                self.state = CALLING
                self.taps = 0
                self.requested = False
                return "CALL"
            return None                     # not allowed yet: try again next loop
        if self.state == CALLING:
            if audio_done:
                self.state = LISTENING
                self.listen_until = now + self.listen_secs
                return "LISTEN"
            return None
        if now >= self.listen_until:        # LISTENING and time is up
            self._finish(now)
            return "NORESP"
        return None

    def heard(self, kind, now):
        """A TAP/VOICE while LISTENING. Returns "RESP" when it counts as an
        answer (>= min_taps knocks, or any voice), else None."""
        if self.state != LISTENING:
            return None
        if kind == "TAP":
            self.taps += 1
        if kind == "VOICE" or self.taps >= self.min_taps:
            self._finish(now)                # answer heard: stop listening early
            return "RESP"
        return None

    def listen_left(self, now):
        return max(0, self.listen_until - now) if self.state == LISTENING else 0

    def next_call_in(self, now):
        return max(0, self.next_call - now) if self.state == IDLE else 0
