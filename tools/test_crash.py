"""Test memory reads and the crash handler on the real receiver.bin in py65.

What is real: the receiver, tools/send.py's reads, and the a2kit crash report.
What is modelled: the 6551, the keyboard, and the Monitor's BRK path. The IRQ
entry at $FA40 matches a real Apple II+ ROM (FA40L, FA59L in the Monitor), and the
save routine at $FF4C is written out from memory of it; printing the registers
is reduced to recording the call.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from py65.devices.mpu6502 import MPU as MPU6502
from py65.devices.mpu65c02 import MPU as MPU65C02
from py65.memory import ObservableMemory
import a2hdr, canary, crash, peek, send
from testlib import expect, finish

NAME = sys.argv[1] if len(sys.argv) > 1 else "receiver.bin"
BAUD = 19200 if "19200" in NAME else a2hdr.DEFAULT_BAUD
code = open(os.path.join(ROOT, NAME), "rb").read()
expect(len(code) <= 380, "%s, language-card lines included, fits the boot image's 380 bytes (%d bytes)" % (NAME, len(code)))
D, S, CMD, CTRL = 0xC0A8, 0xC0A9, 0xC0AA, 0xC0AB
INSDS1, RGDSP1, MON, COUT = 0xF882, 0xFADA, 0xFF65, 0xFDED


class Machine:
    """The receiver on a modelled Apple II, with the pyserial calls the tools use."""
    def __init__(self, cpu=MPU6502):
        self.mem = mem = ObservableMemory()
        self.mpu = cpu(memory=mem)
        self.rx, self.tx, self.key, self.resets, self.calls = [], [], 0, [], []
        mem.subscribe_to_read([S], lambda a: 0x10 | (0x08 if self.rx else 0))
        mem.subscribe_to_read([D], lambda a: self.rx.pop(0) if self.rx else 0)
        mem.subscribe_to_write([D], lambda a, v: (self.tx.append(v), None)[1])
        mem.subscribe_to_write([S], lambda a, v: (self.resets.append(v), None)[1])
        mem.subscribe_to_read([0xC000], lambda a: self.key)
        mem.subscribe_to_read([0xC010], self.strobe)
        for a in (COUT, INSDS1, RGDSP1, MON):
            mem[a] = 0x60
        # the ROM's IRQ/BRK entry and register save
        for a, b in ((0xFFFE, 0x40), (0xFFFF, 0xFA)):
            mem[a] = b
        rom = {0xFA40: [0x85, 0x45, 0x68, 0x48, 0x0A, 0x0A, 0x0A, 0x30, 0x03, 0x6C, 0xFE, 0x03,   # STA ACC ... JMP ($3FE)
                        0x28, 0x20, 0x4C, 0xFF, 0x68, 0x85, 0x3A, 0x68, 0x85, 0x3B, 0x6C, 0xF0, 0x03],  # PLP, JSR SAV1, save PC, JMP ($3F0)
               0xFF4C: [0x86, 0x46, 0x84, 0x47, 0x08, 0x68, 0x85, 0x48, 0xBA, 0x86, 0x49, 0xD8, 0x60]}  # SAV1
        for a, bs in rom.items():
            for i, b in enumerate(bs):
                mem[a + i] = b
        for i, b in enumerate(code):
            mem[0x0800 + i] = b
        for i, b in enumerate([0x20, 0x00, 0x08, 0x00]):        # JSR $0800 / BRK, as the restart loop calls it
            mem[0x0300 + i] = b
        self.mpu.pc, self.mpu.sp = 0x0300, 0xF5
        self.timeout = 300_000

    def strobe(self, a):
        self.key = 0
        return 0

    def step(self, n=1):
        for _ in range(n):
            if self.mpu.pc in (INSDS1, RGDSP1, MON):
                self.calls.append(self.mpu.pc)
            if self.mpu.pc == MON:
                return
            self.mpu.step()

    # pyserial's face, for the tools
    def write(self, data):
        self.rx.extend(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        self.tx.clear()

    def read(self, n=1):
        for _ in range(self.timeout):
            if len(self.tx) >= n:
                break
            self.step()
        out = bytes(self.tx[:n])
        del self.tx[:n]
        return out


def idle():
    m = Machine()
    m.step(20000)
    return m


def crash_at(m, pc, a, x, y, p, s):
    """Put the CPU straight into a BRK at pc, with these registers."""
    m.mem[pc] = 0x00
    m.mpu.pc, m.mpu.a, m.mpu.x, m.mpu.y, m.mpu.p, m.mpu.sp = pc, a, x, y, p, s
    m.step(120)


# ---------------------------------------------------------------- reading memory
m = idle()
expect(m.mem[0x3F0] | m.mem[0x3F1] << 8 > 0x0800, "the receiver points the BRK vector into itself")
expect(m.mem[crash.CRASH_FLAG] == 0, "the crash flag is clear after a normal start")
for i in range(512):
    m.mem[0x4000 + i] = (i * 7 + (i >> 8)) & 0xFF
want = bytes(m.mem[0x4000 + i] for i in range(512))
expect(send.read_memory(m, 0x4000, 1) == want[:1], "reads 1 byte")
expect(send.read_memory(m, 0x4000, 255) == want[:255], "reads 255 bytes")
expect(send.read_memory(m, 0x4000, 256) == want[:256], "reads 256 bytes (count 0)")
expect(send.read_memory(m, 0x4000, 512) == want, "reads across several requests")
expect(send.read_memory(m, 0x5FF0, 32) == bytes(32), "reads across a page boundary")

bad = bytearray(send.make_read_packet(0x4000, 8)); bad[3] ^= 1
m.tx.clear(); m.write(bad)
expect(m.read(1) == bytes([send.NAK]) and m.read(1) == b"", "a damaged read request gets a NAK and no data")
m.tx.clear(); m.write(send.make_read_packet(0x4000, 8)[:3]); m.timeout = 1_500_000
expect(m.read(1) == b"", "a cut-off read request sends nothing")
m.timeout = 300_000
m.step(1_500_000)
expect(send.read_memory(m, 0x4000, 8) == want[:8], "the receiver recovers from a cut-off read request")

m.tx.clear(); m.write(send.make_packet(0, 0x4000, b"\xAA\x55")); expect(m.read(1) == bytes([send.ACK]), "uploads still work after reads")
expect(send.read_memory(m, 0x4000, 2) == b"\xAA\x55", "a read sees what an upload wrote")

class Flaky:
    """Drops the first reply and damages the second, then behaves."""
    def __init__(self, port): self.port, self.n = port, 0
    def write(self, d): self.n += 1; self.port.write(d)
    def flush(self): pass
    def reset_input_buffer(self): self.port.reset_input_buffer()
    def read(self, k=1):
        d = self.port.read(k)
        if self.n == 1: return b""
        if self.n == 2 and k > 1 and d: return bytes([d[0] ^ 1]) + d[1:]
        return d
expect(send.read_memory(Flaky(m), 0x4000, 2) == b"\xAA\x55", "a lost and a damaged reply are retried")

for at, n, ok in ((0xBFFF, 1, True), (0xBFFF, 2, False), (0xC000, 1, False), (0xC0FF, 1, False), (0xC0FF, 2, False),
                  (0xC100, 1, True), (0xC600, 256, True), (0xC800, 2047, True), (0xC800, 2048, False), (0xCFFF, 1, False), (0xCFFF, 2, False), (0xD000, 1, True), (0xFFFF, 1, True), (0xFFFF, 2, False), (0x4000, 0, False)):
    try:
        send.check_read_range(at, n); res = True
    except ValueError:
        res = False
    expect(res == ok, "read range $%04X + %d %s" % (at, n, "allowed" if ok else "refused"))
expect(peek.dump(0x03F5, bytes(range(5))) == ["03F5- 00 01 02", "03F8- 03 04"], "peek's dump lines up on 8-byte boundaries like the Monitor")
expect(peek.parse_hex("$BD00") == peek.parse_hex("0xBD00") == peek.parse_hex("bd00") == 0xBD00, "peek takes $BD00, 0xBD00 or BD00")

# a marker arriving just before a reply, as when a crash races a read
m = idle(); m.tx.clear(); m.tx.append(send.CRASH_MARK)
class KeepMarker:
    def __init__(self, port): self.port = port
    def write(self, d): self.port.write(d)
    def flush(self): pass
    def reset_input_buffer(self): pass
    def read(self, k=1): return self.port.read(k)
m.mem[0x4000] = 0x77
expect(send.read_memory(KeepMarker(m), 0x4000, 1) == b"\x77" and m.tx == [], "a crash marker ahead of a reply is skipped")

# ---------------------------------------------------------------- the crash handler
for cpu_name, cpu in (("6502", MPU6502), ("65C02", MPU65C02)):
    # the registers of the crash seen on the real machine, at $BD00
    m = Machine(cpu); m.step(20000)
    m.tx.clear(); m.resets.clear()
    for i, b in enumerate(b"\x11\x22\x33\x44"):
        m.mem[0x01F8 + i] = b
    crash_at(m, 0xBD00, 0x00, 0x20, 0x28, 0x33, 0xF7)
    expect(m.tx[:1] == [send.CRASH_MARK], "%s: the handler sends its marker" % cpu_name)
    expect(m.calls == [INSDS1, RGDSP1], "%s: the registers are printed on the screen too" % cpu_name)
    expect(m.resets, "%s: the 6551 is set up again in case the crash disturbed it" % cpu_name)
    expect(m.mem[crash.CRASH_FLAG] == send.CRASH_MARK, "%s: the crash flag is set" % cpu_name)
    m.tx.clear()
    expect(crash.crashed(m), "%s: the PC can read the flag" % cpu_name)
    lines = crash.report(m, BAUD)
    print(cpu_name, "report:", *lines, sep="\n  ")
    expect(lines[0].startswith("BD02- A=00 X=20 Y=28 ") and "S=F3" in lines[0] and "BRK at $BD00" in lines[0], "%s: the report shows what the Monitor would" % cpu_name)
    expect(any("11 22 33 44" in l for l in lines), "%s: the report includes the stack above S" % cpu_name)
    expect(len([l for l in lines if l[:2] == "01"]) == 32, "%s: the report includes the whole stack page" % cpu_name)
    expect(lines[-1] == "receiver code: matches " + NAME, "%s: undamaged receiver code is recognised" % cpu_name)
    banner = 0x0800 + len(code) - 2             # in the text, so the damage doesn't stop the receiver
    m.mem[banner] ^= 0xFF
    expect("1 bytes differ" in crash.report(m, BAUD)[-1] and "$%04X" % banner in crash.report(m, BAUD)[-1], "%s: damaged receiver code is reported with its address" % cpu_name)
    m.mem[banner] ^= 0xFF
    m.tx.clear(); m.write(send.make_packet(0, 0x4000, b"\x5A")); m.read(1)
    expect(m.mem[0x4000] == 0x5A, "%s: uploads still work after a crash" % cpu_name)
    m.calls.clear(); m.key = 0x9B; m.step(5000)
    expect(m.calls == [MON], "%s: a key after a crash drops to the Monitor" % cpu_name)

# interrupts stay off: the receiver doesn't use them, and the IRQ vector is junk
m = Machine(); m.mpu.p &= ~0x04; m.step(20000)
expect(m.mpu.p & 0x04, "the receiver turns interrupts off when it starts, whatever it was started with")
m = idle(); crash_at(m, 0xBD00, 0, 0x20, 0x28, 0x33, 0xF7)
expect(m.mpu.p & 0x04, "interrupts stay off after a crash taken with them on")

# the handler's calls leave what the crashed program had near S alone. Only the
# BRK and the Monitor's save write there, in the 5 bytes below S.
for s0 in (0xD2, 0x60):
    m = idle()
    for i in range(0x30, 0x100):
        m.mem[0x0100 + i] = i ^ 0x5A
    crash_at(m, 0x0000, 0, 0x20, 1, 0x37, s0)
    changed = [i for i in range(0x30, 0x100) if m.mem[0x0100 + i] != i ^ 0x5A]
    expect(changed == list(range(s0 - 4, s0 + 1)), "crash with S=$%02X: only the 5 bytes the BRK and the Monitor push change (%s)" % (s0, changed))

# a wild crash with very little stack left and the slot offset gone from X
m = idle(); m.tx.clear()
crash_at(m, 0x4000, 0x80, 0x00, 0x89, 0xB5, 0x2A)
expect(m.tx[:1] == [send.CRASH_MARK], "a wild crash, with X and S disturbed, still sends the marker")
m.tx.clear()
expect(crash.report(m, BAUD)[0].startswith("4002- A=80 X=00 Y=89") and "S=26" in crash.report(m, BAUD)[0], "a wild crash reports its registers")

# an end packet after a crash can't return into the crashed program
m = idle(); crash_at(m, 0xBD00, 0, 0x20, 0x28, 0x33, 0xF7); m.tx.clear(); m.calls.clear()
for i, b in enumerate([0x60]):
    m.mem[0x6000 + i] = b
m.write(send.make_packet(0, 0x6000, b"")); m.read(1); m.step(5000)
expect(m.calls == [MON], "a session started after a crash ends in the Monitor")

# the marker doesn't wait: a line that never reports ready can't stop the handler
m = idle(); m.mem.subscribe_to_read([S], lambda a: 0x00); m.tx.clear()
crash_at(m, 0xBD00, 0, 0x20, 0x28, 0x33, 0xF7)
expect(m.tx[:1] == [send.CRASH_MARK] and m.calls == [INSDS1, RGDSP1], "a stuck transmitter doesn't stop the marker or the printout")

# a restart clears the flag
m = idle(); crash_at(m, 0xBD00, 0, 0x20, 0x28, 0x33, 0xF7)
m.key = 0x9B; m.step(5000)
m.calls.clear(); m.mpu.pc, m.mpu.sp = 0x0300, 0xF5; m.step(20000)
expect(m.mem[crash.CRASH_FLAG] == 0, "restarting the receiver (980G) clears the crash flag")

# ---------------------------------------------------------------- a2kit crash --watch
class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self):
        self.t += 10.0
        return self.t

class Silent:
    """A port with nothing on the other end."""
    def write(self, d): pass
    def flush(self): pass
    def reset_input_buffer(self): pass
    def read(self, n=1): return b""

class Switch:
    """Starts healthy, runs `then` at poll `at`, and reports silence if told to."""
    def __init__(self, m, at, then): self.m, self.at, self.then, self.n, self.dead = m, at, then, 0, False
    def write(self, d):
        if d[0] == send.READ_SYNC and d[3] == crash.CRASH_FLAG:
            self.n += 1
            if self.n == self.at: self.then(self)
        if not self.dead: self.m.write(d)
    def flush(self): pass
    def reset_input_buffer(self): self.m.reset_input_buffer()
    def read(self, n=1): return b"" if self.dead else self.m.read(n)

log = []
m = idle(); m.timeout = 20000
p = Switch(m, 3, lambda s: crash_at(s.m, 0xBD00, 0, 0x20, 0x28, 0x33, 0xF7))
crash.watch(p, BAUD, 30, out=log.append, stamp=lambda: "T", clock=Clock(), polls=5)
print("watch log:", *log, sep="\n  ")
expect(log[1] == "T answering", "watch logs that the Apple II answers")
expect(log.count("T CRASH") == 1, "watch logs a crash once, however many polls follow")
expect(any("BD02- A=00" in l for l in log), "watch logs the crash's registers")

log = []
m = idle(); m.timeout = 20000
class Again(Switch):
    """Crashes at the second poll, and again (at a different place) at the fourth."""
    def write(self, d):
        if d[0] == send.READ_SYNC and d[3] == crash.CRASH_FLAG:
            self.n += 1
            if self.n == 2: crash_at(self.m, 0x0000, 0, 0x20, 1, 0x37, 0xD2)
            if self.n == 4: crash_at(self.m, 0x0322, 8, 0x20, 1, 0xB4, 0x1B)
        self.m.write(d)
crash.watch(Again(m, 0, None), BAUD, 30, out=log.append, stamp=lambda: "T", clock=Clock(), polls=6)
expect(log.count("T CRASH") == 2 and any("0002- " in l for l in log) and any("0324- " in l for l in log),
       "watch logs a second crash after the first, once each")

log = []
m = idle(); m.timeout = 20000
crash.watch(Switch(m, 2, lambda s: setattr(s, "dead", True)), BAUD, 30, out=log.append, stamp=lambda: "T", clock=Clock(), polls=4)
expect(log.count("T NOT ANSWERING") == 1 and "T CRASH" not in log, "watch logs silence once when a hang sends nothing")

log = []
m = idle(); m.timeout = 20000
class Outage(Switch):
    """Silent for the second poll's five tries, then healthy again."""
    def write(self, d):
        if d[0] == send.READ_SYNC and d[3] == crash.CRASH_FLAG:
            self.n += 1
            self.dead = 2 <= self.n <= 6
        if not self.dead: self.m.write(d)
