#!/usr/bin/env python3
"""imgconv: convert an image to an Apple II HGR screen (280x192, monochrome).

Output is the 8192-byte screen in memory order, wrapped in an a2kit block
header, so it can be copied straight to $2000.

Usage:
  imgconv.py logo.png -o logo.a2b
  imgconv.py photo.jpg -o photo.a2b --dither atkinson --crop --preview p.png
"""
import argparse
import sys

from PIL import Image, ImageOps

import a2hdr

W, H = 280, 192


def hgr_offset(y):
    """Byte offset from $2000 of the start of scanline y (0-191)."""
    return (y & 7) * 0x400 + ((y >> 3) & 7) * 0x80 + (y >> 6) * 0x28


def prepare(img, crop, invert):
    img = img.convert("L")
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--dither", choices=["atkinson", "threshold"], default="atkinson")
    ap.add_argument("--crop", action="store_true", help="fill the screen (default: letterbox)")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--raw", action="store_true", help="write the bare 8K screen, no header")
    ap.add_argument("--preview", help="also write the dithered 280x192 result as a PNG")
    args = ap.parse_args()

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
