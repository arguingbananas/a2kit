"""Test `a2kit ping`.

1. Against a fake Apple II on a pseudo-terminal that answers at the real line
   rate, with an optional extra delay (as a USB adapter adds), NAKs, silence or
   garbage: checks the timing arithmetic and each diagnosis.
2. Against the real receiver.bin in the py65 simulator: checks the packets land
   where ping says (the bottom text line and $4000), that a corrupted packet
   gets a NAK from the real receiver, and that the receiver is still listening.
3. The command line, end to end, over the pseudo-terminal.
"""
import os, re, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import serial
from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory
import ping, send
from testlib import expect, finish

BAUD = 19200
CHAR = 10 / BAUD


# ---------------------------------------------------------------- 1. fake Apple II
class FakeApple(threading.Thread):
    """Reads packets from the master side of a pty and answers like the receiver,
    after the time the bytes would take on a real line plus `delay`."""
    def __init__(self, master, mode="ok", delay=0.0):
        super().__init__(daemon=True)
        self.master, self.mode, self.delay, self.mem, self.n = master, mode, delay, {}, 0

    def rd(self, k):
        out = b""
        while len(out) < k:
            out += os.read(self.master, k - len(out))
        return out

    def run(self):
        try:
            while True:
                if self.rd(1)[0] != 0xA5:
                    continue
                seq, ln, lo, hi = self.rd(4)
                data = self.rd(ln) if ln else b""
                ck = self.rd(1)[0]
                x = seq ^ ln ^ lo ^ hi
                for b in data: x ^= b
                self.n += 1
                time.sleep((ln + 7) * CHAR + self.delay)          # line time for packet + reply, plus delay
                if self.mode == "silent":
                    continue
                if self.mode == "garbage":
                    os.write(self.master, b"\xFF"); continue
                if self.mode == "nak3" and self.n % 3 == 0:
                    x ^= 1
                if x != ck:
                    os.write(self.master, b"\x15"); continue
                for i, b in enumerate(data): self.mem[(lo | hi << 8) + i] = b
                os.write(self.master, b"\x06")
        except OSError:
            pass

def fake_link(mode="ok", delay=0.0, timeout=0.5):
    master, slave = os.openpty()
    apple = FakeApple(master, mode, delay); apple.start()
    port = serial.Serial(os.ttyname(slave), BAUD, timeout=timeout)
    return port, apple

def quiet(_):
    pass

def run_and_report(mode, delay=0.0, count=6, speed=8, timeout=0.5):
    port, apple = fake_link(mode, delay, timeout)
    lines = []
    res = ping.run(port, BAUD, count, speed, 128, out=lines.append, sleep=lambda s: None)
    code = ping.report(res, BAUD, 128, out=lines.append)
    port.close()
    return res, code, "\n".join(lines), apple

def avg(xs): return sum(xs) / len(xs) if xs else float("nan")

res, code, text, apple = run_and_report("ok")
line = bytes(apple.mem.get(ping.TEXT_LINE + i, 0) for i in range(40))
print("ok link: %d/%d ACK, port delay %.1f ms, speed %.0f B/s, exit %d" % (res["ack"], res["sent"], avg(res["turnaround"]) * 1000, res["speed"], code))
expect(code == 0 and res["ack"] == 6 and res["speed_ack"] == 8, "a good link: every packet acknowledged, exit 0")
expect(line == ping.apple_text("A2KIT PING 6 OF 6"), "the last ping's text reached the bottom-line address")
expect(abs(avg(res["turnaround"])) < 0.004, "port delay near zero when the link adds none")
expect(0.80 * 1920 < res["speed"] <= 1920, "speed test lands just under the 1920 bytes/s line rate")
expect("Link OK" in text and "faster without it" not in text, "good link: says OK, no advice about delay")

res, code, text, _ = run_and_report("ok", delay=0.016)
d = avg(res["turnaround"])
print("16 ms delay: port delay %.1f ms, speed %.0f B/s" % (d * 1000, res["speed"]))
expect(0.013 < d < 0.021, "a 16 ms adapter delay is measured as about 16 ms")
expect(code == 0 and re.search(r"adds about 1[4-8] ms", text) is not None and "User Manual" in text,
       "slow port: the report names the delay and suggests reducing it")

