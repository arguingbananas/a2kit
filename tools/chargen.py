#!/usr/bin/env python3
"""chargen: read the Apple II's character ROM off its screen, for MAME.

  a2kit chargen capture --port /dev/ttyUSB0 [--device /dev/video0] [--dir DIR]
  a2kit chargen decode [--dir DIR] [-o FILE] [--check]

The 6502 can't read the character generator (341-0036 on a II+), but every dot of
text comes from it. `capture` puts every screen code on the screen and records it
with a USB composite video grabber (Linux: needs ffmpeg and v4l2-ctl); `decode`
reads the dots back and lays the glyphs out as MAME's 341-0036.chr, checking the
result against the real ROM's checksum. A match means the file is byte for byte
the chip in your machine. The ROM is Apple's: keep it for your own MAME, and
don't share it.

Why it works the way it does:
- A II+ sends no colour burst in text mode, and grabbers tend to show nothing
  without one, so the characters are shown in mixed mode: hi-res above, four
  text lines below, 20 characters to a line with a space between each.
- A grabber takes about 2 samples per dot, so single dots come through partly as
  colour. The calibration screens are hi-res screens of random 7-dot patterns
  laid out like the text, through the same video path; a least-squares fit gives
  the strip each of the 128 patterns makes, dark or lit around it, and how much
  the scanlines above and below blend in. Columns of rows are then read together.
- The ROM holds its 64 glyphs four times; the four copies are settled by a vote,
  and bit 7 is tried each way it could plausibly be filled.

capture writes, in DIR (default ~/a2kit-chargen):
  geometry.npz       white lines and bars, to map dots onto the capture
  cal-dark-0..3.npz  random patterns with dark columns between (like normal text)
  cal-lit-0..3.npz   the same with lit columns between (like inverse text)
  text-0..3.npz      all 256 screen codes; flashing ones in both phases
Each screen is uploaded with screenhold.bin ($1200), which shows it until a byte
arrives on the serial line, so nobody needs to press keys. It takes about a minute.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import zlib

import numpy as np

import a2hdr
import rxid
import send

HERE = os.path.dirname(os.path.abspath(__file__))
HOLD_ADDR = 0x1200
TEXT_ROWS = (0x0650, 0x06D0, 0x0750, 0x07D0)          # text lines 20-23
SPACE = {"normal": 0xA0, "inverse": 0x20, "flash": 0x60}
SIZE = (720, 480)
CONTROLS = "brightness=-128,contrast=150"             # MacroSilicon MS210x: black near 17, nothing clipped
REAL_CRC = 0x64F415C6                                 # MAME's 341-0036.chr
COLS = range(0, 40, 2)
MARGIN = 2                                            # dots either side of a cell in a strip
BITS = np.array([[(p >> d) & 1 for d in range(7)] for p in range(128)], float)
GEOMETRY_LINES = (0, 48, 96, 144, 191)
GEOMETRY_BARS = (1, 9, 19, 29, 38)


# ---------------------------------------------------------------- the screens
def hgr(rows):
    """8192 bytes of hi-res page 1 from a 192x40 array of byte values."""
    out = bytearray(0x2000)
    for y in range(192):
        a = (y % 8) * 0x400 + ((y // 8) % 8) * 0x80 + (y // 64) * 0x28
        out[a:a + 40] = bytes(int(v) for v in rows[y])
    return bytes(out)


def geometry_rows():
    rows = np.zeros((192, 40), int)
    rows[list(GEOMETRY_LINES), :] = 0x7F
    for c in GEOMETRY_BARS:
        rows[8:40, c] = 0x7F
        rows[152:184, c] = 0x7F
    return rows


def calibration_rows(lit, k):
    rng = np.random.default_rng(100 * lit + k)
    rows = np.full((192, 40), 0x7F if lit else 0x00, int)
    rows[:, 0::2] = rng.integers(0, 128, (192, 20))
    return rows


def text_screens():
    """4 screens of 4 text lines, each (kind, [20 screen codes, None for padding])."""
    lines = []
    for kind, codes in (("normal", range(0x80, 0x100)), ("inverse", range(0x00, 0x40)), ("flash", range(0x40, 0x80))):
        codes = list(codes)
        for i in range(0, len(codes), 20):
            lines.append((kind, codes[i:i + 20] + [None] * (20 - len(codes[i:i + 20]))))
    lines += [("normal", [None] * 20)] * (16 - len(lines))
    return [lines[i:i + 4] for i in range(0, 16, 4)]


def text_line(kind, codes):
    """The 40 bytes of a text line: each code in an even column, a space after it."""
    out = []
    for c in codes:
        out += [SPACE[kind] if c is None else c, SPACE[kind]]
    return bytes(out)


def screens():
    """[(name, [(addr, bytes)], mixed)] in capture order."""
    out = [("geometry", [(0x2000, hgr(geometry_rows()))], False)]
    for lit in (False, True):
        for k in range(4):
            out.append(("cal-%s-%d" % ("lit" if lit else "dark", k), [(0x2000, hgr(calibration_rows(lit, k)))], False))
    blank = hgr(np.zeros((192, 40), int))
    for k, screen in enumerate(text_screens()):
        out.append(("text-%d" % k, [(0x2000, blank)] + [(TEXT_ROWS[i], text_line(*line)) for i, line in enumerate(screen)], True))
    return out


# ---------------------------------------------------------------- capture
def grab(device, n, controls, skip=10):
    """n frames from the grabber as float32 (n, height, width, 3): Y, U, V."""
    if controls:
        subprocess.run(["v4l2-ctl", "-d", device, "-c", controls], check=True)
    w, h = SIZE
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2", "-input_format", "yuyv422",
                          "-video_size", "%dx%d" % (w, h), "-i", device, "-frames:v", str(n + skip),
                          "-f", "rawvideo", "-pix_fmt", "yuyv422", "-"], check=True, capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8)
    if len(f) < (skip + 1) * w * h * 2:
        raise IOError("%s gave no frames (is it the grabber, and is a picture coming in?)" % device)
    f = f[:len(f) // (w * h * 2) * w * h * 2].reshape(-1, h, w * 2)[skip:]
    y = f[:, :, 0::2].astype(np.float32)
    u = np.repeat(f[:, :, 1::4], 2, axis=2).astype(np.float32)
    v = np.repeat(f[:, :, 3::4], 2, axis=2).astype(np.float32)
    return np.stack([y, u, v], axis=-1)


def phases(frames, rows):
    """The frames' mean, or for flashing characters, the means of the two phases
    (frames caught mid-change are left out). rows: the capture lines of the text."""
    level = frames[:, rows][..., 0].mean(axis=(1, 2))
    lo, hi = level.min(), level.max()
    if hi - lo < 2.0:
        return [frames.mean(0)]
    t = (lo + hi) / 2
    for _ in range(20):
        t = (level[level <= t].mean() + level[level > t].mean()) / 2
    a, b = level[level <= t].mean(), level[level > t].mean()
    return [frames[level <= a + (b - a) / 4].mean(0), frames[level >= b - (b - a) / 4].mean(0)]


def capture(port_name, baud, device, out, controls, frames=30, log=print):
    for exe in ("ffmpeg",) + (("v4l2-ctl",) if controls else ()):
        if not shutil.which(exe):
            raise IOError("%s not found (Linux: sudo apt install %s)" % (exe, "v4l-utils" if exe == "v4l2-ctl" else exe))
    os.makedirs(out, exist_ok=True)
    port = send.open_port(port_name, baud)
    rxid.check(port, baud)
    hold = bytearray(open(os.path.join(HERE, "..", "screenhold.bin"), "rb").read())
    geo = None
    for name, blobs, mixed in screens():
        s = send.Sender(port)
        for addr, data in blobs:
            s.send_blob(data, addr)
        hold[3] = 1 if mixed else 0
        s.send_blob(bytes(hold), HOLD_ADDR)
        s.finish(HOLD_ADDR)
        time.sleep(0.4)
        if mixed:
            got = grab(device, 3 * frames, controls)
            rows = np.unique(np.clip(np.round(geo[1][0] + geo[1][1] * np.arange(160, 192)).astype(int), 0, got.shape[1] - 1))
            groups = phases(got, rows)
        else:
            groups = [grab(device, frames, controls).mean(0)]
        np.savez_compressed(os.path.join(out, name + ".npz"), *groups)
        if name == "geometry":
            geo = geometry(groups[0])
            log("dots: x = %.1f + %.3f per dot; scanlines: y = %.1f + %.3f per line" % (*geo[0], *geo[1]))
        log("%-11s %s" % (name, "two phases of the flashing characters" if len(groups) == 2 else "captured"))
        port.write(b"\x00")                 # screenhold.bin takes it and returns
        port.flush()
        time.sleep(0.3)
        for _ in range(20):
            if s.receiver_listening():
                break
            time.sleep(0.2)
        else:
            raise IOError("the receiver didn't start listening again after the %s screen" % name)


# ---------------------------------------------------------------- decode
def load(d, name):
    path = os.path.join(d, name + ".npz")
    if not os.path.exists(path):
        raise IOError("%s missing: run a2kit chargen capture first" % path)
    z = np.load(path)
    return [z["arr_%d" % i] for i in range(len(z.files))]


def _centres(profile, count):
    """Weighted centres of the first `count` bright runs in a 1-D profile."""
    base = np.median(profile)
    t = base + (profile.max() - base) / 2
    idx = np.where(profile > t)[0]
    runs = np.split(idx, np.where(np.diff(idx) > 3)[0] + 1)
    if len(runs) < count:
        raise ValueError("the geometry screen doesn't show its %d lines and bars (found %d)" % (count, len(runs)))
    return [(r * (profile[r] - base)).sum() / (profile[r] - base).sum() + 0.5 for r in runs[:count]]


def geometry(img):
    """((x0, x per dot), (y0, y per scanline)) in capture pixels, centres at +0.5."""
    Y = img[..., 0]
    w = Y.shape[1]
    ys = _centres(Y[:, w // 5:4 * w // 5].mean(1), len(GEOMETRY_LINES))
    by, ay = np.polyfit([v + 0.5 for v in GEOMETRY_LINES], ys, 1)
    band = Y[int(ay + by * 16):int(ay + by * 32)].mean(0)
    xs = _centres(band, len(GEOMETRY_BARS))
    bx, ax = np.polyfit([7 * c + 3.5 for c in GEOMETRY_BARS], xs, 1)
    return (ax - 0.5, bx), (ay - 0.5, by)


def sampler(geo):
    """strip(img, scanline, column): the cell's row and MARGIN dots either side,
    sampled twice per dot through the middle of the scanline, in Y, U and V."""
    (ax, bx), (ay, by) = geo
    offs = (np.arange(-MARGIN, 7 + MARGIN)[:, None] + np.array([0.25, 0.75])).ravel()

    def strip(img, y, col):
        xs = ax + bx * (7 * col + offs)
        yy = ay + by * (y + np.array([0.5]))       # the scanline's middle: both capture lines around it are this scanline's
        x0 = np.floor(xs).astype(int)
        y0 = np.floor(yy).astype(int)
        fx = (xs - x0)[None, :, None]
        fy = (yy - y0)[:, None, None]
        a = img[y0][:, x0] * (1 - fx) + img[y0][:, x0 + 1] * fx
        b = img[y0 + 1][:, x0] * (1 - fx) + img[y0 + 1][:, x0 + 1] * fx
        return (a * (1 - fy) + b * fy).ravel()
    return strip


def design(pats):
    """Features of a column of patterns: which pattern, and the dots above and below."""
    n = len(pats)
    F = np.zeros((n, 142))
    F[np.arange(n), pats] = 1
    F[1:, 128:135] = BITS[pats[:-1]]
    F[:-1, 135:142] = BITS[pats[1:]]
    return F


def fit(screens):
    """[(strips by column, 192x40 patterns)] -> (T: each pattern's strip, Gup, Gdn: the blend
    from the rows above and below)."""
    F = np.concatenate([design(rows[:, c]) for strips, rows in screens for c in COLS])
    X = np.concatenate([strips[c] for strips, rows in screens for c in COLS])
    B = np.linalg.lstsq(F, X, rcond=None)[0]
    return B[:128], B[128:135], B[135:142]


def read_column(X, models):
    """The patterns of a column of strips (models[i] is row i's): each row's nearest
    pattern first, then one row at a time changed to whatever lowers the error of it
    and its neighbours, until nothing changes."""
    n = len(X)
    pats = np.array([((X[i][None] - models[i][0]) ** 2).sum(1).argmin() for i in range(n)])
    for _ in range(10):
        changed = False
        for i in range(n):
            T, Gu, Gd = models[i]
            pred = T + (BITS[pats[i - 1]] @ Gu if i > 0 else 0) + (BITS[pats[i + 1]] @ Gd if i < n - 1 else 0)
            total = ((X[i][None] - pred) ** 2).sum(1)
            if i > 0:
                Tp, Gup, Gdp = models[i - 1]
                base = Tp[pats[i - 1]] + (BITS[pats[i - 2]] @ Gup if i > 1 else 0)
                total += ((X[i - 1][None] - (base + BITS @ Gdp)) ** 2).sum(1)
            if i < n - 1:
                Tn, Gun, Gdn = models[i + 1]
                base = Tn[pats[i + 1]] + (BITS[pats[i + 2]] @ Gdn if i < n - 2 else 0)
                total += ((X[i + 1][None] - (base + BITS @ Gun)) ** 2).sum(1)
            best = int(total.argmin())
            if best != pats[i]:
                pats[i], changed = best, True
        if not changed:
            break
    return pats


def rom_bytes(glyph, bit7):
    """2048 bytes in MAME's layout: code c's 8 rows at c*8, bit 6 the leftmost dot."""
    rom = bytearray(2048)
    for code in range(256):
        for row in range(8):
            v = sum(1 << (6 - d) for d in range(7) if glyph[code][row, d])
            rom[code * 8 + row] = v | (0x80 if bit7(code) else 0)
    return bytes(rom)


BIT7 = {
    "set in $40-$7F and $C0-$FF": lambda code: bool(code & 0x40),     # the real 341-0036
    "clear": lambda code: False,
    "set": lambda code: True,
    "set in $80-$FF": lambda code: code >= 0x80,
    "set in $00-$7F": lambda code: code < 0x80,
}


def decode(d, want_crc=REAL_CRC, check=False, log=print):
    """(ROM bytes, matched?) from the captures in d."""
    geo = geometry(load(d, "geometry")[0])
    log("dots: x = %.1f + %.3f per dot; scanlines: y = %.1f + %.3f per line" % (*geo[0], *geo[1]))
    strip = sampler(geo)
    cal = {}
    for lit in (False, True):
        for k in range(4):
            img = load(d, "cal-%s-%d" % ("lit" if lit else "dark", k))[0]
            cal[(lit, k)] = ({c: np.array([strip(img, y, c) for y in range(192)]) for c in COLS}, calibration_rows(lit, k))
    if check:
        for lit in (False, True):
            for k in range(4):
                model = fit([cal[(lit, j)] for j in range(4) if j != k])
                strips, rows = cal[(lit, k)]
                right = sum((read_column(strips[c][160:], [model] * 32) == rows[160:, c]).sum() for c in COLS)
                log("calibration %s-%d, read by the other three: %d of 640 rows right" % ("lit" if lit else "dark", k, right))
    model = {lit: fit([cal[(lit, j)] for j in range(4)]) for lit in (False, True)}

    def level(strips):
        """Mean Y of some strips."""
        return np.asarray(strips).reshape(-1, 3)[:, 0].mean()

    dark = level(np.concatenate([cal[(False, j)][0][c][cal[(False, j)][1][:, c] == 0] for j in range(4) for c in COLS]))
    lit_ = level(np.concatenate([cal[(True, j)][0][c][cal[(True, j)][1][:, c] == 127] for j in range(4) for c in COLS]))
    shown = {}
    for k, screen in enumerate(text_screens()):
        for g, img in enumerate(load(d, "text-%d" % k)):
            # the spaces between the characters say whether a line is dark or lit around
            # them (a flashing line is either, by phase)
            lit = [level([strip(img, 160 + 8 * i + r, c) for r in range(1, 7) for c in range(1, 40, 2)]) > (dark + lit_) / 2
                   for i in range(4)]
            models = [model[lit[y // 8]] for y in range(32)]
            for j in range(20):
                pats = read_column(np.array([strip(img, 160 + y, 2 * j) for y in range(32)]), models)
                for i, (kind, codes) in enumerate(screen):
                    if codes[j] is None or (kind != "flash" and g > 0):
                        continue
                    bits = BITS[pats[8 * i:8 * i + 8]].astype(bool)
                    shown.setdefault(codes[j], []).append(~bits if lit[i] else bits)
    glyph = {code: r[0] for code, r in shown.items()}
    for code, r in sorted(shown.items()):
        if any(not np.array_equal(x, r[0]) for x in r[1:]):
            log("$%02X: its two flashing phases disagree" % code)
    for name, base in (("$80-$BF", 0x80), ("$00-$3F (inverse)", 0x00), ("$40-$7F (flashing)", 0x40)):
        odd = ["$%02X" % (base | g) for g in range(64) if not np.array_equal(glyph[base | g], glyph[0xC0 | g])]
        log("%s: %d of 64 glyphs match $C0-$FF%s" % (name, 64 - len(odd), " (not %s)" % ", ".join(odd) if odd else ""))
    for g in range(64):         # one glyph, four copies: settle each dot by majority, the normal two breaking ties
        votes = sum(glyph[b | g].astype(int) for b in (0x00, 0x40, 0x80, 0xC0))
        final = (votes >= 3) | ((votes == 2) & glyph[0x80 | g] & glyph[0xC0 | g])
        for b in (0x00, 0x40, 0x80, 0xC0):
            glyph[b | g] = final
    for name, f in BIT7.items():
        rom = rom_bytes(glyph, f)
        crc = zlib.crc32(rom)
        log("bit 7 %-27s crc %08x%s" % (name + ":", crc, "   MATCH" if crc == want_crc else ""))
        if crc == want_crc:
            return rom, True, glyph
    return rom_bytes(glyph, BIT7["clear"]), False, glyph


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["capture", "decode"])
    ap.add_argument("--dir", default=os.path.expanduser("~/a2kit-chargen"), help="where the captures go (default ~/a2kit-chargen)")
    ap.add_argument("--port", help="capture: the Apple II's serial port")
    a2hdr.add_baud_arg(ap)
    ap.add_argument("--device", default="/dev/video0", help="capture: the video grabber (default /dev/video0)")
    ap.add_argument("--controls", default=CONTROLS, help="capture: v4l2-ctl settings for the grabber, "
                    "or \"\" to leave it as it is (default %s)" % CONTROLS)
    ap.add_argument("-o", "--output", help="decode: the ROM file (default DIR/341-0036.chr)")
    ap.add_argument("--want-crc", default="%08x" % REAL_CRC, help="decode: the checksum to match (default MAME's 341-0036)")
    ap.add_argument("--check", action="store_true", help="decode: also report how well the calibration reads itself")
    a = ap.parse_args()
    if a.command == "capture":
        if not a.port:
            ap.error("capture needs --port")
        capture(a.port, a.baud, a.device, a.dir, a.controls)
        print("captured in %s; next: a2kit chargen decode --dir %s" % (a.dir, a.dir))
        return
    rom, ok, glyph = decode(a.dir, int(a.want_crc, 16), a.check)
    for r0 in range(0xC0, 0x100, 16):
        for row in range(8):
            print("  " + " ".join("".join("#" if b else "." for b in glyph[r0 + g][row]) for g in range(16)))
        print()
    out = a.output or os.path.join(a.dir, "341-0036.chr")
    with open(out, "wb") as f:
        f.write(rom)
    if ok:
        print("wrote %s: it matches the checksum, so it is byte for byte the ROM in your Apple II.\n"
              "For MAME, copy it into your ROM folder (apple2p/341-0036.chr). It is Apple's: don't share it." % out)
    else:
        print("wrote %s, but no layout matched the checksum: compare the glyphs above with the screen,\n"
              "or capture again (see the Manual)." % out)
        sys.exit(1)


if __name__ == "__main__":
    main()
