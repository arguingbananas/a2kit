"""a2kit chargen without hardware: screenhold.bin on py65, the screens' layout, and
capture -> decode on a simulated grabber showing a made-up character ROM.

The simulated grabber is rough on purpose, like the real one: about 2 samples per
dot, a blurred brightness signal, single dots coming out as colour (the colour
carrier is half the dot rate), neighbouring scanlines blended together, and noise.
The made-up glyphs are random, so nothing here is Apple's.
"""
import os, sys, tempfile, zlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory
import chargen
from testlib import expect, finish

# 1. screenhold.bin: shows the screen, waits for a byte on the serial card (or a key), returns
hold = open(os.path.join(HERE, "..", "screenhold.bin"), "rb").read()


def run_hold(mode, by_key):
    """Run screenhold.bin; a byte (or a key) turns up after a while. Returns (mpu, I/O reads)."""
    mem = ObservableMemory(); mpu = MPU(memory=mem)
    seen, polls = [], [0]
    for a in range(0xC050, 0xC058):
        mem.subscribe_to_read([a], lambda addr: (seen.append(addr), 0)[1])
    mem.subscribe_to_read([0xC0A9], lambda addr: (polls.append(0), 0x08 if len(polls) > 50 and not by_key else 0)[1])
    mem.subscribe_to_read([0xC0A8], lambda addr: (seen.append(addr), 0x00)[1])
    mem.subscribe_to_read([0xC000], lambda addr: 0xC1 if by_key and len(polls) > 50 else 0x41)
    mem.subscribe_to_read([0xC010], lambda addr: (seen.append(addr), 0)[1])
    for i, b in enumerate(hold):
        mem[0x1200 + i] = b
    mem[0x1203] = mode
    for i, b in enumerate([0x20, 0x00, 0x12, 0x00]):        # JSR $1200, then BRK
        mem[0x0280 + i] = b
    mpu.pc, mpu.sp = 0x0280, 0xFF
    mpu.p |= mpu.CARRY
    for _ in range(100000):
        if mpu.pc == 0x0283:
            break
        mpu.step()
    return mpu, seen


for mode, by_key in ((0, False), (1, False), (1, True)):
    mpu, seen = run_hold(mode, by_key)
    how = "a key" if by_key else "a serial byte"
    expect(mpu.pc == 0x0283 and not (mpu.p & mpu.CARRY), "screenhold, mode %d: returns with carry clear after %s" % (mode, how))
    want = {0xC050, 0xC057, 0xC054, 0xC053 if mode else 0xC052}
    expect(want <= set(seen) and (0xC051 not in seen), "screenhold, mode %d: graphics, hi-res, page 1, %s" % (mode, "mixed" if mode else "full screen"))
    expect((0xC010 in seen) if by_key else (0xC0A8 in seen), "screenhold: takes the %s, so nothing else sees it" % ("key" if by_key else "byte"))

# 2. the screens: every screen code exactly once, a space after each
codes = [c for screen in chargen.text_screens() for _, line in screen for c in line if c is not None]
expect(sorted(codes) == list(range(256)), "the text screens show all 256 screen codes once each")
names = [n for n, _, _ in chargen.screens()]
expect(names[0] == "geometry" and len(names) == 13 and names[-1] == "text-3", "13 screens: geometry, 8 calibration, 4 text")
expect(all(addr in (0x2000, *chargen.TEXT_ROWS) for _, blobs, _ in chargen.screens() for addr, _ in blobs),
       "screens only write hi-res page 1 and the four text lines")

# frames of flashing characters split by phase; one caught mid-change is left out
frames = np.zeros((9, 40, 50, 3), np.float32)
frames[:, 10:20, :, 0] = np.array([30, 30, 60, 60, 45, 30, 60, 60, 30], np.float32)[:, None, None]
groups = chargen.phases(frames, np.arange(10, 20))
expect(len(groups) == 2 and groups[0][15, 5, 0] == 30 and groups[1][15, 5, 0] == 60,
       "flashing frames are split into their two phases, without the one caught mid-change")
expect(len(chargen.phases(np.full((5, 40, 50, 3), 30, np.float32), np.arange(10, 20))) == 1, "steady frames make one group")

