"""a2kit block header: 8 bytes, little-endian.

  offset size  field
  0      2     magic "A2"
  2      1     format version (runtime refuses mismatches)
  3      1     block type
  4      2     payload length in bytes
  6      2     cycle budget per frame (0 = not timing critical)
"""
import argparse
import os
import struct

# a2kit's own version: ./a2kit --version prints it. Raise it, and add a CHANGELOG.md
# entry, when a change is merged that users would notice (see CONTRIBUTING.md).
VERSION = "1.0.0"

MAGIC = b"A2"
FORMAT_VERSION = 1                  # of the block format, not of a2kit
HDR_SIZE = 8

TYPE_IMAGE_HGR = 1
TYPE_IMAGE_DHGR = 2
TYPE_VIDEO = 3
TYPE_AUDIO = 4
TYPE_EFFECT = 5

_FMT = "<2sBBHH"


def pack(block_type, payload, cycles=0):
    if len(payload) > 0xFFFF:
        raise ValueError("payload too large for a 16-bit length (%d)" % len(payload))
    if not 0 <= cycles <= 0xFFFF:
        raise ValueError("cycle budget out of range")
    return struct.pack(_FMT, MAGIC, FORMAT_VERSION, block_type, len(payload), cycles) + payload


def unpack(data):
    """Return (block_type, cycles, payload). Raises ValueError on bad data."""
    if len(data) < HDR_SIZE:
        raise ValueError("block too short")
    magic, ver, btype, length, cycles = struct.unpack(_FMT, data[:HDR_SIZE])
    if magic != MAGIC:
        raise ValueError("bad magic")
    if ver != FORMAT_VERSION:
        raise ValueError("format version %d, expected %d" % (ver, FORMAT_VERSION))
    payload = data[HDR_SIZE:HDR_SIZE + length]
    if len(payload) != length:
        raise ValueError("truncated payload")
    return btype, cycles, payload


# --- speaker audio player (runtime/sequencer.s) ---
# NTSC Apple II average CPU clock and the player's fixed cycles per sample.
# The converter derives its sample rate from these, and writes
# SPEAKER_PLAYER_CYCLES into the block header's cycle field.
CPU_HZ = 1020484
SPEAKER_PLAYER_CYCLES = 128
SPEAKER_RATE = CPU_HZ / SPEAKER_PLAYER_CYCLES   # ~7972 Hz


# --- where sound lives ---
# Main RAM from $6000 to $BFFF. With a language card in slot 0 (a2kit lcprobe
# checks for one) it carries on from $D000 to $FEFF in bank 2. The top page is
# left alone because it holds the 6502's vectors. Must match splay in speaker.inc.
AUDIO_START = 0x6000
AUDIO_MAIN_END = 0xC000         # exclusive
CARD_START = 0xD000
CARD_END = 0xFF00               # exclusive
AUDIO_PAGES = (AUDIO_MAIN_END - AUDIO_START) // 256
AUDIO_PAGES_CARD = AUDIO_PAGES + (CARD_END - CARD_START) // 256


def audio_pages_limit(language_card=False):
    return AUDIO_PAGES_CARD if language_card else AUDIO_PAGES


def audio_addr(offset):
    """The address of this many bytes into the sound area."""
    main = AUDIO_MAIN_END - AUDIO_START
    return AUDIO_START + offset if offset < main else CARD_START + offset - main


def audio_pieces(offset, data):
    """Split sound starting this far into the sound area into (address, bytes)
    pieces to upload, hopping over $C000-$CFFF."""
    main = AUDIO_MAIN_END - AUDIO_START
    if offset < main < offset + len(data):
        split = main - offset
        return [(audio_addr(offset), data[:split]), (CARD_START, data[split:])]
    return [(audio_addr(offset), data)]


def add_language_card_arg(ap):
    ap.add_argument("--language-card", action="store_true",
                    help="use the RAM of a language card in slot 0 (check with: a2kit lcprobe)")


