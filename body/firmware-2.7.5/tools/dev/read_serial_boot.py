import serial, sys, time, glob
port = sys.argv[1]; secs = float(sys.argv[2]); out = open(sys.argv[3], "wb")
s = serial.Serial(port, 115200, timeout=0.2)
# reset into the application (USB-Serial/JTAG: RTS = EN, DTR = IO0)
s.dtr = False; s.rts = True; time.sleep(0.1); s.rts = False; time.sleep(0.05)
t0 = time.time()
while time.time() - t0 < secs:
    d = s.read(4096)
    if d: out.write(d); out.flush(); sys.stdout.buffer.write(d); sys.stdout.flush()
