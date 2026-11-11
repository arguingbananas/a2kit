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
"""
import os, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
import numpy as np
from py65.devices.mpu6502 import MPU as MPU6502
from py65.devices.mpu65c02 import MPU as MPU65C02
from py65.memory import ObservableMemory
import a2hdr, lcprobe, send, show
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
tmp = tempfile.mkdtemp()


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

# ---------------------------------------------------------------- the host's bookkeeping
pieces = a2hdr.audio_pieces(0, bytes(a2hdr.AUDIO_PAGES_CARD * 256))
expect([(a, len(d)) for a, d in pieces] == [(0x6000, 0x6000), (0xD000, 0x2F00)], "sound splits into main RAM and the card")
expect(a2hdr.audio_addr(0x6000) == 0xD000 and a2hdr.audio_addr(0x5F00) == 0xBF00, "sound addresses hop over $C000-$CFFF")
expect(a2hdr.audio_pieces(0x6000, b"x" * 256) == [(0xD000, b"x" * 256)], "sound starting exactly at the hop goes in the card")
finish("test_language_card")
