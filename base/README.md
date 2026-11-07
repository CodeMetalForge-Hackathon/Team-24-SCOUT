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

# Real receiver plugged in by USB
python -m base.app --source serial --port /dev/ttyACM0          # Pi
python -m base.app --source serial --port /dev/cu.usbmodem1234   # Mac
```

Open **http://localhost:8000**. When the app starts, it prints the address other laptops on the switch
should use, e.g. `http://192.168.1.20:8000`. On a Mac, click **Allow** if the firewall asks.

Options: `--http-port 8000`, `--baud 115200`, `--db path.db`, `--one-node` (simulator with node 1 only).

### What the simulator does
Node 1 loops a ~110 s story: NO SIGNS → POSSIBLE (CO₂ 650→1100) → 2 unanswered calls →
UNRESPONSIVE → taps back → LIKELY → button → CONFIRMED → answers injured=yes, count=2 →
pile shift (DANGER) → restart. Node 2 stays NO SIGNS. The buttons work:
**Call now** gets taps back after 3 s (node 2 stays silent and reports "no answer" after 10 s),
and **Ask** gets an answer after 3 s.

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
| `{"t":"HB","n":1,"st":"POSSIBLE","why":"CO2 rising","co2":812,"base":640,"bat":87,"dg":0,"rssi":-48}` | heartbeat, every 30 s |
| `{"t":"ST","n":1,"st":"LIKELY","why":"tapped back after call"}` | status changed |
| `{"t":"EV","n":1,"e":"BTN"}` | PRESS ME pressed |
| `{"t":"EV","n":1,"e":"TAP","taps":4}` | tapped back after a call |
| `{"t":"EV","n":1,"e":"NORESP","calls":2}` | call went unanswered |
| `{"t":"EV","n":1,"e":"SHIFT"}` | pile movement (DANGER) |
| `{"t":"EV","n":1,"e":"ANS","q":"injured","a":"yes"}` | survivor answered a question |

Base → node (written to the receiver's serial port, one line each):
`{"t":"CALL","n":1}`, `{"t":"MSG","n":1,"txt":"Help is coming. Stay still."}` (max 60 chars),
`{"t":"ASK","n":1,"q":"injured"}` or `"q":"count"`.

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
| `static/index.html` | the dashboard (vanilla JS, canvas chart) |

Logs: `base/events.db` (serial) and `base/events_sim.db` (simulator, kept separate so fake
data never mixes with a real rescue log). On restart, the app rebuilds node state from the last 6 h of the log.
Look inside with `sqlite3 base/events.db "select * from events order by id desc limit 20"`.

## Tests

```bash
python -m unittest discover base/tests
```
