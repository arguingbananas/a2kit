#!/usr/bin/env python3
"""Regenerate the starter assets in this folder. Everything here is original
and generated from code, so you can edit the parameters and re-run:

    python3 examples/make_examples.py

Writes: logo.png, sphere.png, frames/f00..f35.png, bounce.gif,
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
wait = 1.0
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
    make_logo(); make_sphere(); make_frames(); make_stress(); make_audio(); make_long(); make_show()
    print("examples written to", HERE)
