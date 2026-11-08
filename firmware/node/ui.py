# pyrefly: ignore[missing-import]
import board  # type: ignore
# pyrefly: ignore[missing-import]
import displayio  # type: ignore
# pyrefly: ignore[missing-import]
import terminalio  # type: ignore
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
DANGER_COLORS = (0xD50000, 0xFFFFFF)
LINE = 38          # small-font characters across the 240 px screen
BIG_LINE = 20      # characters across at scale 2


def wrap(text, width, lines=2):
    """Split text into at most `lines` lines of `width` chars, breaking at spaces."""
    out, cur = [], ""
    for word in text.split(" "):
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word) if cur else word
    out.append(cur)
    return "\n".join(line[:width] for line in out[:lines])


class UI:
    """Layout (240 x 135):
         header  (small)   node, MIC OFF, arming, CO2 reading
         status  (big)     NO SIGNS / POSSIBLE / ...
         reason  (small)   why, 2 lines            } one or the other:
         prompt  (medium)  instruction for the survivor, 2 lines
         flag    (medium)  !! PILE MOVING
    """
    def __init__(self, node_id):
        self.node_id = node_id
        # background = one big colored rectangle we can recolor instantly
        self.bg = displayio.Palette(1)
        self.bg[0] = 0x303030
        background = displayio.TileGrid(displayio.Bitmap(240, 135, 1), pixel_shader=self.bg)

        self.header = label.Label(terminalio.FONT, text="NODE %d" % node_id, x=5, y=7)
        self.status = label.Label(terminalio.FONT, text="STARTING", scale=3, x=5, y=36)
        self.reason = label.Label(terminalio.FONT, text="", x=5, y=70)
        self.prompt = label.Label(terminalio.FONT, text="", scale=2, x=5, y=76)
        self.flag   = label.Label(terminalio.FONT, text="", scale=2, x=5, y=122)

        group = displayio.Group()
        for item in (background, self.header, self.status, self.reason, self.prompt, self.flag):
            group.append(item)
        board.DISPLAY.root_group = group

    def show(self, status, reason, danger=False, prompt="", note=""):
        """prompt: big instruction for the survivor (replaces the small reason).
        A long prompt (rescuer message) falls back to small text."""
        bg, fg = DANGER_COLORS if prompt.startswith("!! DANGER") else COLORS[status]
        self.bg[0] = bg
        for l in (self.header, self.status, self.reason, self.prompt, self.flag):
            l.color = fg
        self.header.text = ("N%d  " % self.node_id + note)[:LINE]
        self.status.text = status
        if prompt and len(prompt) <= 2 * BIG_LINE:
            self.prompt.text = wrap(prompt, BIG_LINE)
            self.reason.text = ""
        else:
            self.prompt.text = ""
            self.reason.text = wrap(prompt or reason, LINE)
        # the danger flag sits at the bottom; hidden when the big prompt already says DANGER
        self.flag.text = "!! PILE MOVING" if danger and not prompt.startswith("!! DANGER") else ""
