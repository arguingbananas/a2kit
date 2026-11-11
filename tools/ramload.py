#!/usr/bin/env python3
"""ramload: start a new boot image on a running receiver, without touching the disk.

  a2kit ramload --port /dev/ttyUSB0                 load boot.bin, the current build
  a2kit ramload --port /dev/ttyUSB0 --boot OLD.bin  load another build's boot image

The image goes to $2000 and a small loader to $6000. The loader copies the image
to $0800, resets the stack and jumps to $0801, as the Disk II boot ROM would, so
the new receiver starts exactly as if it had booted. Then, if it can answer read
requests, the code in memory is compared with the image.

This lasts until the next reboot or power-off, which brings back whatever is on
the disk. Whatever is at $2000-$21FF and $6000-$601A is overwritten. The receiver
must be listening, and nothing else may have the serial port open.
"""
import argparse
import os
import sys
import time

import a2hdr
import mkdisk
import rxid
import send

IMAGE = 0x2000
LOADER = 0x6000
SECTOR = 256
PAGE = 256
RESTART = 0x0980        # the boot image's restart loop and boot code run from here to $09FF
SETTLE = 0.5            # seconds for the new receiver to clear the screen and print its banner


def loader(disk_slot=6):
    """6502 code: copy the image to $0800, then start it like the boot ROM."""
    return bytes([0xA0, 0x00,                   # LDY #0
                  0xB9, 0x00, IMAGE >> 8,       # lp: LDA $2000,Y
                  0x99, 0x00, 0x08,             # STA $0800,Y
                  0xB9, 0x00, (IMAGE >> 8) + 1,  # LDA $2100,Y
                  0x99, 0x00, 0x09,             # STA $0900,Y
                  0xC8,                         # INY
                  0xD0, 0xF1,                   # BNE lp
                  0xA2, 0xFF, 0x9A,             # LDX #$FF / TXS: nothing returns to the old receiver
                  0xA9, disk_slot * 16,         # LDA #slot*16
                  0x85, 0x2B,                   # STA $2B: the boot code reads the disk card's slot here
                  0x4C, 0x01, 0x08])            # JMP $0801


def check_image(boot):
    """Raise ValueError unless this is a boot image the loader can start."""
    if len(boot) != 2 * SECTOR:
        raise ValueError("a boot image must be exactly 512 bytes (got %d)" % len(boot))
    if boot[0] != 2:
        raise ValueError("a boot image's first byte must be the sector count 2 (got %d)" % boot[0])


def upload(port, boot, disk_slot=6, retries=5):
    check_image(boot)
    s = send.Sender(port, retries=retries)
    s.send_blob(boot, IMAGE)
    s.send_blob(loader(disk_slot), LOADER)
    s.finish(LOADER)


def verify(port, boot, retries=2):
    """True or False if the new code is in memory or not; None if it can't answer reads."""
    try:
        mem = send.read_memory(port, 0x0800, len(boot), retries)
    except IOError:
        return None
    moved = RESTART - 0x0800
    if mem[moved:] != boot[moved:] or mem[:PAGE] != boot[4:4 + PAGE]:
        return False
    # The boot code moves the receiver down 4 bytes. Past its end, the old bytes
    # stay, so each byte up to the restart code must be one or the other.
    return all(mem[i] in (boot[i + 4], boot[i]) for i in range(PAGE, moved))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="e.g. /dev/ttyUSB0")
    a2hdr.add_baud_arg(ap)
    ap.add_argument("--boot", help="boot image to load (default: boot.bin, or boot-19200.bin with --baud 19200)")
    ap.add_argument("--disk-slot", type=int, choices=range(1, 8), default=6, metavar="1-7",
                    help="slot of the disk card, whose motor the boot code turns off (default 6)")
    a = ap.parse_args()
    name = a.boot or os.path.join(mkdisk.ROOT, "boot.bin" if a.baud == a2hdr.DEFAULT_BAUD else "boot-%d.bin" % a.baud)
    if not os.path.exists(name):
        sys.exit("%s not found; run: a2kit build" % name)
    boot = open(name, "rb").read()
    try:
        check_image(boot)
    except ValueError as e:
        sys.exit("refusing to load %s: %s" % (name, e))

    port = send.open_port(a.port, a.baud)
    rxid.check(port, a.baud, screen=False)    # the new receiver clears the screen anyway
    upload(port, boot, a.disk_slot)
    time.sleep(SETTLE)
    result = verify(port, boot)
    if result is False:
        sys.exit("%s started, but the code in memory doesn't match it" % os.path.basename(name))
    print("%s started; %s" % (os.path.basename(name), "the code in memory matches it" if result else
                              "not checked, because it doesn't answer read requests at %d baud" % a.baud))
    print("this lasts until the next reboot or power-off")


if __name__ == "__main__":
    main()
