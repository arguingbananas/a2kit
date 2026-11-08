#!/usr/bin/env python3
"""peek: read Apple II memory over the serial link and print it as a hex dump.

  a2kit peek 0x2000 64 --port /dev/ttyUSB0
  a2kit peek BD00 16 --port /dev/ttyUSB0 -o dump.bin

Addresses are hex, with or without $ or 0x. The receiver must be running, from a
disk built with read support. $C000-$CFFF is refused because reading a soft
switch can change the machine's state. $D000-$FFFF reads ROM, not a language card.
"""
import argparse
import sys

import a2hdr
import send


def parse_hex(s):
    return int(s.lstrip("$").removeprefix("0x").removeprefix("0X"), 16)


def dump(addr, data):
    """Monitor-style lines: 8 bytes each, on 8-byte address boundaries."""
    lines, pos = [], 0
    while pos < len(data):
        at = addr + pos
        n = min(8 - at % 8, len(data) - pos)
        lines.append("%04X- %s" % (at, " ".join("%02X" % b for b in data[pos:pos + n])))
        pos += n
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("addr", type=parse_hex, help="start address in hex, e.g. BD00")
    ap.add_argument("length", nargs="?", type=lambda s: int(s, 0), default=16, help="bytes to read (default 16)")
    ap.add_argument("--port", required=True, help="e.g. /dev/ttyUSB0")
    a2hdr.add_baud_arg(ap)
    ap.add_argument("-o", "--output", help="also write the bytes to this file")
    a = ap.parse_args()
    try:
        send.check_read_range(a.addr, a.length)
    except ValueError as e:
        sys.exit("refusing to read: %s" % e)

    data = send.read_memory(send.open_port(a.port, a.baud), a.addr, a.length)
    print("\n".join(dump(a.addr, data)))
    if a.output:
        with open(a.output, "wb") as f:
            f.write(data)


if __name__ == "__main__":
    main()
