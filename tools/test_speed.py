"""Can the receiver keep up? Cycle-by-cycle check at the real line rate.

The 6551 on the Super Serial Card holds one received byte. If the next byte
finishes arriving before the program has read the last one, the last one is
lost (an overrun). At 115200 baud a byte takes 10 bits / 115200 s = 88.6 cycles
of a 1.020484 MHz Apple II, so every path from one read of the data register to
the next must stay under that.

It also finishes an upload the way "a2kit send" does, with the end packet that
shows the picture, and checks the display switches and the key that ends it.

This runs each receiver build in a py65 6502 (and 65C02) with a 6551 model that
delivers bytes back-to-back at the line rate, exactly as a PC's UART sends a
packet, waits for each ACK plus the port's turnaround, and counts overruns. It
also reports the tightest margin seen: how many cycles were left between
reading a byte and the next one landing.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
from py65.devices.mpu6502 import MPU as MPU6502
from py65.devices.mpu65c02 import MPU as MPU65C02
from py65.memory import ObservableMemory
import a2hdr, send
from testlib import expect, finish

SLOT = 2
D, S = 0xC088 + SLOT * 16, 0xC089 + SLOT * 16
TXTCLR, MIXCLR, LOWSCR, HIRES, TXTSET = 0xC050, 0xC052, 0xC054, 0xC057, 0xC051
TURNAROUND = 0.0022          # measured on a built-in 16550 (ping); a USB adapter adds more, which only helps here


def session(baud, cpu, packets, cpb=None, show=None):
    """Send `packets` (list of bytes objects) one at a time, each after the
    previous reply. Returns replies, overruns, tightest margin (cycles), memory.

    `show`, a dict with "key_after" (cycles), is for a session ending in the
    show-picture packet: a key is pressed that long after graphics go on, and the
    dict gets "switches" (the display switches touched, in order), "carry" and
    "shown_for" (cycles from graphics on until the receiver returned)."""
    code = open(os.path.join(ROOT, "receiver.bin" if baud == a2hdr.DEFAULT_BAUD else "receiver-%d.bin" % baud), "rb").read()
    mem = ObservableMemory(); mpu = cpu(memory=mem)
    cpb = cpb or a2hdr.CPU_HZ * 10.0 / baud          # cycles per byte on the line
    st = {"q": [], "full": False, "reg": 0, "arr": 0.0, "over": 0, "margin": 1e9, "next": 0, "send_at": 30000.0}
    replies = []

    def load_next(at):
        if st["next"] < len(packets):
            pkt = packets[st["next"]]; st["next"] += 1
            st["q"] = [(b, at + (k + 1) * cpb) for k, b in enumerate(pkt)]

    def advance():
        now = mpu.processorCycles
        if not st["q"] and st["send_at"] is not None and now >= st["send_at"]:
            load_next(st["send_at"]); st["send_at"] = None
        while st["q"] and now >= st["q"][0][1]:
            b, t = st["q"].pop(0)
            if st["full"]:
                st["over"] += 1
            st["reg"], st["full"], st["arr"] = b, True, t
            # margin for the byte now waiting: it must be read before the next one lands
            st["deadline"] = st["q"][0][1] if st["q"] else None

    def rd_stat(a):
        advance(); return 0x10 | (0x08 if st["full"] else 0)

    def rd_data(a):
        advance()
        if st["full"] and st.get("deadline") is not None:
            st["margin"] = min(st["margin"], st["deadline"] - mpu.processorCycles)
        st["full"] = False
        return st["reg"]

    def wr_data(a, v):
        replies.append(v)
        # the reply crosses the line, the PC's port and program turn around, then the next packet starts
        st["send_at"] = mpu.processorCycles + cpb + TURNAROUND * a2hdr.CPU_HZ

    mem.subscribe_to_read([S], rd_stat)
    mem.subscribe_to_read([D], rd_data)
    mem.subscribe_to_write([D], wr_data)
    key = {"at": None, "on": None}

    def rd_switch(a):
        show["switches"].append(a)
        if a == TXTCLR:
            key["on"] = mpu.processorCycles
            key["at"] = key["on"] + show["key_after"]
        return 0

    def rd_strobe(a):
        key["at"] = None
        return 0

    mem.subscribe_to_read([0xC000], lambda a: 0xC1 if key["at"] is not None and mpu.processorCycles >= key["at"] else 0)
    mem.subscribe_to_read([0xC010], rd_strobe)
    if show is not None:
        show["switches"] = []
        mem.subscribe_to_read(list(range(0xC050, 0xC058)), rd_switch)
    mem[0xFDED] = 0x60                                        # COUT: RTS
    for i, b in enumerate(code): mem[0x0800 + i] = b
    for i, b in enumerate([0x20, 0x00, 0x08, 0x00]): mem[0x0300 + i] = b
    mem[0x6000] = 0x60                                        # the end packet's JSR target: RTS
    mpu.pc, mpu.sp = 0x0300, 0xFF
    steps = 0
    while mpu.pc != 0x0303 and not st["over"]:      # one lost byte is enough to know
        mpu.step(); steps += 1
        assert steps < 20_000_000, "runaway"
    if show is not None and key["on"] is not None:
        show["carry"] = mpu.p & mpu.CARRY
        show["shown_for"] = mpu.processorCycles - key["on"]
    return replies, st["over"], st["margin"], mem, cpb


picture = bytes((i * 37 + (i >> 5)) & 0x7F for i in range(8192))
for baud in (19200, 115200):
    for name, cpu in (("6502", MPU6502), ("65C02", MPU65C02)):
        for chunk in (128, 255):
            pk = [send.make_packet(i & 0xFF, 0x2000 + off, picture[off:off + chunk])
                  for i, off in enumerate(range(0, len(picture), chunk))]
            bad = bytearray(pk[3]); bad[40] ^= 0x01               # one damaged packet, then its resend
            pk = pk[:3] + [bytes(bad)] + pk[3:] + [send.make_packet(0, 0x6000, b"")]
            replies, over, margin, mem, cpb = session(baud, cpu, pk)
            landed = bytes(mem[0x2000 + i] for i in range(len(picture))) == picture
            want = [0x06] * 3 + [0x15] + [0x06] * (len(pk) - 4)
            print("%6d baud  %-5s  chunk %3d: %d packets, overruns %d, replies ok %s, picture ok %s, "
                  "tightest margin %.1f of %.1f cycles" % (baud, name, chunk, len(pk), over, replies == want, landed, margin, cpb))
            expect(over == 0, "%d baud, %s, %d-byte packets: no byte lost to an overrun" % (baud, name, chunk))
            expect(replies == want and landed, "%d baud, %s, %d-byte packets: every reply right and the picture intact" % (baud, name, chunk))
            expect(margin > 10, "%d baud, %s, %d-byte packets: at least 10 cycles to spare on every byte" % (baud, name, chunk))

# The end packet "a2kit send" really uses: address 0 means show the picture on
# hi-res page 1 until a key is pressed, then go back to text and return. The
# carry must come back clear, or the boot disk's restart loop drops to the Monitor.
KEY_AFTER = 200000
for baud in (19200, 115200):
    for name, cpu in (("6502", MPU6502), ("65C02", MPU65C02)):
        pk = [send.make_packet(i & 0xFF, 0x2000 + off, picture[off:off + 128])
              for i, off in enumerate(range(0, len(picture), 128))]
        pk.append(send.make_packet(len(pk) & 0xFF, 0, b""))
        show = {"key_after": KEY_AFTER}
        replies, over, _, mem, _ = session(baud, cpu, pk, show=show)
        landed = bytes(mem[0x2000 + i] for i in range(len(picture))) == picture
        print("%6d baud  %-5s  show picture: replies ok %s, picture ok %s, switches %s" % (
            baud, name, replies == [0x06] * len(pk), landed, " ".join("$%04X" % a for a in show["switches"])))
        expect(over == 0 and replies == [0x06] * len(pk) and landed, "%d baud, %s, show picture: upload intact and acknowledged" % (baud, name))
        expect(show["switches"] == [TXTCLR, MIXCLR, LOWSCR, HIRES, TXTSET],
               "%d baud, %s: full-screen hi-res page 1 goes on, and text comes back after the key" % (baud, name))
        expect(show.get("shown_for", 0) >= KEY_AFTER, "%d baud, %s: the picture stays up until the key" % (baud, name))
        expect(show.get("carry") == 0, "%d baud, %s: returns ready for the boot disk to listen again" % (baud, name))

# Read requests arrive at the line rate too, and every reply byte must be right.
for baud in (19200, 115200):
    for name, cpu in (("6502", MPU6502), ("65C02", MPU65C02)):
        pk = [send.make_read_packet(0x0800, 16), send.make_read_packet(0x0800, 0), send.make_packet(0, 0x6000, b"")]
        replies, over, margin, mem, _ = session(baud, cpu, pk)
        data = bytes(mem[0x0800 + i] for i in range(256))
        ck = 0
        for b in data: ck ^= b
        short = 0
        for b in data[:16]: short ^= b
        want = [0x06] + list(data[:16]) + [short] + [0x06] + list(data) + [ck] + [0x06]
        print("%6d baud  %-5s  read requests: overruns %d, replies ok %s, tightest margin %.1f of %.1f cycles" % (baud, name, over, replies == want, margin, a2hdr.CPU_HZ * 10.0 / baud))
        expect(over == 0 and replies == want, "%d baud, %s: read requests answered correctly" % (baud, name))
        expect(margin > 10, "%d baud, %s, read requests: at least 10 cycles to spare on every byte" % (baud, name))

# Headroom: squeeze the byte spacing until the 115200 receiver starts losing
# bytes. The slowest path from one byte to the next is what sets this.
pk = [send.make_packet(i, 0x2000 + 255 * i, picture[255 * i:255 * (i + 1)]) for i in range(3)] + [send.make_packet(3, 0x6000, b"")]
lo, hi = 20.0, 88.6                               # lo fails, hi works
while hi - lo > 0.5:
    mid = (lo + hi) / 2
    _, over, _, _, _ = session(115200, MPU6502, pk, cpb=mid)
    lo, hi = (mid, hi) if over else (lo, mid)
print("115200 receiver keeps up with bytes as close as %.1f cycles apart (the line delivers one every 88.6)" % hi)
expect(hi < 75, "the 115200 receiver has at least 15%% headroom (needs %.1f cycles a byte, gets 88.6)" % hi)
finish("test_speed")
