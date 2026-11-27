"""Language card tests, on a py65 model of the card's soft switches.

1. lcprobe.bin reports a good card as good, and finds a missing card, banks that
   aren't separate, and a stuck bit.
2. The real receiver writes $D000 and up to the card, bank 2, with ROM still
   readable, and does nothing harmful with no card.
3. The real sequencer plays sound across the hop from $BFFF to $D000: every
   sample comes out right, the hop is too short to matter, and ROM comes back.
   A show that stays in main RAM never touches the switches.
4. The real video player does the same in bursts, including one that stops
   exactly at the hop, and refuses a burst past the card's last page.
5. The video player draws fast blocks preloaded into the card, with the card
   read only around each one, refuses blocks in I/O, on the vectors' page or
   reading outside the card, and plays a whole clip preloaded into main RAM and
   the card.
6. Store ops (--refill) put bytes in main RAM and the card without drawing,
   refuse a bad check byte or an address outside preload memory, and a clip
   with far too little memory refills it during play with no overruns.
"""
import os, sys, wave
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
import numpy as np
from py65.devices.mpu6502 import MPU as MPU6502
from py65.devices.mpu65c02 import MPU as MPU65C02
from py65.memory import ObservableMemory
import a2hdr, lcprobe, send, show
import testlib
from testlib import expect, finish

ACIA_D, ACIA_S = 0xC0A8, 0xC0A9
CYCLES = a2hdr.SPEAKER_PLAYER_CYCLES
RETURN = 0x0303                       # the test stub: JSR start at $0300, then here


class Machine:
    """A 6502 with ROM at $D000-$FFFF and, optionally, a language card over it."""

    def __init__(self, cpu=MPU6502, card=True, alias=False, stuck=None):
        self.mem = ObservableMemory()
        self.mpu = cpu(memory=self.mem)
        self.card, self.alias, self.stuck = card, alias, stuck
        self.rom = bytearray(b"\xEA" * 0x3000)
        self.rom[0xFDED - 0xD000] = 0x60                    # COUT: RTS
        self.high, self.banks = bytearray(0x2000), {1: bytearray(0x1000), 2: bytearray(0x1000)}
        self.bank, self.read_ram, self.write_ram, self.armed = 2, False, False, False
        self.switches, self.rom_fetches = [], 0
        rng = list(range(0xD000, 0x10000))
        self.mem.subscribe_to_read(rng, self._read)
        self.mem.subscribe_to_write(rng, self._write)
        self.mem.subscribe_to_read(list(range(0xC080, 0xC090)), self._switch)

    def _where(self, a):
        if a >= 0xE000:
            return self.high, a - 0xE000
        return self.banks[2 if self.alias else self.bank], a - 0xD000

    def _read(self, a):
        if self.card and self.read_ram:
            arr, i = self._where(a)
            return arr[i] | (0x10 if a == self.stuck else 0)
        return self.rom[a - 0xD000]

    def _write(self, a, v):
        if self.card and self.write_ram:
            arr, i = self._where(a)
            arr[i] = v

    def _switch(self, a):
        self.switches.append(a)
        if self.card:
            n = a & 15
            self.bank = 1 if n & 8 else 2
            self.read_ram = n & 3 in (0, 3)
            if n & 1:                                       # two reads in a row turn writing on
                self.write_ram = self.write_ram or self.armed
                self.armed = True
            else:
                self.write_ram = self.armed = False
        return 0

    def load(self, addr, data):
        for i, b in enumerate(data):
            self.mem[addr + i] = b

    def load_sound(self, addr, data):
        """As the receiver would: a piece for $D000 and up goes in the card, bank 2."""
        if addr < a2hdr.CARD_START:
            return self.load(addr, data)
        assert addr == a2hdr.CARD_START
        self.banks[2][:len(data[:0x1000])] = data[:0x1000]
        self.high[:len(data[0x1000:])] = data[0x1000:]

    def run(self, start, limit=60_000_000):
        self.load(0x0300, [0x20, start & 255, start >> 8, 0x00])
        self.mpu.pc, self.mpu.sp = 0x0300, 0xFF
        n = 0
        while self.mpu.pc != RETURN:
            self.mpu.step(); n += 1
            assert n < limit, "runaway at $%04X" % self.mpu.pc


