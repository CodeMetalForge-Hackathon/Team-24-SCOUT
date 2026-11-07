# Hardware test results — Sat Nov 7

## Summary
| # | Test | Pass | Result | Who | Time |
|---|------|------|--------|-----|------|
| 01 | Screen + buttons | ✅ | TFT displays text; all 3 buttons detected | Kiran | 3:05 PM |

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