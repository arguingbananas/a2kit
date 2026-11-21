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

Colour (--colour): each frame is converted as `image --colour` does, in the
six hi-res colours (dither threshold by default, or atkinson). The palette
bits travel as $FC/$FD ops, so black and white streams are unchanged. Flat
areas and motion in steps of 2 dots work best; converting takes about a
second a frame.

Page flipping (--flip): without it the player changes the screen while it's
being shown, so during busy moments the picture shows old and new mixed
(tearing). With it, each frame is drawn on the hidden hi-res page and the
player switches to it only once it's complete: when a frame doesn't fit its
budget, the next frame's time goes on finishing the same page (the frames
that come and go meanwhile are skipped), so the screen only ever shows whole
frames of the clip, and busy moments play at a lower frame rate instead of
mixing pictures. Each page catches up from two frames back, so busy scenes
need roughly twice the bytes. The picture shows one frame later, and one more
frame mark at the end shows the last frame. Each flip waits for the screen's
vertical blank (found through the floating bus, with a marker in the screen
holes), so it never lands mid-screen; the host stays quiet for up to 20 ms
meanwhile, which comes out of the budget of each frame that flips.

Audio (--audio clip.wav): the speaker player can't listen to the serial port,
so audio plays in bursts between frames while the video stream pauses. It
keeps its place between bursts, so nothing is dropped; the show just gets
longer by the audio's duration. Audio lives in RAM ($6000-$BFFF, ~6 s max), or
~9 s with --language-card and a language card in slot 0.
--burst-pages sets how many 64 ms pages play per burst (default 1; raised
automatically if there are more bursts than frames).

Preview (--preview clip.gif): an animated GIF of the screen exactly as the
player leaves it after each frame, including detail still catching up, at the
speed it plays: each frame lasts 1/fps, plus the sound burst played after it.
a2kit audio --preview renders the sound itself.
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
# The screen holes ($xx78-$xx7F and $xxF8-$xxFF, never shown) hold MARK, which
# runtime/video.s puts there for its vertical-blank wait; no shown byte may equal it.
MARK = 0x9B
HOLES = (np.arange(0x2000) & 0x7F) >= 0x78


def blank():
    """A page as the player leaves it after clearing: black, MARK in the holes."""
    page = np.zeros(0x2000, dtype=np.uint8)
    page[HOLES] = MARK
    return page


def with_holes(target):
    """target with MARK in the holes, and any shown byte that equals MARK changed by one dot."""
    t = np.array(target, dtype=np.uint8)
    t[(t == MARK) & ~HOLES] ^= 1
    t[HOLES] = MARK
    return t


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


def screen_image(scr):
    """8192-byte HGR screen in memory order -> 280x192 black-and-white image."""
    rows = scr[IDX]
    bits = (rows[:, :, None] & WEIGHTS) > 0
    return Image.fromarray(bits.reshape(H, W).astype(np.uint8) * 255, "L").convert("1")


def write_preview(path, screens, durations, colour=False):
    """An animated GIF of these screens; durations in seconds."""
    imgs = [imgconv.colour_preview(bytes(s)) if colour else screen_image(s) for s in screens]
    # GIF stores times in 10 ms steps: round the running total, not each frame, so the
    # rounding doesn't add up over a long clip
    ends = np.rint(np.cumsum(durations) * 100).astype(int) * 10
    ms = [max(20, int(e - s)) for s, e in zip(np.concatenate(([0], ends[:-1])), ends)]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=ms, loop=0, disposal=1)


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


def _emit(runs, target, mark=0xFE):
    """Frame = a frame mark ($FE draws on page 1, $FB on page 2), then skip/literal
    ops. Every data byte is < $80 and every control byte is >= $80, so the player
    can tell when it has lost sync; the marks are the resync points. Skips are
    1-122 ($81-$FA); longer ones are $FF lo hi with
    7-bit lo/hi. A screen byte's bit 7 (its palette bit, in colour) is sent as
    state instead: $FD turns it on for the literal bytes that follow, $FC off,
    and each frame starts with it off, so black and white frames never need it."""
    out = bytearray([mark])
    pos, pal = 0, 0
    for s, e in runs:
        gap = s - pos
        if gap > 122:
            out += bytes([0xFF, gap & 0x7F, gap >> 7])
        elif gap > 0:
            out.append(0x80 | gap)
        c = s
        while c < e:
            p = target[c] & 0x80
            if p != pal:
                out.append(0xFD if p else 0xFC)
                pal = p
            n = 1                           # the bytes from c with the same palette bit,
            while c + n < e and n < 126 and target[c + n] & 0x80 == p:     # at most 126 ($7F is the audio op)
                n += 1
            out.append(n)
            out += bytes(b & 0x7F for b in target[c:c + n])
            c += n
        pos = e
    return bytes(out)


