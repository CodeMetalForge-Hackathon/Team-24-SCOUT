"""Where messages come from. Every source runs in its own thread, hands raw
text lines to `on_line(line)`, and accepts commands through `send(cmd)`.

The app doesn't care whether lines come from a real LoRa receiver or the simulator.
"""
import json
import logging
import threading

log = logging.getLogger("base.source")


class Source:
    name = "source"

    def __init__(self):
        self.on_line = lambda line: None
        self.on_status = lambda connected, detail: None
        self.connected = False
        self.detail = ""
        self._stop = threading.Event()

    def _set_status(self, connected, detail):
        if (connected, detail) != (self.connected, self.detail):
            self.connected, self.detail = connected, detail
            self.on_status(connected, detail)

    def start(self):
        threading.Thread(target=self.run, name=self.name, daemon=True).start()

    def stop(self):
        self._stop.set()

    def run(self):
        raise NotImplementedError

    def send(self, cmd):
        raise NotImplementedError


class SerialSource(Source):
    """Receiver Feather on USB. Reconnects by itself if the cable is pulled."""
    name = "serial"

    def __init__(self, port, baud=115200):
        super().__init__()
        self.port, self.baud = port, baud
        self.ser = None
        self._write_lock = threading.Lock()

    def run(self):
        import serial  # imported here so sim mode works without pyserial
        while not self._stop.is_set():
            try:
                self.ser = serial.Serial(self.port, self.baud, timeout=1)
                self._set_status(True, f"receiver on {self.port}")
                log.info("serial open on %s", self.port)
                while not self._stop.is_set():
                    line = self.ser.readline()       # returns b"" after 1 s timeout
                    if line:
                        self.on_line(line)
            except Exception as exc:  # unplugged, wrong port, permission...
                self._set_status(False, f"receiver not connected ({exc.__class__.__name__})")
                log.warning("serial %s: %s; retrying", self.port, exc)
            finally:
                if self.ser is not None:
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None
            self._stop.wait(0.5)        # quick: a node restarting on USB comes back in ~1-2 s

    def send(self, cmd):
        data = (json.dumps(cmd, separators=(",", ":")) + "\n").encode()
        with self._write_lock:
            if self.ser is None:
                raise ConnectionError("receiver not connected")
            self.ser.write(data)
            self.ser.flush()
