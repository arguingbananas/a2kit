#!/usr/bin/env python3
"""audioconv: convert a WAV file to the Apple II speaker player's format.

4-bit PWM samples, two per byte (high nibble first), padded to whole 256-byte
pages, at ~7972 Hz. At most 65280 bytes (~16 s) per block; the whole show has
to fit in RAM, so in practice a few seconds.

  audioconv.py clip.wav -o clip.a2b
  audioconv.py clip.wav -o clip.a2b --max-seconds 5 --gain 1.5
  audioconv.py speech.wav -o speech.a2b --even-out
  audioconv.py clip.wav -o clip.a2b --preview clip_apple.wav

--preview also writes a WAV of what the Apple II's speaker will produce: the
player's exact 1-bit clicks, worked out cycle by cycle, averaged to 44.1 kHz.
It includes the whine at the 7,972 Hz sample rate. A real speaker and its
cabinet soften the top end a little more.
"""
import argparse
import sys
import wave

import numpy as np

import a2hdr

MAX_BYTES = 0xFF00


def load_wav(path):
    try:
        with wave.open(path, "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except FileNotFoundError:
        sys.exit("%s: no such file" % path)
    except (wave.Error, EOFError) as e:
        sys.exit("%s: can't read this WAV (%s). Save it as 8- or 16-bit PCM WAV." % (path, e))
    if n == 0:
        sys.exit("%s: the WAV has no samples" % path)
    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    else:
        sys.exit("%s: %d-bit WAV; only 8- and 16-bit PCM are supported" % (path, width * 8))
    x = x.reshape(-1, ch).mean(axis=1)
    return x, rate


def lowpass(x, rate, cutoff, taps=255):
    t = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff / rate * t) * np.hamming(taps)
    return np.convolve(x, h / h.sum(), mode="same")


def resample(x, rate, target):
    if rate > target:
        x = lowpass(x, rate, 0.45 * target)
    n_out = int(len(x) * target / rate)
    t_out = np.arange(n_out) * rate / target
    return np.interp(t_out, np.arange(len(x)), x)


def compress(x, rate, highpass=150.0, threshold_db=-40.0, ratio=10.0, release=0.1):
    """Even out speech for the 4-bit speaker. Speech has rare loud peaks and is mostly
    much quieter, and the samples are scaled to the loudest peak, so most of it
    would use only a level or two either side of the middle. This cuts the bass the
    speaker can't play, then turns down everything above threshold_db (relative to
    the peak) by ratio. The level it follows jumps up at once on a peak, so no peak
    slips through to set the scale, and falls back over `release` seconds. The
    caller scales the result to full volume afterwards."""
    if not len(x):
        return x
    x = x - lowpass(x, rate, highpass)
    peak = np.max(np.abs(x))
    if peak == 0:
        return x
    x = x / peak
    down = 1 - np.exp(-1 / (release * rate))
    env, level = np.empty(len(x)), 0.0
    for i, v in enumerate(np.abs(x)):
        level = v if v > level else level + (v - level) * down
        env[i] = level
    over = np.maximum(20 * np.log10(np.maximum(env, 1e-6)) - threshold_db, 0)
    return x * 10 ** (-over * (1 - 1 / ratio) / 20)


def quantize(x, dither=True, seed=1):
    v = (x + 1.0) / 2.0 * 15.0
    if dither:
        rng = np.random.default_rng(seed)
        v = v + rng.random(len(v)) - rng.random(len(v))   # triangular, +-1 LSB
    return np.clip(np.rint(v), 0, 15).astype(np.uint8)


def pack_nibbles(n):
    n = n.astype(np.uint8)
    if len(n) % 2:
        n = np.append(n, np.uint8(8))
    data = bytearray(((n[0::2] << 4) | n[1::2]).astype(np.uint8).tobytes())
    while len(data) % 256:
        data.append(0x88)            # silence (mid level)
    return bytes(data)


PREVIEW_RATE = 44100
PAGE_SAMPLES = 512                 # samples per 256-byte page
PAGE_EXTRA = 12                    # extra cycles the player spends moving to the next page


