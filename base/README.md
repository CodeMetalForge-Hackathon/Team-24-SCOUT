# Base station + dashboard

Receives messages from the rubble nodes (LoRa → receiver Feather → USB serial), logs every
message to SQLite, and serves a live dashboard to any laptop on our Ethernet switch.
**Runs fully offline**: the dashboard is one local HTML file, with no CDNs or web fonts.

```
node ──LoRa──> receiver Feather ──USB serial, 1 JSON line per packet──> base/app.py ──WebSocket──> browsers
                                <──────── 1 JSON line per command ───────
```

## Setup (once, while you still have internet)

Needs Python 3.11+. Run everything from the **repo root** (`Team-24/`).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r base/requirements.txt
```

Do this on the Pi **before** going offline. Wheels downloaded on a Mac won't work on the Pi.

## Run

```bash
source .venv/bin/activate

# Demo / development: fake nodes, no hardware needed
python -m base.app --source sim

# Real hardware on USB: a node Feather directly (no radio yet), or later the LoRa receiver
python -m base.app --source serial --port /dev/cu.usbmodem1234   # Mac
python -m base.app --source serial --port /dev/ttyACM0          # Pi
```

**Reading a node directly over USB (current setup, until LoRa works):** the node prints the
same JSON lines the radio will carry, alongside its normal debug text (`EVENT: ...`,
`STATUS: ...`), which the app skips. Commands from the dashboard (Reset, messages) go back
down the same USB cable.

1. **Close Mu's Serial panel** (or quit Mu). Only one program can use the port at a time.
2. Find the port: `ls /dev/cu.usbmodem*`
3. Run `python -m base.app --source serial --port /dev/cu.usbmodem…` and open http://localhost:8000.

To see Mu's console again, stop the app first (Ctrl-C).

Open **http://localhost:8000**. When the app starts, it prints the address other laptops on the switch
should use, e.g. `http://192.168.1.20:8000`. On a Mac, click **Allow** if the firewall asks.

Options: `--http-port 8000`, `--baud 115200`, `--db path.db`, `--one-node` (simulator with node 1 only).

### What the simulator does
The fake nodes run the **real node logic** (`firmware/node/assess.py`, `co2logic.py` and
`caller.py`, DEMO profile), so statuses, calls, CO2 levels and countdowns match the board
exactly; only the raw sensor values and the "survivor" are invented. Node 1 loops a ~6 min
story through every rule (calls every ~32 s throughout; the CO2 baseline is learned in the first 30 s):

| Time | What happens | Status |
|---|---|---|
| 36 s | breathing on the CO2 sensor | POSSIBLE "breathing detected …" |
| 50 s | unprompted knock while breathing | **LIKELY "breathing + knocking"** |
| ~110 s | CO2 evidence expires | NO SIGNS (calls keep going unanswered) |
| 175 s | breathing again, several calls already missed | **UNRESPONSIVE "breathing but no answer to N calls"** (red, pulsing) |
| ~218 s | knocks back during a call | **LIKELY "tapped back 2 times after call"** |
| 290 s | unprompted knock | POSSIBLE "heard knocking" |
| 305 / 312 s | press, press again | LIKELY countdown (calls paused) → **CONFIRMED** (calls stop) |
| 330–345 s | danger alarm on/off | ⚠ DANGER banner, siren every 10 s |

Node 2 stays NO SIGNS and never answers its calls. The buttons work: **Call now** starts a call
as soon as the node allows it, **Danger alarm** switches the siren on/off, **Send** messages make
the node beep, and **Reset** clears the node and restarts its story.

Click **🔔 Enable alert sound** once: browsers only play sound after you've clicked on the page.
CONFIRMED then plays three rising beeps.

## Finding the serial port

Plug in the receiver Feather, then:

```bash
ls /dev/ttyACM*            # Pi / Linux  -> usually /dev/ttyACM0
ls /dev/cu.usbmodem*       # Mac
ls /dev/serial/by-id/      # Pi: stable name that doesn't change between reboots (best choice)
```

On the Pi, if you get "permission denied": `sudo usermod -aG dialout $USER`, then log out and back in.
Close Mu / `screen` first: only one program can hold the port.
If the cable is pulled, the app keeps running, shows **Receiver offline** in red, and reconnects
on its own.

## Pi: start on boot (optional)

`/etc/systemd/system/rubble-base.service` (change `pi` and the path to match):

