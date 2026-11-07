# pyrefly: ignore[missing-import]
import board  # type: ignore
# pyrefly: ignore[missing-import]
import displayio  # type: ignore
# pyrefly: ignore[missing-import]
import terminalio  # type: ignore
# pyrefly: ignore[missing-import]
import pwmio  # type: ignore
import time
# pyrefly: ignore[missing-import]
from adafruit_display_text import label  # type: ignore

# status -> (background color, text color)
COLORS = {
    "NO SIGNS":     (0x303030, 0xFFFFFF),   # gray
    "POSSIBLE":     (0xFF8C00, 0x000000),   # orange
    "LIKELY":       (0xFFD700, 0x000000),   # yellow
    "CONFIRMED":    (0x00C853, 0x000000),   # green
    "UNRESPONSIVE": (0xD50000, 0xFFFFFF),   # red
}

class UI:
    def __init__(self, node_id, speaker_pin):
        # background = one big colored rectangle we can recolor instantly
        self.bg = displayio.Palette(1)
        self.bg[0] = 0x303030
        background = displayio.TileGrid(displayio.Bitmap(240, 135, 1), pixel_shader=self.bg)

        self.header = label.Label(terminalio.FONT, text=f"NODE {node_id}", x=5, y=8)
        self.status = label.Label(terminalio.FONT, text="STARTING", scale=3, x=5, y=45)
        self.reason = label.Label(terminalio.FONT, text="", x=5, y=90)
        self.flag   = label.Label(terminalio.FONT, text="", scale=2, x=5, y=118)

        group = displayio.Group()
        for item in (background, self.header, self.status, self.reason, self.flag):
            group.append(item)
        board.DISPLAY.root_group = group

        self.spk = pwmio.PWMOut(speaker_pin, frequency=2000, duty_cycle=0, variable_frequency=True)

    def show(self, status, reason, danger=False):
        bg, fg = COLORS[status]
        self.bg[0] = bg
        for l in (self.header, self.status, self.reason, self.flag):
            l.color = fg
        self.status.text = status
        self.reason.text = reason[:38]          # screen fits ~38 small characters
        self.flag.text = "!! PILE MOVING" if danger else ""

    def beep(self, freq=2000, dur=0.15):
        self.spk.frequency = freq
        self.spk.duty_cycle = 32768
        time.sleep(dur)
        self.spk.duty_cycle = 0
        time.sleep(0.08)