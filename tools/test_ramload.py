"""Test `a2kit ramload` in py65: a booted receiver takes a new boot image over
the link and restarts as it, as if it had booted from disk. The boot images and
the host tool are real; the serial card and the Monitor routines are modelled."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory
import ramload, send
from testlib import expect, finish


class Machine:
    """A boot image already running, with the pyserial calls the tools use."""
    def __init__(self, boot):
        self.mem = mem = ObservableMemory()
        self.mpu = MPU(memory=mem)
        self.rx, self.tx, self.text, self.motor = [], [], [], []
        mem.subscribe_to_read([0xC0A9], lambda a: 0x10 | (8 if self.rx else 0))
        mem.subscribe_to_read([0xC0A8], lambda a: self.rx.pop(0) if self.rx else 0)
        mem.subscribe_to_write([0xC0A8], lambda a, v: (self.tx.append(v), None)[1])
        mem.subscribe_to_read([0xC000], lambda a: 0)
        mem.subscribe_to_read([a for a in range(0xC098, 0xC0F9, 0x10) if a != 0xC0A8],     # slot 2 is the serial card
                              lambda a: (self.motor.append(a), 0)[1])
        for a in (0xFDED, 0xFE93, 0xFE89, 0xFB2F, 0xFC58):
            mem[a] = 0x60
        for i, b in enumerate(boot):
            mem[0x0800 + i] = b
        mem[0x2B] = 0x60
        self.mpu.pc, self.mpu.sp = 0x0801, 0xE0
        self.step(30000)

    def step(self, n):
        for _ in range(n):
            if self.mpu.pc == 0xFDED:
                self.text.append(chr(self.mpu.a & 0x7F))
            self.mpu.step()

    def write(self, d): self.rx.extend(d)
    def flush(self): pass
    def reset_input_buffer(self): self.tx.clear()

    def read(self, n=1):
        for _ in range(100000):
            if len(self.tx) >= n:
                break
            self.step(1)
        out = bytes(self.tx[:n])
        del self.tx[:n]
        return out


boot = open(os.path.join(ROOT, "boot.bin"), "rb").read()
new = bytearray(boot)
at = bytes(new).index(b"A2KIT\r")
new[at:at + 5] = b"A2NEW"               # the same code, with a banner that shows which one is running
new = bytes(new)

m = Machine(boot)
expect("A2KIT" in "".join(m.text), "the old image is running")
m.text.clear()
m.mem[0x4000] = 0x77
m.motor.clear()
ramload.upload(m, new, disk_slot=5)
m.step(60000)
print("banner after load:", "".join(m.text).strip())
expect("A2NEW" in "".join(m.text), "the new image restarted and printed its banner")
expect(bytes(m.mem[0x0800 + i] for i in range(256)) == new[4:260], "the receiver moved into place")
expect(bytes(m.mem[0x0980 + i] for i in range(128)) == new[0x180:], "the restart code is in place")
expect(m.mem[0x4000] == 0x77, "memory outside the documented ranges is left alone")
expect(m.mem[0x2B] == 0x50 and m.motor == [0xC0D8], "the boot code gets the disk slot it was given")
expect(m.mpu.sp > 0xF0, "the stack starts fresh")
expect(ramload.verify(m, new) is True, "the code in memory matches the image")
expect(ramload.verify(m, boot) is False, "a different image is noticed")
for at in (0x0800, 0x08FF, 0x0901, 0x0990, 0x09FF):     # none stops the receiver answering ($0990 is boot code, run once)
    keep = m.mem[at]; m.mem[at] = keep ^ 0xFF
    expect(ramload.verify(m, new) is False, "one damaged byte at $%04X is noticed" % at)
    m.mem[at] = keep
m.tx.clear(); m.write(send.make_packet(0, 0x4000, b"\x5A"))
expect(m.read(1) == bytes([send.ACK]) and m.mem[0x4000] == 0x5A, "the new receiver takes uploads")

class Silent(Machine):
    """A receiver that ignores read requests, like one built before they existed."""
    def write(self, d):
        if d[0] != send.READ_SYNC:
            super().write(d)
    def read(self, n=1):
        return super().read(n) if self.tx else b""
expect(ramload.verify(Silent(boot), boot) is None, "a receiver without read support is reported as unchecked")

m = Machine(boot)
ramload.upload(m, boot); m.step(60000)
ramload.upload(m, boot); m.step(60000)
expect(ramload.verify(m, boot) is True and m.mpu.sp > 0xF0, "loads can repeat without the stack growing")

for bad, what in ((boot[:511], "too short"), (boot + b"\0", "too long"), (b"\x03" + boot[1:], "wrong sector count")):
    try:
        ramload.check_image(bad); refused = False
    except ValueError:
        refused = True
    expect(refused, "an image that is %s is refused" % what)

ld = ramload.loader()
expect(ramload.LOADER + len(ld) - 1 == 0x601A, "the loader ends where the docs say ($601A)")
finish("test_ramload")
