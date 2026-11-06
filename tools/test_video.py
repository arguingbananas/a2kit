"""End-to-end video test. Synthetic animation -> videoconv -> reference decoder
and the real video.bin in a py65 6502 with a UART model (one byte every 532
cycles at 19200 baud, counting overruns).
  1. video only: screen must equal the encoder's model; stress at higher rates
  2. with speaker audio bursts: clicks decode back to the source samples
  2b. the same at 115200 baud, plus how much faster bytes could come
  3. fault injection: dropped / corrupted / inserted bytes must cost at most
     one frame of damage, never write outside $2000-$3FFF, and never read
     audio from outside $6000-$BFFF or the language card; plus boundary cases for both limits
  4. scrub heals what a fault leaves behind
Section 2 drives the simulated serial line from stream.plan_stream, the same
schedule the real host uses, so the host's burst timing is what is tested."""
import os, re, subprocess, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
from PIL import Image, ImageDraw
from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory
import a2hdr, imgconv, videoconv, stream as host
from testlib import expect, finish

ROOT = os.path.join(HERE, "..")
rng = np.random.default_rng(3)
bits = (rng.random((192, 280)) > 0.5).astype(np.uint8)
expect(bytes(videoconv.pack_screen(bits)) == imgconv.to_hgr(bits.tolist()), "fast HGR packer matches the reference packer")
print("pack_screen matches imgconv.to_hgr: checked")

tmp = tempfile.mkdtemp()
FR = os.path.join(tmp, "frames"); os.makedirs(FR)
N_MOVE, N_STILL = 24, 20
for i in range(N_MOVE + N_STILL):
    f = min(i, N_MOVE - 1)
    x = 60 + abs(((f * 9) % 320) - 160); y = 50 + abs(((f * 5) % 180) - 90)
    im = Image.new("L", (280, 192), 30)
    d = ImageDraw.Draw(im)
    for r in range(40, 0, -4): d.ellipse((x - r, y - r, x + r, y + r), fill=int(255 * (1 - r / 50)))
    im.convert("RGB").save(os.path.join(FR, "f%03d.png" % i))
