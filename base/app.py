"""Base station web app.

  python -m base.app --source sim                          # fake nodes (Mac demo)
  python -m base.app --source serial --port /dev/ttyACM0   # real LoRa receiver / node on USB

Then open http://<this-computer>:8000 from any laptop or tablet on the same switch.

Flow: source thread -> raw line -> parse_line -> SQLite + State -> WebSocket push.
Shared team state (incident name, base location, acknowledgements, assigned teams,
notes, site tags) also lives in SQLite and is pushed to every open browser.
Fully offline: the dashboard is one local HTML file, no CDNs.
"""
import argparse
import asyncio
import contextlib
import json
import logging
import socket
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from base.db import EventDB
from base.protocol import parse_line, validate_command, describe
from base.state import State, category

log = logging.getLogger("base")
HERE = Path(__file__).resolve().parent
REPLAY_SECS = 6 * 3600   # on restart, rebuild node state from the last 6 h of the log
ALERT_KINDS = ("UNRESPONSIVE", "CONFIRMED", "DANGER")


class Hub:
    """Owns the state and fans updates out to every open dashboard."""

    def __init__(self, source, db):
        self.source = source
        self.db = db
        self.state = State()
        self.clients = set()
        self.loop = None
        self.bad_lines = 0
        self.last_rx = None          # time of the last line from the radio / USB (link health)
        self._last_bad_log = 0.0
        self._lost = {}

    # ---------- startup ----------
    def replay(self):
        """Rebuild node state from the database so a Pi reboot doesn't wipe the picture."""
        rows = self.db.recent(limit=5000, since=time.time() - REPLAY_SECS)
        for ev_id, ts, node, type_, direction, raw in reversed(rows):
            if direction == "in":
                msg, err = parse_line(raw)
                if msg:
                    self.state.apply(msg, ts, ev_id)
            elif direction == "out":
                with contextlib.suppress(ValueError, TypeError):
                    self.state.outgoing(validate_command(json.loads(raw)), ts, ev_id)
            elif direction == "sys":
                with contextlib.suppress(ValueError, TypeError, KeyError):
                    note = json.loads(raw)
                    self.state.add_note(node, note["desc"], note["cat"], ts, ev_id)
        if rows:
            log.info("replayed %d logged messages", len(rows))

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.replay()
        self.source.on_line = self._line_from_thread
        self.source.on_status = self._status_from_thread
        self.source.start()
        self._ticker = asyncio.create_task(self._tick())

    def stop(self):
        self.source.stop()

    # ---------- incoming ----------
    def _line_from_thread(self, line):
        ts = time.time()  # stamp on arrival, before any queueing delay
        self.loop.call_soon_threadsafe(self.handle_line, line, ts)

    def _status_from_thread(self, connected, detail):
        self.loop.call_soon_threadsafe(self._push, {"type": "source", "source": self.source_info()})

    def handle_line(self, line, ts):
        text = line.decode("utf-8", "replace") if isinstance(line, (bytes, bytearray)) else str(line)
        text = text.strip()
        self.last_rx = ts
        msg, err = parse_line(text)
        if err in ("empty line", "no JSON object"):
            # normal console text from a node on USB ("EVENT: ...", "STATUS: ...")
            log.debug("console: %s", text)
            return
        if err:
            self.bad_lines += 1
            if text:
                self.db.add(ts, None, "BAD", "in", text[:500])
            if ts - self._last_bad_log > 5:      # don't flood the console
                log.warning("ignored line (%s): %.80r  [%d bad so far]", err, text, self.bad_lines)
                self._last_bad_log = ts
            return
        n = msg["n"]
        before = self.state.nodes.get(n)
        prev_status = before.status if before else None
        prev_danger = before.danger if before else False

        ev_id = self.db.add(ts, n, msg["t"], "in", text)
        ev = self.state.apply(msg, ts, ev_id)
        if ev:
            log.info("node %s: %s", n, ev["desc"])
        nd = self.state.nodes[n]
        self._push({"type": "node", "node": nd.to_dict(ts), "event": ev,
                    "server_time": time.time(), "last_rx": self.last_rx})

        # alerts: the moments a responder must not miss
        if nd.status != prev_status and nd.status in ("UNRESPONSIVE", "CONFIRMED"):
            text = (f"Node {n}: UNRESPONSIVE: {nd.why}" if nd.status == "UNRESPONSIVE"
                    else f"Node {n}: SURVIVOR CONFIRMED: {nd.why}")
            self.raise_alert(ts, n, nd.status, text)
        if nd.danger and not prev_danger:
            self.raise_alert(ts, n, "DANGER", f"Node {n}: DANGER: pile movement / danger alarm")

    # ---------- alerts ----------
    def raise_alert(self, ts, n, kind, text):
        for a in self.db.alerts():
            if a["n"] == n and a["kind"] == kind and a["acked_at"] is None:
                return                           # already waiting for an acknowledgement
        self.db.add_alert(ts, n, kind, text)
        log.warning("ALERT %s", text)
        self.note(n, f"ALERT: {text}", "alerts", ts)
        self._push({"type": "alerts", "alerts": self.db.alerts()})

    def ack(self, by, alert_id=None, node=None):
        by = (by or "").strip()[:30] or "Operator"
        ts = time.time()
        acked = []
        for a in self.db.alerts():
            if a["acked_at"] is None and (a["id"] == alert_id or (node is not None and a["n"] == node)):
                if self.db.ack_alert(a["id"], by, ts):
                    acked.append(a)
        for a in acked:
            self.note(a["n"], f"Alert acknowledged by {by}: {a['kind']}", "alerts", ts)
        self._push({"type": "alerts", "alerts": self.db.alerts()})
        return len(acked)

    # ---------- shared team state ----------
    def note(self, n, desc, cat, ts=None):
        """A base-side line on the timeline (acks, assignments, settings), also logged."""
        ts = time.time() if ts is None else ts
        ev_id = self.db.add(ts, n, "NOTE", "sys", {"desc": desc, "cat": cat})
        ev = self.state.add_note(n, desc, cat, ts, ev_id)
        self._push({"type": "event", "event": ev, "server_time": time.time()})
        return ev

    def set_meta(self, body):
        meta = self.db.get_kv()
        by = str(body.get("by") or "Operator")[:30]
        if "incident" in body:
            name = str(body["incident"] or "").strip()[:60]
            if name != meta.get("incident"):
                self.db.set_kv("incident", name)
                self.note(None, f"Incident name set to \"{name}\" by {by}", "system")
        if "base" in body:
            base = body["base"]
            if base is None:
                self.db.set_kv("base", None)
            else:
                try:
                    lat, lon = float(base["lat"]), float(base["lon"])
                except (TypeError, ValueError, KeyError):
                    raise ValueError("base location needs lat and lon numbers")
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("base location out of range")
                self.db.set_kv("base", {"lat": lat, "lon": lon})
                self.note(None, f"Base location set to {lat:.5f}, {lon:.5f} by {by}", "system")
        self._push({"type": "meta", "meta": self.db.get_kv()})

    def set_node_meta(self, body):
        try:
            n = int(body["n"])
        except (TypeError, ValueError, KeyError):
            raise ValueError("node id 'n' missing")
        by = str(body.get("by") or "Operator")[:30]
        old = self.db.get_node_meta().get(n, {"team": "", "notes": "", "tag": ""})
        new = dict(old)
        for key, limit in (("team", 30), ("tag", 30), ("notes", 500)):
            if key in body:
                new[key] = str(body[key] or "").strip()[:limit]
        self.db.set_node_meta(n, new["team"], new["notes"], new["tag"])
        if new["team"] != old["team"]:
            self.note(n, f"Assigned to {new['team'] or 'no team'} by {by}", "system")
        if new["tag"] != old["tag"]:
            self.note(n, f"Site tag set to \"{new['tag']}\" by {by}", "system")
        if new["notes"] != old["notes"]:
            self.note(n, f"Notes updated by {by}", "system")
        self._push({"type": "node_meta", "node_meta": self.db.get_node_meta()})

    # ---------- outgoing ----------
    async def command(self, cmd):
        cmd = validate_command(cmd)            # ValueError -> 400
        # serial writes can block briefly; keep the web server responsive
        await asyncio.to_thread(self.source.send, cmd)   # ConnectionError -> 503
        ts = time.time()
        ev_id = self.db.add(ts, cmd["n"], cmd["t"], "out", json.dumps(cmd, separators=(",", ":")))
        ev = self.state.outgoing(cmd, ts, ev_id)
        log.info("node %s: %s", cmd["n"], ev["desc"])
        self._push({"type": "event", "event": ev, "server_time": ts})
        return ev

    # ---------- push to dashboards ----------
    def source_info(self):
        return {"name": self.source.name, "connected": self.source.connected, "detail": self.source.detail}

    def full(self):
        now = time.time()
        return {"type": "full", "server_time": now, "nodes": list(self.state.snapshot(now).values()),
                "events": list(self.state.events)[:300], "source": self.source_info(),
                "bad_lines": self.bad_lines, "last_rx": self.last_rx,
                "meta": self.db.get_kv(), "node_meta": self.db.get_node_meta(), "alerts": self.db.alerts()}

    def _push(self, payload):
        if self.clients and self.loop is not None:
            asyncio.create_task(self._broadcast(json.dumps(payload)))

    async def _broadcast(self, data):
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=2)
            except Exception:
                self.clients.discard(ws)   # closed tab / dead laptop

    async def _tick(self):
        """Nodes go LOST by *silence*, so no message will announce it. Check every 2 s."""
        while True:
            await asyncio.sleep(2)
            now = time.time()
            lost = self.state.lost_flags(now)
            if lost != self._lost:
                for n, is_lost in lost.items():
                    if is_lost and not self._lost.get(n):
                        log.warning("node %s LOST (no message for 90 s)", n)
                        self.note(n, "Node LOST: no message for 90 s (check battery / radio)", "system")
                self._lost = lost
                self._push(self.full())