# --- streamed blocks (video) ---
# Too big for a 16-bit payload length, so the header's length field holds the
# frame count instead. File layout after the 8-byte header:
#   u8 fps, then per frame: u16 little-endian size, then that many stream bytes,
#   then optionally "AU", u16 length, and the audio payload (4-bit samples, see
#   audioconv.py) that the player expects in RAM at $6000 before the stream starts,
#   then optionally "PL", u16 address, u16 length, and the fast blocks the player
#   expects there (video --preload; see parse_preload).
def stream_header(block_type, nframes, cycles=0):
    return struct.pack(_FMT, MAGIC, FORMAT_VERSION, block_type, nframes, cycles)


def parse_stream(data):
    """Return (fps, [frame bytes, ...], audio bytes or b"") for a TYPE_VIDEO file."""
    if len(data) < HDR_SIZE + 1:
        raise ValueError("file too short")
    magic, ver, btype, nframes, _cycles = struct.unpack(_FMT, data[:HDR_SIZE])
    if magic != MAGIC or ver != FORMAT_VERSION or btype != TYPE_VIDEO:
        raise ValueError("not an a2kit video file")
    fps = data[HDR_SIZE]
    frames, pos = [], HDR_SIZE + 1
    for _ in range(nframes):
        (n,) = struct.unpack_from("<H", data, pos)
        frames.append(data[pos + 2:pos + 2 + n])
        pos += 2 + n
    audio = b""
    if data[pos:pos + 2] == b"AU":
        (n,) = struct.unpack_from("<H", data, pos + 2)
        audio = data[pos + 4:pos + 4 + n]
    return fps, frames, audio


def parse_preload(data):
    """(address, bytes) of the fast blocks a TYPE_VIDEO file preloads, or None.
    They follow the frames and any audio as "PL", u16 address, u16 length, data."""
    _fps, frames, audio = parse_stream(data)
    pos = HDR_SIZE + 1 + sum(2 + len(f) for f in frames) + (4 + len(audio) if audio else 0)
    if data[pos:pos + 2] != b"PL":
        return None
    addr, n = struct.unpack_from("<HH", data, pos + 2)
    return addr, data[pos + 6:pos + 6 + n]


def preload_mem(pre):
    """A 64K memory image holding the preloaded blocks (for timing and checks)."""
    mem = bytearray(0x10000)
    if pre:
        mem[pre[0]:pre[0] + len(pre[1])] = pre[1]
    return mem


def frame_audio_pages(frame):
    """Pages of audio the player will play in this frame (walks the stream
    grammar: $FE frame start, $7F n c audio burst, $01-$7E n literal bytes,
    $FB frame start on page 2, $81-$FA skip, $FC/$FD palette bit off/on,
    $FF lo hi long skip, and the fast block ops below)."""
    return sum(n for kind, n, _i in walk_ops(frame) if kind == "audio")


# --- fast blocks (runtime/video.s) ---
# $7F n c is an audio burst when c = n ^ OP_AUDIO. Right after a frame start
# that changes page, c = n ^ OP_BLOCK sends a fast block of 2n bytes, which the
# player stores while it waits for the vertical blank and draws after the flip.
# $7F l c h with c = l ^ OP_PRELOAD draws the block preloaded at preload_addr(l, h).
OP_AUDIO, OP_BLOCK, OP_PRELOAD = 0x55, 0x2A, 0x3C
FBUF = 0x1300                   # where the player stores a fast block (video.s FBUF)
BLOCK_END = 0xA5                # the last byte of a block after a page change (video.s SENT)
PRELOAD_START = 0x6000          # preloaded blocks: main RAM after the sound, to $BFFF
PRELOAD_END = 0xC000
FLIP_WAIT = 0.019               # a fast block after a page change lasts at least this long on the line
FAST_MARGIN = 0.001             # extra quiet time after a fast block is drawn, seconds
FLIP_QUIET = 0.020              # quiet time after a page change without a fast block: one screen (16.7 ms) plus a little
BLOCK_OVERHEAD = 120            # cycles: the last block byte read (up to one 51-cycle pass), into fast, and back to the line
PRELOAD_OVERHEAD = 100          # cycles: the op's last byte to fast, and back to the line