```ini
[Unit]
Description=Rubble base station
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/Team-24
ExecStart=/home/pi/Team-24/.venv/bin/python -m base.app --source serial --port /dev/ttyACM0
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now rubble-base
journalctl -u rubble-base -f        # live log
```

## Message protocol

Node → base (the receiver adds `rssi`). Lines that aren't valid are logged and skipped. They never crash the app.

| Line | Meaning |
|---|---|
| `{"t":"HB","n":1,"st":"POSSIBLE","why":"sudden CO2 rise +240 ppm in 10s","co2":1050,"base":810,"rh":49.5,"rh_base":48.1,"lvl":"SUDDEN_RISE","hold":42,"prof":"DEMO","dg":0,"danger":false,"call_state":"LISTENING","listen_left":7,"next_call_in":0,"calls_unanswered":1,"mic":"on","rssi":-48}` | heartbeat, every 10 s (DEMO) / 30 s (FIELD); `hold` = seconds of CO2 evidence left |
| `{"t":"EV","n":1,"e":"CO2","lvl":"BREATHING","prev":"NORMAL","rise":380,"secs":10,"rh_jump":6,"hold":60,"base":810}` | CO2 level changed: LEARNING / NORMAL / ELEVATED (info only) / SUDDEN_RISE / BREATHING / SLOW_BUILDUP (FIELD). See [docs/co2.md](../docs/co2.md) |
| `{"t":"ST","n":1,"st":"LIKELY","why":"pressed once - press again (14s)"}` | status changed (countdown ticks at most every 5 s; the page counts down in between) |
| `{"t":"EV","n":1,"e":"BTN","stage":"first"}` | PRESS ME pressed once: waiting for a 2nd press within 20 s |
| `{"t":"EV","n":1,"e":"BTN","stage":"confirmed"}` | pressed twice: **survivor confirmed** (alert sound) |
| `{"t":"EV","n":1,"e":"STUCK"}` | button held ≥ 5 s: probably debris, press ignored |
| `{"t":"EV","n":1,"e":"TAP"}` / `{"t":"EV","n":1,"e":"VOICE"}` | mic heard knocking / a voice |
| `{"t":"EV","n":1,"e":"CALL"}` | node started a call (3 beeps), then listens 10 s |
| `{"t":"EV","n":1,"e":"RESP","kind":"TAP","taps":3}` / `"kind":"VOICE"` | survivor answered the call |
| `{"t":"EV","n":1,"e":"NORESP","calls":2}` | call went unanswered (2 in a row) |
| `{"t":"EV","n":1,"e":"BEEP","p":"CONFIRM"}` | node played a beep pattern (logged, not on the timeline) |
| `{"t":"EV","n":1,"e":"DANGER","on":true}` | danger alarm (siren) on / off |
| `{"t":"EV","n":1,"e":"ANS","q":"injured","a":"yes"}` | survivor answered a question |

