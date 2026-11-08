#!/usr/bin/env python3
"""canary: fill free Apple II RAM with a known pattern and log any byte that changes.

  a2kit canary --port /dev/ttyUSB0              fill, then check every 5 minutes
  a2kit canary --port /dev/ttyUSB0 --no-fill    go on checking a region filled earlier

Fills $0B00-$BFFF (45 KB that the receiver, the Monitor and the text screen
don't use) with a pattern made from each byte's address, so a changed byte shows
where it is and which bits changed. Every --check seconds it reads the region
back and logs each new change, and each byte that goes back to the pattern.
Between checks it watches like `a2kit crash --watch`, so crashes and silences
are logged too, and it logs when the number of zero bytes on the stack page
jumps.

On a II+ (and the original Apple II) each 16K bank ($0000, $4000, $8000) is
one row of eight RAM chips, one per data bit, so the bank and the changed bits
(the xor column) point at a chip. A IIe has one row, so the bit alone does.

It keeps the port open until interrupted, so nothing else can use the receiver
meanwhile. The fill overwrites both hi-res pages and anything else loaded there.
"""
import argparse
import time

import a2hdr
import crash
import send

LO, HI = 0x0B00, 0xC000         # the region checked, [LO, HI)
MAX_LINES = 40                  # changed bytes listed per check; the rest are counted
STATUS_EVERY = 3600             # seconds between "all quiet" lines
STACK_JUMP = 16                 # a change this big in the stack page's zero count is logged


def expected(addr):
    """Differs between neighbours, and between addresses $4000 or $8000 apart (the same
    place in another bank), so a write that lands in the wrong place shows."""
    return ((addr & 0xFF) + (addr >> 8) + 0x5A) & 0xFF


def pattern(lo, hi):
    return bytes(expected(a) for a in range(lo, hi))


class Canary:
    def __init__(self, read, out, stamp, lo=LO, hi=HI):
        self.read, self.out, self.stamp, self.lo, self.hi = read, out, stamp, lo, hi
        self.want = pattern(lo, hi)
        self.seen = {}                  # addr -> value last logged
        self.stack_zeros = None
        self.checks = 0

    def check(self):
        """Read the region back; log what changed since the last check. Returns the bad-byte count."""
        self.checks += 1
        now = self.read(self.lo, self.hi - self.lo)
        bad = {self.lo + i: v for i, v in enumerate(now) if v != self.want[i]}
        new = {a: v for a, v in bad.items() if self.seen.get(a) != v}
        healed = [a for a in self.seen if a not in bad]
        if new:
            self.out("%s CANARY: %d byte(s) changed (%d differ in all)" % (self.stamp(), len(new), len(bad)))
            for a in sorted(new)[:MAX_LINES]:
                self.out("  $%04X expected %02X now %02X (xor %02X)" % (a, expected(a), new[a], expected(a) ^ new[a]))
            if len(new) > MAX_LINES:
                self.out("  ... and %d more" % (len(new) - MAX_LINES))
            bits = 0
            for a, v in new.items():
                bits |= expected(a) ^ v
            self.out("  bits changed: %s" % " ".join("D%d" % b for b in range(8) if bits >> b & 1))
        if healed:
            self.out("%s canary: %d byte(s) back to the pattern: %s" % (
                self.stamp(), len(healed), " ".join("$%04X" % a for a in sorted(healed)[:MAX_LINES])))
        self.seen = bad
        zeros = self.read(crash.STACK, 256).count(0)
        if self.stack_zeros is None:
            self.out("%s stack page: %d zero bytes" % (self.stamp(), zeros))
        elif abs(zeros - self.stack_zeros) >= STACK_JUMP:
            self.out("%s STACK PAGE: %d zero bytes, was %d" % (self.stamp(), zeros, self.stack_zeros))
        self.stack_zeros = zeros
        return len(bad)


def run(port, baud=a2hdr.DEFAULT_BAUD, check_every=300.0, fill=True, interval=30.0, out=print,
        stamp=lambda: time.strftime("%F %T"), clock=time.monotonic, polls=None, lo=LO, hi=HI):
    """Fill (unless told not to), then check and watch until interrupted (or for `polls` polls, for tests)."""
    if fill:
        t = clock()
        send.Sender(port).send_blob(pattern(lo, hi), lo)
        out("%s filled $%04X-$%04X (%d bytes) in %.1f s" % (stamp(), lo, hi - 1, hi - lo, clock() - t))
    c = Canary(lambda a, n: send.read_memory(port, a, n), out, stamp, lo, hi)
    n = c.check()
    out("%s first check: %d byte(s) differ from the pattern; checking every %g s" % (stamp(), n, check_every))
    due = {"check": clock() + check_every, "status": clock()}

    def each(recovered):
        if recovered:
            due["check"] = clock()          # check straight after a silence
        if clock() < due["check"]:
            return
        try:
            n = c.check()
        except IOError:
            return                          # the next poll logs the silence
        due["check"] = clock() + check_every
        if clock() - due["status"] >= STATUS_EVERY:
            out("%s check %d: %d byte(s) differ" % (stamp(), c.checks, n))
            due["status"] = clock()

    crash.watch(port, baud, interval, out=out, stamp=stamp, clock=clock, polls=polls, each=each)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="e.g. /dev/ttyUSB0")
    a2hdr.add_baud_arg(ap)
    ap.add_argument("--check", type=float, default=300.0, help="seconds between memory checks (default 300)")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between crash checks (default 30)")
    ap.add_argument("--no-fill", action="store_true", help="don't fill; go on checking a region an earlier run filled")
    a = ap.parse_args()
    if a.check <= 0 or a.interval <= 0:
        ap.error("--check and --interval must be positive")

    import serial
    port = serial.Serial(a.port, a.baud, timeout=1.0)
    run(port, a.baud, a.check, not a.no_fill, a.interval, out=lambda s: print(s, flush=True))


if __name__ == "__main__":
    main()
