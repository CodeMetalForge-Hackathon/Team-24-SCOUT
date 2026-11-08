# pyrefly: ignore[missing-import]
import time, board, adafruit_gps

i2c = board.I2C()
while not i2c.try_lock():
    pass
print("I2C devices:", [hex(a) for a in i2c.scan()])   # GPS should appear as 0x10
i2c.unlock()

gps = adafruit_gps.GPS_GtopI2C(i2c)
gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")  # position + time sentences
gps.send_command(b"PMTK220,1000")                                     # update once per second

last = 0
while True:
    if gps.update():                         # True when a new message arrived
        print("raw:", gps.nmea_sentence)
    if time.monotonic() - last > 5:
        last = time.monotonic()
        if gps.has_fix:
            print(f"FIX  lat={gps.latitude:.6f} lon={gps.longitude:.6f} "
                  f"sats={gps.satellites} hdop={gps.horizontal_dilution}")
        else:
            print("no fix yet, satellites:", gps.satellites)