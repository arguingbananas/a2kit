"""Packed pictures: the packer, and the real unpack.bin on a py65 6502.

1. pack/unpack round trips on pictures and on awkward data: runs, the longest
   literal run and copy, distances at the near/far boundary, overlapping copies.
2. unpack.bin turns each packed block into exactly the original, touches nothing
   outside the destination (and its own zero page), returns with carry clear,
   and takes about as long as pack.unpack_seconds says.
3. plan() packs only when that is quicker; send_packed() talks the protocol in
   the right order, against a fake receiver.
"""
import os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from py65.devices.mpu6502 import MPU
from PIL import Image
import a2hdr, imgconv, pack, send
from testlib import expect, finish

RETURN = 0x0200                 # where the unpacker's RTS lands: a BRK the test stops on
ZP_OK = set(range(0x06, 0x0E))


def run_unpacker(packed, dest, length):
    mpu = MPU()
    mem = mpu.memory
    junk = random.Random(len(packed))
    for a in range(0x0000, 0xC000):         # junk everywhere, so stray writes show
        mem[a] = junk.randrange(256)
    before = list(mem[0:0xC000])
    code = pack.unpacker(dest)
    for i, b in enumerate(code):
        mem[pack.UNPACKER_ADDR + i] = b
    for i, b in enumerate(packed):
        mem[pack.BUFFER + i] = b
    mem[RETURN] = 0x00
    mem[0x01FE], mem[0x01FF] = (RETURN - 1) & 0xFF, (RETURN - 1) >> 8      # as JSR leaves it
    mpu.sp = 0xFD
    mpu.pc = pack.UNPACKER_ADDR
    mpu.p |= mpu.CARRY                      # set on the way in: it must come back clear
    start = mpu.processorCycles
    steps = 0
    while mpu.pc != RETURN and steps < 2000000:
        mpu.step()
        steps += 1
    out = bytes(mem[dest:dest + length])
    changed = [a for a in range(0xC000) if mem[a] != before[a]
               and not dest <= a < dest + length
               and not pack.UNPACKER_ADDR <= a < pack.UNPACKER_ADDR + len(code)
               and not pack.BUFFER <= a < pack.BUFFER + len(packed)
               and a not in ZP_OK and not 0x0100 <= a <= 0x01FF and a != RETURN]
    return out, mpu.pc == RETURN, not (mpu.p & mpu.CARRY), mpu.processorCycles - start, changed


cases = {}
for name in ("logo", "sphere"):
    g = imgconv.prepare(Image.open(os.path.join(HERE, "..", "examples", name + ".png")), False, False)
    cases[name] = imgconv.to_hgr(imgconv.atkinson(g))
cases["colour"] = imgconv.colour_screen(imgconv.prepare(Image.open(os.path.join(HERE, "..", "examples", "colour.png")), False, False, "RGB"))
rnd = random.Random(7)
cases["random"] = bytes(rnd.randrange(256) for _ in range(8192))
cases["zeros"] = bytes(8192)
cases["runs"] = bytes(b for n in range(300) for b in [n & 0xFF] * (n % 70 + 1))[:8192]
block = bytes(rnd.randrange(256) for _ in range(556))
cases["near/far boundary"] = block + block[:400] * 3        # copies from 556, 956, ... back, and near ones
cases["two-byte pattern"] = b"\xAA\x55" * 4096
cases["literal then copy at 129"] = bytes(rnd.randrange(256) for _ in range(129)) * 5
cases["one byte"] = b"\x2A"

# 1. round trips in Python
for name, data in cases.items():
    p = pack.pack(data)
    expect(pack.unpack(p) == data, "%s: pack/unpack round trip" % name)
    expect(len(p) <= len(data) + len(data) // 128 + 2, "%s: never much bigger than plain (%d -> %d)" % (name, len(data), len(p)))
    print("  %-26s %5d -> %5d bytes" % (name, len(data), len(p)))
expect(len(pack.pack(cases["zeros"])) < 300, "a blank screen packs to almost nothing")

# 2. the real unpacker
expect(len(pack.unpacker_bin()) <= 0x03D0 - pack.UNPACKER_ADDR, "unpack.bin stays below the Monitor's vectors at $03D0")
for name, data in cases.items():
    p = pack.pack(data)
    if pack.BUFFER + len(p) > 0xC000:
        continue
    dest = 0x2000 if len(data) == 8192 else 0x2345          # also an unaligned destination
    out, returned, carry_clear, cycles, stray = run_unpacker(p, dest, len(data))
    expect(returned, "%s: the unpacker returns" % name)
    expect(out == data, "%s: unpack.bin makes the original" % name)
    expect(carry_clear, "%s: returns with carry clear, so the boot loop listens again" % name)
    expect(not stray, "%s: writes nothing outside its places (first stray: %s)" % (name, ["$%04X" % a for a in stray[:3]]))
    est = pack.unpack_seconds(len(data), p) * a2hdr.CPU_HZ
    expect(0.6 * est < cycles < 1.3 * est, "%s: %d cycles, about the estimate (%d)" % (name, cycles, est))
out, *_ = run_unpacker(pack.pack(cases["logo"]), 0x4000, 8192)
expect(out == cases["logo"], "unpacks to page 2 as well")

# 3. when to pack, and the protocol
p, plain, packed_t = pack.plan(cases["logo"], 0x2000)
expect(p is not None and packed_t < plain, "the logo is quicker packed (%.2f s against %.2f s)" % (packed_t, plain))
p, plain, packed_t = pack.plan(cases["random"], 0x2000)
expect(p is None, "random bytes go plain")
p, _, _ = pack.plan(cases["colour"], 0x2000, baud=19200)
expect(p is not None, "the colour picture packs at 19200 baud, where the line is slower")


class FakeSender:
    def __init__(self, listening_after=2):
        self.calls, self.polls, self.listening_after = [], 0, listening_after

    def send_blob(self, data, addr):
        self.calls.append(("blob", addr, len(data)))

    def finish(self, exec_addr=0, probe=True):
        self.calls.append(("finish", exec_addr))
        return True

    def receiver_listening(self):
        self.polls += 1
        return self.polls >= self.listening_after


s = FakeSender()
data = cases["logo"]
pk = pack.pack(data)
pack.send_packed(s, data, 0x4000, pk)
expect(s.calls == [("blob", 0x0300, len(pack.unpacker_bin())), ("blob", 0x6000, len(pk)), ("finish", 0x0300)],
       "send_packed: unpacker, packed bytes, then a session that runs it")
expect(s.polls == 2, "send_packed: waits until the receiver listens again")
code = pack.unpacker(0x4000)
expect(code[3:7] == bytes([0x00, 0x60, 0x00, 0x40]), "the unpacker is told the buffer and the destination")
try:
    pack.send_packed(FakeSender(listening_after=99), data, 0x2000, pk)
    expect(False, "send_packed: a receiver that never comes back is an error")
except IOError as e:
    expect("didn't start listening" in str(e), "send_packed: a receiver that never comes back is an error")
expect(send.upload_seconds([len(pk)]) < send.upload_seconds([8192]), "packed uploads are shorter")

finish("test_pack")
