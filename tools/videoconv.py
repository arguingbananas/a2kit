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
holes), so it never lands mid-screen. Meanwhile the player stores a fast block
that follows the mark (see runtime/video.s), the start of the next frame, and
draws it from memory after the flip; the time it takes counts against the
frame's budget.

Preload (--preload, with --flip): when a picture can't be finished in its own
frame, the smallest part of its work that lets it be on time goes into a fast
block that stream --install uploads to free memory first (after the sound, up
to $BFFF), and the frame draws it from there mid-way. Blocks are handed out to
the busy moments in order until the memory is full.

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
    ops (see _ops)."""
    return bytes([mark]) + _ops(runs, target, 0)


def _ops(runs, target, pos):
    """Skip/literal ops for these runs, from screen offset `pos`. Every data
    byte is < $80 and every control byte is >= $80, so the player can tell
    when it has lost sync; the marks are the resync points. Skips are
    1-122 ($81-$FA); longer ones are $FF lo hi with
    7-bit lo/hi. A screen byte's bit 7 (its palette bit, in colour) is sent as
    state instead: $FD turns it on for the literal bytes that follow, $FC off,
    and each frame starts with it off, so black and white frames never need it."""
    out = bytearray()
    pal = 0
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


def fast_entries(runs, target, pos, data_at, room=None):
    """Encode runs as a fast block (format in a2hdr): the screen bytes, stored
    from address `data_at`, and the entries, drawn from screen offset `pos`.
    With `room`, only as many runs as fit in that many bytes of bytes, entries
    and the 2-byte address (an end entry included). Returns (bytes, entries,
    the runs left over, the offset the line carries on from)."""
    data, ent = bytearray(), bytearray()
    at, y = pos, data_at & 0xFF
    for k, (s, e) in enumerate(runs):
        c = s
        while c < e:
            gap = c - at
            hops = max(0, -(-(gap - 255) // 255))
            n = min(e - c, a2hdr.FAST_MAX, 0x100 - y)
            if room is not None:
                left = room - len(data) - len(ent) - 2 - 2 * hops - 2 - 2     # this entry and the end
                while n > 0 and n + (2 if y + n == 0x100 else 0) > left:
                    n -= 1
            if n < 1:
                ent += bytes([0, a2hdr.FAST_END])
                return bytes(data), bytes(ent), [(c, e)] + list(runs[k + 1:]), at
            for _ in range(hops):
                ent += bytes([255, a2hdr.FAST_HOP])
                at += 255
                gap -= 255
            ent += bytes([gap, a2hdr.FAST_J(n)])
            data += bytes(target[c:c + n])
            at, c, y = c + n, c + n, y + n
            if y == 0x100:                  # the bytes go on to the next page
                y = 0
                ent += bytes([0, a2hdr.FAST_NEXT])
    ent += bytes([0, a2hdr.FAST_END])
    return bytes(data), bytes(ent), [], at


SPAN_MAX = 6                    # with --preload, spread a busy picture over at most this many frames
SHARE_PIECE = 16                # --preload-share benefit: weighs stretches of at most this many frames
COPY_GAP = 12                   # copy over up to this many bytes the other page also has right, rather than start a new entry


def copy_runs(shown, other, target, gap=COPY_GAP):
    """The runs a page showing `shown` can copy from the other page (`other`)
    to get closer to `target`: the bytes it lacks that the other page has
    right, merged over short gaps of bytes the other page has right too, so
    every byte a copy writes ends up right."""
    good = other == target
    want = np.nonzero((shown != target) & good)[0]
    if not len(want):
        return []
    runs, s_, e_ = [], int(want[0]), int(want[0]) + 1
    for a_ in want[1:]:
        a_ = int(a_)
        if a_ - e_ <= gap and good[e_:a_].all():
            e_ = a_ + 1
        else:
            runs.append((s_, e_))
            s_, e_ = a_, a_ + 1
    runs.append((s_, e_))
    return runs


def copy_entries(runs, room=None):
    """The control part of a copy of these runs from the other page (format in
    a2hdr): its address field (a2hdr.COPY_FIELD + where it starts) and its
    entries. With `room`, only as many as fit in that many bytes. Returns
    (bytes, the runs left over)."""
    start = runs[0][0] if runs else 0
    ent = bytearray()
    at = start
    for k, (s, e) in enumerate(runs):
        c = s
        while c < e:
            gap = c - at
            hops = max(0, -(-(gap - 255) // 255))
            n = min(e - c, a2hdr.FAST_MAX, 0x100 - (c & 0xFF))     # (a copy's Y is the screen offset's low byte)
            if room is not None and 2 + len(ent) + 2 * hops + 2 + (2 if (c + n) & 0xFF == 0 else 0) + 2 > room:
                ent += bytes([0, a2hdr.FAST_END])
                return (a2hdr.COPY_FIELD + start).to_bytes(2, "little") + bytes(ent), [(c, e)] + list(runs[k + 1:])
            for _ in range(hops):
                ent += bytes([255, a2hdr.FAST_HOP])
                at += 255
                gap -= 255
            ent += bytes([gap, a2hdr.FAST_J(n)])
            at = c = c + n
            if c & 0xFF == 0:
                ent += bytes([0, a2hdr.FAST_NEXT])
    ent += bytes([0, a2hdr.FAST_END])
    return (a2hdr.COPY_FIELD + start).to_bytes(2, "little") + bytes(ent), []


def fbuf_block(cruns, runs, target, pos, size):
    """A block as the player stores it at a2hdr.FBUF (`size` bytes): a copy
    part with as many of the copy runs as fit, a draw part with as many of the
    runs as fit after it, drawn from offset `pos`, then the offsets of the two
    parts and a2hdr.BLOCK_END. Returns (block, copy runs left, runs left,
    the offset the line carries on from)."""
    room = size - 3
    cpart, cleft = copy_entries(cruns, room - 4 - 1)
    data, ent, left, pos = fast_entries(runs, target, pos, a2hdr.FBUF, room - len(cpart) - 1)
    pad = len(data) & 1
    dctl = len(data) + pad
    blk = data + bytes(pad) + a2hdr.FBUF.to_bytes(2, "little") + ent
    cctl = len(blk)
    blk += cpart
    blk += bytes(size - 3 - len(blk)) + bytes([cctl, dctl, a2hdr.BLOCK_END])
    return blk, cleft, left, pos


def line_copy_blocks(cruns, size=254):
    """Blocks of up to `size` bytes that copy these runs, sent mid-frame with
    their op. Returns [(op and block bytes, block)]."""
    out = []
    while cruns:
        blk, cruns, _, _ = fbuf_block(cruns, [], None, 0, size)
        n = len(blk) // 2
        out.append((bytes([0x7F, n, n ^ a2hdr.OP_BLOCK]) + blk, blk))
    return out


def flip_head(runs, target, mark, size, cruns=()):
    """The start of a frame that changes page: the mark and a block (see
    fbuf_block) of `size` bytes, which the player receives while it waits for
    the vertical blank, so it can tell it arrived whole. Returns (bytes, block,
    runs left, offset after, copy runs left)."""
    blk, cleft, left, pos = fbuf_block(list(cruns), runs, target, 0, size)
    n = size // 2
    return bytes([mark, 0x7F, n, n ^ a2hdr.OP_BLOCK]) + blk, blk, left, pos, cleft


def block_frame(runs, target, mark, size, cruns=()):
    """A frame that changes page: flip_head, then ops for the rest. Something
    always follows the block ($00, ignored, if nothing else), so a block that
    lost a byte takes that, not the next frame's mark. Returns (bytes, block)."""
    head, blk, left, pos, _ = flip_head(runs, target, mark, size, cruns)
    return head + (_ops(left, target, pos) or b"\x00"), blk