crash.watch(Outage(m, 0, None), BAUD, 30, out=log.append, stamp=lambda: "T", clock=Clock(), polls=4)
expect("T answering again" in log, "watch logs when the Apple II comes back")

log = []
crash.watch(Silent(), BAUD, 30, out=log.append, stamp=lambda: "T", clock=Clock(), polls=1)
expect(log[1] == "T NOT ANSWERING", "watch copes with nothing on the line from the start")

calls = []
m = idle(); m.timeout = 20000
crash.watch(Outage(m, 0, None), BAUD, 30, out=lambda s: None, clock=Clock(), polls=4, each=calls.append)
expect(calls == [False, True, False], "watch runs its hook after each answered poll, saying when it follows a silence (%s)" % calls)

# ---------------------------------------------------------------- a2kit canary
e = canary.expected
expect(all(e(a) != e(a + 1) and e(a) != e(a ^ 0x8000) and e(a) != e(a ^ 0x4000) for a in range(canary.LO, canary.HI - 1)),
       "the canary pattern differs between neighbours and between addresses $4000 or $8000 apart")

class Script(Switch):
    """Runs events[n] at the nth crash check."""
    def __init__(self, m, events): super().__init__(m, 0, None); self.events = events
    def write(self, d):
        if d[0] == send.READ_SYNC and d[3] == crash.CRASH_FLAG:
            self.n += 1
            if self.n in self.events: self.events[self.n](self.m)
        self.m.write(d)

