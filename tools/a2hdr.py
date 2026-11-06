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

MAGIC = b"A2"
FORMAT_VERSION = 1
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
#   audioconv.py) that the player expects in RAM at $6000 before the stream starts.
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


def frame_audio_pages(frame):
    """Pages of audio the player will play in this frame (walks the stream
    grammar: $FE frame start, $7F n c audio burst, $01-$7E n literal bytes,
    $81-$FD skip, $FF lo hi long skip)."""
    i, pages = 0, 0
    while i < len(frame):
        b = frame[i]
        i += 1
        if b == 0x7F:
            pages += frame[i]
            i += 2                      # page count, then its check byte
        elif b == 0xFF:
            i += 2
        elif 0 < b < 0x7F:
            i += b
    return pages


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
        raise SystemExit("A2KIT_BAUD: %s" % e)


def add_baud_arg(ap):
    """--baud for every command that talks to (or plans for) the receiver."""
    d = default_baud()
    ap.add_argument("--baud", type=_baud, default=d,
                    help="line speed; must match the number in the receiver's banner "
                         "(default %d%s)" % (d, ", from A2KIT_BAUD" if os.environ.get("A2KIT_BAUD") else "; or set A2KIT_BAUD"))
