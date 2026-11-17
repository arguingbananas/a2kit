#!/usr/bin/env python3
"""pack: shrink a picture for upload, and the Apple II's unpacker for it.

A picture is 8192 bytes, but line art, text and flat areas repeat a lot, so
`send` and `show send` can upload it packed and have the Apple II unpack it:
the packed bytes go to a buffer at $6000, the unpacker (runtime/unpack.s,
about 100 bytes) to $0300, and a session that runs the unpacker writes the
picture to its page. They pack a picture only when that is quicker overall,
counting the unpacking (about 18 cycles a byte at 1 MHz, and 110 for each
control byte: 0.15-0.35 s for a picture) and the extra session.

The format, one control byte at a time:

  $00-$7F  c        c+1 literal bytes follow (1-128)
  $80-$BF  c o      copy (c & $3F) + 2 bytes (2-65) from o+1 bytes back (1-256)
  $C0-$FE  c lo hi  copy (c & $3F) + 3 bytes (3-65) from hi*256+lo bytes back
  $FF               end

Copies run forwards a byte at a time, so a copy from 1 byte back repeats it.
The packer finds the smallest encoding (an optimal parse over every match it
finds), so a picture is never bigger packed than it is plain plus 1 byte per 128.
"""
import os
import time

import a2hdr
import send

UNPACKER_ADDR = 0x0300          # page 3: free; below the Monitor's vectors at $03F0
BUFFER = 0x6000                 # where the packed bytes go: the sound area, free until the sound is sent
END = 0xFF
LIT_MAX = 128
SHORT_MAX, LONG_MAX = 65, 65    # copy lengths
SHORT_OFF = 256
SESSION_SECONDS = 0.15          # an extra end packet, the receiver restarting, and the read that finds it listening
CYCLES_PER_BYTE = 18            # the unpacker's inner loops
CYCLES_PER_TOKEN = 110          # reading a control byte and setting up (measured on py65)
CYCLES_START = 80


def _longest(data, i, j, cap):
    """Length of the common run at i and j (j < i), up to cap, by halving."""
    lo, hi = 0, min(cap, len(data) - i)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if data[j:j + mid] == data[i:i + mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _matches(data, chain=24):
    """For each position, the longest copy found within 256 bytes back, and
    the longest further back: [(length, distance), ...]."""
    heads = {}
    found = []
    for i in range(len(data)):
        key = data[i:i + 3]
        short = far = (0, 0)
        if len(key) == 3:
            for j in reversed(heads.get(key, ())[-chain:]):
                d = i - j
                if d <= SHORT_OFF:
                    if short[0] < SHORT_MAX:
                        n = _longest(data, i, j, SHORT_MAX)
                        if n > short[0]:
                            short = (n, d)
                elif far[0] < LONG_MAX:
                    n = _longest(data, i, j, LONG_MAX)
                    if n > far[0]:
                        far = (n, d)
            heads.setdefault(key, []).append(i)
        if i >= 1 and data[i:i + 1] == data[i - 1:i] and short[0] < 2:     # runs of 2: the 3-byte key misses them
            short = (_longest(data, i, i - 1, SHORT_MAX), 1)
        found.append(([short] if short[0] >= 2 else []) + ([far] if far[0] >= 3 else []))
    return found


def pack(data):
    """The packed form of data (bytes), ending with $FF."""
    n = len(data)
    found = _matches(data)
    INF = float("inf")
    cost = [INF] * (n + 1)
    step = [None] * (n + 1)
    cost[n] = 0
    for i in range(n - 1, -1, -1):
        best, how = INF, None
        for L in range(1, min(LIT_MAX, n - i) + 1):           # a literal run of L
            c = 1 + L + cost[i + L]
            if c < best:
                best, how = c, ("lit", L)
        for length, dist in found[i]:
            short = dist <= SHORT_OFF
            size, low = (2, 2) if short else (3, 3)
            for L in range(low, length + 1):
                c = size + cost[i + L]
                if c < best:
                    best, how = c, ("copy", L, dist)
        cost[i], step[i] = best, how
    out = bytearray()
    i = 0
    while i < n:
        how = step[i]
        if how[0] == "lit":
            L = how[1]
            out.append(L - 1)
            out += data[i:i + L]
        else:
            _, L, dist = how
            if dist <= SHORT_OFF:
                out += bytes([0x80 | (L - 2), dist - 1])
            else:
                out += bytes([0xC0 | (L - 3), dist & 0xFF, dist >> 8])
        i += L
    out.append(END)
    return bytes(out)


def unpack(packed):
    """What the Apple II's unpacker makes of packed (the reference for the tests)."""
    out = bytearray()
    i = 0
    while True:
        c = packed[i]
        if c == END:
            return bytes(out)
        if c < 0x80:
            out += packed[i + 1:i + 2 + c]
            i += 2 + c
            continue
        if c < 0xC0:
            L, dist = (c & 0x3F) + 2, packed[i + 1] + 1
            i += 2
        else:
            L, dist = (c & 0x3F) + 3, packed[i + 1] | packed[i + 2] << 8
            i += 3
        for _ in range(L):
            out.append(out[-dist])


def tokens(packed):
    """How many control bytes packed has, for the timing estimate."""
    count, i = 0, 0
    while packed[i] != END:
        c = packed[i]
        count += 1
        i += 2 + c if c < 0x80 else (2 if c < 0xC0 else 3)
    return count


def unpacker_bin():
    here = os.path.dirname(os.path.abspath(__file__))
    return open(os.path.join(here, "..", "unpack.bin"), "rb").read()


def unpacker(dest):
    """The unpacker, set to read the buffer and write to dest."""
    code = bytearray(unpacker_bin())
    code[3:7] = bytes([BUFFER & 0xFF, BUFFER >> 8, dest & 0xFF, dest >> 8])
    return bytes(code)


def unpack_seconds(data_len, packed):
    return (CYCLES_START + CYCLES_PER_BYTE * data_len + CYCLES_PER_TOKEN * tokens(packed)) / a2hdr.CPU_HZ


def plan(data, dest, baud=a2hdr.DEFAULT_BAUD):
    """(packed bytes or None, seconds plain, seconds packed): None when packing
    isn't quicker, or the packed bytes wouldn't fit in the buffer."""
    plain = send.upload_seconds([len(data)], baud)
    packed = pack(data)
    if BUFFER + len(packed) > 0xC000:
        return None, plain, None
    t = (send.upload_seconds([len(unpacker_bin()), len(packed)], baud)
         + unpack_seconds(len(data), packed) + SESSION_SECONDS)
    return (packed if t < plain else None), plain, t


def send_packed(sender, data, dest, packed):
    """Upload packed and the unpacker, and run it in a session of its own: the
    receiver starts listening again when it returns. Raises IOError if the
    receiver doesn't come back."""
    sender.send_blob(unpacker(dest), UNPACKER_ADDR)
    sender.send_blob(packed, BUFFER)
    sender.finish(UNPACKER_ADDR)
    # a read sent while it unpacks is lost, and would wait out the port's whole
    # timeout: so wait until it should be done, then ask often, briefly
    time.sleep(unpack_seconds(len(data), packed) + 0.05)
    port = getattr(sender, "port", None)
    saved = port.timeout if port else None
    try:
        if port:
            port.timeout = 0.1
        for _ in range(30):
            if sender.receiver_listening():
                return
    finally:
        if port:
            port.timeout = saved
    raise IOError("the receiver didn't start listening again after unpacking at $%04X" % dest)