def encode_frame(shown, target, budget, merge_gap=2, forced=None, mark=0xFE):
    """Return (frame bytes <= budget, new `shown` state). `shown` is what the
    page the frame draws on holds, and `mark` names that page ($FE or $FB).

    `forced` is an optional sorted array of screen addresses that are always
    sent (the scrub window); the changed bytes then fill whatever budget is
    left, most-changed first."""
    forced = np.zeros(0, dtype=np.int64) if forced is None else np.asarray(forced, dtype=np.int64)
    diff = np.nonzero(shown != target)[0]
    if len(diff) == 0 and len(forced) == 0:
        return bytes([mark]), shown
    score = POP[shown[diff] ^ target[diff]]
    cand = diff[np.lexsort((diff, -score.astype(np.int16)))]    # most changed first

    def build(k):
        addrs = np.unique(np.concatenate([forced, cand[:k]]))
        if len(addrs) == 0:
            return bytes([mark]), []
        runs = _runs(addrs, merge_gap)
        return _emit(runs, target, mark), runs

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
    ap.add_argument("--dither", choices=["ordered", "atkinson", "threshold"],
                    help="ordered (default), atkinson or threshold; with --colour, threshold (default) or atkinson")
    ap.add_argument("--colour", "--color", action="store_true", help="use the six hi-res colours, for a colour monitor or TV")
    ap.add_argument("--crop", action="store_true")
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--flip", action="store_true", help="draw each frame on the hidden page and flip to it when complete (no tearing; about twice the bytes)")
    ap.add_argument("--scrub", type=int, default=0, help="screen bytes re-sent every frame as a rotating refresh (default 0 = off)")
    ap.add_argument("--audio", help="WAV file, played in bursts between frames")
    ap.add_argument("--burst-pages", type=int, default=1, help="64 ms pages of audio per burst (default 1)")
    a2hdr.add_language_card_arg(ap)
    ap.add_argument("--gain", type=float, default=1.0, help="audio volume multiplier, as in `a2kit audio` (default 1.0)")
    ap.add_argument("--even-out", action="store_true", help="even out the audio's volume, as in `a2kit audio`; for speech")
    ap.add_argument("--preview", metavar="GIF", help="also write an animated GIF of the screen as the player draws it")
    a = ap.parse_args()

    a.dither = a.dither or ("threshold" if a.colour else "ordered")
    if a.colour and a.dither == "ordered":
        sys.exit("--colour works with --dither threshold or atkinson")
    if not 1 <= a.fps <= 60:
        sys.exit("--fps must be a whole number from 1 to 60")
    if a.src_fps is not None and a.src_fps <= 0:
        sys.exit("--src-fps must be positive")
    if a.burst_pages < 1:
        sys.exit("--burst-pages must be at least 1")
    if not a.audio and (a.gain != 1.0 or a.even_out):
        sys.exit("--gain and --even-out apply to the sound, so they need --audio")
    budget = a.budget or int(a.baud / 10 / a.fps * 0.95)
    flip_budget = a.budget or int(a.baud / 10 * (1.0 / a.fps - stream.FLIP_QUIET) * 0.95)  # a frame that flips
    if a.flip and 1.0 / a.fps <= stream.FLIP_QUIET:
        sys.exit("--flip leaves no time for the frame at %d frames a second; use a lower --fps" % a.fps)
    if min(budget, flip_budget if a.flip else budget) < 8 or budget > 0xFFFF:
        sys.exit("bad budget %d" % budget)
    imgs = load_frames(a.input, a.fps, a.src_fps or a.fps, a.max_seconds)
    if not imgs:
        sys.exit("no frames read")

    audio, bursts = b"", {}
    if a.audio:
        audio = audioconv.convert(a.audio, a.gain, even_out=a.even_out)
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
    pages = [blank(), blank()]          # what pages 1 and 2 hold
    frames, pending, screens = [], [], []
    cursors = [0, 0]
    p = 0                               # the page drawn last (page 1, black, when the player starts)
    goal, done, held = None, True, 0    # with --flip: the frame page p is heading for, and whether it's there

    def draw(target, bud, n):
        nonlocal p
        window = (cursors[p] + np.arange(a.scrub)) % 0x2000
        cursors[p] = (cursors[p] + a.scrub) % 0x2000
        data, pages[p] = encode_frame(pages[p], target, bud - (3 if n else 0), forced=np.sort(window),
                                      mark=0xFB if p else 0xFE)
        if n:
            data += bytes([0x7F, n, n ^ 0x55])
        frames.append(data)

    def screen_of(img):
        if a.colour:
            return with_holes(np.frombuffer(imgconv.colour_screen(imgconv.prepare(img, a.crop, False, "RGB"), a.dither), np.uint8))
        gray = np.asarray(imgconv.prepare(img, a.crop, False), dtype=np.float64)
        return with_holes(pack_screen(to_bits(gray, a.dither)))

    if not a.flip:
        for i, img in enumerate(imgs):
            target = screen_of(img)
            draw(target, budget, bursts.get(i, 0))
            pending.append(int(np.count_nonzero(pages[p] != target)))
            screens.append(pages[p].copy())
    else:
        # Even pacing: at each flip, pick the next picture so that it's due exactly
        # when it will go on screen. Finishing a page that is k frames' time away
        # can use the budget of k frames, so take the first frame ahead whose cost
        # (every byte it still needs, sent at once) fits the budget up to its own
        # turn. Then every picture appears at its right time, and motion keeps its
        # speed through busy moments. A page finished early waits for its turn.
        targets = [screen_of(img) for img in imgs]
        N = len(targets)
        flip_at, shown = 0, 0               # when the page being drawn may go on screen

        def cost(page, target):             # a lower bound: every byte it still needs, sent at once
            diff = np.nonzero(page != target)[0]
            return 1 + (len(_emit(_runs(diff, 2), target)) if len(diff) else 0)

        def finishes(page, target, k):      # exactly: does drawing it for k frames complete it?
            for s_ in range(k):
                _, page = encode_frame(page, target, (flip_budget if s_ == 0 else budget) - a.scrub - 3)
                if np.array_equal(page, target):
                    return True
            return False

        for i in range(N):
            n = bursts.get(i, 0)
            if done and i >= flip_at:       # the page is complete and due: show it, plan the next
                p = 1 - p
                for k in range(1, N - i + 1):
                    room = flip_budget + (k - 1) * budget - (a.scrub + 3) * k
                    if cost(pages[p], targets[i + k - 1]) <= room and finishes(pages[p], targets[i + k - 1], k):
                        break
                goal, flip_at = targets[i + k - 1], i + k
                shown += 1
                screens.append(pages[1 - p].copy())
                draw(goal, flip_budget, n)
            else:
                held += 1                   # finishing the page, or waiting for its turn:
                screens.append(pages[1 - p].copy())     # this frame isn't shown
                if done:
                    frames.append(bytes([0xFB if p else 0xFE] + ([0x7F, n, n ^ 0x55] if n else [])))
                else:
                    draw(goal, budget, n)
            done = np.array_equal(pages[p], goal)
            pending.append(int(np.count_nonzero(pages[p] != targets[i])))
        target = targets[-1]
        while not (done and np.array_equal(goal, target)):    # finish the last page, and then the last frame,
            if done:                            # before a last mark shows it
                p, goal = 1 - p, target
            screens.append(pages[1 - p].copy())
            draw(goal, flip_budget if done else budget, 0)
            done = np.array_equal(pages[p], goal)
        frames.append(bytes([0xFE if p else 0xFB]))
        screens.append(pages[p].copy())
        pending.append(int(np.count_nonzero(pages[p] != target)))

    blob = bytearray(a2hdr.stream_header(a2hdr.TYPE_VIDEO, len(frames)))
    blob.append(a.fps)
    for f in frames:
        blob += len(f).to_bytes(2, "little") + f
    if audio:
        blob += b"AU" + len(audio).to_bytes(2, "little") + audio
    open(a.output, "wb").write(blob)
    total = sum(len(f) for f in frames)
    _, play_s = stream.plan_stream(frames, a.fps, a.baud)
    if a.flip:
        print("page flipping: each frame is drawn on the hidden page and shown once it's complete, on time; "
              "%d of %d frames shown (%d skipped in busy moments); %d extra frame(s) at the end" % (
                  shown, len(imgs), len(imgs) - shown, len(frames) - len(imgs)), file=sys.stderr)
    if a.scrub:
        print("scrub: %d bytes/frame, a full sweep every %d frames (%.0f s)" % (
            a.scrub, -(-0x2000 // a.scrub), -(-0x2000 // a.scrub) / a.fps), file=sys.stderr)
    if audio:
        print("audio: %d pages (%.1f s) in %d bursts between frames" % (
            len(audio) // 256, len(audio) * 2 / a2hdr.SPEAKER_RATE, len(bursts)), file=sys.stderr)
    print("%d frames, %d stream bytes (avg %.0f/frame, budget %d); plays in about %.1f s at %d baud; "
          "unsent screen bytes after last frame: %d" % (len(frames), total, total / len(frames), budget,
                                                         play_s, a.baud, pending[-1]), file=sys.stderr)

    if a.preview:
        write_preview(a.preview, screens, [1.0 / a.fps + stream.burst_seconds(bursts.get(i, 0)) for i in range(len(screens))], a.colour)
        print("wrote %s: the screen as the player draws it, %d frames" % (a.preview, len(screens)), file=sys.stderr)


if __name__ == "__main__":
    main()