def preload_block(runs, target, pos, at):
    """A fast block to preload at address `at` (a multiple of 4) holding all
    these runs, from screen offset `pos`. The screen bytes start where the
    player can index them without starting before the page (their address's
    low byte at most `pos`), so they may start on the next page boundary,
    with the control part before them if it fits there. Returns (the bytes
    from `at`, the control part's address, for the op; a multiple of 4). (A
    block that would run past $FFFF is garbage, but too long for any room.)"""
    def a4(v):
        return v + (-v % 4)
    if at & 0xFF <= pos:
        data, ent, _, _ = fast_entries(runs, target, pos, at)
        ctl = a4(at + len(data))
        return data + bytes(ctl - at - len(data)) + (at & 0xFFFF).to_bytes(2, "little") + ent, ctl
    nxt = (at | 0xFF) + 1
    data, ent, _, _ = fast_entries(runs, target, pos, nxt)
    if 2 + len(ent) <= nxt - at:
        head = (nxt & 0xFFFF).to_bytes(2, "little") + ent
        return head + bytes(nxt - at - len(head)) + data, at
    ctl = a4(nxt + len(data))
    return bytes(nxt - at) + data + bytes(ctl - nxt - len(data)) + (nxt & 0xFFFF).to_bytes(2, "little") + ent, ctl


def copied(cruns, rest):
    """The part of the copy runs done, when `rest` (a tail of them, its first
    perhaps cut short) is left over."""
    if not rest:
        return list(cruns)
    c0 = rest[0][0]
    k = next(i for i, (s_, e_) in enumerate(cruns) if s_ <= c0 < e_)
    return list(cruns[:k]) + ([(cruns[k][0], c0)] if c0 > cruns[k][0] else [])


def apply_copy(page, other, cruns):
    """The page after copying these runs from the other page."""
    new = page.copy()
    for s_, e_ in cruns:
        new[s_:e_] = other[s_:e_]
    return new


