#!/usr/bin/env python3
"""videoconv: turn a video / animated GIF / folder of frames into an a2kit
delta stream for runtime/video.s.

Why deltas: a screen is 8 KB but the serial link moves ~1.9 KB/s at 19200 baud
(~11.5 KB/s at 115200).
So each frame sends only the screen bytes that changed, as many as fit in a
per-frame byte budget, most-changed first. Whatever doesn't fit stays pending
and is sent on later frames, so the picture converges instead of dropping
detail permanently. Ordered (Bayer) dithering is the default because, unlike
error diffusion, it doesn't shimmer between frames and keeps the deltas small.

  videoconv.py clip.gif -o clip.a2v --fps 6
  videoconv.py frames/ -o clip.a2v --fps 8 --baud 115200
  videoconv.py movie.mp4 -o clip.a2v --fps 6      (needs ffmpeg on PATH)

Scrub (--scrub N): every frame also re-sends N screen bytes from a cursor that
sweeps the whole screen, so pixels left wrong by a lost serial byte get
repainted eventually (a full sweep takes 8192/N frames) at a cost of ~N bytes
of each frame's budget. Off by default; 8-16 is cheap insurance on a flaky link.

Audio (--audio clip.wav): the speaker player can't listen to the serial port,
so audio plays in bursts between frames while the video stream pauses. It
keeps its place between bursts, so nothing is dropped; the show just gets
longer by the audio's duration. Audio lives in RAM ($6000-$BFFF, ~6 s max), or
~9 s with --language-card and a language card in slot 0.
--burst-pages sets how many 64 ms pages play per burst (default 1; raised
automatically if there are more bursts than frames).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageSequence

import a2hdr
import audioconv
import imgconv
import stream

W, H = 280, 192
OFFS = np.array([imgconv.hgr_offset(y) for y in range(H)])
IDX = OFFS[:, None] + np.arange(40)[None, :]
WEIGHTS = (1 << np.arange(7)).astype(np.uint8)
POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def bayer(n):
    m = np.array([[0]])
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return m


THRESH = np.tile((bayer(8) + 0.5) / 64 * 255, (H // 8, W // 8))


def to_bits(gray, dither):
    if dither == "ordered":
        return (gray > THRESH).astype(np.uint8)
    if dither == "threshold":
        return (gray >= 128).astype(np.uint8)
    img = Image.fromarray(gray.astype(np.uint8), "L")
    return np.array(imgconv.atkinson(img), dtype=np.uint8)


def pack_screen(bits):
    """280x192 bit grid -> 8192-byte HGR screen in memory order."""
    rows = (bits.reshape(H, 40, 7) * WEIGHTS).sum(axis=2).astype(np.uint8)
    scr = np.zeros(0x2000, dtype=np.uint8)
    scr[IDX] = rows
    return scr


def natural_key(name):
    """Sort "f2.png" before "f10.png"."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def load_frames(path, fps, src_fps, max_seconds):
    """Return a list of PIL images at the target fps."""
    if not os.path.exists(path):
        sys.exit("%s: no such file or folder" % path)
    tmp = None
    if os.path.isdir(path):
        names = sorted((n for n in os.listdir(path) if n.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif"))), key=natural_key)
        if not names:
            sys.exit("%s: no .png/.jpg/.bmp/.gif frames in this folder" % path)
        src = (Image.open(os.path.join(path, n)).convert("RGB") for n in names)
    else:
        img = Image.open(path) if path.lower().endswith((".gif", ".webp", ".png", ".apng")) else None
        if img is not None and getattr(img, "n_frames", 1) > 1:
            src = (f.convert("RGB") for f in ImageSequence.Iterator(img))
        elif shutil.which("ffmpeg"):
            tmp = tempfile.mkdtemp(prefix="a2kit-")
            r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-vf",
                                "fps=%g,scale=%d:%d:force_original_aspect_ratio=decrease" % (src_fps, W, H),
                                os.path.join(tmp, "f%05d.png")], capture_output=True, text=True)
            if r.returncode != 0:
                shutil.rmtree(tmp, ignore_errors=True)
                sys.exit("%s: ffmpeg couldn't read it: %s" % (path, r.stderr.strip()[-300:]))
            src = (Image.open(os.path.join(tmp, n)).convert("RGB") for n in sorted(os.listdir(tmp)))
        else:
            sys.exit("%s: not an animated image and ffmpeg isn't on PATH" % path)
    step = src_fps / fps
    limit = max(1, int(max_seconds * fps)) if max_seconds else None
    out, next_pick = [], 0.0
    try:
        for i, f in enumerate(src):
            if i + 1e-9 >= next_pick:
                out.append(f)
                next_pick += step
                if limit is not None and len(out) >= limit:
                    break
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def _runs(addrs, merge_gap):
    runs = []
    start = prev = int(addrs[0])
    for a in addrs[1:]:
        a = int(a)
        if a - (prev + 1) <= merge_gap:
            prev = a
        else:
            runs.append((start, prev + 1))
            start = prev = a
    runs.append((start, prev + 1))
    return runs


def _emit(runs, target):
    """Frame = $FE, then skip/literal ops. Every data byte is < $80 (HGR bit 7 is
    always clear) and every control byte is >= $80, so the player can tell when
    it has lost sync; $FE is the resync mark. Skips are 1-125 ($81-$FD); longer
    ones are $FF lo hi with 7-bit lo/hi."""
    out = bytearray([0xFE])
    pos = 0
    for s, e in runs:
        gap = s - pos
        if gap > 125:
            out += bytes([0xFF, gap & 0x7F, gap >> 7])
        elif gap > 0:
            out.append(0x80 | gap)
        for c in range(s, e, 126):          # $7F is the audio-burst op, so runs stop at 126
            chunk = target[c:min(c + 126, e)]
            out.append(len(chunk))
            out += bytes(chunk)
        pos = e
    return bytes(out)


