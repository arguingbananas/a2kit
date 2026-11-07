#!/usr/bin/env python3
"""crash: read back what the receiver's crash handler kept.

  a2kit crash --port /dev/ttyUSB0            report any crash since the receiver started
  a2kit crash --port /dev/ttyUSB0 --watch    stay connected and log a crash when it happens

When a program hits BRK, the Monitor saves the registers and the receiver's
handler sends one marker byte, prints the registers and keeps listening. This
reads them, the stack, the vectors and the receiver's own code, which shows
whether the crash damaged it. --watch also logs when the Apple II stops
answering, as a hang or a crash that never reaches BRK can't send anything.

--watch keeps the port open until interrupted. A crash can only be reported if
the handler runs and the card still works, so a quiet log isn't proof of health.
"""
import argparse
import os
import time

import a2hdr
import peek
import send

CRASH_FLAG = 0x00CE     # holds send.CRASH_MARK after a crash
PC_SAVE = 0x003A        # Monitor: PCL, PCH
REG_SAVE = 0x0045       # Monitor: A, X, Y, P, S
STACK = 0x0100
VECTORS = 0x03F0
RECEIVER = 0x0800
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def crashed(port):
    return send.read_memory(port, CRASH_FLAG, 1)[0] == send.CRASH_MARK


def report(port, baud=a2hdr.DEFAULT_BAUD):
    """Lines describing the crash the receiver kept."""
    pcl, pch = send.read_memory(port, PC_SAVE, 2)
    a, x, y, p, s = send.read_memory(port, REG_SAVE, 5)
    pc = pcl | pch << 8
    # the stacked PC is 2 past a BRK; the Monitor's display shows it the same way
    lines = ["%04X- A=%02X X=%02X Y=%02X P=%02X S=%02X   (BRK at $%04X)" % (pc, a, x, y, p, s, (pc - 2) & 0xFFFF),
             "vectors:"] + peek.dump(VECTORS, send.read_memory(port, VECTORS, 16))
    lines.append("stack page (the handler's own calls are just below $0120):")
    lines += peek.dump(STACK, send.read_memory(port, STACK, 256))
    name = "receiver.bin" if baud == a2hdr.DEFAULT_BAUD else "receiver-%d.bin" % baud
    code = open(os.path.join(ROOT, name), "rb").read()
    now = send.read_memory(port, RECEIVER, len(code))
    bad = [RECEIVER + i for i, (u, v) in enumerate(zip(code, now)) if u != v]
    lines.append("receiver code: %s" % ("matches %s" % name if not bad else
                 "%d bytes differ from %s, first at $%04X" % (len(bad), name, bad[0])))
    return lines


def watch(port, baud=a2hdr.DEFAULT_BAUD, interval=30.0, out=print, stamp=lambda: time.strftime("%F %T"),
          clock=time.monotonic, polls=None):
    """Poll until interrupted (or for `polls` polls, for tests), logging each change."""
    out("%s watching; polling every %g s" % (stamp(), interval))
    alive, reported = None, None        # reported: the saved PC and registers last logged
    while polls is None or polls > 0:
        if polls is not None:
            polls -= 1
        try:
            is_crashed = crashed(port)
            saved = send.read_memory(port, PC_SAVE, 2) + send.read_memory(port, REG_SAVE, 5) if is_crashed else None
        except IOError:
            if alive is not False:
                out("%s NOT ANSWERING" % stamp())
            alive = False
        else:
            if alive is False:
                out("%s answering again" % stamp())
            elif alive is None:
                out("%s answering" % stamp())
            alive = True
            if is_crashed and saved != reported:        # a later BRK changes what the Monitor saved
                out("%s CRASH" % stamp())
                for line in report(port, baud):
                    out("  " + line)
            reported = saved
        until = clock() + interval
        while clock() < until:                  # any marker cuts the wait short
            if port.read(1) == bytes([send.CRASH_MARK]):
                break


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="e.g. /dev/ttyUSB0")
    a2hdr.add_baud_arg(ap)
    ap.add_argument("--watch", action="store_true", help="stay connected and log a crash when it happens")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between checks with --watch (default 30)")
    a = ap.parse_args()

    import serial
    port = serial.Serial(a.port, a.baud, timeout=1.0)
    if a.watch:
        watch(port, a.baud, a.interval, out=lambda s: print(s, flush=True))
    elif crashed(port):
        print("\n".join(report(port, a.baud)))
    else:
        print("no crash recorded since the receiver last started")


if __name__ == "__main__":
    main()
