#!/usr/bin/env python3
"""show: build and deploy a scene show from a project file.

  a2kit show plan  show.toml                 # convert and print the upload plan
  a2kit show send  show.toml --port /dev/ttyUSB0 [--install] [--language-card]

show.toml (asset paths are relative to the file):

  [[scene]]
  image = "logo.png"      # shown on HGR page 1, then page 2 (two images max)
  hold  = 3.0             # optional: seconds to stay on it
  crop  = false           # optional, as in `a2kit image`: fill the screen
  invert = false          # optional: swap black and white
  dither = "atkinson"     # optional: or "threshold"

  [[scene]]
  audio = "clip.wav"      # played over whatever is on screen
  gain  = 1.0             # optional
  even_out = false        # optional, as in `a2kit audio --even-out`: clearer speech

  [[scene]]
  wait = 2.0              # seconds

At the end of the show the screen goes back to text, so give the last
picture a hold (or add a wait) if you want it to stay up.

Memory map (matches runtime/sequencer.s):
  $0B00 scene table | $0C00 sequencer | $2000 / $4000 HGR pages | $6000.. audio
--install also uploads sequencer.bin. Without it, send first reads the
sequencer back from the Apple II and uploads it anyway if it's missing or
different, saying so; --install skips that check and always sends a fresh copy.
--language-card keeps sound going into a language card when main RAM is full:
about 9 s of sound instead of 6.
"""
import argparse
import os
import sys
import time
import tomllib

import a2hdr
import audioconv
import imgconv
import send
from PIL import Image

TABLE_ADDR = 0x0B00
TABLE_MAX = 256
SEQ_ADDR = 0x0C00
HGR_PAGES = {1: 0x2000, 2: 0x4000}
AUDIO_START = a2hdr.AUDIO_START

OP_END, OP_SHOW, OP_PLAY, OP_WAIT = 0, 1, 2, 3


def entry(op, a=0, b=0, c=0, d=0, e=0):
    return bytes([op, a, b, c, d, e])


def ticks(seconds):
    t = round(seconds * 10)
    if not 0 < t <= 0xFFFF:
        raise ValueError("wait/hold of %s s is out of range" % seconds)
    return t


KEYS = {"image": {"image", "hold", "crop", "invert", "dither"},
        "audio": {"audio", "gain", "even_out"},
        "wait": {"wait"}}


def convert_image(path, crop=False, invert=False, dither="atkinson"):
    if not os.path.exists(path):
        raise ValueError("%s: no such file" % path)
    img = imgconv.prepare(Image.open(path), crop=crop, invert=invert)
    bits = imgconv.atkinson(img) if dither == "atkinson" else imgconv.threshold(img)
    return imgconv.to_hgr(bits)


def check_scene(sc, where):
    """Each scene has exactly one action and only keys that belong to it."""
    if not isinstance(sc, dict):
        raise ValueError("%s: not a [[scene]] table" % where)
    kinds = [k for k in KEYS if k in sc]
    if len(kinds) != 1:
        known = set().union(*KEYS.values())
        typos = sorted(set(sc) - known)
        raise ValueError("%s: needs exactly one of image, audio or wait (found %s)%s" % (
            where, ", ".join(kinds) or "none", "; unknown key(s): " + ", ".join(typos) if typos else ""))
    extra = set(sc) - KEYS[kinds[0]]
    if extra:
        article = "an" if kinds[0][0] in "aeiou" else "a"
        raise ValueError("%s: unknown key(s) for %s %s scene: %s (allowed: %s)" % (
            where, article, kinds[0], ", ".join(sorted(extra)), ", ".join(sorted(KEYS[kinds[0]]))))
    if sc.get("dither", "atkinson") not in ("atkinson", "threshold"):
        raise ValueError("%s: dither must be \"atkinson\" or \"threshold\"" % where)
    for k in ("hold", "wait", "gain"):
        if k in sc and not isinstance(sc[k], (int, float)):
            raise ValueError("%s: %s must be a number" % (where, k))
    if "even_out" in sc and not isinstance(sc["even_out"], bool):
        raise ValueError("%s: even_out must be true or false" % where)
    return kinds[0]


