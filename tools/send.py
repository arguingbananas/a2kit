#!/usr/bin/env python3
"""send: push a block or raw file to an Apple II over a serial link.

Packet protocol (host -> Apple II), one packet at a time, each ACKed:

  $A5 seq len addr_lo addr_hi data[len] checksum

  seq       wraps at 256
  len       1-255 data bytes; len 0 is the end marker, and addr is then the
            address to JSR to ($0000 = show HGR page 1 until a key is hit)
  checksum  XOR of every byte after the $A5 sync byte (cheap on a 6502)

Apple II replies one byte: $06 ACK, $15 NAK (host resends, up to --retries).

Read request (host -> Apple II), for a2kit peek and a2kit crash:

  $A6 count 0 addr_lo addr_hi checksum

  count     1-255 bytes; 0 means 256
  reply     $06, the bytes, then the XOR of the bytes; or $15 for a bad checksum

Speed: 115200 baud by default; 19200 with the receiver built for it
(a2kit-boot-19200.dsk), the fastest the 6551's own baud-rate generator gives. --baud,
or A2KIT_BAUD, must match the number in the receiver's banner. Per-packet ACKs
keep the 1 MHz CPU from being overrun.

Destinations are checked first: the tool refuses to write over zero page and
the stack ($0000-$01FF), the receiver itself ($0800-$0AFF) or I/O and ROM
($C000-$FFFF). --language-card allows $D000-$FEFF, which goes to bank 2 of a
language card in slot 0 (a2kit lcprobe checks for one).
"""
import argparse
import struct
import sys
import time

import a2hdr

SYNC = 0xA5
READ_SYNC = 0xA6
CRASH_MARK = 0xC3       # the receiver's crash handler sends this by itself, at any moment
ACK = 0x06
NAK = 0x15

# default load address by block type (None = must pass --addr)
DEFAULT_ADDR = {a2hdr.TYPE_IMAGE_HGR: 0x2000}

# Memory the receiver must never be told to write: its own zero page and the
# stack, the receiver and its packet buffer, and I/O / ROM.
PROTECTED = ((0x0000, 0x01FF, "zero page and stack"),
             (0x0800, 0x0AFF, "the receiver and its buffer"),
             (0xC000, 0xFFFF, "I/O and ROM"))


TURNAROUND = 0.0025     # seconds per packet the port and program add (2.2 ms measured on a built-in 16550)


def upload_seconds(sizes, baud=a2hdr.DEFAULT_BAUD, chunk=128, turnaround=TURNAROUND):
    """Expected time to upload blocks of these sizes and end the session.

    Each packet costs its bytes plus 7 (header, checksum, reply) on the line,
    the receiver's copy (18 cycles a byte), and the turnaround. Checked against
    a real Apple II+: an 8 KB picture in about 4.8 s, the starter show in about 15 s.
    """
    char = 10.0 / baud
    t = 0.0
    for n in list(sizes) + [0]:                       # the blocks, then the end packet
        full, rest = divmod(n, chunk)
        for k in [chunk] * full + ([rest] if rest or n == 0 else []):
            t += (k + 7) * char + (18 * k + 40) / a2hdr.CPU_HZ + turnaround
    return t


def check_range(addr, length, language_card=False):
    """Raise ValueError if [addr, addr+length) is off the end of memory or
    touches a protected area. A language card's RAM is allowed only if asked for."""
    if length <= 0:
        return
    end = addr + length - 1
    if addr < 0 or end > 0xFFFF:
        raise ValueError("$%04X + %d bytes runs past $FFFF" % (addr, length))
    if language_card and a2hdr.CARD_START <= addr and end < a2hdr.CARD_END:
        return
    for lo, hi, what in PROTECTED:
        if addr <= hi and end >= lo:
            raise ValueError("$%04X-$%04X would overwrite %s ($%04X-$%04X)" % (addr, end, what, lo, hi))


def make_packet(seq, addr, data):
    body = bytes([seq & 0xFF, len(data)]) + struct.pack("<H", addr) + data
    ck = 0
    for b in body:
        ck ^= b
    return bytes([SYNC]) + body + bytes([ck])


def make_read_packet(addr, count):
    """A read request for count bytes (1-256) at addr. It has a write packet's
    shape with no data, and the count where the sequence number goes."""
    body = bytes([count & 0xFF, 0]) + struct.pack("<H", addr)
    ck = 0
    for b in body:
        ck ^= b
    return bytes([READ_SYNC]) + body + bytes([ck])


def check_read_range(addr, length):
    """Raise ValueError if [addr, addr+length) is off the end of memory or
    touches I/O: just reading a soft switch can change the machine's state."""
    if length <= 0:
        raise ValueError("nothing to read (length %d)" % length)
    end = addr + length - 1
    if addr < 0 or end > 0xFFFF:
        raise ValueError("$%04X + %d bytes runs past $FFFF" % (addr, length))
    if addr <= 0xCFFF and end >= 0xC000:
        raise ValueError("$%04X-$%04X would read I/O ($C000-$CFFF), which can change the machine's state" % (addr, end))