Base → node (written to the receiver's serial port, one line each):
`{"t":"CALL","n":1}`, `{"t":"MSG","n":1,"txt":"Help is coming. Stay still."}` (max 60 chars),
`{"t":"DANGER","n":1,"on":true}`, `{"t":"RESET","n":1}` (false alarm: node forgets all
evidence and counters). The node handles CALL, MSG, DANGER and RESET; `ASK` is for later.

Statuses: `NO SIGNS`, `POSSIBLE`, `LIKELY`, `CONFIRMED`, `UNRESPONSIVE`. The dashboard shows
`LOST` itself when it hasn't heard from a node for 90 s.

## How the node talks to a survivor and decides

### Beep language (Grove Speaker, loudest ~3000 Hz)

| Pattern | Sound | Node screen | Meaning |
|---|---|---|---|
| CALL | 3 beeps (3000 Hz, 0.35 s on/off) | TAP 3 TIMES IF YOU HEAR THIS → TAP 3 TIMES NOW, LISTENING 7s | "Is anyone there?" Then it listens for 10 s |
| PRESS_ME | one long beep (0.9 s) | WE HEAR YOU. PRESS THE RED BUTTON | the node heard an answer |
| AGAIN | 2 quick high beeps | PRESS AGAIN TO CONFIRM (14s) | first press registered |
| CONFIRM | rising 3-note chime | RESCUERS NOTIFIED. HELP IS COMING | pressed twice: survivor confirmed |
| MESSAGE | one 0.6 s beep | the rescuer's text (15 s) | message from the base |
| DANGER | wailing siren, every 10 s | !! DANGER !! CLEAR THE AREA | pile moving: rescuers get out |

**Why the call is 3 beeps:** a survivor (or anyone) naturally answers a rhythm by copying it,
so the node asks for 3 knocks. Debris settling or a single bang rarely produces 2–3 knocks
inside the 10 s listening window, and the node accepts **2 knocks or any voice** as an answer
(someone weak may not manage 3).

**Microphone muting:** the sound sensor sits 10–20 cm from the speaker. While any pattern plays,
and for 0.6 s after it (echo), sounds are ignored, and any sound already in progress is
thrown away when the mic switches back on. So the node's own beeps can never count as knocks.
The screen header shows **MIC OFF** while muted.

**Call schedule:** a call every 20 s (DEMO) / 60 s (FIELD) after the previous one ends. Calls are
paused during the first 10 s after power-on, during the press-again countdown and while the
siren plays, and they stop once CONFIRMED. A button press during a call cancels it. Sounds heard
while listening are *answers*; sounds outside listening are *unprompted* activity.

### Decision table (`firmware/node/assess.py`, strongest first, first match wins)

"CO2 evidence" = CO2 level SUDDEN_RISE, BREATHING or SLOW_BUILDUP (FIELD only). ELEVATED,
NORMAL and LEARNING never change the status. CO2 alone can never make CONFIRMED.

| # | Status | Rule | Reason shown |
|---|---|---|---|
| 1 | **CONFIRMED** | button pressed twice within 20 s (latched until Reset) | pressed button twice |
| 2a | **LIKELY** | first press, waiting for the second | pressed once - press again (14s) |
| 2b | LIKELY | answered a call within 60 s (DEMO) / 300 s (FIELD) | tapped back 3 times after call / answered by voice after call |
| 2c | LIKELY | CO2 evidence + an unprompted knock/voice within 120 s | breathing + knocking / CO2 rise + voice |
| 3 | **UNRESPONSIVE** | CO2 evidence + ≥ 2 calls in a row unanswered | breathing but no answer to N calls |
| 4a | **POSSIBLE** | CO2 BREATHING | breathing detected: CO2 +X ppm, humidity +Y% |
| 4b | POSSIBLE | CO2 SUDDEN_RISE | sudden CO2 rise +X ppm in Ys |
| 4c | POSSIBLE | CO2 SLOW_BUILDUP (FIELD) | slow CO2 build-up +X ppm over N min |
| 4d | POSSIBLE | unprompted knock/voice within 15 s (DEMO) | heard knocking / heard voice |
| 4e | POSSIBLE | a single press that was never confirmed | single press, not confirmed |
| 5 | NO SIGNS | nothing else | no evidence yet |

- The unanswered-call counter goes back to 0 on any answer or button press. If the CO2 evidence
  expires, UNRESPONSIVE falls back by itself, but the counter is kept.
- **DANGER** (siren, from the dashboard or the tilt switch) is a separate flag shown on top of any
  status; it never changes the status, and Reset doesn't clear it.
- **Reset** clears all evidence and counters, not the sensors' baselines.

## API

| | |
|---|---|
| `GET /api/state` | every node's current state + recent timeline |
| `GET /api/events?n=1&limit=200` | logged messages from SQLite, newest first (`include_bad=true` to see rejected lines) |
| `POST /api/cmd` | body `{"t":"CALL","n":1}` etc. → `400` if invalid, `503` if the receiver isn't connected |
| `WS /ws` | full state on connect, then a push for every message |

## Files

| File | What it does |
|---|---|
| `app.py` | web server, WebSocket push, command endpoint, startup options |
| `protocol.py` | parses/validates lines, turns them into plain English for the timeline |
| `state.py` | in-memory status of each node (LOST after 90 s) |
| `db.py` | SQLite log: `events(ts, node, type, dir, raw)` |
| `sources.py` | `SerialSource`: reads the receiver in a background thread, reconnects |
| `simulator.py` | `SimSource`: the fake demo nodes |
| `node_brain.py` | loads `firmware/node/assess.py` and `co2logic.py` so the simulator and tests use the real node rules |
| `static/index.html` | the dashboard (vanilla JS, canvas chart) |

Logs: `base/events.db` (serial) and `base/events_sim.db` (simulator, kept separate so fake
data never mixes with a real rescue log). On restart, the app rebuilds node state from the last 6 h of the log.
Look inside with `sqlite3 base/events.db "select * from events order by id desc limit 20"`.

## Tests

```bash
python -m unittest discover base/tests
```