async def _json_body(request):
    try:
        body = await request.json()
    except Exception:
        raise ValueError("body must be JSON")
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    return body


def create_app(source, db_path, defaults=None):
    db = EventDB(db_path)
    for key, value in (defaults or {}).items():    # e.g. simulator: a base location to start with
        if key not in db.get_kv():
            db.set_kv(key, value)
    hub = Hub(source, db)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await hub.start()
        yield
        hub.stop()

    app = FastAPI(title="Rubble Node Base Station", lifespan=lifespan)
    app.state.hub = hub

    def bad(msg, code=400):
        return JSONResponse({"ok": False, "error": msg}, status_code=code)

    @app.get("/")
    def index():
        return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/state")
    def api_state():
        return hub.full()

    @app.get("/api/events")
    def api_events(n: int | None = None, limit: int = 200, include_bad: bool = False):
        """Logged messages, newest first (the dashboard's CSV/JSON export uses this)."""
        rows = hub.db.recent(node=n, limit=max(1, min(limit, 20000)), include_bad=include_bad)
        out = []
        for ev_id, ts, node, type_, direction, raw in rows:
            item = {"id": ev_id, "ts": ts, "n": node, "type": type_, "dir": direction, "raw": raw}
            if direction == "in":
                msg, _ = parse_line(raw)
                item["desc"] = describe(msg) if msg else "unreadable line"
                item["cat"] = category(msg) if msg else "system"
            elif direction == "out":
                with contextlib.suppress(ValueError, TypeError):
                    cmd = json.loads(raw)
                    item["desc"] = describe(cmd, outgoing=True)
                    item["cat"] = category(cmd, outgoing=True)
            else:
                with contextlib.suppress(ValueError, TypeError, KeyError):
                    note = json.loads(raw)
                    item["desc"], item["cat"] = note["desc"], note["cat"]
            out.append(item)
        return out

    @app.post("/api/cmd")
    async def api_cmd(request: Request):
        try:
            ev = await hub.command(await _json_body(request))
        except ValueError as exc:
            return bad(str(exc))
        except Exception as exc:
            log.warning("command failed: %s", exc)
            return bad(f"could not send: {exc}", 503)
        return {"ok": True, "event": ev}

    @app.post("/api/ack")
    async def api_ack(request: Request):
        """{"id": 12, "by": "Ana"} acknowledges one alert; {"n": 2, "by": ...} all of a node's."""
        try:
            body = await _json_body(request)
        except ValueError as exc:
            return bad(str(exc))
        alert_id = body.get("id") if isinstance(body.get("id"), int) else None
        node = body.get("n") if isinstance(body.get("n"), int) else None
        if alert_id is None and node is None:
            return bad("give an alert id or a node n")
        return {"ok": True, "acked": hub.ack(body.get("by"), alert_id, node)}

    @app.post("/api/meta")
    async def api_meta(request: Request):
        """{"incident": "...", "base": {"lat":..,"lon":..} | null, "by": "..."}"""
        try:
            hub.set_meta(await _json_body(request))
        except ValueError as exc:
            return bad(str(exc))
        return {"ok": True}

    @app.post("/api/node_meta")
    async def api_node_meta(request: Request):
        """{"n": 2, "team": "Team Alpha", "notes": "...", "tag": "Site B", "by": "..."}"""
        try:
            hub.set_node_meta(await _json_body(request))
        except ValueError as exc:
            return bad(str(exc))
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        hub.clients.add(websocket)
        try:
            await websocket.send_text(json.dumps(hub.full()))
            while True:
                await websocket.receive_text()   # we don't expect input; this just waits for close
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(websocket)

    return app