res, code, text, _ = run_and_report("nak3")
print("every 3rd packet damaged: ack %d nak %d, exit %d" % (res["ack"], res["nak"], code))
expect(res["nak"] == 2 and code == 1 and "arrive damaged" in text, "NAKs: counted, exit 1, noise diagnosis")

res, code, text, _ = run_and_report("silent", count=3, timeout=0.2)
print("silent: none %d, speed test run: %s, exit %d" % (res["none"], res["speed"] is not None, code))
expect(res["none"] == 3 and res["speed"] is None and code == 1 and "No reply at all" in text,
       "no reply: counted, speed test skipped, checklist printed")

res, code, text, _ = run_and_report("garbage", count=3)
print("garbage: other %d, exit %d" % (res["other"], code))
expect(res["other"] == 3 and code == 1 and "speed probably doesn't match" in text and "$FF" in text,
       "unexpected bytes: baud-mismatch diagnosis naming the byte")


# ---------------------------------------------------------------- 2. the real receiver
class SimPort:
    """A serial port whose other end is receiver.bin running in py65."""
    def __init__(self, corrupt_packet=None):
        self.mem = ObservableMemory(); self.mpu = MPU(memory=self.mem)
        self.rx, self.tx, self.packets, self.corrupt = [], [], 0, corrupt_packet
        self.mem.subscribe_to_read([0xC0A9], lambda a: 0x10 | (0x08 if self.rx else 0))
        self.mem.subscribe_to_read([0xC0A8], lambda a: self.rx.pop(0) if self.rx else 0)
        self.mem.subscribe_to_write([0xC0A8], lambda a, v: (self.tx.append(v), None)[1])
        self.mem.subscribe_to_read([0xC000], lambda a: 0)
        self.mem[0xFDED] = 0x60                                     # COUT: RTS
        for i, b in enumerate(open(os.path.join(ROOT, "receiver.bin"), "rb").read()): self.mem[0x0800 + i] = b
        for i, b in enumerate([0x20, 0x00, 0x08, 0x00]): self.mem[0x0300 + i] = b   # JSR $0800 ; BRK
        self.mpu.pc, self.mpu.sp = 0x0300, 0xFF
        self.exited = False

    def write(self, data):
        data = bytearray(data)
        self.packets += 1
        if self.packets == self.corrupt:
            data[8] ^= 0x40                                         # damage a data byte in transit
        self.rx.extend(data)

    def read(self, n):
        steps = 0
        while not self.tx and steps < 1_500_000:                    # longer than the receiver's own timeout
            if self.mpu.pc == 0x0303: self.exited = True; break
            self.mpu.step(); steps += 1
        return bytes([self.tx.pop(0)]) if self.tx else b""

    def reset_input_buffer(self):
        self.tx.clear()

sim = SimPort()
lines = []
res = ping.run(sim, BAUD, count=3, speed=4, chunk=128, out=lines.append, sleep=lambda s: None)
code = ping.report(res, BAUD, 128, out=lines.append)
screen = bytes(sim.mem[ping.TEXT_LINE + i] for i in range(40))
scratch = bytes(sim.mem[ping.SPEED_ADDR + i] for i in range(128))
want = bytes((k * 37) & 0xFF for k in range(128))
print("real receiver: %d/%d ACK, speed test %d/%d, bottom line %r" % (
    res["ack"], res["sent"], res["speed_ack"], res["speed_sent"], bytes(c & 0x7F for c in screen).decode().strip()))
