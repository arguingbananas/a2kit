#!/usr/bin/env python3
"""mkdisk: make a bootable 5.25" disk image that starts the a2kit receiver.

  mkdisk.py                       # writes a2kit-boot.dsk (receiver at 19200 baud)
  mkdisk.py --baud 115200         # writes a2kit-boot-115200.dsk
  mkdisk.py -o myboot.po          # same image, any name

Boot it on the Apple II (in a Disk II drive, a Floppy Emu, or an emulator)
and the receiver starts by itself: no typing in at the Monitor. After each
upload session it starts listening again. Press a key while it is waiting to
drop into the Monitor; 980G (or CALL 2432 from BASIC) starts it again.

The image is 140 KB (35 tracks x 16 sectors x 256 bytes) in DOS 3.3 sector
order, but there is no DOS on it: the boot sectors load the receiver directly,
so all of RAM stays free. Only track 0 is used. Because the second boot sector
is written to every one of sectors 1-15, the image boots whether a tool treats
it as DOS order (.dsk/.do) or ProDOS order (.po).

Needs boot.bin, or boot-115200.bin for --baud 115200 (both built from
runtime/boot.s and the receivers by "a2kit build"). The receiver's banner
shows its speed: A2KIT RX READY 19200 or A2KIT RX READY 115200.
"""
import argparse
import os
import sys

TRACKS, SECTORS, SECTOR = 35, 16, 256
IMAGE_SIZE = TRACKS * SECTORS * SECTOR          # 143360

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))


def build_image(boot):
    """Return the disk image bytes for a 512-byte boot.bin."""
    if len(boot) != 2 * SECTOR:
        raise ValueError("boot.bin must be exactly 512 bytes (got %d)" % len(boot))
    if boot[0] != 2:
        raise ValueError("boot.bin's first byte must be the sector count 2 (got %d)" % boot[0])
    img = bytearray(IMAGE_SIZE)
    img[0:SECTOR] = boot[:SECTOR]                           # track 0, sector 0
    for s in range(1, SECTORS):                             # track 0, sectors 1-15
        img[s * SECTOR:(s + 1) * SECTOR] = boot[SECTOR:]
    return bytes(img)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baud", type=int, choices=(19200, 115200), default=19200,
                    help="the receiver's line speed (default 19200)")
    ap.add_argument("-o", "--output", help="image to write (default a2kit-boot.dsk, or a2kit-boot-115200.dsk)")
    ap.add_argument("--boot", help="boot sectors (default: boot.bin, or boot-115200.bin)")
    a = ap.parse_args()
    suffix = "" if a.baud == 19200 else "-%d" % a.baud
    a.output = a.output or "a2kit-boot%s.dsk" % suffix
    a.boot = a.boot or os.path.join(ROOT, "boot%s.bin" % suffix)
    if not os.path.exists(a.boot):
        sys.exit("%s not found; run: a2kit build" % a.boot)
    img = build_image(open(a.boot, "rb").read())
    with open(a.output, "wb") as f:
        f.write(img)
    print("wrote %s (%d bytes, bootable; the receiver starts by itself)" % (a.output, len(img)), file=sys.stderr)


if __name__ == "__main__":
    main()