def unpack_nibbles(data):
    b = np.frombuffer(bytes(data), np.uint8)
    return np.stack([b >> 4, b & 15], axis=1).ravel()


def speaker_edges(data):
    """The cycles at which the player clicks the speaker, two clicks a sample. Sample
    n is a pulse 5(n+1)+3 cycles wide, one every 128 cycles, with 12 more at each
    page change (runtime/speaker.inc; tools/test_sequencer.py checks this against
    the real player, click for click)."""
    n = unpack_nibbles(data).astype(np.int64)
    i = np.arange(len(n), dtype=np.int64)
    first = i * a2hdr.SPEAKER_PLAYER_CYCLES + (i // PAGE_SAMPLES) * PAGE_EXTRA
    return first, first + 5 * (n + 1) + 3


def speaker_preview(data, rate=PREVIEW_RATE):
    """16-bit mono samples of the speaker's sound for this player data: the 1-bit
    signal at cycle resolution, averaged over each output sample, without its DC."""
    first, second = speaker_edges(data)
    total = int(second[-1]) + a2hdr.SPEAKER_PLAYER_CYCLES
    step = np.zeros(total + 1, np.int8)
    step[first] += 1
    step[second] -= 1
    high = np.cumsum(np.cumsum(step[:-1], dtype=np.int32), dtype=np.int64)  # cycles high so far
    cuts = np.rint(np.arange(1, int(total * rate / a2hdr.CPU_HZ)) * a2hdr.CPU_HZ / rate).astype(np.int64)
    level = np.diff(np.concatenate(([0], high[cuts - 1]))) / np.diff(np.concatenate(([0], cuts)))
    level = level - level.mean()
    a = np.exp(-2 * np.pi * 20.0 / rate)     # the cone doesn't hold a steady offset: a 20 Hz high-pass
    out = np.empty_like(level)
    prev_in = prev_out = 0.0
    for k, v in enumerate(level):
        prev_out = a * (prev_out + v - prev_in)
        prev_in = v
        out[k] = prev_out
    peak = np.max(np.abs(out)) or 1.0
    return (out / peak * 0.9 * 32767).astype("<i2")


def write_preview(path, data, rate=PREVIEW_RATE):
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(speaker_preview(data, rate).tobytes())
    w.close()


def convert(path, gain=1.0, max_seconds=None, dither=True, even_out=False):
    x, rate = load_wav(path)
    x = resample(x, rate, a2hdr.SPEAKER_RATE)
    if even_out:
        if max_seconds:
            x = x[: int(max_seconds * a2hdr.SPEAKER_RATE)]     # only work on what's kept
        x = compress(x, a2hdr.SPEAKER_RATE)
    peak = np.max(np.abs(x)) if len(x) else 0
    if peak > 0:
        x = x / peak * 0.95
    x = np.clip(x * gain, -1, 1)
    if max_seconds:
        x = x[: int(max_seconds * a2hdr.SPEAKER_RATE)]
    data = pack_nibbles(quantize(x, dither))
    if len(data) > MAX_BYTES:
        sys.exit("audio is %d bytes after packing; limit is %d (use --max-seconds)" % (len(data), MAX_BYTES))
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--no-dither", action="store_true")
    ap.add_argument("--even-out", action="store_true", help="even out the volume (a compressor); makes speech much clearer")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--preview", metavar="WAV", help="also write what the Apple II's speaker will produce, as a WAV")
    a = ap.parse_args()
    data = convert(a.input, a.gain, a.max_seconds, not a.no_dither, a.even_out)
    out = data if a.raw else a2hdr.pack(a2hdr.TYPE_AUDIO, data, a2hdr.SPEAKER_PLAYER_CYCLES)
    open(a.output, "wb").write(out)
    print("wrote %s: %d bytes, %.1f s" % (a.output, len(out), len(data) * 2 / a2hdr.SPEAKER_RATE), file=sys.stderr)
    if a.preview:
        write_preview(a.preview, data)
        print("wrote %s: what the Apple II's speaker will produce" % a.preview, file=sys.stderr)


if __name__ == "__main__":
    main()
