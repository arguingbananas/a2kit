"""Build a small show, load it into a py65 6502 simulator with the real
sequencer.bin, and check scene order, wait timing, and the speaker waveform
(edge timing is decoded back to the audio nibbles and compared)."""
import os, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
from PIL import Image, ImageDraw
from py65.devices.mpu6502 import MPU
from py65.devices.mpu65c02 import MPU as MPU65C02
from py65.memory import ObservableMemory
import show as a2kit, a2hdr
from testlib import expect, finish

tmp = tempfile.mkdtemp()
for n, shade in (("a.png", "white"), ("b.png", "gray")):
    im = Image.new("RGB", (280, 192), "black"); ImageDraw.Draw(im).ellipse((40, 20, 240, 170), fill=shade); im.save(os.path.join(tmp, n))
t = np.arange(int(0.3 * 22050)) / 22050
w = wave.open(os.path.join(tmp, "s.wav"), "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
w.writeframes((0.7 * np.sin(2 * np.pi * (200 * t + 900 * t * t)) * 32767).astype("<i2").tobytes()); w.close()
open(os.path.join(tmp, "show.toml"), "w").write('''
[[scene]]
image = "a.png"
hold = 0.3
[[scene]]
image = "b.png"
[[scene]]
audio = "s.wav"
[[scene]]
wait = 0.2
''')
plan = a2kit.build(os.path.join(tmp, "show.toml"))
for addr, data, label in plan: print("  plan $%04X %6d  %s" % (addr, len(data), label))
audio = [d for a, d, l in plan if a == a2kit.AUDIO_START][0]

def make(key_after=None, i_flag_before=False, d_flag_before=False, cpu=MPU):
    mem = ObservableMemory(); mpu = cpu(memory=mem)
    log, edges = [], []
    names = {0xC050: "gfx", 0xC051: "text", 0xC052: "full", 0xC054: "page1", 0xC055: "page2", 0xC057: "hires"}
    for a, n in names.items():
        mem.subscribe_to_read([a], lambda addr, _n=n: (log.append((_n, mpu.processorCycles)), 0)[1])
    i_at_click = set()
    mem.subscribe_to_read([0xC030], lambda a: (edges.append(mpu.processorCycles), i_at_click.add(bool(mpu.p & 4)), 0)[2])
    key = {"v": 0}
    mem.subscribe_to_read([0xC000], lambda a: key["v"])
    mem.subscribe_to_read([0xC010], lambda a: (key.update(v=0), 0)[1])
    for addr, data, _ in plan:
        for i, b in enumerate(data): mem[addr + i] = b
    for i, b in enumerate(open(os.path.join(HERE, "..", "sequencer.bin"), "rb").read()): mem[0x0C00 + i] = b
    for i, b in enumerate([0x20, 0x00, 0x0C, 0x00]): mem[0x0300 + i] = b
    mpu.pc = 0x0300; mpu.sp = 0xFF
    if i_flag_before: mpu.p |= 4
    else: mpu.p &= ~4
    if d_flag_before: mpu.p |= 8
    n = 0
    while mpu.pc != 0x0303:
        mpu.step(); n += 1
        if key_after and mpu.processorCycles > key_after: key["v"] = 0x8D
        assert n < 5_000_000, "runaway"
    mpu.i_at_click = i_at_click
    return log, edges, mpu, mem

log, edges, mpu, mem = make()
seq = [n for n, _ in log]
print("event order:", seq)
ok_order = seq == ["gfx", "full", "hires", "page1", "gfx", "full", "hires", "page2", "page1", "text"]

def screen_after(events):
    """What the Apple II displays after these soft-switch accesses. PAGE2 applies to
    the text screen too, so 'text' with page 2 shows $0800 (our own code) as characters."""
    mode, page = "text", 1
    for n in events:
        if n == "gfx": mode = "graphics"
        elif n == "text": mode = "text"
        elif n == "page1": page = 1
        elif n == "page2": page = 2
    return mode, page
t_p1 = [c for n, c in log if n == "page1"][0]; t_p2 = [c for n, c in log if n == "page2"][0]
hold = (t_p2 - t_p1) / a2hdr.CPU_HZ
print("scene order ok:", ok_order, "| image 1 hold: %.3f s (want 0.3)" % hold, "->", abs(hold - 0.3) < 0.01)
expect(ok_order, "scenes run in order: page 1, page 2, then back to page 1 and text")
expect(screen_after(seq) == ("text", 1), "the show ends on text page 1 (not text page 2, which shows garbage)")
expect(abs(hold - 0.3) < 0.01, "a 0.3 s hold lasts 0.3 s")
print("images landed in HGR pages:", mem[0x2000 + 0x1000] is not None and any(mem[0x2000 + i] for i in range(0x2000)) and any(mem[0x4000 + i] for i in range(0x2000)))

e1, e2 = edges[0::2], edges[1::2]
nib = [(w - 3) // 5 - 1 for w in (b - a for a, b in zip(e1, e2))]
want = []
for b in audio: want += [b >> 4, b & 15]
per = [b - a for a, b in zip(e1, e1[1:])]
print("samples played:", len(e1), "of", len(want), "| waveform matches data:", nib == want)
expect(nib == want, "speaker clicks decode back to the audio data")
bad = [p for p in per if p != a2hdr.SPEAKER_PLAYER_CYCLES]
print("period: %d x %d cycles; %d page-boundary outliers %s" % (len(per) - len(bad), a2hdr.SPEAKER_PLAYER_CYCLES, len(bad), sorted(set(bad))))
expect(set(bad) <= {140} and len(bad) == len(audio) // 256 - 1, "sample period is 128 cycles except one 140 at each page change")
t_end = [c for n, c in log if n == "text"][0]
waited = (t_end - e2[-1]) / a2hdr.CPU_HZ
print("wait after audio: %.3f s (want 0.2)" % waited)
expect(abs(waited - 0.2) < 0.01, "a 0.2 s wait lasts 0.2 s")
expect(mpu.i_at_click == {True} and not (mpu.p & 4), "interrupts off while clicking, restored (clear) afterwards")
_, _, mpu_i, _ = make(i_flag_before=True)

# decimal mode set on entry must not change anything (and must be restored)
log_d, edges_d, mpu_d, _ = make(d_flag_before=True)
expect(edges_d == edges and [n for n, _ in log_d] == seq and bool(mpu_d.p & 8),
       "entered in decimal mode: same clicks at the same cycles, decimal mode restored")

# the 65C02 (enhanced IIe, IIc): the cycle-timed player must click identically
log_c, edges_c, _, _ = make(cpu=MPU65C02)
print("65C02 core: %d clicks, identical timing to the 6502: %s" % (len(edges_c), edges_c == edges))
expect(edges_c == edges and [n for n, _ in log_c] == seq, "65C02: same clicks at the same cycles as the 6502")
expect(bool(mpu_i.p & 4), "entered with interrupts off, still off afterwards")

# key press during the first hold stops the show before scene 2
log2, edges2, *_ = make(key_after=t_p1 + 50_000)
stopped = [n for n, _ in log2] == ["gfx", "full", "hires", "page1", "page1", "text"] and not edges2
print("key abort: stopped early:", stopped)
expect(stopped, "a key during a hold stops the show")
log3, _, _, _ = make(key_after=t_p2 + 50_000)
expect(screen_after([n for n, _ in log3]) == ("text", 1), "a key while page 2 is showing still ends on text page 1")
finish("test_sequencer")