def preload_frame(shown, target, budget, mark, block, rate, addr, room, other=None, copies_in_memory=True):
    """A frame that changes page and completes it, with part of its work drawn
    from a fast block preloaded at `addr` (at most `room` bytes): the block
    after the mark takes the copies and the first runs, the copies left over
    go in memory too (or, with copies_in_memory=False, in mid-frame blocks on
    the line, which saves memory but costs line time), and the preloaded block
    takes the fewest runs after those that let the rest fit the budget on the
    line. Returns (frame bytes, preloaded bytes from addr), or None if it
    can't be done in one frame."""
    cruns = copy_runs(shown, other, target) if other is not None else []
    base = apply_copy(shown, other, cruns) if cruns else shown
    diff = np.nonzero(base != target)[0]
    if not len(diff):
        return None
    page = 2 if mark == 0xFB else 1
    head, blk, left, pos, cleft = flip_head(_runs(diff, 2), target, mark, block, cruns)
    if not left:
        return None
    fixed = len(head) + block_quiet(blk, page, rate)
    cpre, ccyc = b"", 0                 # the copies left over: preloaded too, at 2 bytes a run
    if cleft and not copies_in_memory:
        cbs = line_copy_blocks(cleft)
        head += b"".join(d for d, _ in cbs)
        fixed = len(head) + block_quiet(blk, page, rate) + sum(block_quiet(b_, page, rate) for _, b_ in cbs)
    elif cleft:
        cpre, _ = copy_entries(cleft)
        cpre += bytes(-len(cpre) % 4)
        ccyc, _ = a2hdr.fast_cycles(a2hdr.FastMem(cpre, addr), addr, 0x2000 * page + pos, 0x20 + 0x20 * page)
        head += a2hdr.preload_op(addr)
        fixed += 4 + int(np.ceil(((ccyc + a2hdr.preload_overhead(addr)) / a2hdr.CPU_HZ + a2hdr.FAST_MARGIN) * rate))
    at = addr + len(cpre)

    def build(m):
        pre, ctl = preload_block(left[:m], target, pos, at)
        end = left[m - 1][1]
        cyc, _ = a2hdr.fast_cycles(a2hdr.FastMem(pre, at), ctl, 0x2000 * page + pos, 0x20 + 0x20 * page)
        tail = a2hdr.preload_op(ctl) + _ops(left[m:], target, end)
        quiet = int(np.ceil(((cyc + a2hdr.preload_overhead(at)) / a2hdr.CPU_HZ + a2hdr.FAST_MARGIN) * rate))
        return head + tail, cpre + pre, fixed + len(tail) + quiet

    lo, hi = 1, len(left)
    if build(hi)[2] > budget:
        return None                     # too much to draw even from memory
    while lo < hi:                      # the fewest runs preloaded
        mid = (lo + hi) // 2
        if build(mid)[2] <= budget:
            hi = mid
        else:
            lo = mid + 1
    data, pre, _ = build(lo)
    return (data, pre) if len(pre) <= room else None