def block_bytes(baud):
    """Bytes in the fast block after a page change: enough to outlast the
    vertical-blank wait (always even, at most 254)."""
    n = -(-int(FLIP_WAIT * baud / 10) // 2)
    return min(254, 2 * n)


def preload_addr(lo, hi):
    return PRELOAD_START + 4 * (lo + 128 * hi)


def preload_op(addr):
    """The 4-byte op that draws the block preloaded at addr (a multiple of 4)."""
    off = (addr - PRELOAD_START) // 4
    lo, hi = off & 0x7F, off >> 7
    return bytes([0x7F, lo, lo ^ OP_PRELOAD, hi])


def walk_ops(frame):
    """(kind, value, index after the op) for each op in a frame: "mark" (page),
    "audio" (pages), "block" (bytes, which follow the op), "preload" (address),
    "run" (bytes), "skip" (bytes), "pal"."""
    i = 0
    while i < len(frame):
        b = frame[i]
        i += 1
        if b in (0xFE, 0xFB):
            yield "mark", 1 if b == 0xFE else 2, i
        elif b == 0x7F and i + 1 < len(frame):
            n, c = frame[i], frame[i + 1]
            if c == n ^ OP_BLOCK:
                i += 2 + 2 * n
                yield "block", 2 * n, i
            elif c == n ^ OP_PRELOAD and i + 2 < len(frame):
                i += 3
                yield "preload", preload_addr(n, frame[i - 1]), i
            else:
                i += 2
                yield "audio", n, i
        elif b == 0xFF:
            if i + 2 > len(frame):
                break
            i += 2
            yield "skip", frame[i - 2] + 128 * frame[i - 1], i
        elif 0 < b < 0x7F:
            i += b
            yield "run", b, i
        elif 0x80 < b < 0xFB:
            yield "skip", b & 0x7F, i
        elif b in (0xFC, 0xFD):
            yield "pal", b, i


def fast_cycles(mem, addr, ptr, top):
    """Cycles video.s takes to draw the fast block at `addr` of `mem` (anything
    indexable by address) from screen address `ptr` on the page that ends at
    `top` ($40 or $60), from jsr fast to its rts; and the address it leaves.
    Counts every instruction of fast/fent/fnext/fend, page crossings included
    (for an entry the player refuses, up to where it gives up)."""
    c = 6 + 26
    q, lim, bp = (ptr - 2) & 0xFFFF, top - 1, addr
    blim = (addr >> 8) + 1 if addr == FBUF else PRELOAD_END >> 8
    while True:
        s, e = mem[bp], mem[bp + 1]
        c += 2 + 5 + 2 + 3 + 3
        c += 7 if (q & 0xFF) + s > 0xFF else 3
        q = (q + s) & 0xFFFF
        c += 2 + 5 + ((bp & 0xFF) + 1 > 0xFF) + 2
        if e < 3:
            c += 3 + 2 + 2
            if e == 0:
                c += 2
                break
            c += 3
            y = 2
        else:
            c += 2 + 3 + 3 + 3
            qh = q >> 8
            if qh >= lim:
                if qh != lim or (q & 0xFF) + e > 0x100:     # the player refuses it (fbad) and gives up the block
                    return c + 3 + 3 + 6, (q + 2) & 0xFFFF
                c += 3 + 2 + 3 + 2 + 3 + (3 if (q & 0xFF) + e < 0x100 else 5)
            else:
                c += 2
            c += 2
            for y in range(2, e):
                c += 5 + ((bp & 0xFF) + y > 0xFF) + 6 + 2 + 3 + (3 if y + 1 < e else 2)
            y = e
        c += 2 + 2 + 3 + 3
        if (bp & 0xFF) + y > 0xFF:
            bp += y
            c += 2 + 5 + 3 + 3
            if (bp >> 8) >= blim:
                c += 2 + 3
                break
            c += 3
        else:
            bp += y
            c += 3
    c += 2 + 3 + 2 + 3 + 3 + 2 + 3 + 6
    return c, (q + 2) & 0xFFFF


def frame_waits(frame, page, mem=None):
    """Where the host must go quiet inside this frame because the player is
    drawing a fast block, and for how long: ([(index, seconds)], page after).
    `page` is the page drawn before the frame; `mem` holds the preloaded
    blocks. A page change without a block (a stream from before blocks) gets
    the plain vertical-blank wait after the mark and the byte it reads next."""
    cuts, ptr, top = [], 0x2000, 0x40
    ops = list(walk_ops(frame))
    for k, (kind, v, end) in enumerate(ops):
        if kind == "mark":
            changed = v != page
            page, ptr, top = v, 0x2000 * v, 0x20 + 0x20 * v
            if changed:
                nxt = ops[k + 1] if k + 1 < len(ops) else None
                if nxt and nxt[0] == "block":
                    blk = frame[end + 3:nxt[2]]
                    cyc, ptr = fast_cycles(FastMem(blk), FBUF, ptr, top)
                    cuts.append((nxt[2], (cyc + BLOCK_OVERHEAD) / CPU_HZ + FAST_MARGIN))
                else:
                    cuts.append((min(end + 1, len(frame)), FLIP_QUIET))
        elif kind == "preload" and v < PRELOAD_END:         # (the player refuses the others)
            cyc, ptr = fast_cycles(mem if mem is not None else preload_mem(None), v, ptr, top)
            cuts.append((end, (cyc + PRELOAD_OVERHEAD) / CPU_HZ + FAST_MARGIN))
        elif kind == "run":
            ptr += v
        elif kind == "skip":
            ptr += v
    return cuts, page


class FastMem:
    """A fast block as it sits in memory at `at` (the player's buffer at FBUF by default)."""
    def __init__(self, blk, at=FBUF):
        self.blk, self.at = blk, at

    def __getitem__(self, a):
        k = a - self.at
        return self.blk[k] if 0 <= k < len(self.blk) else 0


# Line speeds the receiver can be built for (runtime/config.inc). The Makefile
# builds 115200 (receiver.bin, a2kit-boot.dsk) and 19200 (receiver-19200.bin,
# a2kit-boot-19200.dsk). Set A2KIT_BAUD=19200 once instead of passing --baud.
BAUDS = (115200, 19200, 9600, 4800, 2400, 1200)
DEFAULT_BAUD = 115200


def _baud(text):
    try:
        b = int(text)
    except ValueError:
        b = None
    if b not in BAUDS:
        raise argparse.ArgumentTypeError("%s is not a speed the receiver can use (%s)" % (
            text, ", ".join(str(x) for x in BAUDS)))
    return b


def default_baud():
    """A2KIT_BAUD if set, else DEFAULT_BAUD."""
    env = os.environ.get("A2KIT_BAUD")
    if not env:
        return DEFAULT_BAUD
    try:
        return _baud(env)
    except argparse.ArgumentTypeError as e:
        raise SystemExit("A2KIT_BAUD: %s" % e) from None


def add_baud_arg(ap):
    """--baud for every command that talks to (or plans for) the receiver."""
    d = default_baud()
    ap.add_argument("--baud", type=_baud, default=d,
                    help="line speed; must match the number in the receiver's banner "
                         "(default %d%s)" % (d, ", from A2KIT_BAUD" if os.environ.get("A2KIT_BAUD") else "; or set A2KIT_BAUD"))
