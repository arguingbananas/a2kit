#!/usr/bin/env python3
"""bin2mon: print a binary as Apple II Monitor entry lines.

  bin2mon.py receiver.bin            # starts at $0800
  bin2mon.py receiver.bin --addr 0x6000

Type the printed lines at the Monitor prompt (*), then run the program with the
last line (0800G for the receiver). CALL -151 gets you to the Monitor from
BASIC. To check your typing before running it, type the range shown on stderr
(for receiver.bin: 0800.0924) and compare the dump with the printed lines.
"""
import argparse
import sys

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x0800)
    a = ap.parse_args()
    data = open(a.file, "rb").read()
    if not data:
        sys.exit("%s is empty" % a.file)
    if a.addr + len(data) > 0x10000:
        sys.exit("doesn't fit below $FFFF at $%04X" % a.addr)
    for i in range(0, len(data), 8):
        chunk = data[i:i + 8]
        print("%04X:%s" % (a.addr + i, " ".join("%02X" % b for b in chunk)))
    print("%04XG" % a.addr)
    print("(%d lines to type; to check them, type %04X.%04X and compare)" % (
        -(-len(data) // 8), a.addr, a.addr + len(data) - 1), file=sys.stderr)


if __name__ == "__main__":
    main()