# 3. a simulated grabber
rng = np.random.default_rng(5)
font = rng.integers(0, 128, (64, 8))            # 64 made-up glyphs, 7 dots a row
font[0x20] = 0                                  # the space is blank, as on a real Apple II
rom_bits = {c: np.array([[(font[c & 0x3F][r] >> (6 - d)) & 1 for d in range(7)] for r in range(8)], bool) for c in range(256)}
want_rom = chargen.rom_bytes(rom_bits, chargen.BIT7["set in $40-$7F and $C0-$FF"])
GX, GPX, GY, GPY = 88.0, 1.99, 31.0, 2.08      # not the real grabber's numbers, so nothing is hard-wired


def dots_hgr(rows):
    return np.array([[(rows[y][x // 7] >> (x % 7)) & 1 for x in range(280)] for y in range(192)], float)


def dots_text(screen, phase):
    d = np.zeros((192, 280))
    for i, (kind, line) in enumerate(screen):
        for col, code in enumerate(chargen.text_line(kind, line)):
            g = rom_bits[code].astype(float)
            if code < 0x40 or (0x40 <= code < 0x80 and phase == 1):
                g = 1 - g
            d[160 + 8 * i:168 + 8 * i, 7 * col:7 * col + 7] = g
    return d


def simulate(dots, noise=1.0):
    """What the grabber records for a screen of dots: (480, 720, 3) Y, U, V."""
    x = (np.arange(720) + 0.5 - GX) / GPX                 # each capture column, in dots
    t = np.arange(280) + 0.5
    luma = np.exp(-0.5 * ((x[:, None] - t[None]) / 0.45) ** 2)
    chroma = np.exp(-0.5 * ((x[:, None] - t[None]) / 0.9) ** 2)
    carrier = np.exp(1j * np.pi * t)[None]                  # half the dot rate
    Wy = luma / 1.13
    Wc = chroma * carrier / 2.26
    lines = np.vstack([np.zeros((1, 280)), dots, np.zeros((1, 280))])     # a dark line above and below
    Y, C = lines @ Wy.T, lines @ Wc.T
    u = (np.arange(480) + 0.5 - GY) / GPY                   # each capture line, in scanlines
    k = np.clip(np.floor(u).astype(int) + 1, 0, 193)
    blend = np.zeros((480, 194))
    for r in range(480):
        blend[r, k[r]] += 0.7
        blend[r, max(k[r] - 1, 0)] += 0.15
        blend[r, min(k[r] + 1, 193)] += 0.15
    blend[(u < -1) | (u > 193)] = 0
    Y, C = blend @ Y, blend @ C
    img = np.stack([17 + 120 * Y, 128 + 40 * C.real, 128 + 40 * C.imag], -1)
    return (img + rng.normal(0, noise, img.shape)).astype(np.float32)


tmp = tempfile.mkdtemp(prefix="a2kit-chargen-")
for name, _, _ in chargen.screens():
    if name == "geometry":
        imgs = [simulate(dots_hgr(chargen.geometry_rows()))]
    elif name.startswith("cal"):
        lit, k = name.split("-")[1] == "lit", int(name[-1])
        imgs = [simulate(dots_hgr(chargen.calibration_rows(lit, k)))]
    else:
        screen = chargen.text_screens()[int(name[-1])]
        flashing = any(kind == "flash" for kind, _ in screen)
        imgs = [simulate(dots_text(screen, p)) for p in ((0, 1) if flashing else (0,))]
    np.savez_compressed(os.path.join(tmp, name + ".npz"), *imgs)
geo = chargen.geometry(np.load(os.path.join(tmp, "geometry.npz"))["arr_0"])
expect(abs(geo[0][1] - GPX) < 0.01 and abs(geo[1][1] - GPY) < 0.01 and abs(geo[0][0] - GX) < 0.5 and abs(geo[1][0] - GY) < 0.5,
       "geometry found from the white lines and bars (%.2f + %.3f/dot, %.2f + %.3f/line)" % (*geo[0], *geo[1]))
notes = []
rom, ok, _ = chargen.decode(tmp, zlib.crc32(want_rom), check=True, log=notes.append)
for n in notes:
    print("  " + n)
expect(ok and rom == want_rom, "the made-up ROM comes back byte for byte, and its checksum is recognised")
expect(any("calibration dark-0" in n and "640 of 640" in n for n in notes), "the calibration reads itself back (--check)")
rom2, ok2, _ = chargen.decode(tmp, 0x12345678, log=lambda s: None)
expect(not ok2 and len(rom2) == 2048, "a checksum that nothing matches is reported as no match")

finish("test_chargen")
