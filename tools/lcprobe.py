#!/usr/bin/env python3
"""lcprobe: test a language card (16K of extra RAM) in slot 0 of the Apple II.

  a2kit lcprobe --port /dev/ttyS0

The receiver must be running. This uploads a small test program to $1200, which
writes a pattern and its inverse to all 16K, including both 4K banks at $D000,
checks them, and switches back to ROM. It prints the result on the Apple II's
screen and sends it back over the serial line. Press a key on the Apple II
afterwards to go back to the receiver.

It overwrites everything in the card, so upload sound after running it, not
before. If it reports the card as good, --language-card on show, video, stream
and send can use it.

Exit code: 0 if the card is good, 1 if not, 2 for a usage error.
"""
import argparse
import os
import sys
import time

import a2hdr
import rxid
import send

PROBE_ADDR = 0x1200
END_MARK = b"ANY KEY\r"
GOOD = "RESULT: 16K GOOD"
WAIT_SECONDS = 15.0


def read_result(port, wait=WAIT_SECONDS, clock=time.monotonic):
    """The probe's report, as text with newlines; stops at its last line or after `wait`."""
    buf, t0 = b"", clock()
    while not buf.endswith(END_MARK) and clock() - t0 < wait:
        buf += port.read(64)
    return buf.decode("ascii", "replace").replace("\r", "\n").strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. /dev/ttyS0, /dev/ttyUSB0, COM3")
    a2hdr.add_baud_arg(ap)
    a = ap.parse_args()

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lcprobe.bin")
    if not os.path.exists(path):
        sys.exit("lcprobe.bin not found; run: a2kit build")
    import serial
    port = serial.Serial(a.port, a.baud, timeout=0.5)
    rxid.check(port, a.baud, screen=False)    # the probe prints its own report
    s = send.Sender(port)
    s.send_blob(open(path, "rb").read(), PROBE_ADDR)
    s.finish(PROBE_ADDR)
    report = read_result(port)
    port.close()
    print(report or "No report from the test program.")
    if GOOD in report:
        print("\nLanguage card OK: --language-card is safe to use. Press a key on the Apple II to go back to the receiver.")
        return
    print("\nNo working language card found. Use a2kit without --language-card, or see the User Manual.")
    print("Press a key on the Apple II to go back to the receiver.")
    sys.exit(1)


if __name__ == "__main__":
    main()