t = np.arange(int(0.5 * 22050)) / 22050
w = wave.open(os.path.join(tmp, "s.wav"), "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
w.writeframes((0.7 * np.sin(2 * np.pi * (200 * t + 900 * t * t)) * 32767).astype("<i2").tobytes()); w.close()

def convert(extra=(), src=None):
    out = os.path.join(tmp, "clip.a2v")
    r = subprocess.run([sys.executable, os.path.join(HERE, "videoconv.py"), src or FR, "-o", out, "--fps", "6", "--baud", "19200", *extra], check=True, capture_output=True, text=True)
    print("  " + r.stderr.strip().replace("\n", "\n  "))
    return a2hdr.parse_stream(open(out, "rb").read())

def ref_decode(stream, screen, written=None):
    pos = i = 0
    while i < len(stream):
        b = stream[i]; i += 1
        if b == 0xFE: pos = 0
        elif b == 0: pass
        elif b == 0x80: break
        elif b == 0x7F: i += 2
        elif b == 0xFF: pos += stream[i] + 128 * stream[i + 1]; i += 2
        elif b > 0x80: pos += b & 0x7F
        else:
            screen[pos:pos + b] = np.frombuffer(stream[i:i + b], np.uint8)
            if written is not None: written.update(range(pos, pos + b))
            pos += b; i += b
    return screen

def model(budget):
    shown = np.zeros(0x2000, np.uint8)
    for i in range(N_MOVE + N_STILL):
        g = np.asarray(imgconv.prepare(Image.open(os.path.join(FR, "f%03d.png" % i)), False, False), dtype=np.float64)
        target = videoconv.pack_screen(videoconv.to_bits(g, "ordered"))
        _, shown = videoconv.encode_frame(shown, target, budget)
    return shown, target

LABELS = {m.group(2): int(m.group(1), 16) for m in re.finditer(r"al ([0-9A-F]+) \.(\w+)", open(os.path.join(ROOT, "video.lbl")).read())}

START = 400_000          # cycles before the first byte arrives (the player clears the screen first)
ACIA_D, ACIA_S = 0xC0A8, 0xC0A9
KNOWN_IO = {0xC000, 0xC010, 0xC030, 0xC050, 0xC051, 0xC052, 0xC054, 0xC057, ACIA_D, ACIA_S}


def arrivals(data, spacing):
    """Back-to-back bytes, one every `spacing` cycles."""
    return [(b, START + k * spacing) for k, b in enumerate(data)]


def from_plan(sched, baud=19200):
    """Byte arrival times (cycles) for a stream.plan_stream schedule."""
    byte_t = 10.0 / baud
    out = []
    for t, data, _kind in sched:
        for k, b in enumerate(data):
            out.append((b, START + int((t + (k + 1) * byte_t) * a2hdr.CPU_HZ)))
    return out


def run_6502(items, audio=b"", pokes=None, i_flag_before=False, d_flag_before=False):
    """items: [(byte, arrival_cycle)]. A byte that arrives while the previous one
    is still unread is an overrun (lost). pokes: {addr: value} written the first
    time the player reaches vframe (used to put the audio pointer near its limit).
    Returns screen, overruns, speaker edges, stray writes, unexpected reads of
    I/O or ROM, whether the recovery path ran, and the interrupt flag."""
    mem = ObservableMemory(); mpu = MPU(memory=mem)
    st = {"i": 0, "full": False, "reg": 0, "over": 0}
    edges, iow, bad_reads, i_at_click = [], [], [], set()

    def advance():
        while st["i"] < len(items) and mpu.processorCycles >= items[st["i"]][1]:
            if st["full"]: st["over"] += 1
            else: st["reg"] = items[st["i"]][0]; st["full"] = True
            st["i"] += 1

    def io_read(a):
        if a == ACIA_S:
            advance(); return 0x10 | (0x08 if st["full"] else 0)
        if a == ACIA_D:
            advance(); st["full"] = False; return st["reg"]
        if a == 0xC030:
            edges.append(mpu.processorCycles); i_at_click.add(bool(mpu.p & 0x04)); return 0
        if a not in KNOWN_IO:
            bad_reads.append(a)
        return 0
    mem.subscribe_to_read(range(0xC000, 0x10000), io_read)
    mem.subscribe_to_write(range(0xC000, 0xC100), lambda a, v: (iow.append(a), None)[1])
    for i, b in enumerate(open(os.path.join(ROOT, "video.bin"), "rb").read()): mem[0x0E00 + i] = b
    for i, b in enumerate(audio): mem[0x6000 + i] = b
    for i, b in enumerate([0x20, 0x00, 0x0E, 0x00]): mem[0x0300 + i] = b
    before = list(mem._subject)
    mpu.pc = 0x0300; mpu.sp = 0xFF
    if i_flag_before: mpu.p |= 0x04
    else: mpu.p &= ~0x04
    if d_flag_before: mpu.p |= 0x08
    visited, n, poked = set(), 0, False
    watch = {LABELS["lost"], LABELS["ctl"]}
    while mpu.pc != 0x0303:
        if mpu.pc in watch: visited.add(mpu.pc)
        if pokes and not poked and mpu.pc == LABELS["vframe"]:
            for a, v in pokes.items(): mem[a] = v
            poked = True
        mpu.step(); n += 1
        assert n < 30_000_000, "runaway"
    after = list(mem._subject)
    # only the screen, zero page, the stack and the test stub may change
    ok_zones = [(0x2000, 0x3FFF), (0x0000, 0x00FF), (0x0100, 0x01FF), (0x0300, 0x0303)]
    stray = [a for a in range(0x10000) if before[a] != after[a] and not any(lo <= a <= hi for lo, hi in ok_zones) and not (0xC000 <= a < 0xD000)]
    acia = {0xC0A9, 0xC0AA, 0xC0AB}
    return dict(screen=bytes(after[0x2000:0x4000]), over=st["over"], edges=edges, stray=stray,
                io_bad=[a for a in iow if a not in acia], bad_reads=bad_reads,
                lost=LABELS["lost"] in visited or LABELS["ctl"] in visited,
                i_after=bool(mpu.p & 0x04), d_after=bool(mpu.p & 0x08), i_at_click=i_at_click)


PRE, END = b"\xFE" * 4, b"\x80\x80"
budget = int(19200 / 10 / 6 * 0.95)

print("--- 1. video only ---")
fps, frames, audio = convert()
print("frames: %d | largest frame %d bytes (budget %d)" % (len(frames), max(map(len, frames)), budget))
expect(max(map(len, frames)) <= budget, "every frame fits its byte budget")
ref = np.zeros(0x2000, np.uint8)
for f in frames: ref_decode(f, ref)
shown, target = model(budget)
print("reference decoder == encoder model:", np.array_equal(ref, shown), "| converged (0 unsent bytes):", np.array_equal(shown, target))
expect(np.array_equal(ref, shown) and np.array_equal(shown, target), "stream decodes to the encoder's model and converges")
sched, total = host.plan_stream(frames, fps, 19200)
r = run_6502(from_plan(sched))
print("6502 player, host schedule at 19200 baud (%.1f s): overruns %d, screen == model: %s, stray writes %d, bad reads %d, I flag restored: %s" % (
    total, r["over"], r["screen"] == bytes(ref), len(r["stray"]) + len(r["io_bad"]), len(r["bad_reads"]), r["i_after"] is False))
expect(r["over"] == 0 and r["screen"] == bytes(ref) and not r["stray"] and not r["io_bad"] and not r["bad_reads"] and r["i_after"] is False,
       "player with the host's real schedule: no overruns, right picture, no stray access, interrupts restored")
stream_bytes = PRE + b"".join(frames) + END
r = run_6502(arrivals(stream_bytes, 532), i_flag_before=True)
print("   back-to-back at line rate, entered with interrupts off: overruns %d, screen ok: %s, I flag still set: %s" % (
    r["over"], r["screen"] == bytes(ref), r["i_after"]))
expect(r["over"] == 0 and r["screen"] == bytes(ref) and r["i_after"], "back-to-back at line rate; interrupts left off if they were off")
r = run_6502(arrivals(stream_bytes, 532), d_flag_before=True)
print("   entered in decimal mode: screen ok: %s, decimal mode restored: %s" % (r["screen"] == bytes(ref), r["d_after"]))
expect(r["over"] == 0 and r["screen"] == bytes(ref) and r["d_after"], "entered in decimal mode: same picture, decimal mode restored")
for sp in (200, 120, 90, 70):
    r = run_6502(arrivals(stream_bytes, sp))
    print("   stress %3d cycles/byte (%.1fx line rate): overruns %d, screen ok: %s" % (sp, 532 / sp, r["over"], r["screen"] == bytes(ref)))
    expect(r["over"] == 0 and r["screen"] == bytes(ref), "keeps up at %.1fx line rate" % (532 / sp))

print("--- 2. with audio bursts ---")
fps, afr, audio = convert(["--audio", os.path.join(tmp, "s.wav")])
bursts = [a2hdr.frame_audio_pages(f) for f in afr]
print("audio %d pages; bursts at frames %s (pages %s)" % (len(audio) // 256, [i for i, n in enumerate(bursts) if n], [n for n in bursts if n]))
aref = np.zeros(0x2000, np.uint8)
for f in afr: ref_decode(f, aref)
sched, total = host.plan_stream(afr, fps, 19200)
gaps_ok = True
bt = 10 / 19200
for (t, f, k), (t2, _f2, _k2) in zip(sched, sched[1:]):
    pages = a2hdr.frame_audio_pages(f) if k == "frame" else 0
    if pages and t2 < t + len(f) * bt + host.burst_seconds(pages):
        gaps_ok = False
print("host plan: %.1f s, line silent for every whole burst: %s" % (total, gaps_ok))
expect(gaps_ok, "the host plan keeps the line silent during every burst")
r = run_6502(from_plan(sched), audio)
print("6502 with the host's schedule: overruns %d, screen == model: %s, stray writes %d, bad reads %d" % (
    r["over"], r["screen"] == bytes(aref), len(r["stray"]) + len(r["io_bad"]), len(r["bad_reads"])))
expect(r["over"] == 0 and r["screen"] == bytes(aref) and not r["stray"] and not r["io_bad"] and not r["bad_reads"],
       "video with audio bursts: no overruns, right picture, no stray access")
e1, e2 = r["edges"][0::2], r["edges"][1::2]
nib = [(e2[k] - e1[k] - 3) // 5 - 1 for k in range(len(e1))]
want = [x for b in audio for x in (b >> 4, b & 15)]
print("speaker: %d samples, decoded waveform == audio data: %s | interrupts off while clicking: %s | I flag restored: %s" % (
    len(e1), nib == want, r["i_at_click"] == {True}, r["i_after"] is False))
expect(nib == want and r["i_at_click"] == {True} and r["i_after"] is False, "burst audio is exact, with interrupts off while it plays")
sched0, _ = host.plan_stream(afr, fps, 19200, quiet_for_bursts=False)
r = run_6502(from_plan(sched0), audio)
print("negative: host doesn't stay quiet for bursts -> overruns %d, screen == model: %s" % (r["over"], r["screen"] == bytes(aref)))
expect(r["over"] > 0, "negative control: without the quiet time bytes are lost (so the test can see the difference)")

print("--- 2b. at 115200 baud (a2kit-boot.dsk) ---")
FAST = 115200
fps, ffr, _ = convert(["--baud", str(FAST)])
fbudget = int(FAST / 10 / fps * 0.95)
fref = np.zeros(0x2000, np.uint8)
for f in ffr: ref_decode(f, fref)
print("frames: %d | largest frame %d bytes (budget %d, %.0fx the 19200 budget)" % (len(ffr), max(map(len, ffr)), fbudget, fbudget / budget))
expect(max(map(len, ffr)) <= fbudget, "115200: every frame fits its byte budget")
sched, total = host.plan_stream(ffr, fps, FAST)
r = run_6502(from_plan(sched, FAST))
print("6502 player, host schedule at %d baud (%.1f s): overruns %d, screen == model: %s, stray %d" % (
    FAST, total, r["over"], r["screen"] == bytes(fref), len(r["stray"]) + len(r["io_bad"]) + len(r["bad_reads"])))
expect(r["over"] == 0 and r["screen"] == bytes(fref) and not r["stray"] and not r["io_bad"] and not r["bad_reads"],
       "115200, host schedule: no overruns, right picture, no stray access")
cpb = a2hdr.CPU_HZ * 10 / FAST
r = run_6502(arrivals(PRE + b"".join(ffr) + END, cpb))
print("   back-to-back at %.1f cycles a byte: overruns %d, screen ok: %s" % (cpb, r["over"], r["screen"] == bytes(fref)))
expect(r["over"] == 0 and r["screen"] == bytes(fref), "115200: keeps up with bytes back to back")
fps, fafr, faudio = convert(["--baud", str(FAST), "--audio", os.path.join(tmp, "s.wav")])
far = np.zeros(0x2000, np.uint8)
for f in fafr: ref_decode(f, far)
sched, total = host.plan_stream(fafr, fps, FAST)
r = run_6502(from_plan(sched, FAST), faudio)
e1, e2 = r["edges"][0::2], r["edges"][1::2]
nib = [(e2[k] - e1[k] - 3) // 5 - 1 for k in range(len(e1))]
want = [x for b in faudio for x in (b >> 4, b & 15)]
print("with audio bursts (%.1f s): overruns %d, screen == model: %s, audio exact: %s" % (total, r["over"], r["screen"] == bytes(far), nib == want))
expect(r["over"] == 0 and r["screen"] == bytes(far) and nib == want and not r["stray"],
       "115200 with audio bursts: no overruns, right picture, exact sound")
# headroom: squeeze the spacing until the player loses bytes
stress = PRE + b"".join(ffr[:8]) + END
lo, hi = 20.0, cpb
while hi - lo > 1:
    mid = (lo + hi) / 2
    over = run_6502(arrivals(stress, mid))["over"]
    lo, hi = (mid, hi) if over else (lo, mid)
print("player keeps up with bytes as close as %.0f cycles apart (115200 delivers one every %.1f)" % (hi, cpb))
expect(hi < 0.85 * cpb, "the player has at least 15%% headroom at 115200")

print("--- 3. fault injection (video-only stream, first 22 frames) ---")
K = 22
sub = frames[:K]
ref_per, W = np.zeros(0x2000, np.uint8), []
for f in sub:
    s = set(); ref_decode(f, ref_per, s); W.append(s)
bounds = np.cumsum([0] + [len(f) for f in sub])
body = b"".join(sub)
clean = PRE + body + END
def run_clean(stream_bytes, pause_after=None, **kw):
    """pause_after=(n, cycles): bytes after the first n arrive `cycles` later
    (the host staying quiet while a burst plays)."""
    items = arrivals(stream_bytes, 150)
    if pause_after:
        n, extra = pause_after
        items = items[:n] + [(b, t + extra) for b, t in items[n:]]
    return run_6502(items, **kw)

# targeted cases
def case(name, stream_bytes, check, audio_ok=False, **kw):
    r = run_clean(stream_bytes, **kw)
    ok = check(r) and not r["stray"] and not r["io_bad"] and not r["bad_reads"] and (audio_ok or not r["edges"])
    print("  %-56s %s" % (name, "ok" if ok else "FAILED"))
    if not ok:
        expect(False, name)
    return ok
def skip_to(addr):                       # long skip from $2000 to addr
    off = addr - 0x2000
    return bytes([0xFF, off & 0x7F, off >> 7])
lit = lambda *b: bytes([len(b), *b])
case("skip walks off the screen, then good frame",
     b"\xFE\xFF\x7F\x7F" + lit(1, 2, 3) + b"\xFE" + lit(0x41, 0x42) + END,
     lambda r: r["screen"][:2] == b"AB" and r["lost"])
case("audio burst, bad check byte",
     b"\xFE\x7F\x70\x00\xFE" + lit(0x55) + END, lambda r: r["screen"][0] == 0x55 and r["lost"])
case("audio burst, valid check but runs past what a language card holds",
     b"\xFE\x7F\x70\x25\xFE" + lit(0x66) + END, lambda r: r["screen"][0] == 0x66 and r["lost"], pokes={0x0C: 0x80})
case("lone $80 mid-stream (not an end of stream)",
     b"\xFE" + lit(0x11) + b"\x80" + lit(1, 2, 3, 4, 5) + b"\xFE" + lit(0x22) + END,
     lambda r: r["screen"][0] == 0x22)
case("control byte inside a literal run",
     b"\xFE\x05\x11\x22\x90\x33\x44\xFE" + lit(0x77) + END, lambda r: r["screen"][0] == 0x77 and r["lost"])
case("run that would end past $3FFF is refused",
     b"\xFE" + skip_to(0x3FFD) + bytes([126]) + bytes([0x2A] * 126) + b"\xFE" + lit(0x12) + END,
     lambda r: r["screen"][0] == 0x12 and r["screen"][0x1FFD] == 0 and r["lost"])
case("run that ends exactly at $3FFF is allowed",
     b"\xFE" + skip_to(0x3FF0) + bytes([16]) + bytes(range(0x30, 0x40)) + END,
     lambda r: r["screen"][0x1FF0:0x2000] == bytes(range(0x30, 0x40)) and not r["lost"])
case("burst count that wraps past $FF00 is refused",
     b"\xFE\x7F\x7F" + bytes([0x7F ^ 0x55]) + b"\xFE" + lit(0x33) + END,
     lambda r: r["screen"][0] == 0x33 and r["lost"], pokes={0x0C: 0xA0})
case("burst ending exactly at $BFFF plays",
     b"\xFE\x7F\x01" + bytes([0x01 ^ 0x55]) + b"\xFE" + lit(0x44) + END,
     lambda r: r["screen"][0] == 0x44 and len(r["edges"]) == 1024 and not r["lost"], audio_ok=True,
     pokes={0x0C: 0xBF}, pause_after=(4, 512 * a2hdr.SPEAKER_PLAYER_CYCLES + 5000))
case("a burst right after a run that ends at $3FFF still plays",
     b"\xFE" + skip_to(0x3FF8) + bytes([8]) + bytes(8) + b"\x7F\x01" + bytes([0x01 ^ 0x55]) + b"\xFE" + lit(0x46) + END,
     lambda r: r["screen"][0] == 0x46 and len(r["edges"]) == 1024 and not r["lost"], audio_ok=True,
     pause_after=(16, 512 * a2hdr.SPEAKER_PLAYER_CYCLES + 5000))
case("burst past the card's last page is refused",
     b"\xFE\x7F\x02" + bytes([0x02 ^ 0x55]) + b"\xFE" + lit(0x45) + END,
     lambda r: r["screen"][0] == 0x45 and r["lost"], pokes={0x0C: 0xFE})

# random faults: drop / corrupt / insert one byte
frng = np.random.default_rng(11)
TRIALS = 24
good, worst, over_total, lost_hits, stray_total = 0, 0, 0, 0, 0
for tr in range(TRIALS):
    kind = ("drop", "corrupt", "insert")[tr % 3]
    p = int(frng.integers(0, len(body)))
    k = int(np.searchsorted(bounds, p, side="right") - 1)
    b = bytearray(body)
    if kind == "drop": del b[p]
    elif kind == "corrupt": b[p] ^= int(frng.integers(1, 256))
    else: b.insert(p, int(frng.integers(0, 256)))
    r = run_clean(PRE + bytes(b) + END)
    scr = np.frombuffer(r["screen"], np.uint8)
    later = sorted(set().union(*W[k + 1:])) if k + 1 < len(W) else []
    recovered = (not later) or np.array_equal(scr[later], ref_per[later])
    dmg = int(np.count_nonzero(scr != ref_per))
    stray = len(r["stray"]) + len(r["io_bad"]) + len(r["bad_reads"])
    good += recovered and not stray
    worst = max(worst, dmg); over_total += r["over"]; lost_hits += r["lost"]; stray_total += stray
    print("  trial %2d %-7s byte %5d (frame %2d): later frames correct: %-5s | wrong bytes left %4d | recovery path %s | stray accesses %d" % (
        tr, kind, p, k, recovered, dmg, "taken" if r["lost"] else "-", stray))
expect(good == TRIALS, "every random fault recovers by the next frame with no stray access")
print("fault injection: %d/%d trials recovered by the next frame with no stray writes or reads; worst residual damage %d of 8192 screen bytes; %d took the discard-until-$FE path" % (good, TRIALS, worst, lost_hits))


print("--- 4. scrub heals what a fault leaves behind ---")
import shutil
FR2 = os.path.join(tmp, "frames_long"); os.makedirs(FR2)
for n in sorted(os.listdir(FR)): shutil.copy(os.path.join(FR, n), os.path.join(FR2, n))
last = os.path.join(FR, sorted(os.listdir(FR))[-1])
for i in range(44, 68): shutil.copy(last, os.path.join(FR2, "f%03d.png" % i))      # 24 moving + 44 still
SCRUB, BUD = 256, 600
def a2v_frames(extra):
    return convert(["--budget", str(BUD), *extra], src=FR2)[1]
fr_scrub = a2v_frames(["--scrub", str(SCRUB)])
fr_plain = a2v_frames([])
def final_ref(fr):
    scr = np.zeros(0x2000, np.uint8)
    for f in fr: ref_decode(f, scr)
    return scr
ref_s, ref_p = final_ref(fr_scrub), final_ref(fr_plain)
print("clean stream with scrub reaches the same final picture as without: %s" % np.array_equal(ref_s, ref_p))
expect(np.array_equal(ref_s, ref_p), "scrub doesn't change a clean stream's final picture")
def damage(fr, kind, frame, off, seed):
    """apply one fault to the byte `off` into frame `frame`, return the whole stream"""
    r_ = np.random.default_rng(seed)
    pre = b"".join(fr[:frame]); cur = bytearray(fr[frame])
    off = min(off, len(cur) - 1)
    if kind == "drop": del cur[off]
    elif kind == "corrupt": cur[off] ^= int(r_.integers(1, 256))
    else: cur.insert(off, int(r_.integers(0, 256)))
    return PRE + pre + bytes(cur) + b"".join(fr[frame + 1:]) + END
for kind, frame, off, seed in (("drop", 3, 150, 1), ("corrupt", 8, 120, 2), ("insert", 14, 200, 3)):
    healed = run_clean(damage(fr_scrub, kind, frame, off, seed))
    plain = run_clean(damage(fr_plain, kind, frame, off, seed))
    h = int(np.count_nonzero(np.frombuffer(healed["screen"], np.uint8) != ref_s))
    p_ = int(np.count_nonzero(np.frombuffer(plain["screen"], np.uint8) != ref_p))
    print("  %-7s in frame %2d: wrong screen bytes at the end -> without scrub %3d, with scrub (%d B/frame) %d | stray writes %d" % (
        kind, frame, p_, SCRUB, h, len(healed["stray"]) + len(healed["io_bad"])))
    expect(h == 0 and p_ > 0 and not healed["stray"], "scrub heals a %s fault that would otherwise stay" % kind)
finish("test_video")