def binary(name):
    return open(os.path.join(ROOT, name), "rb").read()


# ---------------------------------------------------------------- 1. the probe
def probe(**kw):
    m = Machine(**kw)
    tx, key = [], {"at": None, "hit": False}

    def kbd(a):
        if key["at"] is None and bytes(tx).endswith(lcprobe.END_MARK):
            key["at"] = m.mpu.processorCycles + 1000
        return 0xC1 if key["at"] is not None and m.mpu.processorCycles >= key["at"] and not key["hit"] else 0

    def strobe(a):
        if key["at"] is not None and m.mpu.processorCycles >= key["at"]:
            key["hit"] = True
        return 0
    m.mem.subscribe_to_read([ACIA_S], lambda a: 0x10)
    m.mem.subscribe_to_write([ACIA_D], lambda a, v: (tx.append(v), None)[1])
    m.mem.subscribe_to_read([0xC000], kbd)
    m.mem.subscribe_to_read([0xC010], strobe)
    m.load(lcprobe.PROBE_ADDR, binary("lcprobe.bin"))
    m.run(lcprobe.PROBE_ADDR)
    return bytes(tx).decode().replace("\r", "\n"), m


for name, kw, good, want in (
        ("good card, 6502", {}, True, lcprobe.GOOD),
        ("good card, 65C02", {"cpu": MPU65C02}, True, lcprobe.GOOD),
        ("no card", {"card": False}, False, "AT D000"),
        ("banks not separate", {"alias": True}, False, "BANK 2: FAIL"),
        ("a stuck bit at $F123", {"stuck": 0xF123}, False, "AT F123 GOT")):
    text, m = probe(**kw)
    print("probe, %s: %s" % (name, text.strip().splitlines()[-2]))
    expect((lcprobe.GOOD in text) == good and want in text, "probe, %s: reported correctly" % name)
    expect(not m.read_ram and not m.write_ram, "probe, %s: leaves ROM switched in" % name)
    expect(m.mpu.sp == 0xFF and not m.mpu.p & m.mpu.CARRY, "probe, %s: returns cleanly, carry clear" % name)


# ---------------------------------------------------------------- 2. the receiver
def receive(name, packets, **kw):
    m = Machine(**kw)
    st = {"q": list(packets[0]), "next": 1}

    def tx(a, v):
        if st["next"] < len(packets):
            st["q"] = list(packets[st["next"]]); st["next"] += 1
        return None

    def rx(a):
        return st["q"].pop(0) if st["q"] else 0
    m.mem.subscribe_to_read([ACIA_S], lambda a: 0x10 | (0x08 if st["q"] else 0))
    m.mem.subscribe_to_read([ACIA_D], rx)
    m.mem.subscribe_to_write([ACIA_D], tx)
    m.mem.subscribe_to_read([0xC000], lambda a: 0)
    m.mem.subscribe_to_read([0xC010], lambda a: 0)
    m.mem[0x6000] = 0x60                                    # the end packet's JSR target: RTS
    m.load(0x0800, binary(name))
    m.run(0x0800)
    return m


chunk = bytes((i * 7 + 3) & 0xFF for i in range(128))
pk = [send.make_packet(0, 0xD000, chunk), send.make_packet(1, 0xE000, chunk), send.make_packet(2, 0xFE80, chunk),
      send.make_packet(3, 0x6000, b"")]
for name in ("receiver.bin", "receiver-19200.bin"):
    m = receive(name, pk)
    expect(bytes(m.banks[2][:128]) == chunk and bytes(m.banks[1][:128]) == bytes(128),
           "%s: $D000 lands in the card's bank 2 only" % name)
    expect(bytes(m.high[:128]) == chunk and bytes(m.high[0x1E80:0x1F00]) == chunk, "%s: $E000 and up land in the card" % name)
    expect(not m.read_ram, "%s: ROM stays readable" % name)
    expect(m.rom[0] == 0xEA, "%s: ROM itself is untouched" % name)
    m = receive(name, pk, card=False)
    expect(m.mpu.pc == RETURN, "%s: with no card, the session still finishes" % name)

