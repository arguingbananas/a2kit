#!/usr/bin/env python3
"""stream: play an .a2v video on the Apple II over serial.

  stream.py clip.a2v --port /dev/ttyUSB0 --install

--install first uploads the clip's audio (if any) and runtime/video.bin
through the receiver, which must be running, then starts the player.
Without it, the player must already be running.

After that there is no flow control, so the host keeps its own model of the
serial line: each frame is sent at its due time, but never before the line is
free, and while the Apple II plays an audio burst the line stays silent until
the burst is over. This doesn't rely on the OS or the USB adapter reporting
when bytes have actually left. Press a key on the Apple II to stop.
"""
import argparse
import os
import sys
import time

import a2hdr

PLAYER_ADDR = 0x0E00
BURST_MARGIN = 0.05     # extra quiet time after each burst, seconds (USB scheduling jitter)
PREAMBLE = b"\xFE" * 4  # frame-start marks: the player starts in sync
END = b"\x80\x80"       # end of stream (twice: one corrupted byte mustn't end it)
LATE_WARN = 0.25        # seconds


def burst_seconds(pages):
    return pages * 256 * 2 * a2hdr.SPEAKER_PLAYER_CYCLES / a2hdr.CPU_HZ


def plan_stream(frames, fps, baud, margin=BURST_MARGIN, quiet_for_bursts=True):
    """Work out when to send each piece, in seconds from the start.

    Returns (schedule, total) where schedule is a list of (send_time, bytes,
    kind) with kind "preamble", "frame" or "end", and total is when the last
    byte has arrived. The model: bytes cross the line at baud/10 per second; a
    frame is due at i/fps (shifted by the audio played so far) but can't start
    before the line is free; a burst starts when the Apple II has the frame's
    last byte, and the line must then stay quiet for the burst plus `margin`.
    `quiet_for_bursts=False` ignores bursts (only used to test that it matters).
    """
    byte_t = 10.0 / baud
    sched = [(0.0, PREAMBLE, "preamble")]
    line_free = len(PREAMBLE) * byte_t
    shift = 0.0
    for i, f in enumerate(frames):
        start = max(i / fps + shift, line_free)
        sched.append((start, f, "frame"))
        line_free = start + len(f) * byte_t
        pages = a2hdr.frame_audio_pages(f)
        if pages and quiet_for_bursts:
            quiet = burst_seconds(pages) + margin
            line_free += quiet
            shift += quiet
    sched.append((max(len(frames) / fps + shift, line_free), END, "end"))
    total = sched[-1][0] + len(END) * byte_t
    return sched, total


def lateness(sched, fps):
    """Largest delay of a frame behind its due time (seconds), from the plan."""
    worst, shift, i = 0.0, 0.0, 0
    for t, f, kind in sched:
        if kind != "frame":
            continue
        worst = max(worst, t - (i / fps + shift))
        pages = a2hdr.frame_audio_pages(f)
        if pages:
            shift += burst_seconds(pages) + BURST_MARGIN
        i += 1
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

    fps, frames, audio = a2hdr.parse_stream(open(a.file, "rb").read())
    if fps < 1:
        sys.exit("%s: bad frame rate in file" % a.file)
    if audio and not a.install:
        sys.exit("this clip has audio, which is uploaded through the receiver: use --install")
    if len(audio) // 256 > a2hdr.AUDIO_PAGES and not a.language_card:
        sys.exit("this clip has more sound than main RAM holds: it needs --language-card")
    sched, total = plan_stream(frames, fps, a.baud)
    late = lateness(sched, fps)
    if late > LATE_WARN:
        print("warning: frames run up to %.2f s behind at %d baud; re-encode with a lower --fps or --budget"
              % (late, a.baud), file=sys.stderr)

    import serial
    port = serial.Serial(a.port, a.baud, timeout=2.0, rtscts=a.rtscts)
    port.reset_input_buffer()

    if a.install:
        import send
        s = send.Sender(port, language_card=a.language_card)
        for addr, piece in (a2hdr.audio_pieces(0, audio) if audio else ()):     # audio must be in RAM before the stream starts
            print("uploading %d bytes of audio to $%04X" % (len(piece), addr), file=sys.stderr)
            s.send_blob(piece, addr)
        here = os.path.dirname(os.path.abspath(__file__))
        player = open(os.path.join(here, "..", "video.bin"), "rb").read()
        s.send_blob(player, PLAYER_ADDR)
        s.finish(PLAYER_ADDR)
        time.sleep(1.0)                 # let it clear the screen and start listening

    print("playing %d frames, about %.1f s" % (len(frames), total), file=sys.stderr)
    t0 = time.monotonic()
    for when, data, _kind in sched:
        wait = t0 + when - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        port.write(data)
    port.flush()
    rest = t0 + total - time.monotonic()
    if rest > 0:
        time.sleep(rest)                # don't close the port while bytes are still going out


if __name__ == "__main__":
    main()
