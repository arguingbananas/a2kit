"""Boot a2kit-boot.dsk in a py65 6502 simulator.

What is real here: the disk image, the boot code (boot.bin) and the receiver.
What is modelled: the Disk II boot ROM, as documented - it reads physical
sectors 0 .. N-1 of track 0 to $0800, $0900, ... where N is the byte at $0800,
then jumps to $0801 with slot*16 in $2B and X - and the few Monitor routines
the boot code calls. The image is read through both the DOS 3.3 and the
ProDOS sector orders, since tools disagree about which one a .dsk uses.

The I/O hooks start out pointing at the disk card ($C600), as they are after
booting with PR#6 from BASIC, so a boot that forgot to reset them would jump
back into the disk ROM on its first character of output.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory
import send, mkdisk
from testlib import expect, finish

img = open(os.path.join(ROOT, "a2kit-boot.dsk"), "rb").read()
receiver = open(os.path.join(ROOT, "receiver.bin"), "rb").read()

# ---------------------------------------------------------------- the image itself
SEC = 256
def sector(i): return img[i * SEC:(i + 1) * SEC]
expect(len(img) == 143360, "image is a 140 KB 5.25-inch disk (143360 bytes)")
expect(all(sector(s) == sector(1) for s in range(1, 16)), "track 0 sectors 1-15 are identical")
expect(not any(img[16 * SEC:]), "tracks 1-34 are empty")
try:
    built = mkdisk.build_image(open(os.path.join(ROOT, "boot.bin"), "rb").read())
except ValueError as e:
    built = None
    print("  mkdisk refused boot.bin: %s" % e)
expect(img == built, "image matches what mkdisk builds from boot.bin")

# logical (file) sector -> physical sector, for the two common image orders
DOS_ORDER = [0, 13, 11, 9, 7, 5, 3, 1, 14, 12, 10, 8, 6, 4, 2, 15]
PRODOS_ORDER = [0, 2, 4, 6, 8, 10, 12, 14, 1, 3, 5, 7, 9, 11, 13, 15]
def physical(p, order):
    return sector(order.index(p))

# ---------------------------------------------------------------- simulator
SLOT6 = 0x60                        # disk controller in slot 6
ACIA_D, ACIA_S = 0xC0A8, 0xC0A9     # Super Serial Card in slot 2
COUT, COUT1, SETVID, SETKBD, INIT, HOME, MONZ = 0xFDED, 0xFDF0, 0xFE93, 0xFE89, 0xFB2F, 0xFC58, 0xFF69

def boot(order, max_steps=3_000_000, script=None):
    mem = ObservableMemory(); mpu = MPU(memory=mem)
    rx, tx, screen_text, calls, io = [], [], [], [], []
    key = {"v": 0}

    def io_read(a):
        if a == ACIA_S: return 0x10 | (0x08 if rx else 0)
        if a == ACIA_D: return rx.pop(0) if rx else 0
        if a == 0xC000: return key["v"]
        if a == 0xC010: key["v"] &= 0x7F; return 0
        io.append(a); return 0
    mem.subscribe_to_read(range(0xC000, 0xC100), io_read)
    mem.subscribe_to_write([ACIA_D], lambda a, v: (tx.append(v), None)[1])

    # Monitor routine stand-ins (just the effects the boot code relies on)
    rom = {COUT: [0x6C, 0x36, 0x00],                                  # JMP (CSW)
           COUT1: [0x60],                                             # RTS (text captured below)
           SETVID: [0xA9, 0xF0, 0x85, 0x36, 0xA9, 0xFD, 0x85, 0x37, 0x60],   # CSW = COUT1
           SETKBD: [0xA9, 0x1B, 0x85, 0x38, 0xA9, 0xFD, 0x85, 0x39, 0x60],   # KSW = KEYIN
           INIT: [0x60], HOME: [0x60]}
    for a, code in rom.items():
        for i, b in enumerate(code): mem[a + i] = b

    # the boot ROM's work: sectors 0..N-1 in, then jump to $0801
    s0 = physical(0, order)
    for p in range(s0[0]):
        for i, b in enumerate(physical(p, order)): mem[0x0800 + p * SEC + i] = b
    mem[0x2B] = SLOT6
    mem[0x36], mem[0x37] = 0x00, 0xC6           # CSW -> $C600, as after PR#6
    mem[0x38], mem[0x39] = 0x00, 0xC6           # KSW -> $C600, as after IN#6
    mpu.pc, mpu.x, mpu.sp = 0x0801, SLOT6, 0xFF

    st = {"steps": 0, "outcome": "ran out of steps"}
    while st["steps"] < max_steps:
        pc = mpu.pc
        if pc in (INIT, HOME, SETVID, SETKBD): calls.append(pc)
        if pc == COUT1: screen_text.append(chr(mpu.a & 0x7F))
        if pc == 0xC600: st["outcome"] = "jumped into the disk ROM (would reboot)"; break
        if pc == MONZ: st["outcome"] = "monitor"; break
        if script: script(st["steps"], "".join(screen_text), rx, key, mem)
        mpu.step(); st["steps"] += 1
    return dict(outcome=st["outcome"], text="".join(screen_text), tx=tx, calls=calls, io=io, mem=mem)

# ---------------------------------------------------------------- 1. boot alone, both orders
BANNER = "A2KIT RX READY"
# The receiver clears any pending key just after its banner (a key typed while
# it starts is ignored), so the scripted key presses come a moment later.
SETTLE = 20000          # steps after the banner before "pressing" a key
for name, order in (("DOS 3.3 order", DOS_ORDER), ("ProDOS order", PRODOS_ORDER)):
    seen = {}
    def quit_when_ready(steps, text, rx, key, mem, seen=seen):
        if BANNER in text and "t" not in seen: seen["t"] = steps
        if "t" in seen and steps == seen["t"] + SETTLE: key["v"] = 0x9B      # ESC while idle
    r = boot(order, script=quit_when_ready)
    moved = bytes(r["mem"][0x0800 + i] for i in range(len(receiver)))
    print("%-14s outcome: %s | banner shown: %s | receiver in place: %s | motor off: %s | hooks reset before output: %s" % (
        name, r["outcome"], BANNER in r["text"], moved == receiver, 0xC0E8 in r["io"],
        r["calls"][:2] == [SETVID, SETKBD]))
    expect(r["outcome"] == "monitor", "%s: boots, waits, and a key drops to the Monitor" % name)
    expect(moved == receiver, "%s: receiver.bin moved byte-for-byte to $0800" % name)
    expect(0xC0E8 in r["io"], "%s: drive motor turned off (slot 6: $C0E8)" % name)
    expect(r["calls"][:2] == [SETVID, SETKBD] and INIT in r["calls"], "%s: I/O hooks reset before any output" % name)
    expect(r["text"].count(BANNER) == 1, "%s: banner printed once" % name)

# ---------------------------------------------------------------- 2. sessions in a row, no retyping
# session 1: a picture to $2000, end packet with address 0 -> picture shown, key -> back to listening
# session 2: a few bytes of code to $6000, end packet runs it -> back to listening
# then a key while idle -> Monitor
picture = bytes(range(64))
code = bytes([0xA9, 0x42, 0x8D, 0x00, 0x70, 0x60])          # LDA #$42 / STA $7000 / RTS
plan = {"phase": 0, "t": 0}
def sessions(steps, text, rx, key, mem):
    n = text.count(BANNER)
    if plan["phase"] == 0 and n == 1:
        rx.extend(send.make_packet(0, 0x2000, picture) + send.make_packet(1, 0x0000, b""))
        plan.update(phase=1, t=steps)
    elif plan["phase"] == 1 and not rx and steps > plan["t"] + 20000 and key["v"] == 0:
        key["v"] = 0xA0                                          # look at the picture, then a key
        plan["phase"] = 2
    elif plan["phase"] == 2 and n == 2 and not (key["v"] & 0x80):
        rx.extend(send.make_packet(2, 0x6000, code) + send.make_packet(3, 0x6000, b""))
        plan["phase"] = 3
    elif plan["phase"] == 3 and n == 3 and not rx:
        plan.update(phase=4, t=steps)
    elif plan["phase"] == 4 and steps == plan["t"] + SETTLE:
        key["v"] = 0x9B                                          # done: ESC while idle
        plan["phase"] = 5
r = boot(DOS_ORDER, script=sessions)
mem = r["mem"]
pic_ok = bytes(mem[0x2000 + i] for i in range(64)) == picture
print("sessions: outcome %s | banners %d | replies %s | picture landed %s | uploaded code ran %s" % (
    r["outcome"], r["text"].count(BANNER), [hex(b) for b in r["tx"]], pic_ok, mem[0x7000] == 0x42))
expect(pic_ok, "session 1: picture uploaded")
expect(0xC050 in r["io"] and 0xC057 in r["io"], "session 1: hi-res shown at the end of the session")
expect(mem[0x7000] == 0x42, "session 2: uploaded code ran")
expect(r["text"].count(BANNER) == 3, "the receiver restarted by itself after each session (3 banners)")
expect(r["calls"].count(INIT) == 3 and r["calls"].count(HOME) == 3,
       "the screen is reset (INIT, HOME) before each of the 3 starts, so only one banner shows at a time")
expect(r["tx"] == [0x06] * 4, "every packet ACKed")
expect(r["outcome"] == "monitor", "a key while idle ends in the Monitor")

# ---------------------------------------------------------------- 3. the ROM model's assumption, stated
expect(sector(0)[0] == 2, "boot sector asks the ROM for exactly 2 sectors")
finish("test_boot")
