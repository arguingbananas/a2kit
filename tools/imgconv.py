#!/usr/bin/env python3
"""imgconv: convert an image to an Apple II HGR screen (280x192, black and white or colour).

Output is the 8192-byte screen in memory order, wrapped in an a2kit block
header, so it can be copied straight to $2000.

Usage:
  imgconv.py logo.png -o logo.a2b
  imgconv.py photo.jpg -o photo.a2b --dither atkinson --crop --preview p.png
  imgconv.py photo.jpg -o photo.a2b --colour --preview p.png

--colour uses the six hi-res colours a colour monitor or TV shows: black,
white, violet, green, blue and orange. Each byte of the screen holds 7 dots and
a palette bit. A dot on its own shows a colour, set by its column and its
byte's palette bit: violet or green (bit off), blue or orange (bit on); two
dots side by side show white, and a gap between two dots of the same colour
fills with it. So colour comes at half the width, 140 dots across, and only
two colours besides black and white in each group of 7 dots. The converter
picks each byte's dots and palette bit together, closest to the picture, and
dithers the difference into the dots that follow. A black and white monitor
shows the same screen as dot patterns.

--preview writes the screen as the Apple II shows it: 280x192 black and white,
or with --colour 560x384 in colour (each dot two half-dots wide, as on the
screen, where palette-bit-on bytes sit half a dot to the right).
"""
import argparse
import sys

import numpy as np
from PIL import Image, ImageOps

import a2hdr

W, H = 280, 192


def hgr_offset(y):
    """Byte offset from $2000 of the start of scanline y (0-191)."""
    return (y & 7) * 0x400 + ((y >> 3) & 7) * 0x80 + (y >> 6) * 0x28


def prepare(img, crop, invert, mode="L"):
    """Scale to 280x192, as grey levels ("L") or for --colour as "RGB"."""
    img = img.convert(mode)
    if crop:
        img = ImageOps.fit(img, (W, H), Image.LANCZOS)
    else:
        img = ImageOps.pad(img, (W, H), Image.LANCZOS, color=0)
    if invert:
        img = ImageOps.invert(img)
    return img


def threshold(img):
    px = img.load()
    return [[1 if px[x, y] >= 128 else 0 for x in range(W)] for y in range(H)]


def atkinson(img):
    """Atkinson dither: diffuses 6/8 of the error, keeps highlights clean."""
    px = img.load()
    buf = [[float(px[x, y]) for x in range(W)] for y in range(H)]
    bits = [[0] * W for _ in range(H)]
    spread = ((1, 0), (2, 0), (-1, 1), (0, 1), (1, 1), (0, 2))
    for y in range(H):
        for x in range(W):
            old = buf[y][x]
            new = 255.0 if old >= 128 else 0.0
            bits[y][x] = 1 if new else 0
            err = (old - new) / 8.0
            for dx, dy in spread:
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and ny < H:
                    buf[ny][nx] += err
    return bits


def to_hgr(bits):
    """Pack a 280x192 bit grid into HGR memory layout.

    Each byte holds 7 pixels, bit 0 = leftmost. Bit 7 (palette) stays 0.
    """
    out = bytearray(0x2000)
    for y in range(H):
        base = hgr_offset(y)
        row = bits[y]
        for col in range(40):
            b = 0
            for k in range(7):
                if row[col * 7 + k]:
                    b |= 1 << k
            out[base + col] = b
    return bytes(out)