def preload_span(shown, target, budgets, mark, block, rate, addr, room, other=None, copies_in_memory=True):
    """A picture too busy for one frame even with help from memory, spread over
    len(budgets) frames: the first changes page (mark and block, as in
    preload_frame); each frame draws a share of the runs from a block preloaded
    at `addr` on (at most `room` bytes in all), then as many of the next runs
    as fit on the line. Every frame gets the same share of memory: the
    smallest that completes the picture in time. Returns ([frame bytes, ...],
    preloaded bytes from addr), or None if it can't be done in that many frames."""
    cruns = copy_runs(shown, other, target) if other is not None else []
    base = apply_copy(shown, other, cruns) if cruns else shown
    diff = np.nonzero(base != target)[0]
    if not len(diff):
        return None
    page = 2 if mark == 0xFB else 1
    head, blk, left, pos, cleft = flip_head(_runs(diff, 2), target, mark, block, cruns)
    if not left:
        return None
    scr, top = 0x2000 * page, 0x20 + 0x20 * page
    fixed = len(head) + block_quiet(blk, page, rate)
    cpre = b""
    if cleft and not copies_in_memory:
        cbs = line_copy_blocks(cleft)
        head += b"".join(d for d, _ in cbs)
        fixed = len(head) + block_quiet(blk, page, rate) + sum(block_quiet(b_, page, rate) for _, b_ in cbs)
    elif cleft:
        cpre, _ = copy_entries(cleft)
        cpre += bytes(-len(cpre) % 4)
        ccyc, _ = a2hdr.fast_cycles(a2hdr.FastMem(cpre, addr), addr, scr + pos, top)
        head += a2hdr.preload_op(addr)
        fixed += 4 + int(np.ceil(((ccyc + a2hdr.preload_overhead(addr)) / a2hdr.CPU_HZ + a2hdr.FAST_MARGIN) * rate))

    def quiet(cyc, at):
        return int(np.ceil(((cyc + a2hdr.preload_overhead(at)) / a2hdr.CPU_HZ + a2hdr.FAST_MARGIN) * rate))

    def take(rest, q):                  # the first q bytes' worth of runs (the last one split), and the rest
        chunk, n, rest = [], 0, list(rest)
        while rest and n < q:
            s_, e_ = rest[0]
            if n + e_ - s_ > q:
                chunk.append((s_, s_ + q - n)); rest[0] = (s_ + q - n, e_); n = q
            else:
                chunk.append(rest.pop(0)); n += e_ - s_
        return chunk, rest

    def plan(q):                        # frames and preloaded bytes with up to q bytes from memory a frame, or None
        rest, frames_, blob = list(left), [], bytearray(cpre)
        for f, bud in enumerate(budgets):
            data, used, at0 = (head, fixed, pos) if f == 0 else (bytes([mark]), 1, 0)
            if used + 4 > bud:
                return None
            at, at0_line = addr + len(blob), at0

            def drawn(qq, rest=rest, at0=at0, at=at):   # this frame's block of qq bytes from memory, and its line time
                chunk, rest_ = take(rest, qq)
                if not chunk:
                    return None, None, rest_, 0
                pre, ctl = preload_block(chunk, target, at0, at)
                cyc, _ = a2hdr.fast_cycles(a2hdr.FastMem(pre, at), ctl, scr + at0, top)
                return (pre, ctl, chunk), cyc, rest_, 4 + quiet(cyc, at)
            got = drawn(q)
            if got[0] and used + got[3] > bud:      # more than the frame has time to draw: as much as it has
                lo_, hi_ = 0, q
                while lo_ < hi_:
                    mid = (lo_ + hi_ + 1) // 2
                    if used + drawn(mid)[3] <= bud:
                        lo_ = mid
                    else:
                        hi_ = mid - 1
                got = drawn(lo_)
            if got[0]:
                (pre, ctl, chunk), _, rest, cost_ = got
                data += a2hdr.preload_op(ctl)
                used += cost_
                blob += pre + bytes(-len(pre) % 4)
                at0_line = chunk[-1][1]
            lo, hi = 0, len(rest)       # then as many whole runs as fit on the line
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if used + len(_ops(rest[:mid], target, at0_line)) <= bud:
                    lo = mid
                else:
                    hi = mid - 1
            data += _ops(rest[:lo], target, at0_line) or b""
            del rest[:lo]
            frames_.append(data)
            if not rest:
                frames_ += [bytes([mark])] * (len(budgets) - f - 1)     # (done early: the rest just wait)
                break
        return (frames_, bytes(blob)) if not rest and len(blob) <= room else None

    lo, hi = 1, 0x2000
    if plan(hi) is None:
        return None
    while lo < hi:                      # the smallest share of memory a frame that's enough
        mid = (lo + hi) // 2
        if plan(mid) is not None:
            hi = mid
        else:
            lo = mid + 1
    return plan(lo)


