import serial, sys, time
s = serial.Serial("/dev/cu.usbmodem101", 115200, timeout=0.2)
s.dtr = False; s.rts = False          # do NOT reset the board
t0 = time.time(); n = 0
with open(sys.argv[2], "ab") as f:
    while time.time() - t0 < float(sys.argv[1]):
        d = s.read(4096)
        if d: n += len(d); f.write(d); f.flush()
print("drained %d bytes of serial log" % n)