def read_memory(port, addr, length, retries=5):
    """Read Apple II memory through the receiver; returns the bytes."""
    check_read_range(addr, length)
    out = bytearray()
    while len(out) < length:
        count = min(length - len(out), 256)
        at = addr + len(out)
        for _ in range(retries):
            port.reset_input_buffer()
            port.write(make_read_packet(at, count))
            port.flush()
            reply = port.read(1)
            if reply and reply[0] == CRASH_MARK:
                reply = port.read(1)
            if reply and reply[0] == ACK:
                body = port.read(count + 1)
                ck = 0
                for b in body:
                    ck ^= b
                if len(body) == count + 1 and ck == 0:
                    out += body[:-1]
                    break
            while port.read(64):        # let a late reply finish, or it would answer the retry
                pass
        else:
            raise IOError("no good reply to a read of $%04X after %d tries; "
                          "is the receiver running, and from a disk built with read support?" % (at, retries))
    return bytes(out)


class Sender:
    def __init__(self, port, chunk=128, retries=5, language_card=False):
        if not 1 <= chunk <= 255:
            raise ValueError("chunk must be 1-255")
        self.port = port
        self.chunk = chunk
        self.retries = retries
        self.seq = 0
        self.language_card = language_card

    def _send_packet(self, addr, data):
        pkt = make_packet(self.seq, addr, data)
        for _ in range(self.retries):
            self.port.reset_input_buffer()      # drop any late reply to an earlier try
            self.port.write(pkt)
            self.port.flush()
            reply = self.port.read(1)
            if reply and reply[0] == ACK:
                self.seq = (self.seq + 1) & 0xFF
                return
            # NAK, garbage, or timeout: resend
        raise IOError("no ACK after %d tries (seq %d, addr $%04X)" % (self.retries, self.seq, addr))

    def send_blob(self, data, addr, progress=None):
        check_range(addr, len(data), self.language_card)
        pos = 0
        while pos < len(data):
            part = data[pos:pos + self.chunk]
            self._send_packet((addr + pos) & 0xFFFF, part)
            pos += len(part)
            if progress:
                progress(pos, len(data))

    def finish(self, exec_addr=0):
        self._send_packet(exec_addr, b"")


def set_low_latency(port):
    """Ask the driver to pass replies on at once; True if it accepted. On Linux this
    turns an FTDI adapter's 16 ms latency timer down to 1 ms (until it's unplugged),
    with no sudo. Elsewhere, or on a port without the setting, it does nothing."""
    try:
        port.set_low_latency_mode(True)
        return True
    except (AttributeError, ValueError, OSError):
        return False


def open_port(name, baud, timeout=2.0, rtscts=False):
    """Open a serial port for the receiver, in low-latency mode where the driver offers it."""
    import serial  # imported here so the module is usable without pyserial for tests
    port = serial.Serial(name, baud, timeout=timeout, rtscts=rtscts)
    set_low_latency(port)
    return port


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--port", required=True, help="e.g. /dev/ttyUSB0")
    a2hdr.add_baud_arg(ap)
    ap.add_argument("--addr", type=lambda s: int(s, 0), help="load address, e.g. 0x2000")
    ap.add_argument("--exec", dest="exec_addr", type=lambda s: int(s, 0), default=0,
                    help="JSR to this address when done (default 0: display HGR page 1)")
    ap.add_argument("--raw", action="store_true", help="send file as-is (needs --addr)")
    ap.add_argument("--keep-header", action="store_true", help="send the a2kit header too")
    ap.add_argument("--chunk", type=int, default=128)
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--rtscts", action="store_true", help="hardware flow control")
    a2hdr.add_language_card_arg(ap)
    args = ap.parse_args()

    data = open(args.file, "rb").read()
    addr = args.addr
    if not args.raw:
        btype, _cycles, payload = a2hdr.unpack(data)
        if addr is None:
            addr = DEFAULT_ADDR.get(btype)
        if not args.keep_header:
            data = payload
    if addr is None:
        sys.exit("need --addr for this file")
    try:
        check_range(addr, len(data), args.language_card)
    except ValueError as e:
        sys.exit("refusing to send: %s" % e)
    if not 1 <= args.chunk <= 255:
        sys.exit("--chunk must be 1-255")

    port = open_port(args.port, args.baud, rtscts=args.rtscts)
    s = Sender(port, args.chunk, args.retries, args.language_card)
    t0 = time.time()

    def progress(done, total):
        print("\r%d/%d bytes" % (done, total), end="", file=sys.stderr)

    s.send_blob(data, addr, progress)
    s.finish(args.exec_addr)
    print("\ndone in %.1fs" % (time.time() - t0), file=sys.stderr)


if __name__ == "__main__":
    main()
