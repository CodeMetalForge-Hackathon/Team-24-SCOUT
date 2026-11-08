import time
import board
import displayio
import terminalio
from adafruit_display_text import label
# pyrefly: ignore[missing-import]
import adafruit_scd4x

i2c = board.I2C()

# 1) which sensors can the Feather see?
while not i2c.try_lock():
    pass
print("I2C devices:", [hex(a) for a in i2c.scan()])
i2c.unlock()

# 2) start the CO2 sensor
scd = adafruit_scd4x.SCD4X(i2c)
scd.stop_periodic_measurement()
scd.start_periodic_measurement()
print("CO2 sensor started, first reading in ~5 s")

text = label.Label(terminalio.FONT, text="CO2 starting", scale=3, x=5, y=60)
g = displayio.Group(); g.append(text)
board.DISPLAY.root_group = g

t0 = time.monotonic()
while True:
    if scd.data_ready:
        t = int(time.monotonic() - t0)
        print(f"{t:4d}s  CO2={scd.CO2} ppm  T={scd.temperature:.1f}C  RH={scd.relative_humidity:.0f}%")
        text.text = f"CO2 {scd.CO2}"
    time.sleep(1)