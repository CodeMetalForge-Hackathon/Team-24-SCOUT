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
Three fake nodes run the **real node logic** (`firmware/node/assess.py`, `co2logic.py`, `caller.py`,
`location.py`, DEMO profile) and send exactly the messages the board sends. Only the raw sensor
values and the "survivors" are invented. The story loops every ~7 min; at the start every node
boots (BOOT → NO SIGNS "starting up (arming 10s)") and calls every ~32 s.

| Time | Node 1 (GPS LIVE, ~40 m NE) | Node 2 (~145 m SW) | Node 3 (no GPS) |
|---|---|---|---|
| 0–10 s | arming | arming | arming |
| 15 s | | GPS fix → **LIVE** | NO POSITION → set it on the dashboard → **MANUAL** |
| 36 s | breathing → POSSIBLE "breathing detected" | | |
| 50 s | + unprompted knock → **LIKELY "breathing + knocking"** | | |
| 105 s | | voice → POSSIBLE "heard voice" | |
| 150 s | | fix lost → **LAST KNOWN**, age growing | |
| 175 s | breathing, calls missed → **UNRESPONSIVE** (alert) | | |
| ~218 s | knocks back in a call → LIKELY "tapped back 2 times" | | |
| 240 s | | | goes silent → **LOST** at ~330 s |
| 290 s | unprompted knock → POSSIBLE "heard knocking" | | |
| 305 / 312 s | press → countdown, press again → **CONFIRMED** (alert) | | |
| 330–345 s | **DANGER** alarm on/off (alert) | | |

The dashboard commands work against the simulator too: Call now, messages, Danger alarm,
Set location manually, Reset (restarts the story).

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

## Wireless link

```
node (Feather ESP32-S3) ──ESP-NOW broadcast──> LilyGO ESP32 receiver ──USB, 1 JSON line per packet──> base/app.py
                        <──────── commands: the receiver broadcasts each one 3x ────────
```

**Prototype: ESP-NOW.** Direct ESP32-to-ESP32 radio: no router, no WiFi network, no pairing. Each node
broadcasts every message (it still prints it to USB too). The LilyGO receiver keeps only packets with our
network ID `"net":"T24"` (other teams broadcast ESP-NOW too), adds `rssi` and prints one JSON line per packet.
Measured: received at RSSI about −26 dBm at 1 m.

**Production: LoRa.** The LilyGO already has a LoRa radio. LoRa reaches kilometres and gets through rubble
much better than 2.4 GHz. **The message protocol is identical**, so the radio is swappable without changing
the node logic or the dashboard (`LINK_TYPE` in `firmware/node/config.py`).

How it stays reliable:
- every message has `net` + a short `id`; **urgent** ones (button presses, call answers / missed calls, danger,
  status UNRESPONSIVE / CONFIRMED) are sent **3 times ~100 ms apart with the same id**. The base keeps the first
  copy (same node + id within 10 s) and drops the rest;
