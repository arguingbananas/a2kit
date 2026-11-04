import os, sys, threading, struct, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from PIL import Image, ImageDraw
import a2hdr, imgconv, send, serial
from testlib import expect, finish

# 1. make a test image and convert it
img = Image.new("RGB", (400, 300), "black")
d = ImageDraw.Draw(img)
d.ellipse((50, 30, 350, 270), fill="white")
d.rectangle((150, 100, 250, 200), fill="gray")
img.save("/tmp/t.png")
os.system("cd " + HERE + " && python3 imgconv.py /tmp/t.png -o /tmp/t.a2b --preview /tmp/t_prev.png")
blk = open("/tmp/t.a2b", "rb").read()
btype, cyc, payload = a2hdr.unpack(blk)
expect(btype == 1 and len(payload) == 0x2000, "image block type and size")

# 2. layout check: row y pixel x must be at the right byte/bit
prev = Image.open("/tmp/t_prev.png").convert("1")
px = prev.load()
bad = 0
for y in range(192):
    for x in range(280):
        byte = payload[imgconv.hgr_offset(y) + x // 7]
        if bool(byte >> (x % 7) & 1) != bool(px[x, y]): bad += 1
        if byte & 0x80: bad += 1
print("layout mismatches:", bad)
expect(bad == 0, "HGR layout matches the preview")
# known anchors: row 0 -> $2000, row 1 -> $2400, row 8 -> $2080, row 64 -> $2028, row 191 -> $3FD0
anchors = [0x2000 + imgconv.hgr_offset(y) for y in (0, 1, 8, 64, 191)]
print([hex(a) for a in anchors])
expect(anchors == [0x2000, 0x2400, 0x2080, 0x2028, 0x3FD0], "HGR row addresses")

# 3. send over a pty with a fake Apple II receiver (includes one injected corruption)
master, slave = os.openpty()
mem = {}
state = {"corrupted": False, "exec": None}
def apple2():
    def rd(n):
        out = b""
        while len(out) < n: out += os.read(master, n - len(out))
        return out
    while True:
        if rd(1)[0] != 0xA5: continue
        seq, ln, lo, hi = rd(4)
        data = rd(ln) if ln else b""
        ck = rd(1)[0]
        x = seq ^ ln ^ lo ^ hi
        for b in data: x ^= b
        if not state["corrupted"] and seq == 3:
            state["corrupted"] = True; x ^= 1   # force one NAK
        if x != ck:
            os.write(master, b"\x15"); continue
        addr = lo | hi << 8
        if ln == 0:
            state["exec"] = addr; os.write(master, b"\x06"); return
        for i, b in enumerate(data): mem[addr + i] = b
        os.write(master, b"\x06")
t = threading.Thread(target=apple2, daemon=True); t.start()
port = serial.Serial(os.ttyname(slave), 19200, timeout=2.0)
s = send.Sender(port)
s.send_blob(payload, 0x2000)
s.finish(0)
t.join(3)
got = bytes(mem[0x2000 + i] for i in range(0x2000))
print("screen transferred intact:", got == payload, "| retry exercised:", state["corrupted"], "| exec:", state["exec"])
expect(got == payload and state["corrupted"] and state["exec"] == 0, "pty transfer with one NAK and resend")

# 4. the sender refuses destinations that would clobber the receiver, the stack or I/O
def refused(addr, n):
    try:
        send.check_range(addr, n); return False
    except ValueError:
        return True
cases = [(0x2000, 0x2000, False), (0x6000, 0x6000, False), (0x0B00, 256, False), (0x0200, 0x600, False),
         (0x07FF, 2, True), (0x0AFF, 1, True), (0x0100, 1, True), (0xBFFF, 2, True), (0xFFF0, 0x20, True)]
bad_cases = [(hex(a), n) for a, n, want in cases if refused(a, n) != want]
print("destination guard: %d cases, wrong: %s" % (len(cases), bad_cases or "none"))
expect(not bad_cases, "send.check_range allows and refuses the right ranges")
# 5. the upload-time model agrees with what a real Apple II+ measured
pic = send.upload_seconds([8192], 19200)
starter = send.upload_seconds([332, 8192, 6912, 8192, 2048, 48], 19200)       # show send --install, examples/show.toml
print("upload model: 8 KB picture %.2f s (measured about 4.8 s), starter show %.1f s (measured about 15 s)" % (pic, starter))
expect(4.6 < pic < 5.0 and 14.5 < starter < 15.5, "upload-time model matches the real-hardware measurements")
finish("test_a2kit")
