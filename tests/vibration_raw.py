# Raw vibration sensor test: is the HARDWARE working?
# No assess/ui code, just the pin. Prints twice a second:
#   value   = what the pin reads right now (True/False)
#   changes = how many times it flipped in the last 0.5 s
#
# Run it from the REPL so it doesn't replace code.py:
#   copy this file to CIRCUITPY, press Ctrl-C in Mu, then type:  import vibration_raw
#   (Ctrl-C again to stop, Ctrl-D to go back to the normal program)
import time
import board  # type: ignore
import digitalio  # type: ignore

pin = digitalio.DigitalInOut(board.D5)
pin.direction = digitalio.Direction.INPUT
pin.pull = digitalio.Pull.UP   # module's output is open-collector on some boards; pull-up makes it read cleanly

last = pin.value
changes = 0
t0 = time.monotonic()
print("Vibration raw test on D5. Leave still 5 s, then shake.")
while True:
    v = pin.value
    if v != last:          # count every flip, we want to see ALL activity
        changes += 1
        last = v
    if time.monotonic() - t0 >= 0.5:
        print("value:", v, " changes:", changes)
        changes = 0
        t0 = time.monotonic()