- the base ignores everything without `"net":"T24"` (`--net` to change it) and every non-JSON line (the
  receiver's boot text);
- commands from the dashboard get `net` + `id`; the receiver broadcasts each 3 times and the node handles it
  once (same id within 5 s). The node only accepts our network ID, its own node number and known commands;
- nothing received over the air can crash the node: malformed packets ("Invalid buffer"), non-text and
  non-JSON payloads are skipped. If ESP-NOW doesn't start, the node carries on USB-only.

**Run it:** plug the LilyGO into the laptop, power the node from a power bank, then
`python -m base.app --source serial --port /dev/cu.usbserial-…` (find it with `ls /dev/cu.*`; the LilyGO often
shows up as `usbserial` or `wchusbserial`; add `--baud` if its sketch doesn't use 115200).
The dashboard shows the link type (ESP-NOW / USB), each node's RSSI and link quality: **good** (> −70 dBm),
**weak** (−70 to −85), **poor** (< −85), **lost** (no message for 30 s).

## Message protocol

One JSON object per line, every message < 200 bytes so it fits one LoRa packet. The receiver
adds `rssi`. Lines that aren't valid are logged and skipped and never crash the app; unknown
fields are ignored.

**Node → base**

| Line | When / meaning |
|---|---|
| `{"t":"EV","n":1,"e":"BOOT","sensors":{"sound":true,"co2":true,"gps":false,"button":true,"speaker":true}}` | node started: self-check of its parts |
| `{"t":"ST","n":1,"st":"UNRESPONSIVE","why":"breathing but no answer to 2 calls","ev":{…}}` | status or reason changed (countdowns at most every 5 s) |
| `{"t":"HB","n":1,"st":"POSSIBLE","ev":{"btn":"none","snd":"TAP","co2":"BREATHING","resp":0,"miss":1,"dg":0},"cs":"LISTENING","ll":7,"nc":0,"mic":"on","prof":"DEMO"}` | heartbeat every 10 s (DEMO) / 30 s (FIELD): status, evidence, call cycle (`cs` state, `ll` listen seconds left, `nc` next call in) |
| `{"t":"SN","n":1,"co2":1127,"base":813,"rh":52.3,"rhb":48.1,"lvl":"BREATHING","hold":54,"snd":12000,"bat":87}` | sensor readings, with every heartbeat |
| `{"t":"LOC","n":1,"src":"LAST","lat":42.36031,"lon":-71.0588,"acc":8,"age":312,"sats":7}` | location, every 30 s and when the source changes (`label` for MANUAL) |
| `{"t":"EV","n":1,"e":"BTN","stage":"first"}` / `"stage":"confirmed"` | button pressed once / twice |
| `{"t":"EV","n":1,"e":"STUCK"}` | button held ≥ 5 s: probably debris, ignored |
| `{"t":"EV","n":1,"e":"TAP"}` / `{"t":"EV","n":1,"e":"VOICE"}` | unprompted knocking / voice |
| `{"t":"EV","n":1,"e":"CALL"}` | node started a call (3 beeps), then listens 10 s |
| `{"t":"EV","n":1,"e":"RESP","kind":"TAP","taps":3}` / `"kind":"VOICE"` | survivor answered the call |
| `{"t":"EV","n":1,"e":"NORESP","calls":2}` | call went unanswered (2 in a row) |
| `{"t":"EV","n":1,"e":"CO2","lvl":"BREATHING","prev":"NORMAL","rise":380,"secs":10,"rh_jump":6,"hold":60}` | CO2 level changed. See [docs/co2.md](../docs/co2.md) |
| `{"t":"EV","n":1,"e":"DANGER","on":true}` | danger alarm (siren) on / off |
| `{"t":"EV","n":1,"e":"BEEP","p":"CONFIRM"}` | node played a beep pattern (logged, not on the timeline) |

`ev`, the **evidence snapshot**, says why the node chose its status: `btn` none/first/confirmed,
`snd` none/TAP/VOICE (unprompted, last 2 min), `co2` level, `resp` 1 if it answered a call recently,
`miss` calls missed in a row, `dg` danger alarm. The dashboard shows it as the "Why this status" list.
(Older nodes that send the long field names `call_state`, `listen_left`, `next_call_in`,
`calls_unanswered`, `rh_base` and everything in HB are still understood.)

**Base → node** (written to the serial port, one line each): `{"t":"CALL","n":1}`,
`{"t":"MSG","n":1,"txt":"Help is coming. Stay still."}` (max 60 chars), `{"t":"DANGER","n":1,"on":true}`,
`{"t":"SETLOC","n":3,"lat":42.36,"lon":-71.05,"label":"Site B NE corner"}`, `{"t":"RESET","n":1}`.

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
| 0 | NO SIGNS | first 10 s (DEMO) / 30 s (FIELD) after power-on: **arming**. Sensors settle, no evidence is recorded, no calls | starting up (arming 7s) |
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

### Location sources

| Source | Meaning | Dashboard |
|---|---|---|
| **LIVE** | the GPS (PA1010D) has a fix right now (< 10 s old) | green "GPS LIVE", accuracy ± m (estimate: HDOP × 5) |
| **LAST** | the last good fix; the GPS lost it (indoors, under rubble). Saved in the node's flash, so it survives a restart; after a restart the age is unknown | orange "LAST 5 min" with its age |
| **MANUAL** | typed in on the dashboard ("Set location manually" → SETLOC). A LIVE fix always wins; MANUAL beats LAST only if it's newer | blue "MANUAL" with its label |
| **NONE** | nothing known | "NO POSITION" (listed beside the radar) |

## Using the dashboard as a team

Open `http://<base-station>:8000` on every laptop/tablet at the command post. Everything is shared:
an acknowledgement, a reset or a pause made on one screen appears on all of them.

1. **Set up once:** click the incident name to rename it; on **Situation**, enter the base
   (command post) location so the radar can place nodes; click **👤** to enter your name
   (recorded with your acknowledgements); click **🔇** to turn the alert sound on.
2. **Work the triage list (main view).** It is sorted by urgency, then by who has waited longest:

   | Order | Status | Guidance (also shown as tooltip and in the detail panel) |
   |---|---|---|
   | 1 | ⚠ **UNRESPONSIVE** (red, pulsing) | Breathing detected but no answer to calls: possible unconscious survivor. HIGH PRIORITY. |
   | 2 | ✔ **CONFIRMED** (green) | Conscious survivor in contact at this node. Dispatch extraction team. |
   | 3 | ● **LIKELY** (yellow) | Probable survivor responding. Attempt contact. |
   | 4 | ◐ **POSSIBLE** (orange) | Weak sign of life. Investigate when able. |
   | 5 | ○ **NO SIGNS** (gray) | No evidence yet. |
   | 6 | ✕ **LOST** (dark) | Node silent: check battery or radio. |
   | flag | ⛔ **DANGER** | Pile movement / danger alarm: clear the area. |

3. **Alerts.** When a node becomes UNRESPONSIVE or CONFIRMED, or its danger alarm goes on, a banner
   appears and a sound repeats every 30 s until someone presses **Acknowledge**. The ack (who,
   when) shows in the list's Alert column on every screen.
4. **Click a node** for the detail panel. It shows why the node chose its status (evidence), the
   call cycle, the CO₂ chart and location (copy lat/lon, distance and bearing from base), and lets
   you talk to the survivor (Call now, preset or typed messages: the survivor sees them on the
   node's screen).
5. **Situation** is an offline "radar": base in the centre, range rings, north up, nodes coloured
   by status at their best known position. Nodes without a position are listed beside it.
6. **Event log**: everything that happened, filterable (Alerts / Survivor comms / Sensors /
   System). **Export CSV / JSON** downloads the whole log for the after-action report.
7. **Reset node** only for a confirmed false alarm: it clears the node's evidence and counters.
   **Danger alarm (test)** sounds the node's siren.

Use the sun / moon button to switch between day and night mode.

**Pause all nodes (demo):** top-bar menu → *Pause all nodes*. Every node stops sensing, calling, beeping and
reporting (screen: PAUSED (DEMO), LEDs off) and only sends "I'm paused" every 30 s, so it isn't marked LOST.
Pausing clears every node's evidence: all nodes show **NO SIGNS** (marked "paused (demo)") and, after you resume
(blue *PAUSED · resume* badge or the menu), they stay at NO SIGNS until something new and real happens.

### Resetting a node

| Level | Where | What the node does | What the dashboard clears | Kept |
|---|---|---|---|---|
| **Clear status** (`RESET`) | detail panel → Reset node… | forgets evidence and counters, stops beeping, NO SIGNS "status cleared by base" | nothing | everything, incl. history |
| **Full reset** (`REBOOT`) | detail panel → Reset node… (type RESET) | restarts: arming, CO₂ baseline re-learned, BOOT | that node's timeline, charts, alerts + acks, messages, manual location | GPS last fix if ticked (default on); one audit line |
| **New incident** | top-bar menu → New incident… (type RESET) | every node restarts | everything (all nodes, alerts, charts) | base location; one audit line |

Before anything is deleted it is **archived** to `base/archive/<incident>_<node1|all>_<date-time>.json` and `.csv`
(not committed to git). The live log keeps one audit line, e.g. *"Node 1 fully reset by Ana at 00:12
(archived: …)"*. After a full reset the node shows RESTARTING until it reports again (its BOOT, or, if the base
missed that while the USB port was re-opening, its next status or heartbeat); readings sent just before the restart are
ignored. After 30 s without anything: *"Reset sent, node not responding: check power"*.

## API

| | |
|---|---|
| `GET /api/state` | every node's current state + recent timeline |
| `GET /api/events?n=1&limit=200` | logged messages from SQLite, newest first, with plain-English `desc` and `cat` (`include_bad=true` to see rejected lines; up to 20000 for export) |
| `POST /api/ack` | `{"id":12,"by":"Ana"}` or `{"n":2,"by":"Ana"}` (all of a node's alerts) |
| `POST /api/meta` | `{"incident":"…","base":{"lat":…,"lon":…},"by":"…"}` |
| `POST /api/node_meta` | `{"n":2,"team":"…","tag":"…","notes":"…","by":"…"}` (kept in the API; no longer shown on the dashboard) |
| `POST /api/pause` | `{"on":true,"by":"Ana"}` pauses every node for a demo, `{"on":false}` resumes |
| `POST /api/reset` | full reset of one node: `{"n":1,"keep":{"gps":true},"confirm":"RESET","by":"Ana"}` |
| `POST /api/new_incident` | `{"name":"…","keep_gps":true,"confirm":"RESET","by":"Ana"}` |
| `POST /api/cmd` | body `{"t":"CALL","n":1}` etc. → `400` if invalid, `503` if the receiver isn't connected |
| `WS /ws` | full state on connect (nodes, timeline, alerts, incident settings, team notes), then a push for every change |

## Files

| File | What it does |
|---|---|
| `app.py` | web server, WebSocket push, commands, alerts + acknowledgements, incident / team settings |
| `protocol.py` | parses/validates lines, turns them into plain English for the timeline |
| `state.py` | in-memory status of each node (LOST after 90 s) |
| `db.py` | SQLite: `events` (every message in/out + base notes), `alerts` (who acked, when), `kv` (incident name, base location), `node_meta` (team, notes, site tag) |
| `sources.py` | `SerialSource`: reads the receiver in a background thread, reconnects |
| `simulator.py` | `SimSource`: 3 fake demo nodes |
| `node_brain.py` | loads the node's plain-Python logic (`assess.py`, `co2logic.py`, `caller.py`, `location.py`) so the simulator and tests use the real node rules |
| `static/index.html` | the responder dashboard (one offline file: vanilla JS, canvas chart and radar) |

Logs: `base/events.db` (serial) and `base/events_sim.db` (simulator, kept separate so fake
data never mixes with a real rescue log). On restart, the app rebuilds node state from the last 6 h of the log.
Look inside with `sqlite3 base/events.db "select * from events order by id desc limit 20"`.

## Tests

Run on the laptop, no hardware needed (`pip install -r base/requirements.txt` installs pytest):

```bash
python -m pytest tests/            # node decision logic: every status category (tests/test_logic.py)
python -m pytest tests/ base/tests # + base station: parser, state, simulator helpers
```

`tests/test_logic.py` (every status category, arming, priority order) and `tests/test_location.py`
(LIVE / LAST / MANUAL / NONE, flash save rules) import the real node files (`firmware/node/assess.py`,
`co2logic.py`, `caller.py`, `buttonlogic.py`, `location.py`): they are plain Python and take the time
`now` as an argument, so the tests run them with a fake clock.