class Refill:
    """--refill: preload memory used again once the frames drawing from it are
    past. Blocks go in the fresh memory first (uploaded before playing); when
    it's full, the next block goes where the oldest ones were (a ring over the
    regions), and its bytes are sent in store ops added to the frames between
    the last use of what was there and the frame that draws it, in each
    frame's spare line time (what its own bytes and quiet gaps leave)."""
    SAFETY = 8                          # bytes of each frame's budget left unused

    def __init__(self, regions, budget, rate):
        self.regions, self.budget, self.rate = list(regions), budget, rate
        self.allocs = []                # [start, end, last frame drawing from it]
        self.avail = []                 # spare line bytes left in each frame planned so far
        self.page = 1
        self.mem = bytearray(0x10000)   # the preload memory as the frames planned so far leave it
        self.cursor = None

    def fresh(self, addr, data, last):
        self.allocs.append([addr, addr + len(data), last])
        self.mem[addr:addr + len(data)] = data

    def track(self, frames, bursts):    # the spare time of frames just planned
        for f in range(len(self.avail), len(frames)):
            cuts, self.page = a2hdr.frame_waits(frames[f], self.page, self.mem)
            quiet = int(np.ceil(sum(s_ for _, s_ in cuts) * self.rate))
            spare = self.budget - len(frames[f]) - quiet - self.SAFETY
            self.avail.append(0 if bursts.get(f) else max(0, spare))

    @staticmethod
    def usable(spare):                  # store bytes (a multiple of 4) that fit in this many line bytes
        t = max(0, spare - a2hdr.STORE_OVERHEAD) & ~3
        while t > 0 and t + a2hdr.STORE_OVERHEAD * -(-t // a2hdr.STORE_MAX) > spare:
            t -= 4
        return t

    def cap(self, last, i):             # store bytes frames last+1 .. i-1 can still carry
        return self.pre[i] - self.pre[last + 1]

    def _at(self, c, e, i, share):      # the most a block from c (below e) can hold, and the frame it must wait for
        best, best_last, last, x = 0, -1, -1, c
        for s_, e_, l_ in sorted(o for o in self.allocs if o[1] > c and o[0] < e):
            if s_ > x:
                size = min(s_ - c, self.cap(last, i), share)
                if size > best:
                    best, best_last = size, last
            if l_ >= i - 1:             # still to be drawn (or no frame left to send it in)
                return best & ~3, best_last
            last, x = max(last, l_), max(x, e_)
            size = min(min(x, e) - c, self.cap(last, i), share)
            if size > best:
                best, best_last = size, last
        size = min(e - c, self.cap(last, i), share)
        if size > best:
            best, best_last = size, last
        return best & ~3, best_last

    def place(self, i, share):
        """Where a block for frame i can go: (address, most bytes, the last
        frame drawing from what's there now), trying the ring position, then
        each region's start."""
        self.pre = np.concatenate([[0], np.cumsum([self.usable(s_) for s_ in self.avail])]).astype(int)
        if len(self.pre) <= i:
            self.pre = np.concatenate([self.pre, [self.pre[-1]] * (i + 1 - len(self.pre))])
        tries = {s_ for s_, _e in self.regions} | {o[1] for o in self.allocs} | {o[0] for o in self.allocs}
        best = (0, 0, -1)
        for c in sorted(tries):
            e = next((e_ for s_, e_ in self.regions if s_ <= c < e_), None)
            if e is None or c % 4:
                continue
            size, last = self._at(c, e, i, share)
            if size > best[1] or (size == best[1] and size and last < best[2]):
                best = (c, size, last)
        return best if best[1] >= 256 else (0, 0, -1)

    def fill(self, frames, at, data, last, i, last_use):
        """Put a block at `at`, for frames i .. last_use, sending its bytes in
        frames last+1 .. i-1."""
        end = at + len(data)
        keep = []
        for o in self.allocs:           # what was there is past
            if o[1] <= at or o[0] >= end:
                keep.append(o)
            else:
                if o[0] < at:
                    keep.append([o[0], at, o[2]])
                if o[1] > end:
                    keep.append([end, o[1], o[2]])
        self.allocs = keep + [[at, end, last_use]]
        self.mem[at:end] = data
        self.cursor = end
        off = 0
        for f in range(last + 1, i):
            if off >= len(data):
                break
            t = min(self.usable(self.avail[f]), len(data) - off)
            if t <= 0:
                continue
            ops = a2hdr.store_ops(at + off, data[off:off + t])
            frames[f] = frames[f] + ops
            self.avail[f] -= len(ops)
            off += t
        assert off >= len(data), "refill: not enough spare time to send a block"


def block_quiet(blk, page, rate, ptr=0):
    """Bytes' worth of line time the host stays quiet while the player runs
    the block blk (stored at FBUF) from screen offset ptr."""
    cyc, _ = a2hdr.block_cycles(blk, 0x2000 * page + ptr, 0x20 + 0x20 * page)
    return int(np.ceil(((cyc + a2hdr.BLOCK_OVERHEAD) / a2hdr.CPU_HZ + a2hdr.FAST_MARGIN) * rate))


def encode_frame(shown, target, budget, merge_gap=2, forced=None, mark=0xFE, block=0, rate=0, other=None):
    """Return (frame bytes, new `shown` state), the frame within budget. `shown`
    is what the page the frame draws on holds, and `mark` names that page ($FE
    or $FB). With `block` (bytes; a frame that changes page), the frame starts
    with a fast block, and the time the player takes to draw it counts against
    the budget too, as bytes at `rate` bytes a second. With `other` (what the
    other page holds, while it's on screen), the bytes it already has right
    are copied from it rather than sent: in the block, then in mid-frame
    blocks, as many as the budget allows.

    `forced` is an optional sorted array of screen addresses that are always
    sent (the scrub window); the changed bytes then fill whatever budget is
    left, most-changed first."""
    forced = np.zeros(0, dtype=np.int64) if forced is None else np.asarray(forced, dtype=np.int64)
    page = 2 if mark == 0xFB else 1
    cruns = copy_runs(shown, other, target) if other is not None and rate else []
    # the copies: first in the block (if any), then in mid-frame blocks, as many as fit with nothing else
    after = (copy_entries(cruns, block - 3 - 4 - 1)[1] if block else cruns) if cruns else []
    cbs = line_copy_blocks(after)

    def head_cost(ncb):
        if block:
            head, blk, _, _, _ = flip_head([], target, mark, block, cruns)
            c_ = len(head) + block_quiet(blk, page, rate)
        else:
            c_ = 1
        return c_ + sum(len(d) + block_quiet(b_, page, rate) for d, b_ in cbs[:ncb])

    ncb = len(cbs)
    while ncb and head_cost(ncb) > budget:
        ncb -= 1
    rest = list(after)
    for _ in cbs[:ncb]:
        _, rest, _, _ = fbuf_block(rest, [], None, 0, 254)
    done = copied(cruns, rest)
    base = apply_copy(shown, other, done) if done else shown
    diff = np.nonzero(base != target)[0]
    if len(diff) == 0 and len(forced) == 0 and not block and not done:
        return bytes([mark]), shown
    score = POP[base[diff] ^ target[diff]]
    cand = diff[np.lexsort((diff, -score.astype(np.int16)))]    # most changed first

    def build(k):
        addrs = np.unique(np.concatenate([forced, cand[:k]]))
        runs = _runs(addrs, merge_gap) if len(addrs) else []
        if block:
            head, blk, left, pos, _ = flip_head(runs, target, mark, block, cruns)
            quiet = block_quiet(blk, page, rate)
        else:
            head, left, pos, quiet = bytes([mark]), runs, 0, 0
        quiet += sum(block_quiet(b_, page, rate) for _, b_ in cbs[:ncb])
        ops = _ops(left, target, pos)
        data = head + b"".join(d for d, _ in cbs[:ncb]) + ops
        if (block or ncb) and not ops:
            data += b"\x00"            # (something always follows a block, so a damaged one can't take the next mark)
        return data, runs, len(data) + quiet

    if build(0)[2] > budget:
        raise ValueError("scrub window doesn't fit in the %d-byte frame budget; lower --scrub or raise --budget" % budget)
    lo, hi = 0, len(cand)
    while lo < hi:                      # largest k whose encoding fits
        mid = (lo + hi + 1) // 2
        if build(mid)[2] <= budget:
            lo = mid
        else:
            hi = mid - 1
    data, runs, _ = build(lo)
    new = apply_copy(shown, other, done) if done else shown.copy()
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
    ap.add_argument("--preload", action="store_true",
                    help="with --flip: put part of the busiest frames' work in the Apple II's free memory before playing, "
                         "so they keep up (stream it with --install)")
    ap.add_argument("--preload-share", choices=["benefit", "first"], default="benefit",
                    help="with --preload: how the memory is shared out. benefit (default): to the busy stretches "
                         "it smooths the most per byte (slower to convert); first: to the busy frames in order, until it's full")
    ap.add_argument("--refill", action=argparse.BooleanOptionalAction, default=None,
                    help="with --preload (on by default): when the memory is full, reuse what earlier frames have drawn from, "
                         "sending later frames' blocks into it in the line's spare time while it plays; "
                         "--no-refill keeps each preloaded block where it was uploaded")
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
    rate = a.baud / 10                  # bytes a second
    block = a2hdr.block_bytes(a.baud)   # with --flip, a frame that changes page starts with a fast block
    if a.flip and budget < 4 + block + 16 + a.scrub:
        sys.exit("--flip leaves no time for the frame at %d frames a second; use a lower --fps" % a.fps)
    if budget < 8 or budget > 0xFFFF:
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
    if a.preload and not a.flip:
        sys.exit("--preload works with --flip")
    if a.preload and a.scrub:
        sys.exit("--preload and --scrub can't be used together")
    if a.refill and not a.preload:
        sys.exit("--refill works with --preload")
    if a.refill is None:
        a.refill = a.preload            # (on by default with --preload)
    # free memory after the sound: main RAM to $BFFF, then with --language-card the card's
    # $D000-$FEFF. The preload is laid out like sound (hopping $C000-$CFFF), no block across the hop.
    pl_start = a2hdr.audio_addr(-(-len(audio) // 256) * 256)
    # [start, end, bytes] for each stretch the preload may use: what's left of main RAM, then the card
    pl_regions = [(pl_start, a2hdr.PRELOAD_END)] if pl_start < a2hdr.PRELOAD_END else []
    if a.language_card:
        pl_regions.append((max(pl_start, a2hdr.CARD_START), a2hdr.CARD_END))
    pl_total = sum(e - s_ for s_, e in pl_regions)
    preload = bytearray()
    pages = [blank(), blank()]          # what pages 1 and 2 hold
    frames, pending, screens = [], [], []
    cursors = [0, 0]
    p = 0                               # the page drawn last (page 1, black, when the player starts)

    def draw(target, bud, n, flips=False):
        nonlocal p
        window = (cursors[p] + np.arange(a.scrub)) % 0x2000
        cursors[p] = (cursors[p] + a.scrub) % 0x2000
        data, pages[p] = encode_frame(pages[p], target, bud - (3 if n else 0), forced=np.sort(window),
                                      mark=0xFB if p else 0xFE, block=block if flips else 0, rate=rate,
                                      other=pages[1 - p] if a.flip else None)
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
        targets = [screen_of(img) for img in imgs]

        def plan(caps):
            """The whole --flip stream. caps: None = preload while memory lasts,
            first come first served; else [(first frame, end, bytes)]: preload
            only in those windows, up to that many bytes each. Returns (frames,
            screens, pending, preload, when each picture went up, bytes used per window)."""
            nonlocal p
            p = 0
            pages[:] = [blank(), blank()]
            cursors[:] = [0, 0]
            frames.clear(); screens.clear(); pending.clear()     # (draw appends to frames)
            regions = [[s_, e, bytearray()] for s_, e in pl_regions]
            refill = Refill(pl_regions, budget - a.scrub - 3, rate) if a.refill else None
            queue = []                      # frames of a picture spread over several, still to send
            goal, done, held = None, True, 0
            used = [0] * len(caps or [])
            ups = []
            # Even pacing: at each flip, pick the next picture so that it's due exactly
            # when it will go on screen. Finishing a page that is k frames' time away
            # can use the budget of k frames, so take the first frame ahead whose cost
            # (every byte it still needs, sent at once) fits the budget up to its own
            # turn. Then every picture appears at its right time, and motion keeps its
            # speed through busy moments. A page finished early waits for its turn.
            # The frame that changes page starts with a fast block (sent while the
            # player waits for the vertical blank), so it has the same budget.
            N = len(targets)
            flip_at, shown = 0, 0               # when the page being drawn may go on screen

            def cost(page, target, other):      # a lower bound: what it still needs, sent at once (copies at 2 bytes a run)
                cr = copy_runs(page, other, target)
                diff = np.nonzero((apply_copy(page, other, cr) if cr else page) != target)[0]
                return 1 + 2 * len(cr) + (len(_emit(_runs(diff, 2), target)) if len(diff) else 0)

            def finishes(page, target, k, mark, other):     # exactly: does drawing it for k frames complete it?
                for s_ in range(k):
                    _, page = encode_frame(page, target, budget - a.scrub - 3, mark=mark,
                                           block=block if s_ == 0 else 0, rate=rate, other=other)
                    if np.array_equal(page, target):
                        return True
                return False

            for i in range(N):
                n = bursts.get(i, 0)
                if done and i >= flip_at:       # the page is complete and due: show it, plan the next
                    p = 1 - p
                    pre = None
                    for k in range(1, N - i + 1):
                        room = k * budget - 4 - (a.scrub + 3) * k     # (the block carries bytes too)
                        if (cost(pages[p], targets[i + k - 1], pages[1 - p]) <= room
                                and finishes(pages[p], targets[i + k - 1], k, 0xFB if p else 0xFE, pages[1 - p])):
                            break
                        if a.preload and k <= SPAN_MAX:     # in k frames with help from memory, while there's room
                            w = next((j for j, (c0, c1, _) in enumerate(caps) if c0 <= i < c1), -1) if caps is not None else -1
                            share = (caps[w][2] - used[w] if w >= 0 else 0) if caps is not None else 1 << 20
                            tgt, mark_ = targets[i + k - 1], 0xFB if p else 0xFE
                            for reg in regions:     # main RAM first; the card when what's left there is too small
                                at_ = reg[0] + len(reg[2])
                                limit = min(reg[1] - at_, share)
                                if limit > 0 and k == 1:
                                    r_ = preload_frame(pages[p], tgt, budget - (3 if n else 0), mark_, block, rate, at_, limit,
                                                       other=pages[1 - p], copies_in_memory=caps is not None)
                                    pre = ([r_[0]], r_[1]) if r_ else None
                                elif limit > 0:     # a picture too busy for one frame, even from memory
                                    pre = preload_span(pages[p], tgt, [budget - (3 if bursts.get(i + f, 0) else 0) for f in range(k)],
                                                       mark_, block, rate, at_, limit, other=pages[1 - p],
                                                       copies_in_memory=caps is not None)
                                if pre:
                                    break
                            recyc = None
                            if not pre and refill and share > 0:    # memory full: reuse what's been drawn from
                                at_, limit, last = refill.place(i, share)
                                if limit > 0 and k == 1:
                                    r_ = preload_frame(pages[p], tgt, budget - (3 if n else 0), mark_, block, rate, at_, limit,
                                                       other=pages[1 - p], copies_in_memory=caps is not None)
                                    pre = ([r_[0]], r_[1]) if r_ else None
                                elif limit > 0:
                                    pre = preload_span(pages[p], tgt, [budget - (3 if bursts.get(i + f, 0) else 0) for f in range(k)],
                                                       mark_, block, rate, at_, limit, other=pages[1 - p],
                                                       copies_in_memory=caps is not None)
                                recyc = (at_, last) if pre else None
                            if pre:
                                if w >= 0:
                                    used[w] += len(pre[1]) + (-len(pre[1]) % 4)
                                break
                    goal, flip_at = targets[i + k - 1], i + k
                    shown += 1
                    ups.append(i)
                    screens.append(pages[1 - p].copy())
                    if pre:
                        fl, blob = pre
                        if recyc:                   # sent in earlier frames' spare time, into memory they're done with
                            refill.fill(frames, recyc[0], blob + bytes(-len(blob) % 4), recyc[1], i, i + k - 1)
                        else:
                            if refill:
                                refill.fresh(reg[0] + len(reg[2]), blob + bytes(-len(blob) % 4), i + k - 1)
                            reg[2] += blob + bytes(-len(blob) % 4)     # blocks start on a multiple of 4
                        frames.append(fl[0] + (bytes([0x7F, n, n ^ 0x55]) if n else b""))
                        queue[:] = fl[1:]           # the frames that finish it
                        if not queue:
                            pages[p] = goal.copy()
                    else:
                        draw(goal, budget, n, flips=True)
                else:
                    held += 1                   # finishing the page, or waiting for its turn:
                    screens.append(pages[1 - p].copy())     # this frame isn't shown
                    if queue:
                        frames.append(queue.pop(0) + (bytes([0x7F, n, n ^ 0x55]) if n else b""))
                        if not queue:
                            pages[p] = goal.copy()
                    elif done:
                        frames.append(bytes([0xFB if p else 0xFE] + ([0x7F, n, n ^ 0x55] if n else [])))
                    else:
                        draw(goal, budget, n)
                done = np.array_equal(pages[p], goal)
                pending.append(int(np.count_nonzero(pages[p] != targets[i])))
                if refill:
                    refill.track(frames, bursts)
            target = targets[-1]
            while not (done and np.array_equal(goal, target)):    # finish the last page, and then the last frame,
                flips = done                        # before a last mark shows it
                if done:
                    p, goal = 1 - p, target
                    ups.append(N)                   # (one more picture: the last)
                screens.append(pages[1 - p].copy())
                draw(goal, budget, 0, flips=flips)
                done = np.array_equal(pages[p], goal)
            frames.append(block_frame([], target, 0xFE if p else 0xFB, block)[0])     # the last mark shows the last page
            screens.append(pages[p].copy())
            pending.append(int(np.count_nonzero(pages[p] != target)))


            preload = bytearray()           # laid out like sound: main RAM up to $BFFF, then the card from $D000
            for k_, (s_, e, b_) in enumerate(regions):
                later = any(r[2] for r in regions[k_ + 1:])
                preload += b_ + (bytes(e - s_ - len(b_)) if later else b"")
            return list(frames), list(screens), list(pending), preload, ups, used

        def penalty(ups, c0, c1):           # how much the holds in [c0, c1) stutter: (hold - 1)^2 summed
            t = [u for u in ups if c0 <= u < c1]
            prev = max([0] + [u for u in ups if u < c0])
            nxt = [u for u in ups if u >= c1][:1]
            h = np.diff([prev] + t + nxt)
            return int(((h - 1) ** 2).sum())

        if not a.preload:
            frames, screens, pending, preload, ups, _ = plan([])
        elif a.preload_share == "first":
            frames, screens, pending, preload, ups, _ = plan(None)
        else:
            # Share the memory by benefit: find the stretches where pictures are
            # held (planning without preload), see how much memory each uses and
            # how much smoother it gets with all of it, then give it out to the
            # stretches that gain the most per byte.
            _, _, _, _, ups0, _ = plan([])
            spans = []
            for t0, t1 in zip([0] + ups0, ups0):
                if t1 - t0 > 1:
                    c0, c1 = max(0, t0 - 2), t1 + 2
                    if spans and c0 <= spans[-1][1]:
                        spans[-1][1] = c1
                    else:
                        spans.append([c0, c1])
            pieces = []                     # long stretches in pieces, so each part competes for memory on its own
            for c0, c1 in spans:
                n_ = -(-(c1 - c0) // SHARE_PIECE)
                cuts = [c0 + (c1 - c0) * j // n_ for j in range(n_ + 1)]
                pieces += [[x0, x1] for x0, x1 in zip(cuts, cuts[1:])]
            spans = pieces
            mem = pl_total * 4 if a.refill else pl_total     # (with --refill, memory comes back as it plays)
            offers = []
            for c0, c1 in spans:
                _, _, _, _, ups_w, used_w = plan([(c0, c1, mem)])
                gain = penalty(ups0, c0, c1) - penalty(ups_w, c0, c1)
                if gain > 0 and used_w[0]:
                    offers.append((gain / used_w[0], c0, c1, used_w[0]))
            chosen, left = [], mem
            for _, c0, c1, u in sorted(offers, reverse=True):
                if left > 512:                  # (the last one may get only part of what it would use)
                    chosen.append((c0, c1, min(u + 256, left)))
                    left -= chosen[-1][2]
            frames, screens, pending, preload, ups, _ = plan(sorted(chosen))
        shown = len(ups)                    # (each picture planned goes on screen, the last at the final mark)
    blob = bytearray(a2hdr.stream_header(a2hdr.TYPE_VIDEO, len(frames)))
    blob.append(a.fps)
    for f in frames:
        blob += len(f).to_bytes(2, "little") + f
    if audio:
        blob += b"AU" + len(audio).to_bytes(2, "little") + audio
    if preload:
        blob += b"PL" + pl_start.to_bytes(2, "little") + len(preload).to_bytes(2, "little") + preload
    open(a.output, "wb").write(blob)
    total = sum(len(f) for f in frames)
    _, play_s = stream.plan_stream(frames, a.fps, a.baud, mem=a2hdr.preload_mem((pl_start, preload)))
    if a.flip:
        print("page flipping: each frame is drawn on the hidden page and shown once it's complete, on time; "
              "%d of %d frames shown (%d skipped in busy moments); %d extra frame(s) at the end" % (
                  shown, len(imgs), len(imgs) - shown, len(frames) - len(imgs)), file=sys.stderr)
    if a.preload:
        print("preload: %d bytes at $%04X-$%04X for %d frames (about %.1f s to upload)" % (
            len(preload), pl_start, a2hdr.audio_addr(a2hdr.area_offset(pl_start) + max(len(preload), 1) - 1),
            sum(1 for f in frames if any(k == "preload" for k, _v, _i in a2hdr.walk_ops(f))), len(preload) / 8200), file=sys.stderr)
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