log = []
m = idle(); m.timeout = 20000
for i in range(0xC0):
    m.mem[0x0100 + i] = 0xFF                    # stack-page noise, below the receiver's live stack
events = {3: lambda m: m.mem.__setitem__(0x8080, 0x00),
          5: lambda m: [m.mem.__setitem__(0x0100 + i, 0) for i in range(0x80)],
          7: lambda m: m.mem.__setitem__(0x8080, canary.expected(0x8080)),
          9: lambda m: crash_at(m, 0xBD00, 0, 0x20, 0x28, 0x33, 0xF7)}
canary.run(Script(m, events), BAUD, check_every=60, interval=30, out=log.append, stamp=lambda: "T",
           clock=Clock(), polls=12, lo=0x7F00, hi=0x8100)
print("canary log:", *log, sep="\n  ")
text = "\n".join(log)
expect("T filled $7F00-$80FF (512 bytes)" in text and "first check: 0 byte(s) differ" in text, "canary fills the region and finds it intact")
expect(all(m.mem[a] == canary.expected(a) for a in range(0x7F00, 0x8100)), "canary's fill wrote the pattern")
x = canary.expected(0x8080)
expect(text.count("CANARY:") == 1 and "  $8080 expected %02X now 00 (xor %02X: %s)" % (x, x, canary.bit_names(x)) in text,
       "canary logs a changed byte once, with what it should be and its changed bits")
expect("STACK PAGE: " in text, "canary logs a wiped stack page")
expect("back to the pattern: $8080" in text, "canary logs a byte that goes back to the pattern")
expect(text.count("T CRASH") == 1 and "BD02- A=00" in text, "canary logs a crash like crash --watch")

# more changes than are listed: the rest are counted per bit, as from one failing chip
mem = bytearray(canary.pattern(0x8000, 0x8100))
for i in range(0, 0x100, 2):
    mem[i] ^= 0x01                              # D0 in 128 bytes
mem[1] ^= 0x80                                  # and D7 in one
log = []
canary.Canary(lambda a, n: bytes(mem) if a == 0x8000 else bytes(n), log.append, lambda: "T", 0x8000, 0x8100).check()
expect(sum(l.startswith("  $") for l in log) == canary.MAX_LINES and
       "  ... and %d more; in all 129, bits changed: D0 in 128, D7 in 1" % (129 - canary.MAX_LINES) in log,
       "canary lists the first changes and counts every change per bit (%s)" % [l for l in log if "more" in l])
finish("test_crash " + NAME)
