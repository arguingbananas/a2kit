"""Run runtime/receiver.bin in a py65 6502 simulator with a fake 6551 and
feed it real packets from send.py. Needs: ca65/ld65 build of the receiver, py65."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory
import send
from testlib import expect, finish

BIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "receiver.bin")
code = open(BIN, "rb").read()

SLOT = 2
D, S = 0xC088 + SLOT*16, 0xC089 + SLOT*16
rx = []            # bytes the "host" has sent, waiting to be read
tx = []            # bytes the Apple II sent back
key = {"v": 0}

mem = ObservableMemory()
mpu = MPU(memory=mem)
def rd_stat(a): return 0x10 | (0x08 if rx else 0)
def rd_data(a): return rx.pop(0) if rx else 0
def wr_data(a, v): tx.append(v)
mem.subscribe_to_read([S], rd_stat)
mem.subscribe_to_read([D], rd_data)
mem.subscribe_to_write([D], wr_data)
mem.subscribe_to_read([0xC000], lambda a: key["v"])
mem.subscribe_to_write([0xC010], lambda a, v: key.update(v=0))
mem.subscribe_to_read([0xC010], lambda a: (key.update(v=0), 0)[1])
switches = []
for a in (0xC050, 0xC051, 0xC052, 0xC054, 0xC057):
    mem.subscribe_to_read([a], lambda a, _a=a: (switches.append(_a), 0)[1])
# fake monitor COUT: collect text
out = []
mem[0xFDED] = 0x60   # RTS stub; capture A at JSR via breakpoint check below
for i, b in enumerate(code): mem[0x0800 + i] = b
# stub at $0300: JSR $0800 ; BRK
for i, b in enumerate([0x20, 0x00, 0x08, 0x00]): mem[0x0300 + i] = b
# exec target at $6000: LDA #$42 ; STA $7000 ; RTS
for i, b in enumerate([0xA9, 0x42, 0x8D, 0x00, 0x70, 0x60]): mem[0x6000 + i] = b

mpu.pc = 0x0300
mpu.sp = 0xFF

def run(max_steps=60_000_000, on_idle=None):
    steps = 0
    while steps < max_steps:
        if mpu.pc == 0xFDED: out.append(chr(mpu.a & 0x7F))
        if mem[mpu.pc] == 0x00 and mpu.pc == 0x0303: return True
        mpu.step(); steps += 1
        if on_idle: on_idle(steps)
    return False

# queue a full session up front: a good packet, a corrupted one, its resend, end packet w/ exec, end packet
data1 = bytes(range(200))
data2 = bytes((i * 7) & 0xFF for i in range(128))
rx.extend(send.make_packet(0, 0x4000, data1))
bad = bytearray(send.make_packet(1, 0x4100, data2)); bad[10] ^= 0x55
rx.extend(bad)                                   # expect NAK
rx.extend(send.make_packet(1, 0x4100, data2))     # resend, expect ACK
rx.extend(send.make_packet(2, 0x6000, b""))       # end: exec $6000
ok = run()
print("finished:", ok, "| banner:", repr("".join(out)))
print("replies :", [hex(b) for b in tx], "(want 06, 15, 06, 06)")
print("data1 ok:", bytes(mem[0x4000 + i] for i in range(200)) == data1)
print("data2 ok:", bytes(mem[0x4100 + i] for i in range(128)) == data2)
print("exec ran:", mem[0x7000] == 0x42)
expect(ok and "".join(out).startswith("A2KIT"), "receiver finishes and prints its banner")
expect(tx == [0x06, 0x15, 0x06, 0x06], "replies are ACK, NAK, ACK, ACK")
expect(bytes(mem[0x4000 + i] for i in range(200)) == data1 and bytes(mem[0x4100 + i] for i in range(128)) == data2, "both packets landed")
expect(mem[0x7000] == 0x42, "end packet ran the code at its address")

# corrupted-address packet must NOT write anywhere
mem[0x5000] = 0
b2 = bytearray(send.make_packet(3, 0x5000, b"\xAA" * 8)); b2[3] ^= 0x01   # flip addr bit, checksum now bad
rx.clear(); tx.clear()
rx.extend(b2); rx.extend(send.make_packet(0, 0, b""))   # then end marker (show screen)
mpu.pc = 0x0300; mpu.sp = 0xFF; switches.clear()
def press(steps):
    if steps > 200000: key["v"] = 0x8D
ok = run(on_idle=press)
print("bad-addr packet NAKed, nothing written:", tx[:1] == [0x15] and mem[0x5000] == 0 and mem[0x5001] == 0)
print("show-screen path: finished", ok, "| HGR switches hit:", [hex(a) for a in switches])
expect(tx[:1] == [0x15] and mem[0x5000] == 0 and mem[0x5001] == 0, "packet with a corrupted address is refused and writes nothing")
expect(ok and switches == [0xC050, 0xC052, 0xC054, 0xC057, 0xC051], "address 0 shows HGR page 1, then text after a key")

# a packet cut short must time out (~1 s) and the receiver must resync on the next one
mem[0x5800] = 0
rx.clear(); tx.clear(); key["v"] = 0
rx.extend(send.make_packet(4, 0x5800, b"\x11" * 16)[:9])          # sync, header and 3 data bytes, then silence
cut = len(rx)
def feed_rest(steps):
    # the rest of the session arrives only after the truncated packet has been abandoned
    if steps == 400_000 and len(rx) == 0:
        rx.extend(send.make_packet(5, 0x5800, b"\x22" * 16) + send.make_packet(0, 0x6000, b""))
mem[0x7000] = 0
mpu.pc = 0x0300; mpu.sp = 0xFF
ok = run(on_idle=feed_rest)
print("truncated packet: finished", ok, "| replies", [hex(b) for b in tx], "| data", hex(mem[0x5800]))
expect(ok and tx == [0x06, 0x06] and mem[0x5800] == 0x22 and mem[0x5801] == 0x22 and mem[0x7000] == 0x42,
       "a truncated packet times out, writes nothing, and the next packet is accepted")
finish("test_receiver")