# How MAME 0.264 colours an Apple II+ hi-res screen, measured from its
# screenshots (every pattern a screen can make, checked on random screens with
# no exceptions). A scanline is 560 half-dots, two per dot. Each half-dot shows
# one of the 16 low-res colours, set by its position along the line (mod 4, one
# string each) and by the 7 half-dots centred on it (the index, bit 6 = the
# leftmost). Each hex digit is a low-res colour; "-" is a pattern a hi-res
# screen can't make. A real monitor blends more softly, and its colours depend
# on its tint and colour controls.
HALF_DOT_COLOUR = (
    "00-02-00----3-7f8--c----99-9b-ff----------------08-c----dd--f-ff00-0--22----3-7f----------------00-46-66----3--708-c----ff-df-ff",
    "0000--0022--66ef--------33--7-ff00----cc--------0199--ddbb--ffff0000--4422--66ef--------33----ff00-8--cc--------0199--ddff--ffff",
    "00-08-00----c-df2--3----66-6e-ff----------------02-3----77--f-ff00-0--88----c-df----------------00-19-99----c--d02-3----ff-7f-ff",
    "0000--0088--99bf--------cc--d-ff00----33--------0466--77ee--ffff0000--1188--99bf--------cc----ff00-2--33--------0466--77ff--ffff",
)
LORES = ((0, 0, 0), (167, 11, 64), (64, 28, 247), (230, 40, 255), (0, 116, 64), (128, 128, 128),
         (25, 144, 255), (191, 156, 255), (64, 99, 0), (230, 111, 0), (128, 128, 128), (255, 139, 191),
         (25, 215, 0), (191, 227, 8), (88, 244, 191), (255, 255, 255))    # the two greys never occur in hi-res
LUT_INDEX = np.array([[int(c, 16) if c != "-" else 0 for c in row] for row in HALF_DOT_COLOUR])
LUT = np.array(LORES, dtype=np.float64)[LUT_INDEX]
COLOUR_WEIGHTS = np.array([2.0, 4.0, 3.0])      # red, green, blue: the eye's sensitivity, roughly


def half_dots(row):
    """The 560 half-dots that one scanline's 40 bytes put out. A byte with the
    palette bit on comes out half a dot late: its first half-dot repeats the one
    before it, and its last spills into the next byte's first, unless that byte
    (palette bit off) overwrites it."""
    h = np.zeros(561, dtype=np.uint8)
    last = 0
    for col, b in enumerate(row):
        x, p = col * 14, b >> 7
        if p:
            h[x] = last
        for k in range(7):
            h[x + p + 2 * k] = h[x + p + 2 * k + 1] = b >> k & 1
        last = b >> 6 & 1
    return h[:560]


def half_dot_colours(h, first=0):
    """RGB colour of each half-dot, from the bits around it. h is padded with 3
    half-dots on each side; first is the screen position of h's first real one."""
    n = len(h) - 6
    codes = sum(h[t:t + n].astype(np.int64) << (6 - t) for t in range(7))
    return LUT[(first + np.arange(n)) % 4, codes]


def colour_preview(screen):
    """The screen in colour as MAME shows it, 560x384: two half-dots per dot
    across, each scanline drawn twice for the screen's shape."""
    rows = []
    for y in range(H):
        base = hgr_offset(y)
        h = np.concatenate(([0, 0, 0], half_dots(screen[base:base + 40]), [0, 0, 0]))
        rows.append(half_dot_colours(h))
    img = Image.fromarray(np.array(rows, dtype=np.uint8), "RGB")
    return img.resize((560, 384), Image.NEAREST)


def _byte_half_dots():
    """For each byte value, the 15 half-dots it puts out from its own first one,
    for each value of the dot before it: the 15th is the spill of a byte with
    the palette bit on (0 otherwise; the next byte sets it)."""
    out = np.zeros((2, 256, 15), dtype=np.uint8)
    for last in (0, 1):
        for b in range(256):
            p = b >> 7
            if p:
                out[last, b, 0] = last
            for k in range(7):
                out[last, b, p + 2 * k] = out[last, b, p + 2 * k + 1] = b >> k & 1
    return out


def _window_codes(cand):
    """The candidates' part of the 7-half-dot pattern around each of the 17
    half-dots a byte affects (its own 14 and the 3 before it): the 6 half-dots
    before the byte add the rest, the same for every candidate."""
    win = np.concatenate((np.zeros(cand.shape[:2] + (6,), dtype=np.int64), cand, np.zeros(cand.shape[:2] + (2,), dtype=np.int64)), axis=2)
    return sum(win[..., t:t + 17] << (6 - t) for t in range(7))


