# Project context: earthquake survivor-detection sensor node (Code Metal Forge hackathon)

## What we're building
A drop-in sensor node placed in/on earthquake rubble that detects trapped survivors and
reports their status to a US&R (Urban Search & Rescue) team over LoRa, with no internet,
cell, or GPS. The node decides locally whether someone is "a person in trouble" and shows it
on its own screen; later it will also send status + raw evidence to a Raspberry Pi base
station dashboard.

Hackathon: Code Metal Forge, Nov 7–8 2026, Boston. Build window Sat 10:00 → Sun 12:00 (hard
cutoff). Judged equally on real-world relevance, working live demo, deployability
(power/latency/bandwidth/reliability), and ideation. Judges ask: what existed before vs. what
we built this weekend, so commit often with clear messages. A working, demonstrable slice
beats a half-finished whole.

## Core logic: what counts as "a person in trouble"
Sensors only produce EVIDENCE; `assess.py` turns evidence into a STATUS. Rules are checked
strongest-first (first match wins):

| Status | Rule |
|---|---|
| CONFIRMED (green) | PRESS ME button pressed or survivor answered on screen (latched, never expires) |
| LIKELY (yellow) | Taps detected in response to the node's "call" beep (within last 5 min) |
| UNRESPONSIVE (red, urgent) | CO2 rising AND 2+ calls unanswered (possibly unconscious) |
| POSSIBLE (orange) | CO2 rising, OR unprompted taps/noise (within ACTIVITY_WINDOW) |
| NO SIGNS (gray) | No evidence |
| + DANGER flag | Tilt/pile movement (shown on top of any status) |

