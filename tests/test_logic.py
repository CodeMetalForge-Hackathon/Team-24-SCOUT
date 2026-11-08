"""Proves every status category of the node is decided correctly.

Runs on a normal computer (no hardware): the node's decision logic is plain
Python in firmware/node/ (assess.py, co2logic.py, caller.py, buttonlogic.py),
and every function takes the time `now` explicitly, so the tests drive a fake clock.

    python -m pytest tests/
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "firmware", "node"))

import assess  # noqa: E402
import buttonlogic  # noqa: E402
import caller  # noqa: E402
import co2logic  # noqa: E402

# DEMO profile numbers (firmware/node/config.py)
ARMING, ACTIVITY, RESPONSE, CONFIRM, COMBINE = 10, 15, 60, 20, 120
HOLD = 60
DEMO_CO2 = dict(baseline_secs=30, rate_window=15, sudden_ppm=120, event_hold=HOLD,
                rh_rise_points=3, follow=0.02, slow_enabled=False)
EVIDENCE = ("SUDDEN_RISE", "BREATHING", "SLOW_BUILDUP")
ORDER = ["CONFIRMED", "LIKELY", "UNRESPONSIVE", "POSSIBLE", "NO SIGNS"]


def brain(arm_until=None):
    return assess.Assessor(activity_window=ACTIVITY, response_window=RESPONSE,
                           confirm_window=CONFIRM, combine_window=COMBINE, arm_until=arm_until)


def armed():
    """A node that powered on at t=0 and finished arming at t=10."""
    return brain(arm_until=ARMING)


def status(b, now):
    return b.evaluate(now)[0]


def breathing(b, now, rise=380, secs=10, rh=6):
    b.set_co2("BREATHING", rise, secs, rh, now)


# ---------------------------------------------------------------- boot / arming

def test_boot_is_no_signs_starting_up():
    b = armed()
    assert b.evaluate(0.1) == ("NO SIGNS", "starting up (arming 10s)")
    assert b.evaluate(3.2) == ("NO SIGNS", "starting up (arming 7s)")


def test_nothing_changes_status_while_arming():
    b = armed()
    b.activity(1, "TAP")
    b.activity(2, "VOICE")
    breathing(b, 3)
    assert b.button_press(4) is None
    b.call_response(5, "TAP", 3)
    b.no_response(6)
    b.no_response(7)
    assert b.evaluate(9.9)[0] == "NO SIGNS"
    assert b.evaluate(10.1) == ("NO SIGNS", "no evidence yet")       # nothing was recorded
    assert b.unanswered_calls == 0


def test_after_arming_no_input_stays_no_signs():
    b = armed()
    for t in range(11, 131):                                          # 2 quiet minutes
        assert b.evaluate(t) == ("NO SIGNS", "no evidence yet")


def test_co2_learning_and_first_reading_are_not_evidence():
    c = co2logic.CO2Levels(start=0, **DEMO_CO2)
    c.feed(5, 1400, 60)                                               # weird first reading
    assert c.level == "LEARNING"
    for t in range(10, 35, 5):
        c.feed(t, 810, 48)
    assert c.level not in EVIDENCE


# ---------------------------------------------------------------- sounds

def test_unprompted_tap_is_possible_then_expires():
    b = armed()
    b.activity(20, "TAP")
    assert b.evaluate(21) == ("POSSIBLE", "heard knocking")
    assert status(b, 20 + ACTIVITY - 0.1) == "POSSIBLE"
    assert b.evaluate(20 + ACTIVITY + 0.1) == ("NO SIGNS", "no evidence yet")


def test_unprompted_voice_is_possible():
    b = armed()
    b.activity(20, "VOICE")
    assert b.evaluate(21) == ("POSSIBLE", "heard voice")


# ---------------------------------------------------------------- CO2

def test_co2_sudden_rise_and_breathing_are_possible():
    b = armed()
    b.set_co2("SUDDEN_RISE", 240, 10, 1, 40)
    assert b.evaluate(41) == ("POSSIBLE", "sudden CO2 rise +240 ppm in 10s")
    breathing(b, 42)
    assert b.evaluate(43) == ("POSSIBLE", "breathing detected: CO2 +380 ppm, humidity +6%")


@pytest.mark.parametrize("level", ["ELEVATED", "NORMAL", "LEARNING"])
def test_co2_elevated_normal_learning_change_nothing(level):
    b = armed()
    b.set_co2(level, 300, 10, 6, 40)
    assert b.evaluate(41) == ("NO SIGNS", "no evidence yet")


def test_co2_evidence_expires_after_hold():
    """Real co2logic: a breath is held for CO2_EVENT_HOLD, then high-but-flat = ELEVATED."""
    c = co2logic.CO2Levels(start=0, **DEMO_CO2)
    t = 0
    while t <= 30:
        c.feed(t, 810, 48)
        t += 5
    for d, drh in [(150, 2), (380, 4)]:                              # measured breath
        c.feed(t, 810 + d, 48 + drh)
        t += 5
    assert c.level == "BREATHING"
    b = armed()
    # (a reading still within 15 s of the rise re-triggers and extends the hold,
    #  so follow the latest trigger time)
    while t - c.last_sudden_time < HOLD:
        c.feed(t, 1190, 52)                                           # stays high, flat
        b.set_co2(c.level, c.rise, c.rise_secs, c.rh_jump, t)
        assert status(b, t) == "POSSIBLE"
        t += 5
    c.feed(t, 1190, 52)
    b.set_co2(c.level, c.rise, c.rise_secs, c.rh_jump, t)
    assert c.level == "ELEVATED"
    assert b.evaluate(t) == ("NO SIGNS", "no evidence yet")


def test_co2_plus_unprompted_tap_is_likely():
    b = armed()
    b.activity(20, "TAP")
    breathing(b, 20 + COMBINE - 1)                                    # knock 119 s earlier
    assert b.evaluate(20 + COMBINE - 0.5) == ("LIKELY", "breathing + knocking")
    b2 = armed()
    b2.activity(20, "TAP")
    breathing(b2, 20 + COMBINE + 1)                                   # 121 s: too old
    assert status(b2, 20 + COMBINE + 1) == "POSSIBLE"


# ---------------------------------------------------------------- calls

def run_call(c, t, allowed=True):
    """Drive a Caller through CALL -> LISTEN. Returns the time listening started."""
    assert c.update(t, allowed, False) == "CALL"
    assert c.update(t + 2.4, allowed, True) == "LISTEN"
    return t + 2.4


def test_call_answer_by_two_taps_is_likely_then_expires():
    c = caller.Caller(interval=20, listen_secs=10, min_taps=2, first_call_at=20)
    b = armed()
    t = run_call(c, 20)
    assert c.heard("TAP", t + 1) is None                              # 1 knock isn't enough
    assert c.heard("TAP", t + 1.5) == "RESP"
    b.call_response(t + 1.5, "TAP", c.taps)
    assert b.evaluate(t + 2) == ("LIKELY", "tapped back 2 times after call")
    assert status(b, t + 1.5 + RESPONSE - 0.1) == "LIKELY"
    assert status(b, t + 1.5 + RESPONSE + 0.1) == "NO SIGNS"


def test_call_answer_by_voice_is_likely():
    c = caller.Caller(interval=20, listen_secs=10, min_taps=2, first_call_at=20)
    b = armed()
    t = run_call(c, 20)
    assert c.heard("VOICE", t + 1) == "RESP"
    b.call_response(t + 1, "VOICE")
    assert b.evaluate(t + 2) == ("LIKELY", "answered by voice after call")


def test_unanswered_call_ends_listening():
    c = caller.Caller(interval=20, listen_secs=10, min_taps=2, first_call_at=20)
    t = run_call(c, 20)
    assert c.update(t + 10.1, True, True) == "NORESP"


def test_no_calls_while_arming_or_confirmed():
    c = caller.Caller(interval=20, listen_secs=10, min_taps=2, first_call_at=20)
    assert c.update(25, False, True) is None                          # not allowed: waits
    assert c.update(26, True, True) == "CALL"


# ---------------------------------------------------------------- unresponsive

def test_co2_and_two_missed_calls_is_unresponsive():
    b = armed()
    breathing(b, 40)
    b.no_response(45)
    assert status(b, 46) == "POSSIBLE"
    b.no_response(77)
    assert b.evaluate(78) == ("UNRESPONSIVE", "breathing but no answer to 2 calls")


@pytest.mark.parametrize("answer", ["response", "press"])
def test_response_or_press_resets_missed_calls(answer):
    b = armed()
    breathing(b, 40)
    b.no_response(45)
    b.no_response(77)
    if answer == "response":
        b.call_response(80, "TAP", 3)
    else:
        b.button_press(80)
    assert b.unanswered_calls == 0
    assert status(b, 81) == "LIKELY"


def test_unresponsive_falls_back_when_co2_expires_counter_kept():
    b = armed()
    breathing(b, 40)
    b.no_response(45)
    b.no_response(77)
    b.set_co2("ELEVATED", 0, 0, 0, 101)                               # hold over
    assert status(b, 102) == "NO SIGNS"
    assert b.unanswered_calls == 2


# ---------------------------------------------------------------- button

def test_first_press_countdown_then_confirmed():
    b = armed()
    assert b.button_press(20) == "first"
    assert b.evaluate(20) == ("LIKELY", "pressed once - press again (20s)")
    assert b.evaluate(26.5) == ("LIKELY", "pressed once - press again (14s)")
    assert b.button_press(20 + CONFIRM - 1) == "confirmed"
    assert b.evaluate(40) == ("CONFIRMED", "pressed button twice")


def test_confirmed_is_never_downgraded():
    b = armed()
    b.button_press(20)
    b.button_press(25)
    b.activity(30, "TAP")
    breathing(b, 31)
    b.no_response(40)
    b.no_response(70)
    b.set_co2("NORMAL", 0, 0, 0, 200)
    b.set_danger(True)
    for t in (30, 100, 1000):
        assert status(b, t) == "CONFIRMED"


def test_single_press_expires_to_possible():
    b = armed()
    b.button_press(20)
    assert b.evaluate(20 + CONFIRM + 0.1) == ("POSSIBLE", "single press, not confirmed")
    assert status(b, 20 + CONFIRM + ACTIVITY + 0.1) == "NO SIGNS"


def test_stuck_press_is_ignored():
    p = buttonlogic.PressLogic(arm_until=10, stuck_secs=5)
    assert p.update(20, True) is None                                 # pressed...
    assert p.update(24.9, True) is None
    assert p.update(25.0, True) == "STUCK"                            # ...held 5 s
    assert p.update(30, False) is None                                # release: NOT a press
    assert p.update(40, True) is None
    assert p.update(40.3, False) == "PRESS"                           # a normal press works again


def test_press_during_arming_is_ignored():
    p = buttonlogic.PressLogic(arm_until=10, stuck_secs=5)
    p.update(3, True)
    assert p.update(3.3, False) is None


# ---------------------------------------------------------------- priority / flags / reset

def test_priority_order():
    """Build up evidence one rule at a time: each stronger rule must win."""
    b = armed()
    assert status(b, 20) == "NO SIGNS"
    breathing(b, 20)
    assert status(b, 21) == "POSSIBLE"
    b.no_response(22)
    b.no_response(23)
    assert status(b, 24) == "UNRESPONSIVE"                            # > POSSIBLE
    b.activity(25, "TAP")
    assert status(b, 26) == "LIKELY"                                  # > UNRESPONSIVE
    b.button_press(27)
    b.button_press(28)
    assert status(b, 29) == "CONFIRMED"                               # > LIKELY
    assert ORDER.index("CONFIRMED") < ORDER.index("LIKELY") < ORDER.index("UNRESPONSIVE") \
        < ORDER.index("POSSIBLE") < ORDER.index("NO SIGNS")


def test_danger_never_changes_status():
    b = armed()
    b.set_danger(True)
    assert status(b, 20) == "NO SIGNS"
    b.activity(21, "TAP")
    assert status(b, 22) == "POSSIBLE"
    assert b.evidence(22)["dg"] == 1


def test_reset_clears_evidence_and_counters():
    b = armed()
    b.button_press(20)
    b.button_press(21)
    b.activity(22, "VOICE")
    breathing(b, 23)
    b.no_response(24)
    b.call_response(25, "TAP", 2)
    b.no_response(26)
    b.reset()
    assert b.evaluate(27) == ("NO SIGNS", "no evidence yet")
    assert b.unanswered_calls == 0
    assert b.evidence(27) == {"btn": "none", "snd": "none", "co2": "NONE", "resp": 0, "miss": 0, "dg": 0}


def test_evidence_snapshot_explains_status():
    b = armed()
    breathing(b, 20)
    b.no_response(21)
    b.no_response(22)
    b.activity(23, "TAP")
    ev = b.evidence(24)
    assert ev == {"btn": "none", "snd": "TAP", "co2": "BREATHING", "resp": 0, "miss": 2, "dg": 0}
    b.button_press(25)
    assert b.evidence(26)["btn"] == "first"


# ---------------------------------------------------------------- reset (base "Clear status")

def test_reset_returns_to_no_signs_and_old_evidence_cannot_come_back():
    b = armed()
    b.activity(20, "TAP")                       # would be POSSIBLE until 35
    breathing(b, 21)                             # would be POSSIBLE until new CO2 reading
    b.button_press(22)                           # countdown until 42, then "single press" until 57
    b.call_response(23, "VOICE")                 # would be LIKELY until 83
    b.no_response(24)
    b.reset("status cleared by base")
    for t in (25, 30, 40, 45, 60, 80, 200):      # every old window would still be open
        assert b.evaluate(t) == ("NO SIGNS", "status cleared by base")
    assert b.evidence(25) == {"btn": "none", "snd": "none", "co2": "NONE", "resp": 0, "miss": 0, "dg": 0}


def test_after_reset_new_evidence_counts_normally():
    b = armed()
    b.activity(20, "TAP")
    b.reset("status cleared by base")
    b.activity(30, "VOICE")
    assert b.evaluate(31) == ("POSSIBLE", "heard voice")
    assert b.evaluate(50) == ("NO SIGNS", "no evidence yet")    # normal wording again


def test_reset_is_not_rearming_and_keeps_danger():
    b = armed()
    b.set_danger(True)
    b.reset("status cleared by base")
    b.activity(20, "TAP")                        # no arming phase after a clear
    assert b.evaluate(21)[0] == "POSSIBLE"
    assert b.danger


# ---------------------------------------------------------------- pause / resume (demo)

def test_clear_event_drops_a_held_co2_rise():
    """Pause/resume: a breath held as evidence must not bring POSSIBLE back."""
    c = co2logic.CO2Levels(start=0, **DEMO_CO2)
    t = 0
    while t <= 30:
        c.feed(t, 810, 48)
        t += 5
    for d, drh in [(150, 2), (380, 4)]:
        c.feed(t, 810 + d, 48 + drh)
        t += 5
    assert c.level == "BREATHING"
    c.clear_event()
    t += 30                                      # CO2 still high and flat after the resume
    c.feed(t, 1190, 52)
    assert c.level == "ELEVATED"
    b = armed()
    b.reset("no evidence yet")
    b.set_co2(c.level, c.rise, c.rise_secs, c.rh_jump, t)
    assert b.evaluate(t) == ("NO SIGNS", "no evidence yet")