def encode_frame(shown, target, budget, merge_gap=2, forced=None):
    """Return (frame bytes <= budget, new `shown` state).

    `forced` is an optional sorted array of screen addresses that are always
    sent (the scrub window); the changed bytes then fill whatever budget is
    left, most-changed first."""
    forced = np.zeros(0, dtype=np.int64) if forced is None else np.asarray(forced, dtype=np.int64)
    diff = np.nonzero(shown != target)[0]
    if len(diff) == 0 and len(forced) == 0:
        return b"\xFE", shown
    score = POP[shown[diff] ^ target[diff]]
    cand = diff[np.lexsort((diff, -score.astype(np.int16)))]    # most changed first

    def build(k):
        addrs = np.unique(np.concatenate([forced, cand[:k]]))
        if len(addrs) == 0:
            return b"\xFE", []
        runs = _runs(addrs, merge_gap)
        return _emit(runs, target), runs

    if len(build(0)[0]) > budget:
        raise ValueError("scrub window doesn't fit in the %d-byte frame budget; lower --scrub or raise --budget" % budget)
    lo, hi = 0, len(cand)
    while lo < hi:                      # largest k whose encoding fits
        mid = (lo + hi + 1) // 2
        if len(build(mid)[0]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    data, runs = build(lo)
    new = shown.copy()
    for s_, e_ in runs:
        new[s_:e_] = target[s_:e_]
    return data, new


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--fps", type=int, default=6, help="playback frames per second, a whole number 1-60 (default 6)")
    ap.add_argument("--src-fps", type=float, help="frame rate of the input (default: same as --fps)")
    a2hdr.add_baud_arg(ap)
    ap.add_argument("--budget", type=int, help="bytes per frame (default: what the baud rate allows)")
    ap.add_argument("--dither", choices=["ordered", "atkinson", "threshold"], default="ordered")
    ap.add_argument("--crop", action="store_true")
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--scrub", type=int, default=0, help="screen bytes re-sent every frame as a rotating refresh (default 0 = off)")
    ap.add_argument("--audio", help="WAV file, played in bursts between frames")
    ap.add_argument("--burst-pages", type=int, default=1, help="64 ms pages of audio per burst (default 1)")
    a2hdr.add_language_card_arg(ap)
    a = ap.parse_args()

    if not 1 <= a.fps <= 60:
        sys.exit("--fps must be a whole number from 1 to 60")
    if a.src_fps is not None and a.src_fps <= 0:
        sys.exit("--src-fps must be positive")
    if a.burst_pages < 1:
        sys.exit("--burst-pages must be at least 1")
    budget = a.budget or int(a.baud / 10 / a.fps * 0.95)
    if budget < 8 or budget > 0xFFFF:
        sys.exit("bad budget %d" % budget)
    imgs = load_frames(a.input, a.fps, a.src_fps or a.fps, a.max_seconds)
    if not imgs:
        sys.exit("no frames read")

    audio, bursts = b"", {}
    if a.audio:
        audio = audioconv.convert(a.audio)
        P = len(audio) // 256
        if P > a2hdr.audio_pages_limit(a.language_card):
            sys.exit("audio is %d pages; only %d fit. Use a shorter clip%s." % (
                P, a2hdr.audio_pages_limit(a.language_card),
                "" if a.language_card else ", or --language-card if you have one in slot 0"))
        B = min(len(imgs), -(-P // max(1, a.burst_pages)))      # number of bursts
        for i in range(B):
            bursts[(i * len(imgs)) // B] = P // B + (1 if i < P % B else 0)

    if not 0 <= a.scrub < 0x2000:
        sys.exit("--scrub must be 0-8191")
    shown = np.zeros(0x2000, dtype=np.uint8)
    frames, pending = [], []
    cursor = 0
    for i, img in enumerate(imgs):
        gray = np.asarray(imgconv.prepare(img, a.crop, False), dtype=np.float64)
        target = pack_screen(to_bits(gray, a.dither))
        n = bursts.get(i, 0)
        window = (cursor + np.arange(a.scrub)) % 0x2000
        cursor = (cursor + a.scrub) % 0x2000
        data, shown = encode_frame(shown, target, budget - (3 if n else 0), forced=np.sort(window))
        if n:
            data += bytes([0x7F, n, n ^ 0x55])
        frames.append(data)
        pending.append(int(np.count_nonzero(shown != target)))

    blob = bytearray(a2hdr.stream_header(a2hdr.TYPE_VIDEO, len(frames)))
    blob.append(a.fps)
    for f in frames:
        blob += len(f).to_bytes(2, "little") + f
    if audio:
        blob += b"AU" + len(audio).to_bytes(2, "little") + audio
    open(a.output, "wb").write(blob)
    total = sum(len(f) for f in frames)
    _, play_s = stream.plan_stream(frames, a.fps, a.baud)
    if a.scrub:
        print("scrub: %d bytes/frame, a full sweep every %d frames (%.0f s)" % (
            a.scrub, -(-0x2000 // a.scrub), -(-0x2000 // a.scrub) / a.fps), file=sys.stderr)
    if audio:
        print("audio: %d pages (%.1f s) in %d bursts between frames" % (
            len(audio) // 256, len(audio) * 2 / a2hdr.SPEAKER_RATE, len(bursts)), file=sys.stderr)
    print("%d frames, %d stream bytes (avg %.0f/frame, budget %d); plays in about %.1f s at %d baud; "
          "unsent screen bytes after last frame: %d" % (len(frames), total, total / len(frames), budget,
                                                         play_s, a.baud, pending[-1]), file=sys.stderr)

if __name__ == "__main__":
    main()