expect(res["ack"] == 3 and res["speed_ack"] == 4 and code == 0, "real receiver acknowledges every ping and speed packet")
expect(screen == ping.apple_text("A2KIT PING 3 OF 3"), "real receiver wrote the ping text to the bottom line ($07D0)")
expect(scratch == want, "speed-test data landed at $4000")
expect(not sim.exited, "the receiver is still listening afterwards (ping never ends the session)")
touched = [a for a in range(0xC000) if sim.mem[a] and not (0x0000 <= a <= 0x01FF or 0x0300 <= a <= 0x0303 or 0x03F0 <= a <= 0x03F1 or 0x03FE <= a <= 0x03FF
           or 0x0800 <= a <= 0x0AFF or ping.TEXT_LINE <= a < ping.TEXT_LINE + 40 or ping.SPEED_ADDR <= a < ping.SPEED_ADDR + 128
           )]
expect(not touched, "ping writes nowhere except the text line and the scratch bytes")

sim = SimPort(corrupt_packet=2)
lines = []
res = ping.run(sim, BAUD, count=3, speed=0, out=lines.append, sleep=lambda s: None)
print("real receiver, packet 2 damaged: ack %d nak %d" % (res["ack"], res["nak"]))
expect(res["ack"] == 2 and res["nak"] == 1, "a damaged packet gets a NAK from the real receiver")


# The real FTDI adapter on its 16 ms timer (8 Nov): pings showed 11 ms of port delay, but the
# speed test still ran at 8,011 bytes/s, and the 1 ms timer only raised that to 8,546 (7%).
ftdi = {"sent": 20, "ack": 20, "nak": 0, "other": 0, "none": 0, "others": set(), "rtt": [0.0159] * 20,
        "turnaround": [0.0111] * 20, "speed": 8011.0, "speed_sent": 64, "speed_ack": 64}
lines = []
ping.report(ftdi, 115200, 128, out=lines.append)
m = re.search(r"about (\d+)% faster", "\n".join(lines))
print("FTDI on its 16 ms timer: %s%% faster predicted" % (m.group(1) if m else "?"))
expect(m is not None and 5 <= int(m.group(1)) <= 15, "the gain is judged from the speed test, not by adding the ping delay to every packet")
lines = []
ping.report(dict(ftdi, speed=None, speed_sent=0, speed_ack=0), 115200, 128, out=lines.append)
expect("up to" in "\n".join(lines), "without a speed test the gain is given as an upper limit")


# ---------------------------------------------------------------- low-latency mode
class LowLatencyPort:
    def __init__(self, error=None): self.error, self.calls = error, []
    def set_low_latency_mode(self, on):
        self.calls.append(on)
        if self.error: raise self.error

p = LowLatencyPort()
expect(send.set_low_latency(p) is True and p.calls == [True], "low-latency mode is switched on when the driver offers it")
expect(send.set_low_latency(LowLatencyPort(ValueError("not supported"))) is False, "a driver that refuses it is ignored")
expect(send.set_low_latency(object()) is False, "a port without the setting (Windows) is ignored")
master, slave = os.openpty()
pty = serial.Serial(os.ttyname(slave), BAUD)
expect(send.set_low_latency(pty) is False, "a pseudo-terminal, which has no such setting, is ignored")
pty.close()
port = send.open_port(os.ttyname(slave), BAUD, timeout=0.5)
expect(port.is_open and port.timeout == 0.5, "send.open_port opens the port even where low-latency mode isn't offered")
port.close()


# ---------------------------------------------------------------- 3. command line
master, slave = os.openpty()
FakeApple(master, "ok").start()
r = subprocess.run([sys.executable, os.path.join(HERE, "a2kit.py"), "ping", "--port", os.ttyname(slave),
                    "--count", "3", "--speed", "4"], capture_output=True, text=True, timeout=60)
print("CLI: exit %d, last line: %s" % (r.returncode, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(none)"))
expect(r.returncode == 0 and "3 of 3 answered with ACK" in r.stdout and "Link OK" in r.stdout, "a2kit ping end to end: exit 0 and a summary")
r = subprocess.run([sys.executable, os.path.join(HERE, "a2kit.py"), "ping", "--port", "/dev/null", "--chunk", "300"],
                   capture_output=True, text=True)
expect(r.returncode == 1 and "--chunk" in r.stderr, "bad --chunk is refused before opening the port")
finish("test_ping")
