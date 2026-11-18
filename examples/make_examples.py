#!/usr/bin/env python3
"""Regenerate the starter assets in this folder. Everything here is original
and generated from code, so you can edit the parameters and re-run:

    python3 examples/make_examples.py

Writes: logo.png, sphere.png, colour.png, balloon.gif, hello_apple.gif,
        frames/f00..f35.png, bounce.gif,
        stress/f000..f089.png, jingle.wav, boing.wav, chirp.wav, long.wav,
        show.toml, long_show.toml
"""
import math
import os
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 280, 192          # Apple II HGR resolution
OUT = lambda *p: os.path.join(HERE, *p)


def font(size):
    return ImageFont.load_default(size=size)


def centered(d, y, text, size, fill=255):
    f = font(size)
    box = d.textbbox((0, 0), text, font=f)
    d.text(((W - (box[2] - box[0])) // 2 - box[0], y), text, font=f, fill=fill)


# ---------------------------------------------------------------- images
def make_logo():
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    d.rectangle((4, 4, W - 5, H - 5), outline=255, width=2)
    d.rectangle((9, 9, W - 10, H - 10), outline=140, width=1)
    for r in range(80, 0, -10):                         # concentric rings behind the title
        d.ellipse((W // 2 - r * 1.6, 96 - r, W // 2 + r * 1.6, 96 + r), outline=50 + r, width=1)
    centered(d, 52, "A2KIT", 52)
    centered(d, 118, "HELLO, APPLE II", 20)
    centered(d, 150, "280 x 192  .  HIRES", 12, 170)
    im.save(OUT("logo.png"))


def make_sphere():
    y, x = np.mgrid[0:H, 0:W].astype(float)
    cx, cy, R = 140, 88, 74
    dx, dy = (x - cx) / R, (y - cy) / R
    r2 = dx * dx + dy * dy
    nz = np.sqrt(np.clip(1 - r2, 0, None))
    light = np.clip(0.55 * -dx + 0.55 * -dy + 0.63 * nz, 0, 1)      # light from the upper left
    ball = 25 + 230 * light ** 1.3
    floor = 40 + 70 * ((y - 140) / 52).clip(0, 1) + 30 * (((x // 16) + (y // 12)) % 2)
    img = np.where(r2 <= 1, ball, np.where(y > 150, floor, 18 + 30 * (y / 150)))
    shadow = (((x - 168) / 78) ** 2 + ((y - 168) / 11) ** 2 <= 1) & (r2 > 1) & (y > 150)
    img = np.where(shadow, img * 0.35, img)
    Image.fromarray(img.clip(0, 255).astype(np.uint8), "L").save(OUT("sphere.png"))


def landscape(bx=92, by=50):
    """A landscape for `a2kit image --colour`, drawn in the six hi-res colours, as
    Apple II artists drew: flat areas convert cleanly, where shades would turn to
    grain. The sky turns from blue to orange through stripes. Below: a sun, blue
    mountains, green hills, a lake, a balloon at (bx, by) and a strip of the six colours."""
    BLACK, VIOLET, GREEN, BLUE, ORANGE, WHITE = (0, 0, 0), (230, 40, 255), (25, 215, 0), (25, 144, 255), (230, 111, 0), (255, 255, 255)
    y, x = np.mgrid[0:H, 0:W].astype(float)
    img = np.zeros((H, W, 3))
    img[:] = BLUE                                                                          # sky
    img[(y >= 60) & ((y - 60) % 6 < (y - 60) // 6 + 1)] = ORANGE                           # sunset: orange stripes widen, solid from 96
    img[(x - 200) ** 2 + ((y - 70) * 1.15) ** 2 <= 22 ** 2] = WHITE                         # sun
    img[y > 112 + 14 * np.sin(x / 23) + 8 * np.sin(x / 9 + 1)] = BLUE                       # mountains
    img[y > 132 + 18 * np.sin(x / 41 + 2)] = GREEN                                         # hills
    img[((x - 70) / 52) ** 2 + ((y - 160) / 12) ** 2 <= 1] = BLUE                           # lake
    balloon = ((x - bx) / 15) ** 2 + ((y - by) / 19) ** 2 <= 1
    img[balloon] = ORANGE                                                                  # balloon, orange
    img[balloon & (abs(x - bx) < 4)] = WHITE                                               # with a white stripe:
    img[(abs(x - bx) < 4) & (y > by + 22) & (y < by + 30)] = BLACK                         # violet beside blue looks blocky
    for i, c in enumerate((BLACK, VIOLET, GREEN, BLUE, ORANGE, WHITE)):                    # the six colours
        img[(y >= 182) & (x >= i * W / 6) & (x < (i + 1) * W / 6)] = c
    return Image.fromarray(img.astype(np.uint8), "RGB")


def make_colour():
    landscape().save(OUT("colour.png"))


BALLOON_FRAMES = 160    # 16 s at 10 fps


def make_balloon():
    """The balloon drifting across the landscape's sky, for `a2kit video --colour`:
    2 dots a frame, so it keeps its colours (a colour is 2 dots wide), bobbing a little."""
    pics = []
    for i in range(BALLOON_FRAMES):
        bx = -24 + 2 * i
        by = 50 + round(5 * math.sin(i / 12))
        pics.append(landscape(bx, by).quantize(colors=8, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE))
    pics[0].save(OUT("balloon.gif"), save_all=True, append_images=pics[1:], duration=100, loop=0)


# ---------------------------------------------------------------- video
N_FRAMES = 36          # 6 seconds at 6 fps; slow, small motion keeps each frame inside the serial budget


def ball_pos(i):
    t = i / (N_FRAMES - 1)
    x = 20 + t * (W - 40)
    bounce = abs(math.sin(t * math.pi * 2))                          # two bounces
    return x, 154 - 100 * bounce


def make_frames():
    os.makedirs(OUT("frames"), exist_ok=True)
    pics = []
    for i in range(N_FRAMES):
        im = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(im)
        d.line((0, 165, W, 165), fill=200, width=1)
        x, y = ball_pos(i)
        r = 9
        sh = 1 - (165 - y) / 140                                     # shadow shrinks as the ball rises
        d.ellipse((x - r * (0.6 + 0.4 * sh), 168, x + r * (0.6 + 0.4 * sh), 172), fill=110)
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)
        d.ellipse((x - r * 0.6, y - r * 0.7, x - r * 0.1, y - r * 0.2), fill=90)   # gives the dither some texture
        im.save(OUT("frames", "f%02d.png" % i))
        pics.append(im.convert("P"))
    pics[0].save(OUT("bounce.gif"), save_all=True, append_images=pics[1:], duration=167, loop=0)


# ---------------------------------------------------------------- silhouettes
SIL_FPS = 10


def ease(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def draw_apple(d, cx, cy, s, fill):
    """An apple of size s centred at (cx, cy): two lobes, a dent on top, a stem and a leaf."""
    if s < 1:
        return
    r = 0.62 * s
    for dx in (-0.33 * s, 0.33 * s):
        d.ellipse((cx + dx - r, cy - r, cx + dx + r, cy + r * 1.05), fill=fill)
    d.ellipse((cx - 0.6 * s, cy - 0.15 * s, cx + 0.6 * s, cy + 0.75 * s), fill=fill)
    bg = 255 - fill
    d.ellipse((cx - 0.12 * s, cy - 0.72 * s, cx + 0.12 * s, cy - 0.5 * s), fill=bg)      # the dent
    d.line((cx, cy - 0.55 * s, cx + 0.08 * s, cy - 0.95 * s), fill=fill, width=max(1, int(0.09 * s)))
    leaf = [(cx + 0.1 * s + 0.4 * s * u, cy - 0.85 * s - 0.25 * s * math.sin(math.pi * u) * sgn - 0.12 * s * u)
            for sgn in (1, -1) for u in (np.linspace(0, 1, 8) if sgn > 0 else np.linspace(1, 0, 8))]
    d.polygon(leaf, fill=fill)


def leaf_shape(cx, cy, size, angle):
    pts = []
    for a in np.linspace(0, 2 * math.pi, 12, endpoint=False):
        x, y = size * math.cos(a), 0.45 * size * math.sin(a)
        pts.append((cx + x * math.cos(angle) - y * math.sin(angle), cy + x * math.sin(angle) + y * math.cos(angle)))
    return pts


def hill_y(x):
    return 150 - 28 * math.exp(-((x - 130) / 85) ** 2)


def draw_walker(d, x, t):
    """A figure walking, feet on the hill; t sets the stride."""
    fy = hill_y(x)
    swing = 0.5 * math.sin(t * 0.9)
    hip = (x, fy - 18)
    for sgn in (1, -1):                                                    # legs
        a = sgn * swing
        knee = (hip[0] + 6 * math.sin(a), hip[1] + 9)
        d.line((hip, knee, (knee[0] + 6 * math.sin(a - 0.25 * abs(a)), fy)), fill=0, width=3)
    neck = (x + 1, fy - 34)
    d.line((hip, neck), fill=0, width=5)                                   # body
    for sgn in (1, -1):                                                    # arms
        a = -sgn * swing
        d.line((neck[0], neck[1] + 3, neck[0] + 8 * math.sin(a), neck[1] + 15), fill=0, width=2)
    d.ellipse((x - 4, fy - 44, x + 6, fy - 34), fill=0)                    # head


def draw_tree(d, x):
    y = hill_y(x)
    d.line((x, y + 2, x, y - 30), fill=0, width=5)
    for dx, dy, r in ((0, -42, 16), (-12, -32, 11), (12, -33, 12), (-5, -52, 10), (8, -50, 9)):
        d.ellipse((x + dx - r, y + dy - r, x + dx + r, y + dy + r), fill=0)


def silhouette_frame(f):
    """Frame f of 'Hello, Apple': an original shadow-play animation, black and white."""
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    # 1. an apple grows, bounces and splits to show the title (0-6 s)
    if f < 60:
        t = f
        s = 60 * ease((t + 1) / 17)
        cy = 96 - (abs(math.sin((t - 16) * math.pi / 9)) * 30 * max(0, 1 - (t - 16) / 26) if 17 <= t < 36 else 0)
        if t < 36:
            draw_apple(d, 140, cy, s, 255)
        else:
            centered(d, 82, "A2KIT", 30)
            apple = Image.new("L", (W, H), 0)
            draw_apple(ImageDraw.Draw(apple), 140, 96, 60, 255)
            u = ((t - 35) / 25) ** 1.5
            for x0, x1, sgn in ((0, 140, -1), (140, W, 1)):
                half = apple.crop((x0, 0, x1, H)).rotate(sgn * 50 * u, center=((140 - x0), 96))
                mask = half.point(lambda v: 255 if v else 0)
                im.paste(half, (x0 + int(sgn * 150 * u), int(60 * u * u)), mask)
        return im
    # ink spills up from the bottom until the screen is white (6-7.2 s)
    if f < 72:
        t = f - 60
        centered(d, 82, "A2KIT", 30)
        for bx, k in ((40, 1.0), (120, 1.3), (200, 0.9), (260, 1.15), (90, 0.8)):
            r = (t + 2) ** 2 * 1.1 * k
            d.ellipse((bx - r, H - r, bx + r, H + r), fill=255)
        return im
    # 2. sunset on a hill: a figure walks past a tree, the sun sinks (7.2-16.2 s)
    if f < 162:
        t = f - 72
        im.paste(255, (0, 0, W, H))
        sy = 70 + 0.55 * t
        for r, fill in ((34, 0), (27, 255), (22, 0)):
            d.ellipse((190 - r, sy - r, 190 + r, sy + r), fill=fill)
        for k, y in enumerate(range(32, 62, 8)):                           # bands of cloud
            off = (t * (1 + k * 0.4)) % 340 - 60
            d.rounded_rectangle((off, y, off + 50 + 10 * k, y + 3), radius=1, fill=0)
        d.polygon([(0, H)] + [(x, hill_y(x)) for x in range(0, W + 1, 4)] + [(W, H)], fill=0)
        draw_tree(d, 62)
        draw_walker(d, -20 + 3.4 * t, t)
        return im
    # a diagonal wipe to black (16.2-17.2 s)
    if f < 172:
        t = f - 162
        im = silhouette_frame(161)
        d = ImageDraw.Draw(im)
        k = (t + 1) * 47
        d.polygon([(0, 0), (k, 0), (k - H, H), (0, H)], fill=0)
        return im
    # 3. a flock of birds, which fall and become leaves (17.2-26.2 s)
    if f < 262:
        t = f - 172
        rng = np.random.default_rng(7)
        start = rng.uniform(-120, 0, 14)
        height = rng.uniform(30, 120, 14)
        phase = rng.uniform(0, 6.3, 14)
        for i in range(14):
            x = start[i] + 4.5 * min(t, 45)
            y = height[i] + 6 * math.sin(0.15 * t + phase[i])
            if t < 45:
                flap = 9 * math.sin(0.9 * t + phase[i])
                d.line(((x - 13, y - flap), (x, y), (x + 13, y - flap)), fill=255, width=2)
            else:
                u = t - 45
                fy = min(y + 2.2 * u + 0.03 * u * u, H - 6 - (i % 3) * 3)
                fx = x + 12 * math.sin(0.25 * u + phase[i])
                angle = 0.6 * math.sin(0.3 * u + phase[i]) if fy < H - 10 else 0.2 * i
                d.polygon(leaf_shape(fx, fy, 6 + i % 3, angle), fill=255)
        return im
    # an iris opens to white (26.2-27.2 s)
    if f < 272:
        t = f - 262
        im = silhouette_frame(261)
        d = ImageDraw.Draw(im)
        r = 18 * (t + 1)
        d.ellipse((140 - r, 96 - r, 140 + r, 96 + r), fill=255)
        return im
    # 4. the apple again, the title, and an iris closing (27.2-32 s)
    t = f - 272
    im.paste(255, (0, 0, W, H))
    draw_apple(d, 140, 70, 40 + 4 * math.sin(t * 0.35), 0)
    centered(d, 120, "HELLO, APPLE", 22, 0)
    bx, by = -20 + 7 * t, 34 + 4 * math.sin(0.4 * t)                     # a bird crosses
    flap = 7 * math.sin(0.9 * t)
    d.line(((bx - 11, by - flap), (bx, by), (bx + 11, by - flap)), fill=0, width=2)
    if t >= 28:
        r = 175 * (1 - ease((t - 28) / 19))
        ring = Image.new("L", (W, H), 0)
        ImageDraw.Draw(ring).ellipse((140 - r, 96 - r, 140 + r, 96 + r), fill=255)
        im = Image.composite(im, Image.new("L", (W, H), 0), ring)
    return im


SIL_FRAMES = 320        # 32 s at 10 fps


def make_silhouettes():
    """'Hello, Apple': an original black and white shadow-play animation, the
    kind of full-screen motion that tests how fast the stream can keep up."""
    pics = [silhouette_frame(f).point(lambda v: 255 if v > 127 else 0).convert("1") for f in range(SIL_FRAMES)]
    pics[0].save(OUT("hello_apple.gif"), save_all=True, append_images=pics[1:], duration=1000 // SIL_FPS, loop=0)


# ---------------------------------------------------------------- stress test
STRESS_MOVING, STRESS_HOLD = 48, 42     # 8 s of motion, then 7 s of test card at 6 fps
STRESS_RINGS = 8


def stress_moving(i):
    """Large rings and a spinning cross: enough change to fill the 115200 byte budget."""
    t = i / 6
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    for b in range(STRESS_RINGS):
        r = 16 + b * 3
        x = W / 2 + (W / 2 - r - 4) * math.sin(t * (1.1 + b * 0.37) + b * 1.7)
        y = H / 2 + (H / 2 - r - 4) * math.cos(t * (1.3 + b * 0.29) + b * 0.9)
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)
        d.ellipse((x - r / 2, y - r / 2, x + r / 2, y + r / 2), fill=0)
    a = t * 1.4
    for k in range(4):
        q = a + k * math.pi / 2
        d.line((W / 2, H / 2, W / 2 + 150 * math.cos(q), H / 2 + 150 * math.sin(q)), fill=255, width=3)
    return im


def stress_card():
    """A still grid: bytes lost during the motion show up on it as stray marks."""
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W - 1, H - 1), outline=255)
    d.rectangle((8, 8, W - 9, H - 9), outline=255)
    for x in range(20, W - 20, 20):
        d.line((x, 20, x, H - 21), fill=255)
    for y in range(20, H - 20, 20):
        d.line((20, y, W - 21, y), fill=255)
    d.ellipse((W // 2 - 60, H // 2 - 60, W // 2 + 60, H // 2 + 60), outline=255)
    return im


def make_stress():
    os.makedirs(OUT("stress"), exist_ok=True)
    for i in range(STRESS_MOVING):
        stress_moving(i).save(OUT("stress", "f%03d.png" % i))
    card = stress_card()
    for i in range(STRESS_MOVING, STRESS_MOVING + STRESS_HOLD):
        card.save(OUT("stress", "f%03d.png" % i))


# ---------------------------------------------------------------- audio
RATE = 22050


def write_wav(name, x):
    x = np.clip(x / max(1e-9, np.max(np.abs(x))) * 0.9, -1, 1)
    with wave.open(OUT(name), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def tone(freq, dur, decay=6.0):
    t = np.arange(int(dur * RATE)) / RATE
    wave_ = np.sign(np.sin(2 * np.pi * freq * t)) * 0.5 + np.sin(2 * np.pi * freq * t) * 0.5   # square-ish: good on a speaker
    return wave_ * np.exp(-decay * t) * np.minimum(1, t * 400)


def make_audio():
    notes = [261.63, 329.63, 392.0, 523.25, 392.0, 523.25]          # C E G C G C
    write_wav("jingle.wav", np.concatenate([tone(f, 0.22 if k < 5 else 0.6, 5) for k, f in enumerate(notes)]))
    t = np.arange(int(0.26 * RATE)) / RATE
    one = np.sin(2 * np.pi * np.cumsum(520 * np.exp(-9 * t) + 120) / RATE) * np.exp(-7 * t)   # pitch drops: a "boing"
    gap = np.zeros(int(0.07 * RATE))
    write_wav("boing.wav", np.concatenate([one, gap, one * 0.8, gap, one * 0.65]))
    t = np.arange(int(0.5 * RATE)) / RATE
    write_wav("chirp.wav", np.sin(2 * np.pi * (200 * t + 1400 * t * t)) * np.minimum(1, (0.5 - t) * 40))


def make_show():
    open(OUT("show.toml"), "w").write('''# A starter show. Asset paths are relative to this file.
# Run:  ./a2kit show plan examples/show.toml
#       ./a2kit show send examples/show.toml --port /dev/ttyUSB0 --install

[[scene]]
image = "logo.png"      # HGR page 1
hold  = 3.0             # stay on it for 3 seconds

[[scene]]
audio = "jingle.wav"    # plays after the logo's 3 s hold, over the same picture (about 1.7 s)

[[scene]]
image = "sphere.png"    # HGR page 2: shows how a grey ramp dithers
hold  = 3.0

[[scene]]
audio = "chirp.wav"
gain  = 1.0

[[scene]]
image  = "colour.png"   # a third picture: kept packed, then unpacked onto page 1
colour = true           # in colour (on a colour monitor or TV)
hold   = 3.0
''')


def make_long():
    """A rising scale of half-second notes, 9.1 s in all: too long for main RAM, so
    it needs a language card. The sound reaches the card at 6.2 s, partway through
    a note, so anything wrong there is easy to hear."""
    notes = [220 * 2 ** (k / 12) for k in range(18)]
    x = np.concatenate([tone(f, 0.5, decay=0.5) for f in notes] + [tone(notes[-1], 0.1, decay=0.5)])
    write_wav("long.wav", x)
    with open(OUT("long_show.toml"), "w") as f:
        f.write('''# A long sound that needs a language card in slot 0 (check with: a2kit lcprobe).
# Run:  ./a2kit show plan examples/long_show.toml --language-card
#       ./a2kit show send examples/long_show.toml --port /dev/ttyUSB0 --install --language-card

[[scene]]
image = "sphere.png"

[[scene]]
audio = "long.wav"
''')


if __name__ == "__main__":
    make_logo(); make_sphere(); make_colour(); make_balloon(); make_silhouettes(); make_frames(); make_stress(); make_audio(); make_long(); make_show()
    print("examples written to", HERE)