def build(project_path, language_card=False):
    """Return a list of (addr, bytes, label) to upload, sequencer excluded."""
    base = os.path.dirname(os.path.abspath(project_path))
    with open(project_path, "rb") as f:
        proj = tomllib.load(f)
    unknown = set(proj) - {"scene"}
    if unknown:
        raise ValueError("unknown top-level key(s): %s (scenes go in [[scene]] tables)" % ", ".join(sorted(unknown)))
    scenes = proj.get("scene", [])
    if not scenes:
        raise ValueError("no [[scene]] entries")

    plan, table = [], bytearray()
    next_page, audio_used = 1, 0

    for i, sc in enumerate(scenes, 1):
        where = "scene %d" % i
        kind = check_scene(sc, where)
        if kind == "image":
            if next_page > 2:
                raise ValueError("%s: only two image scenes fit (HGR pages 1 and 2)" % where)
            data = convert_image(os.path.join(base, sc["image"]), bool(sc.get("crop", False)),
                                 bool(sc.get("invert", False)), sc.get("dither", "atkinson"))
            plan.append((HGR_PAGES[next_page], data, "%s: %s -> HGR page %d" % (where, sc["image"], next_page)))
            table += entry(OP_SHOW, next_page)
            next_page += 1
            if sc.get("hold"):
                table += entry(OP_WAIT, *divmod(ticks(sc["hold"]), 256)[::-1])
        elif kind == "audio":
            if not os.path.exists(os.path.join(base, sc["audio"])):
                raise ValueError("%s: %s: no such file" % (where, sc["audio"]))
            data = audioconv.convert(os.path.join(base, sc["audio"]), sc.get("gain", 1.0),
                                     even_out=sc.get("even_out", False))
            if audio_used + len(data) > a2hdr.audio_pages_limit(language_card) * 256:
                raise ValueError("%s: audio doesn't fit below $%04X%s" % (where, *(
                    (a2hdr.CARD_END, "") if language_card else
                    (a2hdr.AUDIO_MAIN_END, " (with a language card in slot 0, --language-card holds more)"))))
            for addr, piece in a2hdr.audio_pieces(audio_used, data):
                plan.append((addr, piece, "%s: %s -> $%04X (%.1f s)" % (
                    where, sc["audio"], addr, len(piece) * 2 / a2hdr.SPEAKER_RATE)))
            table += entry(OP_PLAY, 0, a2hdr.audio_addr(audio_used) >> 8, len(data) // 256)
            audio_used += len(data)
        else:
            table += entry(OP_WAIT, *divmod(ticks(sc["wait"]), 256)[::-1])

    table += entry(OP_END)
    if len(table) > TABLE_MAX:
        raise ValueError("too many scenes (table is %d bytes, max %d)" % (len(table), TABLE_MAX))
    plan.append((TABLE_ADDR, bytes(table), "scene table (%d entries)" % (len(table) // 6 - 1)))
    return plan


def sequencer_bin():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "sequencer.bin")
    if not os.path.exists(path):
        sys.exit("sequencer.bin not found; run: a2kit build")
    return open(path, "rb").read()


def sequencer_present(port, seq):
    """True if the sequencer in memory matches seq, False if it doesn't, None if
    the receiver couldn't be asked. Its variables are all in zero page, so a copy
    that has already played a show still matches."""
    try:
        return send.read_memory(port, SEQ_ADDR, len(seq)) == seq
    except IOError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "send"])
    ap.add_argument("project")
    ap.add_argument("--port")
    a2hdr.add_baud_arg(ap)
    ap.add_argument("--install", action="store_true", help="also upload the sequencer")
    ap.add_argument("--rtscts", action="store_true")
    a2hdr.add_language_card_arg(ap)
    a = ap.parse_args()

    plan = build(a.project, a.language_card)
    if a.install:
        plan.insert(0, (SEQ_ADDR, sequencer_bin(), "sequencer"))
    total = sum(len(d) for _, d, _ in plan)
    for addr, data, label in plan:
        print("$%04X  %6d bytes  %s" % (addr, len(data), label))
    print("total %d bytes, about %.0f s to upload at %d baud" % (
        total, send.upload_seconds([len(d) for _, d, _ in plan], a.baud), a.baud))
    if a.command == "plan":
        return

    if not a.port:
        sys.exit("--port required")
    port = send.open_port(a.port, a.baud, rtscts=a.rtscts)
    if not a.install:
        # the upload ends by jumping to $0C00, so make sure the sequencer is really there
        seq = sequencer_bin()
        found = sequencer_present(port, seq)
        if not found:
            print("the sequencer %s, so it is uploaded too, as with --install"
                  % ("isn't in memory" if found is False else "couldn't be checked"), file=sys.stderr)
            plan.insert(0, (SEQ_ADDR, seq, "sequencer"))
    s = send.Sender(port, language_card=a.language_card)
    t0 = time.monotonic()
    for addr, data, label in plan:
        print("sending", label, file=sys.stderr)
        s.send_blob(data, addr)
    s.finish(SEQ_ADDR)
    print("uploaded in %.1f s; the show is playing" % (time.monotonic() - t0), file=sys.stderr)


if __name__ == "__main__":
    main()