# ---------------------------------------------------------------- 3. the sequencer
tmp = testlib.tempdir()


def make_wav(path, seconds):
    t = np.arange(int(seconds * 22050)) / 22050
    w = wave.open(path, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
    w.writeframes((0.7 * np.sin(2 * np.pi * (300 * t + 40 * t * t)) * 32767).astype("<i2").tobytes()); w.close()


def decode(edges):
    e1, e2 = edges[0::2], edges[1::2]
    return bytes((((w1 - 3) // 5 - 1) << 4) | ((w2 - 3) // 5 - 1) for w1, w2 in zip(
        [b - a for a, b in zip(e1[0::2], e2[0::2])], [b - a for a, b in zip(e1[1::2], e2[1::2])])), e1


def play_show(seconds, language_card, **kw):
    make_wav(os.path.join(tmp, "s.wav"), seconds)
    open(os.path.join(tmp, "show.toml"), "w").write('[[scene]]\naudio = "s.wav"\n')
    plan = show.build(os.path.join(tmp, "show.toml"), language_card)
    m = Machine(**kw)
    for addr, data, _label in plan:
        m.load_sound(addr, data)
    audio = b"".join(d for a, d, _ in plan if a >= a2hdr.AUDIO_START)
    edges = []

    def click(a):
        edges.append(m.mpu.processorCycles); return 0
    m.mem.subscribe_to_read([0xC030], click)
    m.mem.subscribe_to_read([0xC000], lambda a: 0)
    m.load(0x0C00, binary("sequencer.bin"))
    m.run(0x0C00)
    return m, plan, audio, edges


m, plan, audio, edges = play_show(0.5, False)
out, _ = decode(edges)
expect(out == audio, "show in main RAM: sound plays exactly")
expect(m.switches == [], "show in main RAM: the card's switches are never touched")

m, plan, audio, edges = play_show(9.1, True)
pages = len(audio) // 256
print("long show: %d pages (%.1f s), pieces %s" % (pages, len(audio) * 2 / a2hdr.SPEAKER_RATE,
                                                  [("$%04X" % a, len(d)) for a, d, _ in plan if a >= a2hdr.AUDIO_START]))
expect(pages > a2hdr.AUDIO_PAGES and pages <= a2hdr.AUDIO_PAGES_CARD, "long show: more sound than main RAM holds")
out, e1 = decode(edges)
expect(out == audio, "long show: every sample across the hop plays exactly")
per = np.diff(e1)
extra = sorted(set(int(p) for p in per if p not in (CYCLES, CYCLES + 12)))
print("long show: periods %d x 128, %d x 140 (page change), hop %s" % (
    int((per == CYCLES).sum()), int((per == CYCLES + 12).sum()), extra))
expect(len(extra) == 1 and extra[0] < 2 * CYCLES, "long show: the hop adds less than a sample, once")
expect(m.switches == [0xC080, 0xC082], "long show: the card is switched in once, for the pages in it, then ROM comes back")
expect(not m.read_ram and not m.write_ram, "long show: ROM is back at the end")
make_wav(os.path.join(tmp, "s.wav"), 10.0)
try:
    show.build(os.path.join(tmp, "show.toml"), True); refused = False
except ValueError as e:
    refused = "$FF00" in str(e)
expect(refused, "show plan refuses sound past the card's last usable page")
make_wav(os.path.join(tmp, "s.wav"), 7.0)
try:
    show.build(os.path.join(tmp, "show.toml")); refused = False
except ValueError as e:
    refused = "--language-card" in str(e)
expect(refused, "without --language-card, 7 s of sound is refused with a hint")


# ---------------------------------------------------------------- 4. the video player
def burst(n):
    return bytes([0x7F, n, n ^ 0x55])


def play_video(stream, audio, **kw):
    m = Machine(**kw)
    for addr, piece in a2hdr.audio_pieces(0, audio):
        m.load_sound(addr, piece)
    st = {"q": list(stream)}
    edges = []

    def click(a):
        edges.append(m.mpu.processorCycles); return 0
    m.mem.subscribe_to_read([ACIA_S], lambda a: 0x10 | (0x08 if st["q"] else 0))
    m.mem.subscribe_to_read([ACIA_D], lambda a: st["q"].pop(0) if st["q"] else 0)
    m.mem.subscribe_to_read([0xC000], lambda a: 0)
    m.mem.subscribe_to_read([0xC010], lambda a: 0)
    m.mem.subscribe_to_read([0xC030], click)
    m.load(0x0E00, binary("video.bin"))
    m.run(0x0E00)
    return m, edges


FRAME = b"\xFE\x01\x2A"                                     # frame start, one byte at $2000
END = b"\x80\x80"
rng = np.random.default_rng(5)
long_audio = bytes(rng.integers(0, 256, a2hdr.AUDIO_PAGES_CARD * 256, dtype=np.uint8).tolist())
# 90 pages in main RAM, then a burst across the hop, then to the very end
m, edges = play_video(FRAME + burst(90) + FRAME + burst(10) + FRAME + burst(43) + FRAME + END, long_audio)
out, _ = decode(edges)
expect(out == long_audio, "video: bursts across the hop play every sample, up to the card's last page")
expect(m.switches == [0xC080, 0xC082, 0xC080, 0xC082], "video: the card is switched in only for the pages in it")
expect(not m.read_ram and not m.write_ram, "video: ROM is back at the end")
expect(m.mem[0x2000] == 0x2A, "video: frames still draw between bursts")
# a burst that stops exactly at $C000, so the next one starts by hopping
m, edges = play_video(FRAME + burst(96) + FRAME + burst(2) + FRAME + END, long_audio)
out, _ = decode(edges)
expect(out == long_audio[:98 * 256], "video: a burst stopping exactly at $C000 carries on at $D000")
expect(m.switches == [0xC080, 0xC082], "video: nothing switches until the sound reaches the card")
# past the end: refused, and the stream carries on
m, edges = play_video(FRAME + burst(96) + burst(47) + FRAME + burst(1) + b"\xFE\x01\x33" + END, long_audio)
out, _ = decode(edges)
expect(out == long_audio, "video: a burst past the card's last page is refused")
expect(m.mem[0x2000] == 0x33, "video: the stream recovers after a refused burst")
# staying in main RAM never touches the card
m, edges = play_video(FRAME + burst(5) + FRAME + END, long_audio[:5 * 256])
expect(m.switches == [], "video: sound in main RAM never touches the card's switches")

# ---------------------------------------------------------------- 5. preloaded fast blocks in the card
import videoconv                                            # noqa: E402


def card_put(m, addr, data):
    """Put bytes in the card's RAM (bank 2) at addr, as the receiver's upload would."""
    for i, b in enumerate(data):
        arr, j = m._where(addr + i)
        arr[j] = b


def play_pre(stream, pieces=(), main=()):
    m = Machine()
    for addr, blob in pieces:
        card_put(m, addr, blob)
    for addr, blob in main:
        m.load(addr, blob)
    st = {"q": list(stream), "t": 0}                       # a byte at most every 87 cycles, as at 115200 baud

    def take(a):
        st["t"] = m.mpu.processorCycles
        return st["q"].pop(0) if st["q"] else 0
    m.mem.subscribe_to_read([ACIA_S], lambda a: 0x10 | (0x08 if st["q"] and m.mpu.processorCycles - st["t"] >= 87 else 0))
    m.mem.subscribe_to_read([ACIA_D], take)
    for sw_ in (0xC000, 0xC010, 0xC030, 0xC050, 0xC051, 0xC052, 0xC054, 0xC055, 0xC057):
        m.mem.subscribe_to_read([sw_], lambda a: 0)
    m.load(0x0E00, binary("video.bin"))
    m.run(0x0E00)
    return m


tg = rng.integers(0, 0x7F, 0x2000, dtype=np.uint8)
runs = [(0, 40), (300, 330), (2000, 2100), (0x1F80, 0x2000)]
want = {0x2000 + a: int(tg[a]) for s_, e_ in runs for a in range(s_, e_)}
for at, rr in ((0xD000, runs), (0xDFF4, runs), (0xFE00, runs[:2])):     # bank 2, across into $E000, the last page
    want = {0x2000 + a: int(tg[a]) for s_, e_ in rr for a in range(s_, e_)}
    blob, ctl = videoconv.preload_block(rr, tg, 0, at)
    m = play_pre(b"\xFE" + a2hdr.preload_op(ctl) + END, pieces=[(at, blob)])
    expect(all(m.mem[a] == v for a, v in want.items()), "card preload at $%04X: draws its bytes from the card" % at)
    expect(m.switches == [0xC080, 0xC082] and not m.read_ram, "card preload at $%04X: the card is read only while it's drawn" % at)
want = {0x2000 + a: int(tg[a]) for s_, e_ in runs for a in range(s_, e_)}
blob, ctl = videoconv.preload_block(runs, tg, 0, 0x7000)
m = play_pre(b"\xFE" + a2hdr.preload_op(ctl) + END, main=[(0x7000, blob)])
expect(all(m.mem[a] == v for a, v in want.items()) and m.switches == [], "a preload in main RAM never touches the card's switches")
# a copy kept in the card: page 2 gets 40 bytes from the line, then a copy puts them on page 1
cpart, _ = videoconv.copy_entries([(100, 140)])
p2 = bytes(rng.integers(1, 0x7F, 40, dtype=np.uint8).tolist())
m2 = play_pre(b"\xFB" + bytes([0x80 | 100, 40]) + p2 + b"\xFE\x00" + a2hdr.preload_op(0xE000) + b"\x01\x11" + END,
              pieces=[(0xE000, cpart)])
expect(bytes(m2.mem[0x2000 + a] for a in range(100, 140)) == p2 and m2.mem[0x2000] == 0x11,
       "a copy kept in the card copies from the other page, and the line carries on where it was")
expect(m2.switches == [0xC080, 0xC082], "a copy kept in the card: ROM back afterwards")
# refused: in I/O, on the vectors' page, bytes outside the card, and a damaged block
FR = b"\xFE\x01\x33"
for name, op, pieces, sw in (
        ("an op pointing into $C000-$CFFF", a2hdr.preload_op(0xC800), [], []),
        ("an op pointing at the top page", a2hdr.preload_op(0xFF00), [], []),
        ("a card block whose bytes are in main RAM", a2hdr.preload_op(0xD000), [(0xD000, b"\x00\x70\x00\xF5\x00\xFD")], [0xC080, 0xC082]),
        ("a card block whose bytes are on the top page", a2hdr.preload_op(0xD000), [(0xD000, b"\x00\xFF\x00\xF5\x00\xFD")], [0xC080, 0xC082]),
        ("a card block whose entries run onto the top page", a2hdr.preload_op(0xFEF8), [(0xFEF8, b"\x00\xFE" + b"\x00\xFA" * 3)], [0xC080, 0xC082])):
    m = play_pre(b"\xFE" + op + FR + END, pieces=pieces)
    expect(m.mem[0x2000] == 0x33 and m.switches == sw and not m.read_ram,
           "card preload: %s is refused, ROM comes back, and the stream recovers" % name)
# a picture too busy for one frame even from memory, spread over three with a block from the card in each
busy = videoconv.blank().copy()
busy[0x0400:0x1C00] = rng.integers(0, 0x7F, 0x1800, dtype=np.uint8)
busy = videoconv.with_holes(busy)
span = videoconv.preload_span(videoconv.blank(), busy, [1094] * 3, 0xFB, a2hdr.block_bytes(115200), 11520, 0xD000, 12032,
                              other=videoconv.blank())
one = videoconv.preload_frame(videoconv.blank(), busy, 1094, 0xFB, a2hdr.block_bytes(115200), 11520, 0xD000, 12032,
                              other=videoconv.blank())
expect(one is None and span is not None and len(span[0]) == 3
       and sum(1 for f in span[0] for k, _v, _i in a2hdr.walk_ops(f) if k == "preload") == 3,
       "a picture too busy for one frame is spread over three, drawing from memory in each")
m = play_pre(b"".join(span[0]) + END, pieces=[(0xD000, span[1])])
expect(bytes(m.mem[0x4000 + i] for i in range(0x2000)) == bytes(busy) and m.switches == [0xC080, 0xC082] * 3,
       "the spread picture comes out exactly, the card read only around each frame's block")
# a whole clip: main RAM is nearly full, so the encoder carries on into the card
gif = os.path.join(tmp, "busy.gif")
from PIL import Image, ImageDraw                            # noqa: E402
frames_ = []
for k in range(12):                                         # a wipe: too busy for the line alone
    im = Image.new("RGB", (280, 192), "black")
    ImageDraw.Draw(im).rectangle([0, 0, int(279 * k / 11), 191], fill="white")
    frames_.append(im)
frames_[0].save(gif, save_all=True, append_images=frames_[1:], duration=100, loop=0)
a2v = os.path.join(tmp, "busy.a2v")
saved = sys.argv, a2hdr.PRELOAD_END, a2hdr.AUDIO_MAIN_END
sys.argv = ["videoconv.py", gif, "-o", a2v, "--fps", "10", "--flip", "--preload", "--preload-share", "first",
            "--language-card", "--budget", "700"]
a2hdr.PRELOAD_END = a2hdr.AUDIO_MAIN_END = 0x6100           # (main RAM as if it ended at $60FF: more goes in the card)
import contextlib, io                                       # noqa: E401,E402
with contextlib.redirect_stderr(io.StringIO()):
    videoconv.main()
raw = open(a2v, "rb").read()
_fps, vfr, _ = a2hdr.parse_stream(raw)
pre = a2hdr.parse_preload(raw)
pcs = a2hdr.preload_pieces(pre)
sys.argv, a2hdr.PRELOAD_END, a2hdr.AUDIO_MAIN_END = saved
ops = [v for f in vfr for k, v, _i in a2hdr.walk_ops(f) if k == "preload"]
in_card = [v for v in ops if v >= a2hdr.CARD_START]
print("clip into the card: %d preloaded blocks, %d of them in the card; pieces %s" % (
    len(ops), len(in_card), ", ".join("$%04X+%d" % (a, len(d)) for a, d in pcs)))
expect(in_card and [a for a, _ in pcs] == [0x6000, a2hdr.CARD_START],
       "the encoder fills main RAM, then carries the preload on into the card, as sound does")
def play_host(vfr, fps, pcs):
    """Play a stream on the host's schedule (stream.plan_stream: quiet while each
    block is drawn), a byte every 10/115200 s; a byte that arrives while the
    last is still unread is an overrun (lost). pcs: the preload's (address,
    bytes) pieces. Returns (machine, overruns, pages)."""
    m = Machine()
    mem = bytearray(0x10000)
    for addr, blob in pcs:
        mem[addr:addr + len(blob)] = blob
    for addr, blob in pcs:
        if addr >= a2hdr.CARD_START:
            card_put(m, addr, blob)
        else:
            m.load(addr, blob)
    sched, _total = host.plan_stream(vfr, fps, 115200, mem=mem)
    START = 400_000                                         # (the player clears both pages first)
    arr = [(b, START + int((t + (k + 1) * 10 / 115200) * a2hdr.CPU_HZ)) for t, data, _kind in sched for k, b in enumerate(data)]
    arr += [(b, arr[-1][1] + 100 * (j + 1)) for j, b in enumerate(END)]
    st = {"i": 0, "over": 0}

    def ready(a):
        now, i = m.mpu.processorCycles, st["i"]
        return 0x10 | (0x08 if i < len(arr) and arr[i][1] <= now else 0)

    def take(a):
        now, i = m.mpu.processorCycles, st["i"]
        while i + 1 < len(arr) and arr[i + 1][1] <= now:      # a newer byte overwrote this one
            st["over"] += 1
            i += 1
        st["i"] = i + 1
        return arr[i][0] if i < len(arr) else 0
    m.mem.subscribe_to_read([ACIA_S], ready)
    m.mem.subscribe_to_read([ACIA_D], take)
    for io_ in (0xC000, 0xC010, 0xC030, 0xC050, 0xC051, 0xC052, 0xC054, 0xC055, 0xC057):
        m.mem.subscribe_to_read([io_], lambda a: 0)
    m.load(0x0E00, binary("video.bin"))
    m.run(0x0E00, limit=400_000_000)
    pg = [bytes(m.mem[base + i] for i in range(0x2000)) for base in (0x2000, 0x4000)]     # (it shows page 1 again as it quits)
    return m, st["over"], pg


import stream as host                                       # noqa: E402
m, over, pg = play_host(vfr, _fps, pcs)
last = videoconv.with_holes(videoconv.pack_screen(videoconv.to_bits(
    np.asarray(__import__("imgconv").prepare(frames_[-1], False, False), dtype=np.float64), "ordered")))
st = {"over": over}
print("clip into the card, on the host's schedule: %d overruns" % st["over"])
expect(st["over"] == 0 and bytes(last) in pg,
       "a clip preloaded into main RAM and the card plays on the host's schedule with no overruns, to its last frame exactly")
expect(m.switches == [0xC080, 0xC082] * len(in_card) and not m.read_ram, "the card is switched in only around each block in it")

# ---------------------------------------------------------------- 6. stores: memory refilled during play (--refill)
data = bytes(rng.integers(0, 256, 100, dtype=np.uint8).tolist())
m = play_pre(b"\xFE" + a2hdr.store_ops(0x7000, data) + FR + END)
expect(bytes(m.mem[0x7000 + i] for i in range(100)) == data and m.switches == [] and m.mem[0x2000] == 0x33,
       "a store puts its bytes in main RAM, draws nothing, and the line carries on")
big = bytes(rng.integers(0, 256, 600, dtype=np.uint8).tolist())
m = play_pre(b"\xFE" + a2hdr.store_ops(0xD000, big) + FR + END)
got = bytes(m._where(0xD000 + i)[0][m._where(0xD000 + i)[1]] for i in range(600))
expect(got == big and m.switches == [0xC081, 0xC081, 0xC082] * 3 and not m.write_ram and not m.read_ram and m.mem[0x2000] == 0x33,
       "stores into the card write its RAM (ROM still read) in three ops, and turn writing off after each")
bad = bytearray(a2hdr.store_ops(0x7000, data))
bad[-1] ^= 1
m = play_pre(b"\xFE" + bytes(bad) + b"\x01\x44" + FR + END)
expect(m.mem[0x2000] == 0x33 and m.switches == [], "a store with a bad check byte discards the rest of the frame, then recovers at the next mark")
for name, at, n in (("ending past $BFFF", 0xBFF0, 32), ("into $C000-$CFFF", 0xC800, 32),
                    ("ending on the top page", 0xFEF0, 32), ("from below $6000", 0x5FF0, 32)):
    op = bytearray(a2hdr.store_ops(max(at, 0x6000), bytes(n)))
    if at < 0x6000:                 # (store_ops can't address it; the player sees h past $FFFF instead)
        op[4] = 0x7F
    else:
        off = (at - 0x6000) // 4
        op[3], op[4] = off & 0x7F, off >> 7
    m = play_pre(b"\xFE" + bytes(op) + FR + END)
    expect(m.mem[0x2000] == 0x33 and not m.write_ram and all(m.mem[a] == 0 for a in range(0xBFF0, 0xC000)),
           "a store %s is refused and the stream recovers" % name)
# a clip whose preload memory is far too small: blocks go into memory earlier frames are done with
clip = os.path.join(tmp, "patches")
os.makedirs(clip, exist_ok=True)
rs = np.random.default_rng(7)
cur = Image.new("RGB", (280, 192), "black")
for k in range(36):                                         # every third frame a patch of noise somewhere new; calm in between
    if k % 3 == 0:
        cur = cur.copy()
        x, y = int(rs.integers(0, 200)), int(rs.integers(0, 140))
        cur.paste(Image.fromarray((rs.random((48, 72)) > 0.5).astype(np.uint8) * 255).convert("RGB"), (x, y))
    cur.save(os.path.join(clip, "f%03d.png" % k))
last_im = cur
saved = sys.argv, a2hdr.PRELOAD_END, a2hdr.AUDIO_MAIN_END, a2hdr.CARD_END
a2hdr.PRELOAD_END = a2hdr.AUDIO_MAIN_END = 0x6200           # (512 bytes of main RAM and 512 of card)
a2hdr.CARD_END = 0xD200
shown_, raws = {}, {}
for refill in ("--no-refill", None, "--refill"):           # (None: the default, which is --refill)
    sys.argv = ["videoconv.py", clip, "-o", a2v, "--fps", "10", "--flip", "--preload", "--preload-share", "first",
                "--language-card", "--budget", "600"] + ([refill] if refill else [])
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        videoconv.main()
    shown_[refill != "--no-refill"] = int(err.getvalue().split(" frames shown")[0].split(";")[-1].split(" of ")[0])
    raws[refill] = open(a2v, "rb").read()
raw = raws["--refill"]
_fps, vfr, _ = a2hdr.parse_stream(raw)
pre = a2hdr.parse_preload(raw)
stores = [v for f in vfr for k, v, _i in a2hdr.walk_ops(f) if k == "store"]
draws = [v for f in vfr for k, v, _i in a2hdr.walk_ops(f) if k == "preload"]
m, over, pg = play_host(vfr, _fps, a2hdr.preload_pieces(pre))
sys.argv, a2hdr.PRELOAD_END, a2hdr.AUDIO_MAIN_END, a2hdr.CARD_END = saved
print("refill: %d of 36 frames shown (%d without), %d preloaded blocks drawn, %d stores (%d bytes, %d into the card), %d overruns" % (
    shown_[True], shown_[False], len(draws), len(stores), sum(len(d) for _a, d in stores),
    sum(1 for a_, _d in stores if a_ >= a2hdr.CARD_START), over))
last = videoconv.with_holes(videoconv.pack_screen(videoconv.to_bits(
    np.asarray(__import__("imgconv").prepare(last_im, False, False), dtype=np.float64), "ordered")))
expect(shown_[True] > shown_[False], "--refill shows more of a clip than the memory alone allows")
expect(raws[None] == raws["--refill"] != raws["--no-refill"], "--preload refills by default, and --no-refill turns it off")
expect(stores and any(a_ >= a2hdr.CARD_START for a_, _d in stores) and any(a_ < a2hdr.CARD_START for a_, _d in stores),
       "with too little memory, --refill sends later blocks into main RAM and the card during play")
expect(over == 0 and bytes(last) in pg, "a refilled clip plays on the host's schedule with no overruns, to its last frame exactly")
expect(not m.write_ram and not m.read_ram, "after a refilled clip, the card is neither read nor written")

# ---------------------------------------------------------------- the host's bookkeeping
pieces = a2hdr.audio_pieces(0, bytes(a2hdr.AUDIO_PAGES_CARD * 256))
expect([(a, len(d)) for a, d in pieces] == [(0x6000, 0x6000), (0xD000, 0x2F00)], "sound splits into main RAM and the card")
expect(a2hdr.audio_addr(0x6000) == 0xD000 and a2hdr.audio_addr(0x5F00) == 0xBF00, "sound addresses hop over $C000-$CFFF")
expect(a2hdr.audio_pieces(0x6000, b"x" * 256) == [(0xD000, b"x" * 256)], "sound starting exactly at the hop goes in the card")
finish("test_language_card")
