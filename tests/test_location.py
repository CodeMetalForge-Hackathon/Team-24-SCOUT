"""Location sources (LIVE / LAST / MANUAL / NONE), flash save rules and the
header text: the real firmware/node/location.py with a fake clock.

    python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "firmware", "node"))

import location  # noqa: E402

LAT, LON = 42.36031, -71.05880


def test_no_gps_no_manual_is_none():
    loc = location.Location()
    assert loc.best(5) == {"src": "NONE"}
    assert location.header_tag(loc.best(5)) == "NO GPS"


def test_live_fix_then_lost_becomes_last_with_growing_age():
    loc = location.Location(live_secs=10)
    loc.on_fix(100, LAT, LON, 1.6, 7)
    b = loc.best(101)
    assert (b["src"], b["acc"], b["sats"]) == ("LIVE", 8, 7)          # acc ~ hdop x 5 (estimate)
    assert location.header_tag(b) == "GPS LIVE"
    b = loc.best(112)                                                  # no fix for 12 s
    assert (b["src"], b["age"]) == ("LAST", 12)
    assert loc.best(400)["age"] == 300
    assert location.header_tag(loc.best(400)) == "GPS 5m ago"


def test_live_always_beats_manual():
    loc = location.Location()
    loc.set_manual(50, 42.0, -71.0, "Site B")
    loc.on_fix(60, LAT, LON, 1.0, 9)
    assert loc.best(61)["src"] == "LIVE"


def test_manual_beats_older_last_fix_but_not_newer():
    loc = location.Location(live_secs=10)
    loc.on_fix(100, LAT, LON, 1.0, 8)
    loc.set_manual(200, 42.0, -71.0, "Site B NE corner")
    b = loc.best(210)
    assert (b["src"], b["label"]) == ("MANUAL", "Site B NE corner")
    assert location.header_tag(b) == "LOC MANUAL"
    loc2 = location.Location(live_secs=10)
    loc2.set_manual(50, 42.0, -71.0, "old guess")
    loc2.on_fix(100, LAT, LON, 1.0, 8)
    assert loc2.best(150)["src"] == "LAST"                             # fix is newer than manual


def test_flash_round_trip_age_unknown_after_restart():
    loc = location.Location()
    loc.on_fix(100, LAT, LON, 1.6, 7)
    data = bytearray(64)
    data[0:location.NVM_SIZE] = loc.to_saved()
    after_restart = location.Location()
    assert after_restart.load_saved(data)
    b = after_restart.best(3)
    assert b["src"] == "LAST" and b["age"] == -1
    assert abs(b["lat"] - LAT) < 1e-6 and abs(b["lon"] - LON) < 1e-6
    assert location.header_tag(b) == "GPS old"
    after_restart.set_manual(5, 42.0, -71.0, "x")                     # any manual set now is newer
    assert after_restart.best(6)["src"] == "MANUAL"


def test_blank_or_garbage_flash_is_ignored():
    assert not location.Location().load_saved(bytearray(64))
    assert not location.Location().load_saved(b"\xff" * 64)


def test_save_at_most_once_a_minute_and_only_if_moved():
    loc = location.Location(save_every=60, save_move_m=10)
    loc.on_fix(100, LAT, LON, 1.0, 8)
    assert loc.should_save(100)
    loc.mark_saved(100)
    loc.on_fix(130, LAT + 0.001, LON, 1.0, 8)                          # moved ~110 m but too soon
    assert not loc.should_save(130)
    loc.on_fix(170, LAT + 0.00005, LON, 1.0, 8)                        # 1 min later, moved ~5 m
    assert not loc.should_save(170)
    loc.on_fix(171, LAT + 0.001, LON, 1.0, 8)                          # moved ~110 m
    assert loc.should_save(171)


def test_distance():
    assert abs(location.dist_m(LAT, LON, LAT + 0.001, LON) - 111.3) < 0.5