Call-and-listen: every CALL_INTERVAL seconds (20 s demo, 60 s real) the node beeps 3x, shows
"TAP 3 TIMES", then listens ~10 s. 3+ taps = response → LIKELY. No response = unanswered call.
The node must ignore its own beep (don't listen while beeping + ~0.5 s after).

## Hardware (current, real)
- **Adafruit Feather ESP32-S3 Reverse TFT**: node brain. 240x135 ST7789 TFT on the back,
  3 built-in buttons D0/D1/D2, built-in red LED (board.LED), STEMMA QT/Qwiic I2C port,
  LiPo JST port + MAX17048 fuel gauge.
  - Running **CircuitPython 10.3.1** (installed via Chrome web installer; the shipped
    TinyUF2 0.12.3 bootloader + macOS couldn't copy .UF2 files).
  - D0 = active-LOW (Pull.UP, pressed=False). D1, D2 = active-HIGH (Pull.DOWN, pressed=True).
  - Mounted on a breadboard **screen side up, USB at top**. This flips the usual diagrams:
    the 16-pin side is on the RIGHT (top→bottom: RST, 3V, AR, GND, A0, A1, A2, A3, A4, A5,
    SCK, MO, MI, RX, TX); the 12-pin side is on the LEFT (top→bottom: BAT, EN, USB, 13, 12,
    11, 10, 9, 6, 5, SCL, SDA). Board labels show numbers only ("9" = board.D9).
  - Breadboard power rails were unreliable for us. Sensors are powered by plugging red/black
    into free holes in the same row as the 3V / GND pins.
- **SparkFun SEN-25200**: SCD41 (true CO2, I2C 0x62) + SEN55 (PM/VOC/NOx/temp/RH, 0x69) via
  Qwiic → STEMMA QT. Battery gauge at 0x36. SEN55 fan can cause brownouts; CO2 (SCD41) is
  what matters. Library: adafruit_scd4x. NOT YET TESTED in product code.
- **Adafruit RFM95 LoRa (915 MHz)**: needs headers + ~7.8 cm wire antenna soldered. Never
  transmit without an antenna. Planned wiring: SCK/MO/MI, CS→D10, RST→D11, VIN→3V, GND.
  Library: adafruit_rfm9x. Two Feathers+RFM95 = node + receiver (receiver on Pi via USB).
- **Grove Creator Kit (40-module) modules in use / planned:**
  - Vibration SW-420 → D5 (digital, sensitivity screw). Currently being debugged: it was
    counting taps nonstop (fixed with pull-up + edge detection + learning the 'still' value
    over 1 s at startup).
  - Speaker → D9 (PWM tone, 2000 Hz loudest; red can go to the USB pin if too quiet).
  - Sound/Loudness → A0 (analog). Readings were constant earlier, likely because wires weren't
    connected before headers were soldered. Needs retesting. Power from 3V only (never 5V
    on analog pins).
  - Tilt switch → D6 (pile movement → DANGER flag).
  - Grove Red LED Button: abandoned. The button half never changed state (likely power/rail
    issue). Using the built-in D1 button as PRESS ME and board.LED as the attention LED.
  - Grove wire colors: yellow = signal, white = 2nd signal/unused, red = VCC, black = GND.
- **Base station**: Raspberry Pi 5 (16 GB, AI HAT), 5-port Ethernet switch + USB-C hub for a
  wired offline LAN. Plan: receiver Feather → USB serial (JSON lines) → Python bridge →
  FastAPI + WebSocket + SQLite → single-page dashboard. Pi accessed over SSH, no keyboard.

## Code structure (firmware/node/ → copied to CIRCUITPY)
- `config.py`: ALL pins and thresholds (NODE_ID, DEMO_MODE, CALL_INTERVAL, pin constants).
- `ui.py`: `UI` class: full-screen status color + status/reason/flag labels; `beep()`.
- `assess.py`: `Assessor` class: evidence inputs (`confirmed`, `tap_response`,
  `no_response`, `activity`, `set_co2_rising`, `set_danger`) + `evaluate(now)` → (status, reason).
- `sensors.py`: one small class per sensor, each with `update(now)` that returns quickly
  (e.g. `Button` returns True once per press; `Vibration` returns True once per tap).
- `code.py`: main loop, SENSE → DECIDE → SHOW, ~100x/s, redraw only when status changes.
- `radio.py`: LoRa (not written yet).
- `tests/`: standalone hardware tests + `tests/RESULTS.md` (measured values/thresholds).
- Planned base: `base/bridge.py`, `base/app.py`, `base/simulator.py`, `base/static/index.html`.

## Rules for code you write
1. CircuitPython 10.x on ESP32-S3. Only use libraries available in the Adafruit bundle
   (installed with `circup`). Keep memory use small.
2. **The main loop must never block.** No long `time.sleep()`. Use `time.monotonic()` timers
   ("do X every N seconds") so a tap or button press is never missed. Short blocking beeps are OK.
3. Sensors report evidence only; all judgment lives in `assess.evaluate()`.
4. New pins/thresholds go in `config.py`, never hard-coded elsewhere.
5. Every sensor class learns its baseline/idle state at startup and uses edge detection +
   debounce, not "is it different from idle right now."
6. Print a clear console line for every EVENT and every STATUS change (it's our debug log
   and demo narration).
7. Keep code simple and explained. The team is new to hardware. Add short comments on *why*.
8. Radio messages: compact JSON well under ~250 bytes, e.g.
   `{"t":"HB","n":1,"co2":812,"bat":87}`, `{"t":"TAP","n":1,"taps":4}`, `{"t":"BTN","n":1}`;
   base→node: `{"t":"CALL"}`, `{"t":"MSG","txt":"..."}`, `{"t":"ASK","q":"injured"}`.
   Use send_with_ack for urgent ones (BTN, ANS, SHIFT).

## Dev workflow
- Edit in repo `firmware/node/`, then deploy: `cp firmware/node/*.py /Volumes/CIRCUITPY/`
- Serial console: Mu (Serial/Plotter) or `screen /dev/cu.usbmodem*`. Ctrl-C = stop/REPL,
  Ctrl-D = soft reload. Print tuples like `print((value,))` for the Mu Plotter.
- Commit after each working step: `type(area): what happened + key number`.

## Build order (where we are)
1. ✅ Main loop + status brain + screen
2. ✅ PRESS ME (built-in D1) → CONFIRMED
3. 🔧 Vibration (D5) → POSSIBLE (unprompted taps), currently debugging false triggers
4. ⏳ Speaker call-and-listen cycle → taps after call = LIKELY; no answer counted
5. ⏳ CO2 (SCD41) baseline + rise detection → POSSIBLE; + unanswered calls → UNRESPONSIVE
6. ⏳ Tilt (D6) → DANGER flag
7. ⏳ Radio (RFM95) + receiver Feather + Pi bridge + dashboard
8. ⏳ Two-way: dashboard MSG/ASK shown on node screen; survivor answers with D0/D1/D2

When helping, work on ONE step at a time, explain changes in plain language, give complete
file contents for any file you change, and tell me exactly how to test it on the hardware.