#!/usr/bin/env python3
"""stream: play an .a2v video on the Apple II over serial.

  stream.py clip.a2v --port /dev/ttyUSB0 --install

--install first uploads the clip's audio and preloaded frames (if any) and
runtime/video.bin through the receiver, which must be running, then starts
the player.
Without it, the player must already be running.

After that there is no flow control, so the host keeps its own model of the
serial line: each frame is sent at its due time, but never before the line is
free, and while the Apple II plays an audio burst the line stays silent until
the burst is over. This doesn't rely on the OS or the USB adapter reporting
when bytes have actually left. Press a key on the Apple II to stop.

A flipped stream (video --flip) changes page at its frame marks, and the
player then waits for the screen's vertical blank before it flips, up to one
screen refresh (about 17 ms). Each such mark is followed by a fast block that
the player stores while it waits, so the line keeps working; once the block
is in, the host stays quiet while the player draws it, and likewise after an
op that draws a block preloaded into the Apple II's memory (a2hdr.frame_waits
works out where and for how long).
"""
import argparse
import os
import sys
import time

import a2hdr
import rxid
import send

PLAYER_ADDR = 0x0E00
BURST_MARGIN = 0.05     # extra quiet time after each burst, seconds (USB scheduling jitter)
PREAMBLE = b"\xFE" * 4  # frame-start marks: the player starts in sync
END = b"\x80\x80"       # end of stream (twice: one corrupted byte mustn't end it)
LATE_WARN = 0.25        # seconds
FLIP_QUIET = a2hdr.FLIP_QUIET
MARK_PAGE = {0xFE: 1, 0xFB: 2}


def burst_seconds(pages):
    return pages * 256 * 2 * a2hdr.SPEAKER_PLAYER_CYCLES / a2hdr.CPU_HZ


def plan_stream(frames, fps, baud, margin=BURST_MARGIN, quiet_for_bursts=True, mem=None):
    """Work out when to send each piece, in seconds from the start.

    Returns (schedule, total) where schedule is a list of (send_time, bytes,
    kind) with kind "preamble", "frame", "rest" (more of a frame, after the
    line went quiet for a fast block or a vertical-blank wait) or "end", and
    total is when the last byte has arrived. The model: bytes cross the line at
    baud/10 per second; a frame is due at i/fps (shifted by the audio played
    so far) but can't start before the line is free; inside a frame the line
    goes quiet where a2hdr.frame_waits says (`mem`: the preloaded blocks); a
    burst starts when the Apple II has the frame's last byte, and the line
    must then stay quiet for the burst plus `margin`. `quiet_for_bursts=False`
    ignores bursts (only used to test that it matters).
    """
    byte_t = 10.0 / baud
    sched = [(0.0, PREAMBLE, "preamble")]
    line_free = len(PREAMBLE) * byte_t
    shift = 0.0
    page = 1
    for i, f in enumerate(frames):
        t = max(i / fps + shift, line_free)
        cuts, page = a2hdr.frame_waits(f, page, mem)
        prev, kind = 0, "frame"
        for idx, wait in cuts + [(len(f), 0.0)]:
            if idx > prev or kind == "frame":
                sched.append((t, f[prev:idx], kind))
                kind = "rest"
            t += (idx - prev) * byte_t + wait
            prev = idx
        line_free = t
        pages = a2hdr.frame_audio_pages(f)
        if pages and quiet_for_bursts:
            quiet = burst_seconds(pages) + margin
            line_free += quiet
            shift += quiet
    sched.append((max(len(frames) / fps + shift, line_free), END, "end"))
    total = sched[-1][0] + len(END) * byte_t
    return sched, total


