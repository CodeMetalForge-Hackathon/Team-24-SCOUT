"""Base station web app.

  python -m base.app --source sim                          # fake nodes (Mac demo)
  python -m base.app --source serial --port /dev/ttyACM0   # real LoRa receiver (Pi)

Then open http://<this-computer>:8000 from any laptop on the same switch.

Flow: source thread -> raw line -> parse_line -> SQLite + State -> WebSocket push.
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
from base.state import State, make_event

log = logging.getLogger("base")
HERE = Path(__file__).resolve().parent
REPLAY_SECS = 6 * 3600   # on restart, rebuild node state from the last 6 h of the log


class Hub:
    """Owns the state and fans updates out to every open dashboard."""

    def __init__(self, source, db):
        self.source = source
        self.db = db
        self.state = State()
        self.clients = set()
        self.loop = None
        self.bad_lines = 0
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
            else:
                with contextlib.suppress(ValueError, TypeError):
                    self.state.outgoing(validate_command(json.loads(raw)), ts, ev_id)
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
        msg, err = parse_line(text)
        if err:
            self.bad_lines += 1
            if text:
                self.db.add(ts, None, "BAD", "in", text[:500])
            if ts - self._last_bad_log > 5:      # don't flood the console
                log.warning("ignored line (%s): %.80r  [%d bad so far]", err, text, self.bad_lines)
                self._last_bad_log = ts
            return
        ev_id = self.db.add(ts, msg["n"], msg["t"], "in", text)
        ev = self.state.apply(msg, ts, ev_id)
        if ev:
            log.info("node %s: %s", msg["n"], ev["desc"])
        self._push({"type": "node", "node": self.state.nodes[msg["n"]].to_dict(ts),
                    "event": ev, "server_time": time.time()})

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
                "events": list(self.state.events)[:150], "source": self.source_info(),
                "bad_lines": self.bad_lines}

    def _push(self, payload):
        if self.clients:
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
                self._lost = lost
                self._push(self.full())


def create_app(source, db_path):
    hub = Hub(source, EventDB(db_path))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await hub.start()
        yield
        hub.stop()

    app = FastAPI(title="Rubble Node Base Station", lifespan=lifespan)
    app.state.hub = hub

    @app.get("/")
    def index():
        return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/state")
    def api_state():
        return hub.full()

    @app.get("/api/events")
    def api_events(n: int | None = None, limit: int = 200, include_bad: bool = False):
        rows = hub.db.recent(node=n, limit=max(1, min(limit, 2000)), include_bad=include_bad)
        out = []
        for ev_id, ts, node, type_, direction, raw in rows:
            item = {"id": ev_id, "ts": ts, "n": node, "type": type_, "dir": direction, "raw": raw}
            if direction == "in":
                msg, _ = parse_line(raw)
                item["desc"] = describe(msg) if msg else "unreadable line"
            else:
                with contextlib.suppress(ValueError, TypeError):
                    item["desc"] = describe(json.loads(raw), outgoing=True)
            out.append(item)
        return out

    @app.post("/api/cmd")
    async def api_cmd(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "body must be JSON"}, status_code=400)
        try:
            ev = await hub.command(body)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            log.warning("command failed: %s", exc)
            return JSONResponse({"ok": False, "error": f"could not send: {exc}"}, status_code=503)
        return {"ok": True, "event": ev}

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
    if args.source == "serial":
        if not args.port:
            p.error("--source serial needs --port (try: ls /dev/ttyACM* or ls /dev/cu.usbmodem*)")
        from base.sources import SerialSource
        source = SerialSource(args.port, args.baud)
        db = args.db or str(HERE / "events.db")
    else:
        from base.simulator import SimSource
        source = SimSource(second_node=not args.one_node)
        db = args.db or str(HERE / "events_sim.db")   # keep fake data out of the real log

    import uvicorn
    print(f"\n  Dashboard:  http://localhost:{args.http_port}   (other laptops: http://{lan_ip()}:{args.http_port})"
          f"\n  Source:     {args.source}{' ' + args.port if args.port else ''}\n  Log DB:     {db}\n")
    uvicorn.run(create_app(source, db), host=args.host, port=args.http_port, log_level="warning")


if __name__ == "__main__":
    main()
