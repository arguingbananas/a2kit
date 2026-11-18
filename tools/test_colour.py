"""Colour pictures: the screen model against what MAME showed, the converter on flat
colours and the colour example, and the `image --colour` and `show` colour options."""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
from PIL import Image
import a2hdr, imgconv, show
from testlib import expect, finish

CODE = {(0, 0, 0): ".", (255, 255, 255): "W", imgconv.LORES[3]: "V", imgconv.LORES[12]: "G",
        imgconv.LORES[6]: "B", imgconv.LORES[9]: "O"}


def line(row_bytes, n=60):
    """The first n half-dots of one scanline as letters, as colour_preview draws them."""
    h = np.concatenate(([0, 0, 0], imgconv.half_dots(row_bytes), [0, 0, 0]))
    return "".join(CODE.get(tuple(int(v) for v in c), "?") for c in imgconv.half_dot_colours(h)[:n])


def row_of(pal, lit):
    out = []
    for col in range(40):
        b = sum(1 << k for k in range(7) if lit(col * 7 + k))
        out.append(b | (0x80 if pal else 0))
    return out


# 1. the screen model, against a MAME 0.264 screenshot of the same rows (16 Nov 2026)
seen = {
    (0, "even"): "V" * 60,
    (0, "odd"): ".." + "G" * 58,
    (1, "even"): "." + "B" * 59,
    (1, "odd"): "..." + "O" * 57,
    (0, "all"): "W" * 60,
    (0, "every 4th"): "VV......" * 7 + "VV..",
    (0, "pairs"): "WWWW...." * 7 + "WWWW",
    (1, "every 4th"): ".BB......BB......BB......BB......BB......BB......BB......BB.",
}
lits = {"even": lambda x: x % 2 == 0, "odd": lambda x: x % 2 == 1, "all": lambda x: True,
        "every 4th": lambda x: x % 4 == 0, "pairs": lambda x: x % 4 in (0, 1)}
for (pal, name), want in seen.items():
    got = line(row_of(pal, lits[name]))
    expect(got == want, "palette %d, %s dots: %s, MAME showed %s" % (pal, name, got, want))
expect(imgconv.colour_preview(bytes(0x2000)).size == (560, 384), "colour preview is 560x384")

# 2. flat areas of each colour convert to that colour
for name, rgb in (("violet", imgconv.LORES[3]), ("green", imgconv.LORES[12]), ("blue", imgconv.LORES[6]),
                  ("orange", imgconv.LORES[9]), ("white", (255, 255, 255)), ("black", (0, 0, 0))):
    scr = imgconv.colour_screen(Image.new("RGB", (280, 192), rgb))
    prev = np.asarray(imgconv.colour_preview(scr))[100, 20:540]
    share = np.mean(np.all(prev == rgb, axis=1))
    expect(share > 0.95, "flat %s comes out %s (%.0f%%)" % (name, name, 100 * share))
# bands of the six colours, one above another: away from where they meet, no stray dots
SIX = [(0, 0, 0), imgconv.LORES[3], imgconv.LORES[12], imgconv.LORES[6], imgconv.LORES[9], (255, 255, 255)]
bands = np.zeros((192, 280, 3), np.uint8)
for i, rgb in enumerate(SIX):
    bands[32 * i:32 * i + 32] = rgb
prev = np.asarray(imgconv.colour_preview(imgconv.colour_screen(Image.fromarray(bands, "RGB"))))
stray = sum(int(np.any(prev[64 * i + 8:64 * i + 56, 8:552] != rgb, axis=2).sum()) for i, rgb in enumerate(SIX))
expect(stray == 0, "bands of the six colours, dithered: no stray dots inside them (%d)" % stray)

# 3. the colour example: close to the picture once blurred, the way the eye sees it from a distance
pic = imgconv.prepare(Image.open(os.path.join(HERE, "..", "examples", "colour.png")), False, False, "RGB")
scr = imgconv.colour_screen(pic)
expect(len(scr) == 0x2000 and any(b & 0x80 for b in scr), "colour screen is 8 KB and uses the palette bit")
seen_img = np.asarray(imgconv.colour_preview(scr).resize((140, 96), Image.BOX), dtype=float)
want_img = np.asarray(pic.resize((140, 96), Image.BOX), dtype=float)
rms = np.sqrt(((seen_img - want_img) ** 2).mean())
print("colour example, blurred RMS difference: %.1f" % rms)
expect(rms < 20, "colour example matches the picture when blurred (RMS %.1f)" % rms)
grey = imgconv.colour_screen(Image.new("RGB", (280, 192), (128, 128, 128)), "threshold")
expect(len(set(grey)) <= 3, "threshold: a flat grey is one byte pattern, no dither")

# 4. the command line and show scenes
TMP = tempfile.mkdtemp(prefix="a2kit-test-")
src = os.path.join(TMP, "c.png")
Image.new("RGB", (280, 192), imgconv.LORES[9]).save(src)
for opt in ("--colour", "--color"):
    r = subprocess.run([sys.executable, os.path.join(HERE, "imgconv.py"), src, "-o", os.path.join(TMP, "c.a2b"),
                        opt, "--preview", os.path.join(TMP, "p.png")], capture_output=True, text=True)
    btype, _, payload = a2hdr.unpack(open(os.path.join(TMP, "c.a2b"), "rb").read())
    expect(r.returncode == 0 and btype == a2hdr.TYPE_IMAGE_HGR and len(payload) == 0x2000, "image %s writes an image block" % opt)
    expect(Image.open(os.path.join(TMP, "p.png")).size == (560, 384), "image %s --preview is 560x384" % opt)
    expect(sum(b >> 7 for b in payload) > 7000, "orange picture: palette bit on almost everywhere (%s)" % opt)


def plan(scene_lines):
    path = os.path.join(TMP, "s.toml")
    open(path, "w").write('[[scene]]\nimage = "c.png"\n' + scene_lines)
    return show.build(path)


expect(any(b & 0x80 for b in plan("colour = true\n")[0][1]), "show: colour = true makes a colour picture")
expect(any(b & 0x80 for b in plan("color = true\n")[0][1]), "show: color = true does too")
expect(not any(b & 0x80 for b in plan("")[0][1]), "show: without it, black and white as before")
for bad, why in (('colour = "yes"\n', "must be true or false"), ("colour = true\ncolor = true\n", "not both")):
    try:
        plan(bad)
        expect(False, "show: %r is refused" % bad.strip())
    except ValueError as e:
        expect(why in str(e), "show: %r is refused (%s)" % (bad.strip(), e))

finish("test_colour")