def lan_ip():
    """Best guess at this machine's address on the Ethernet switch (sends no packets)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "localhost"


def main():
    p = argparse.ArgumentParser(description="Survivor-detection base station")
    p.add_argument("--source", choices=["sim", "serial"], default="sim")
    p.add_argument("--port", help="serial port of the receiver, e.g. /dev/ttyACM0 or /dev/cu.usbmodem1234")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--http-port", type=int, default=8000)
    p.add_argument("--db", help="SQLite file (default: base/events.db, or base/events_sim.db in sim mode)")
    p.add_argument("--one-node", action="store_true", help="simulator: only node 1")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    defaults = {}
    if args.source == "serial":
        if not args.port:
            p.error("--source serial needs --port (try: ls /dev/ttyACM* or ls /dev/cu.usbmodem*)")
        from base.sources import SerialSource
        source = SerialSource(args.port, args.baud)
        db = args.db or str(HERE / "events.db")
    else:
        from base.simulator import SimSource, SIM_BASE
        source = SimSource(second_node=not args.one_node)
        db = args.db or str(HERE / "events_sim.db")   # keep fake data out of the real log
        defaults = {"incident": "DEMO: simulated collapse", "base": SIM_BASE}

    import uvicorn
    print(f"\n  Dashboard:  http://localhost:{args.http_port}   (other laptops: http://{lan_ip()}:{args.http_port})"
          f"\n  Source:     {args.source}{' ' + args.port if args.port else ''}\n  Log DB:     {db}\n")
    uvicorn.run(create_app(source, db, defaults), host=args.host, port=args.http_port, log_level="warning")


if __name__ == "__main__":
    main()