def send_times(sched, baud):
    """For each piece of the schedule: (planned time, bytes, at), where at(t0)
    is when to send it, and at.sent(t) records when it went. A piece normally
    goes at its planned time; if it's late, the bytes after it just queue up.
    But a piece that follows a quiet gap (the player drawing a fast block, or
    playing a sound burst) must not lose any of that gap when the piece before
    it went late, so it goes no sooner than the gap after the line actually
    finished the piece before."""
    byte_t = 10.0 / baud
    line = {"end": None}

    class At:
        def __init__(self, when, gap):
            self.when, self.gap = when, gap

        def __call__(self, t0):
            t = t0 + self.when
            if self.gap is not None and line["end"] is not None:
                t = max(t, line["end"] + self.gap)
            return t

        def sent(self, t):
            line["end"] = max(t, line["end"] or t) + self.n * byte_t

    out = []
    for k, (when, data, kind) in enumerate(sched):
        gap = None
        if k:
            pwhen, pdata, pkind = sched[k - 1]
            if kind == "rest" or (pkind in ("frame", "rest") and a2hdr.frame_audio_pages(pdata)):
                gap = when - (pwhen + len(pdata) * byte_t)
        at = At(when, gap)
        at.n = len(data)
        out.append((when, data, at))
    return out


def lateness(sched, fps):
    """Largest delay of a frame behind its due time (seconds), from the plan."""
    worst, shift, i = 0.0, 0.0, 0
    for t, f, kind in sched:
        if kind == "frame":
            worst = max(worst, t - (i / fps + shift))
            i += 1
        if kind in ("frame", "rest"):
            pages = a2hdr.frame_audio_pages(f)
            if pages:
                shift += burst_seconds(pages) + BURST_MARGIN
    return worst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--port", required=True)
    a2hdr.add_baud_arg(ap)
    ap.add_argument("--install", action="store_true", help="upload the player (and any audio) through the receiver first")
    ap.add_argument("--rtscts", action="store_true")
    a2hdr.add_language_card_arg(ap)
    a = ap.parse_args()

    raw = open(a.file, "rb").read()
    fps, frames, audio = a2hdr.parse_stream(raw)
    pre = a2hdr.parse_preload(raw)
    if fps < 1:
        sys.exit("%s: bad frame rate in file" % a.file)
    if audio and not a.install:
        sys.exit("this clip has audio, which is uploaded through the receiver: use --install")
    if pre and not a.install:
        sys.exit("this clip preloads part of its frames, which is uploaded through the receiver: use --install")
    if len(audio) // 256 > a2hdr.AUDIO_PAGES and not a.language_card:
        sys.exit("this clip has more sound than main RAM holds: it needs --language-card")
    sched, total = plan_stream(frames, fps, a.baud, mem=a2hdr.preload_mem(pre))
    late = lateness(sched, fps)
    if late > LATE_WARN:
        print("warning: frames run up to %.2f s behind at %d baud; re-encode with a lower --fps or --budget"
              % (late, a.baud), file=sys.stderr)

    port = send.open_port(a.port, a.baud, rtscts=a.rtscts)
    port.reset_input_buffer()

    if a.install:
        rxid.check(port, a.baud)     # without --install the player is running, and can't answer reads
        s = send.Sender(port, language_card=a.language_card)
        for addr, piece in (a2hdr.audio_pieces(0, audio) if audio else ()):     # audio must be in RAM before the stream starts
            print("uploading %d bytes of audio to $%04X" % (len(piece), addr), file=sys.stderr)
            s.send_blob(piece, addr)
        if pre:
            print("uploading %d bytes of preloaded frames to $%04X" % (len(pre[1]), pre[0]), file=sys.stderr)
            s.send_blob(pre[1], pre[0])
        here = os.path.dirname(os.path.abspath(__file__))
        player = open(os.path.join(here, "..", "video.bin"), "rb").read()
        s.send_blob(player, PLAYER_ADDR)
        if not s.finish(PLAYER_ADDR):
            print(send.PROBABLY_STARTED, file=sys.stderr)
        time.sleep(1.0)                 # let it clear the screen and start listening

    print("playing %d frames, about %.1f s" % (len(frames), total), file=sys.stderr)
    t0 = time.monotonic()
    for _when, data, at in send_times(sched, a.baud):
        wait = at(t0) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        at.sent(time.monotonic())
        port.write(data)
    port.flush()
    rest = t0 + total - time.monotonic()
    if rest > 0:
        time.sleep(rest)                # don't close the port while bytes are still going out


if __name__ == "__main__":
    main()
