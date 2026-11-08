"""Where is this node? GPS (Adafruit Mini GPS PA1010D, I2C 0x10) + a manual fallback.

Location sources, best first:
  LIVE    the GPS has a fix right now (a fix in the last GPS_LIVE_SECS)
  LAST    the last good fix, with its age. Saved in flash (microcontroller.nvm),
          so it survives a restart; after a restart its age is unknown (-1)
  MANUAL  typed in on the dashboard ({"t":"SETLOC",...}). A LIVE fix always wins;
          MANUAL beats LAST only if it was set more recently than that fix
  NONE    nothing known

Location (the decision part) is plain Python: the laptop tests run it.
GPSReader talks to the hardware and never blocks for long.
"""
import math
import struct

NVM_FMT = "<BiiHB"      # valid byte, lat*1e6, lon*1e6, hdop*10, satellites = 12 bytes
NVM_SIZE = struct.calcsize(NVM_FMT)
NVM_VALID = 0xA5


def dist_m(lat1, lon1, lat2, lon2):
    """Distance in metres (flat-earth approximation: fine for a few km)."""
    k = 111320.0                                      # metres per degree of latitude
    dx = (lon2 - lon1) * k * math.cos((lat1 + lat2) / 2 * math.pi / 180)
    dy = (lat2 - lat1) * k
    return math.sqrt(dx * dx + dy * dy)


class Location:
    def __init__(self, live_secs=10, save_every=60, save_move_m=10):
        self.live_secs = live_secs
        self.save_every = save_every
        self.save_move_m = save_move_m
        self.fix = None          # last good fix: lat, lon, hdop, sats, t (None = before restart)
        self.live_t = None       # last time the GPS reported a fix
        self.manual = None       # lat, lon, label, t
        self.saved = None        # (lat, lon) last written to flash
        self.last_save = None

    # ---- inputs ----
    def on_fix(self, now, lat, lon, hdop, sats):
        self.fix = {"lat": lat, "lon": lon, "hdop": hdop, "sats": sats, "t": now}
        self.live_t = now

    def set_manual(self, now, lat, lon, label=""):
        self.manual = {"lat": lat, "lon": lon, "label": label, "t": now}

    # ---- flash (nvm) ----
    def load_saved(self, data):
        """Restore the last fix from flash. Its age is unknown (t = None)."""
        try:
            valid, lat6, lon6, hdop10, sats = struct.unpack(NVM_FMT, bytes(data[:NVM_SIZE]))
        except Exception:
            return False
        lat, lon = lat6 / 1e6, lon6 / 1e6
        if valid != NVM_VALID or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return False
        self.fix = {"lat": lat, "lon": lon, "hdop": hdop10 / 10, "sats": sats, "t": None}
        self.saved = (lat, lon)
        return True

    def to_saved(self):
        f = self.fix
        return struct.pack(NVM_FMT, NVM_VALID, int(round(f["lat"] * 1e6)), int(round(f["lon"] * 1e6)),
                           int(min(6553, (f["hdop"] or 0) * 10)), int(f["sats"] or 0))

    def should_save(self, now):
        """Save a live fix at most once a minute, and only if it moved > 10 m
        (flash wears out if written all the time)."""
        f = self.fix
        if f is None or f["t"] is None or not self.is_live(now):
            return False
        if self.last_save is not None and now - self.last_save < self.save_every:
            return False
        if self.saved is not None and dist_m(self.saved[0], self.saved[1], f["lat"], f["lon"]) <= self.save_move_m:
            return False
        return True

    def mark_saved(self, now):
        self.saved = (self.fix["lat"], self.fix["lon"])
        self.last_save = now

    # ---- output ----
    def is_live(self, now):
        return self.live_t is not None and now - self.live_t < self.live_secs

    def best(self, now):
        """The location to report: {"src", "lat", "lon", "acc", "age", "sats", "label"}.
        acc is an ESTIMATE in metres (hdop x 5); age -1 = unknown (fix from before a restart)."""
        f, m = self.fix, self.manual
        if f is not None and self.is_live(now):
            src = "LIVE"
        elif m is not None and (f is None or f["t"] is None or m["t"] > f["t"]):
            src = "MANUAL"
        elif f is not None:
            src = "LAST"
        else:
            return {"src": "NONE"}
        if src == "MANUAL":
            return {"src": "MANUAL", "lat": m["lat"], "lon": m["lon"], "label": m["label"],
                    "age": int(now - m["t"])}
        out = {"src": src, "lat": f["lat"], "lon": f["lon"], "sats": f["sats"],
               "age": -1 if f["t"] is None else int(now - f["t"])}
        if f["hdop"]:
            out["acc"] = int(f["hdop"] * 5 + 0.5)
        return out


def short_age(sec):
    if sec < 0:
        return "old"
    if sec < 60:
        return "%ds" % sec
    if sec < 3600:
        return "%dm" % (sec // 60)
    return "%dh" % (sec // 3600)


def header_tag(best):
    """Small location indicator for the node's screen header."""
    src = best["src"]
    if src == "LIVE":
        return "GPS LIVE"
    if src == "LAST":
        return "GPS %s ago" % short_age(best["age"]) if best["age"] >= 0 else "GPS old"
    if src == "MANUAL":
        return "LOC MANUAL"
    return "NO GPS"


class GPSReader:
    """Non-blocking reader for the PA1010D on I2C. Polls 4x per second and
    parses at most one NMEA sentence per poll (each I2C read takes a few ms)."""
    POLL = 0.25

    def __init__(self, i2c):
        self.ok = False
        self.gps = None
        self.last_poll = None
        self.errors = 0
        try:
            import adafruit_gps  # type: ignore
            self.gps = adafruit_gps.GPS_GtopI2C(i2c)            # raises if nothing at 0x10
            self.gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")  # RMC + GGA only
            self.gps.send_command(b"PMTK220,1000")                                     # 1 Hz
            self.ok = True
            print("GPS found (PA1010D); waiting for a fix (needs sky view)")
        except Exception as e:
            print("GPS not found (", e, ") - continuing without it")

    def update(self, now, loc):
        if not self.ok or (self.last_poll is not None and now - self.last_poll < self.POLL):
            return
        self.last_poll = now
        try:
            g = self.gps
            if g.update() and g.has_fix and g.latitude is not None and g.longitude is not None:
                loc.on_fix(now, g.latitude, g.longitude, g.horizontal_dilution, g.satellites)
        except Exception as e:                                  # bus hiccup / unplugged
            self.errors += 1
            if self.errors == 1 or self.errors % 100 == 0:
                print("GPS read failed (", e, ")")
