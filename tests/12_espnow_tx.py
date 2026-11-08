import time, espnow

e = espnow.ESPNow()
peer = espnow.Peer(mac=b"\xff\xff\xff\xff\xff\xff")
e.peers.append(peer)

i = 0
while True:
    msg = '{"net":"T24","t":"TEST","n":1,"i":%d}' % i
    e.send(msg, peer)
    print("sent", msg)
    i += 1
    time.sleep(2)