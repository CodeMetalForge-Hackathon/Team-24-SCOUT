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
The fake nodes run the **real node logic** (`firmware/node/assess.py` and `co2logic.py`, DEMO
profile), so statuses, CO2 levels and countdowns match the board exactly; only the raw sensor
values are invented. Node 1 loops a ~6.5 min story (CO2 baseline learned in the first 30 s):

| Time | What happens | Status |
|---|---|---|
| 6 s | hears a voice | POSSIBLE "heard voice" |
| 25 s | **accidental single press** | LIKELY countdown → POSSIBLE "single press, not confirmed" |
| 40–150 s | room CO2 drifts up (+2 ppm/s, people arriving) | only "CO2 high but steady (room air?)": **no status change** |
| 175 s | breathing burst on the sensor | POSSIBLE "breathing detected …" at ~183 s, chart shaded, "evidence expires in N s" |
| ~250 s | hold time over | "CO2 evidence expired" → NO SIGNS |
| 270 + 285 s | breathing again + knocking | **LIKELY "breathing + knocking"** |
| 345 / 353 s | press, press again | **CONFIRMED** → answers → pile shift → stuck button |

Node 2 stays NO SIGNS. The buttons work:
**Call now** gets taps back after 3 s (node 2 stays silent and reports "no answer" after 10 s),
**Ask** gets an answer after 3 s, and **Reset** clears the node and restarts its story.

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
| `{"t":"HB","n":1,"st":"POSSIBLE","why":"sudden CO2 rise +240 ppm in 10s","co2":1050,"base":810,"rh":49.5,"rh_base":48.1,"lvl":"SUDDEN_RISE","hold":42,"prof":"DEMO","dg":0,"rssi":-48}` | heartbeat, every 10 s (DEMO) / 30 s (FIELD); `hold` = seconds of CO2 evidence left |
| `{"t":"EV","n":1,"e":"CO2","lvl":"BREATHING","prev":"NORMAL","rise":380,"secs":10,"rh_jump":6,"hold":60,"base":810}` | CO2 level changed: LEARNING / NORMAL / ELEVATED (info only) / SUDDEN_RISE / BREATHING / SLOW_BUILDUP (FIELD). See [docs/co2.md](../docs/co2.md) |
| `{"t":"ST","n":1,"st":"LIKELY","why":"pressed once - press again (14s)"}` | status changed (countdown ticks at most every 5 s; the page counts down in between) |
| `{"t":"EV","n":1,"e":"BTN","stage":"first"}` | PRESS ME pressed once: waiting for a 2nd press within 20 s |
| `{"t":"EV","n":1,"e":"BTN","stage":"confirmed"}` | pressed twice: **survivor confirmed** (alert sound) |
| `{"t":"EV","n":1,"e":"STUCK"}` | button held ≥ 5 s: probably debris, press ignored |
| `{"t":"EV","n":1,"e":"TAP"}` / `{"t":"EV","n":1,"e":"VOICE"}` | mic heard knocking / a voice |
| `{"t":"EV","n":1,"e":"TAP","taps":4}` | tapped back after a call (has a count) |
| `{"t":"EV","n":1,"e":"NORESP","calls":2}` | call went unanswered |
| `{"t":"EV","n":1,"e":"SHIFT"}` | pile movement (DANGER) |
| `{"t":"EV","n":1,"e":"ANS","q":"injured","a":"yes"}` | survivor answered a question |

Base → node (written to the receiver's serial port, one line each):
`{"t":"CALL","n":1}`, `{"t":"MSG","n":1,"txt":"Help is coming. Stay still."}` (max 60 chars),
`{"t":"ASK","n":1,"q":"injured"}` or `"q":"count"`, `{"t":"RESET","n":1}` (false alarm: node
forgets all evidence). The node handles RESET and MSG today; CALL and ASK come later.

Statuses: `NO SIGNS`, `POSSIBLE`, `LIKELY`, `CONFIRMED`, `UNRESPONSIVE`. The dashboard shows
`LOST` itself when it hasn't heard from a node for 90 s.

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
