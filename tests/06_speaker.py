import time, board, pwmio

spk = pwmio.PWMOut(board.D9, frequency=2700, duty_cycle=0, variable_frequency=True)

LOUD = 3000       # <-- after step 1, set this to the loudest frequency you heard
MAX = 32768        # 50% duty = the loudest a beep can be

def tone(freq, dur):
    spk.frequency = int(freq)
    spk.duty_cycle = MAX
    time.sleep(dur)
    spk.duty_cycle = 0

def slide(f_from, f_to, dur, steps=50):
    """Smoothly slide the pitch (speaker stays on)."""
    for i in range(steps + 1):
        spk.frequency = int(f_from + (f_to - f_from) * i / steps)
        time.sleep(dur / steps)

def siren(cycles=3):
    """DANGER: wailing siren, pitch sweeps up and down."""
    spk.duty_cycle = MAX
    for _ in range(cycles):
        slide(1500, 3800, 0.6)    # rise
        slide(3800, 1500, 0.6)    # fall
    spk.duty_cycle = 0

# ---- STEP 1: find the loudest frequency (runs once) ----
print("Listen: which frequency is loudest?")
for f in range(1500, 4001, 250):
    print("freq", f)
    tone(f, 0.4)
    time.sleep(0.3)
time.sleep(2)

# ---- STEP 2: the beep language, using the loudest frequency ----
while True:
    print("CALL: 3 beeps")
    for _ in range(3):
        tone(LOUD, 0.35)
        time.sleep(0.35)
    time.sleep(1.5)

    print("FIRST PRESS: 2 quick beeps")
    tone(LOUD + 300, 0.12); time.sleep(0.1); tone(LOUD + 300, 0.12)
    time.sleep(1.5)

    print("CONFIRM: rising chime")
    tone(LOUD - 700, 0.2); tone(LOUD - 200, 0.2); tone(LOUD + 300, 0.45)
    time.sleep(1.5)

    print("DANGER: siren")
    siren(3)
    time.sleep(3)