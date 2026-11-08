import time, espnow

NET = "T24"                                   # our team's network ID
e = espnow.ESPNow(buffer_size=1024)           # bigger buffer for a busy room
print("listening...")

while True:
    try:
        packet = e.read()
    except ValueError as err:                 # malformed packet from someone: skip it
        print("skipped bad packet:", err)
        continue
    if not packet:
        time.sleep(0.01)
        continue
    try:
        text = packet.msg.decode()
    except Exception:
        continue                              # not text: not ours
    if '"net":"%s"' % NET in text:
        print(text, "RSSI", packet.rssi)      # ours: this is what the dashboard reads
    # else: another team's message, ignore silently