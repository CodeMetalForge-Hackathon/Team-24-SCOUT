# Hardware test results — Sat Nov 7

## Summary
| # | Test | Pass | Result | Who | Time |
|---|------|------|--------|-----|------|
| 01 | Screen + buttons | ✅ | TFT displays text; all 3 buttons detected | Kiran | 3:05 PM |
| 03 | CO2 (SCD41) | ✅ | Room ~810 ppm; breath +150 in 5 s, +380 in 10 s, peak +460 at 30 s; RH 48→54 % | Kiran | Sat |

---

## 01 — Screen + buttons
**File:** `tests/01_board_buttons.py`
**Board:** Feather ESP32-S3 Reverse TFT, CircuitPython 10.3.1
**Wiring:** none (built-in screen and buttons)

**Results**
| Button | Idle value | Pressed value | Screen |
|--------|-----------|---------------|--------|
| D0 | True | False | "Pressed D0" ✅ |
| D1 | False | True | "Pressed D1" ✅ |
| D2 | False | True | "Pressed D2" ✅ |

**Notes / issues**
- D0 is active-low (pull-up); D1 and D2 are active-high (pull-down). Pulls must be set explicitly in code; reading the idle state at startup gave false "Pressed D0".
- Board shipped with old TinyUF2 bootloader (0.12.3), and macOS couldn't copy the .UF2. Fixed with the Chrome web installer (Full CircuitPython Install).

**Use in final node**
- D0 / D1 / D2 = survivor answer buttons (Yes / No / Count)
- Screen = rescuer messages to survivor

| 06 | Speaker | ✅ | 3-beep call + alarm audible at ~2 m inside box | Kiran | 3:15 PM |

## 06 — Speaker
**File:** `tests/06_speaker.py`
**Wiring:** red → 3V (or USB if quiet), black → GND, yellow → D9
**Results:** call tone 880 Hz, alarm 1500/1000 Hz; audible at __ m inside box; volume knob at __
**Notes:** __
**Use in final node:** call-and-listen beeps, "help is coming" signal, pile-shift alarm

## 03 — CO2 (SparkFun SEN-25200: SCD41 + SEN55)
**File:** `tests/03_co2.py`
**Wiring:** Qwiic-to-male cable into breadboard rows: red → 3V, black → GND, blue → SDA, yellow → SCL. `board.I2C()`.
**I2C scan:** `0x36` (battery gauge), `0x62` (SCD41 CO2), `0x69` (SEN55)
**Library:** `adafruit_scd4x`. New reading every ~5 s.

**Results**
| Situation | CO2 | Humidity |
|---|---|---|
| Room baseline | ~810 ppm (drifts as the room fills) | ~48 % |
| Breathing on the sensor, after ~5 s | +150 ppm | rising |
| Breathing on the sensor, after ~10 s | +380 ppm | rising |
| Peak, after ~30 s | +460 ppm (1275 ppm) | ~54 % |
| 3 min after stopping | +240 ppm (slow decay) | |

**Chosen DEMO thresholds** (`PROFILE = "DEMO"` in `firmware/node/config.py`, details in `docs/co2.md`)

Only a **sudden rise** counts as evidence: the room drifts, so "ppm above baseline" gave false alarms.

| Setting | Value | Why |
|---|---|---|
| `CO2_RATE_WINDOW` / `CO2_SUDDEN_PPM` | +120 ppm within 15 s | a breath passes it on the 1st reading (+150) and 2nd (+380); slow room drift never does |
| 2 readings in a row | | one noisy reading can't trigger it → BREATHING ~10 s after breathing starts |
| `CO2_EVENT_HOLD` | 60 s | evidence clears 1 min after the breath, although CO2 takes minutes to decay |
| `RH_RISE_POINTS` | 3 | breath raised RH ~6 points → BREATHING vs SUDDEN_RISE |
| `CO2_BASELINE_SECS` | 30 | 6 readings to learn the room (display + ELEVATED label only) |
| `CO2_BASELINE_FOLLOW` | 0.02 | baseline follows room drift; frozen during a rise |
| `SLOW_BUILDUP_ENABLED` | False | FIELD only (real voids fill slowly) |

**Notes / issues**
- The SEN55 is NOT started in the node: its fan adds noise right next to the sound sensor.
- Decay is slow (+240 after 3 min); that's why evidence uses a fixed hold time instead of "still above baseline".

**Use in final node:** sudden CO2 rise / breathing = supporting evidence (POSSIBLE; with knocking or a voice → LIKELY). Never CONFIRMED.