def colour_screen(img, dither="atkinson"):
    """An RGB 280x192 picture -> 8192-byte HGR screen with palette bits.

    For each byte, left to right and top to bottom, tries all 256 values and
    keeps the one whose colours, as the screen shows them, come closest to the
    picture: its own 14 half-dots and the 3 before it, which it also changes.
    With "atkinson" the difference is spread to the right and the lines below,
    as the black and white dither does, so areas between the screen's colours
    come out as mixes of them; "threshold" spreads nothing."""
    cand = _byte_half_dots()
    codes = _window_codes(cand)                                 # (2, 256, 17)
    # the 6 half-dots before a byte, as their part of each of the 17 patterns
    before = np.array([[sum(int(b) << (6 - t) for t, b in enumerate(bits[j:j + 7]) if t + j < 6)
                        for j in range(17)] for bits in np.unpackbits(np.arange(64, dtype=np.uint8)[:, None], axis=1)[:, 2:]])
    pos = np.arange(17)
    palette = np.array(LORES, dtype=np.float64)
    # the picture in half-dots, with 3 spare on the left and room to spill on the right
    buf = np.zeros((H, 3 + 560 + 14, 3))
    buf[:, 3:563] = np.repeat(np.asarray(img.convert("RGB"), dtype=np.float64), 2, axis=1)
    out = bytearray(0x2000)
    for y in range(H):
        base = hgr_offset(y)
        h = np.zeros(6 + 560 + 15, dtype=np.uint8)      # this line's half-dots, from position -6
        last = 0
        for col in range(40):
            x = col * 14
            prior = int(h[x:x + 6].dot(1 << np.arange(5, -1, -1)))
            shown = LUT_INDEX[(x - 3 + pos) % 4, codes[last] + before[prior]]     # (256, 17): half-dots x-3 .. x+13
            want = np.clip(buf[y, x:x + 17], 0, 255)
            dist = (((palette[None] - want[:, None]) ** 2) * COLOUR_WEIGHTS).sum(axis=2)    # (17, 16)
            b = int(np.argmin(dist[pos, shown].sum(axis=1)))
            out[base + col] = b
            h[x + 6:x + 21] = cand[last, b]
            last = b >> 6 & 1
            if dither != "atkinson":
                continue
            # the error of the half-dots now settled: the 3 before the byte and its
            # first 11. Its last 3 depend on the next byte's dots too, so they wait
            # for it (counting them now, as if black followed, specks flat areas)
            err = (buf[y, x:x + 14] - palette[shown[b, :14]]) / 8.0     # Atkinson: spread 6/8 of it
            # right: a byte's half-dots are chosen together, so its error goes to
            # the same places in the next byte; down: to the half-dots below
            buf[y, x + 14:x + 28] += 2 * err
            for dy, w in ((1, 3), (2, 1)):
                if y + dy < H:
                    buf[y + dy, x:x + 14] += w * err
            buf[y] = np.clip(buf[y], -128, 383)
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--dither", choices=["atkinson", "threshold"], default="atkinson")
    ap.add_argument("--crop", action="store_true", help="fill the screen (default: letterbox)")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--colour", "--color", action="store_true", help="use the six hi-res colours (see above)")
    ap.add_argument("--raw", action="store_true", help="write the bare 8K screen, no header")
    ap.add_argument("--preview", help="also write the screen as a PNG, as the Apple II shows it")
    args = ap.parse_args()

    if args.colour:
        screen = colour_screen(prepare(Image.open(args.input), args.crop, args.invert, "RGB"), args.dither)
        if args.preview:
            colour_preview(screen).save(args.preview)
    else:
        img = prepare(Image.open(args.input), args.crop, args.invert)
        bits = atkinson(img) if args.dither == "atkinson" else threshold(img)
        screen = to_hgr(bits)
        if args.preview:
            prev = Image.new("1", (W, H))
            prev.putdata([v for row in bits for v in row])
            prev.save(args.preview)

    data = screen if args.raw else a2hdr.pack(a2hdr.TYPE_IMAGE_HGR, screen)
    with open(args.output, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes)" % (args.output, len(data)), file=sys.stderr)


if __name__ == "__main__":
    main()
