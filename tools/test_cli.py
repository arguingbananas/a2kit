"""Drive the unified `a2kit` command end to end as a user would, in a temp dir."""
import os, subprocess, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__))
CLI = [sys.executable, os.path.join(HERE, "a2kit.py")]
sys.path.insert(0, HERE)
import numpy as np
from PIL import Image, ImageDraw
import a2hdr

tmp = tempfile.mkdtemp()
P = lambda n: os.path.join(tmp, n)
fails, total = [], [0]

def run(*args, expect=0):
    r = subprocess.run(CLI + list(args), capture_output=True, text=True, cwd=tmp)
    return r

def check(name, cond, detail=""):
    total[0] += 1
    print("  %-52s %s" % (name, "ok" if cond else "FAILED " + detail))
    if not cond: fails.append(name)

# inputs
im = Image.new("RGB", (320, 200), "black"); ImageDraw.Draw(im).ellipse((60, 20, 260, 180), fill="white"); im.save(P("pic.png"))
os.makedirs(P("frames"))
for i in range(6):
    f = Image.new("L", (280, 192), 20); ImageDraw.Draw(f).ellipse((20 + i * 30, 60, 80 + i * 30, 120), fill=255); f.convert("RGB").save(P("frames/f%02d.png" % i))
t = np.arange(int(0.3 * 22050)) / 22050
w = wave.open(P("s.wav"), "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
w.writeframes((0.6 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2").tobytes()); w.close()
open(P("show.toml"), "w").write('[[scene]]\nimage = "pic.png"\nhold = 1.0\n[[scene]]\naudio = "s.wav"\n')

r = run("--help"); check("help lists commands", r.returncode == 0 and "a2kit video" in r.stdout)
r = run(); check("no arguments prints help, exit 0", r.returncode == 0 and "a2kit image" in r.stdout)
r = run("bogus"); check("unknown command exits 2 with usage", r.returncode == 2 and "unknown command" in r.stderr)

r = run("image", "pic.png", "-o", "pic.a2b"); b = open(P("pic.a2b"), "rb").read()
check("image -> HGR block", r.returncode == 0 and a2hdr.unpack(b)[0] == a2hdr.TYPE_IMAGE_HGR and len(a2hdr.unpack(b)[2]) == 0x2000)
r = run("audio", "s.wav", "-o", "s.a2b")
check("audio -> block with speaker cycle count", r.returncode == 0 and a2hdr.unpack(open(P("s.a2b"), "rb").read())[1] == a2hdr.SPEAKER_PLAYER_CYCLES)
r = run("video", "frames", "-o", "c.a2v", "--fps", "6"); fps, frames, aud = a2hdr.parse_stream(open(P("c.a2v"), "rb").read())
check("video -> stream (6 frames, each starts with $FE)", r.returncode == 0 and len(frames) == 6 and all(f[0] == 0xFE for f in frames))
r = run("video", "frames", "-o", "ca.a2v", "--fps", "6", "--audio", "s.wav", "--scrub", "8"); _, fr2, aud2 = a2hdr.parse_stream(open(P("ca.a2v"), "rb").read())
check("video with --audio and --scrub", r.returncode == 0 and len(aud2) > 0 and sum(map(a2hdr.frame_audio_pages, fr2)) == len(aud2) // 256, r.stderr[-200:])
r = run("show", "plan", "show.toml"); check("show plan lists image, audio and table", r.returncode == 0 and "HGR page 1" in r.stdout and "scene table" in r.stdout)
r = run("mon", os.path.join(HERE, "..", "receiver.bin")); L = r.stdout.splitlines()
check("mon prints Monitor lines ending in 0800G", r.returncode == 0 and L[0].startswith("0800:") and L[-1] == "0800G")
r = run("send", "pic.a2b"); check("send without --port fails cleanly (usage error)", r.returncode == 2 and "--port" in r.stderr)
r = run("stream", "c.a2v"); check("stream without --port fails cleanly", r.returncode == 2 and "--port" in r.stderr)
r = run("video", "nope", "-o", "x.a2v"); check("video with a missing input fails non-zero", r.returncode != 0)
r = run("doctor"); import re
check("doctor reports Python, packages and built binaries", r.returncode == 0 and bool(re.search(r"^ok\s+video\.bin", r.stdout, re.M)) and bool(re.search(r"^ok\s+python 3\.", r.stdout, re.M)) and "pillow" in r.stdout)
# ---- validation and error messages (no tracebacks for ordinary mistakes)
open(P("typo.toml"), "w").write('[[scene]]\nimgae = "pic.png"\n')
open(P("two.toml"), "w").write('[[scene]]\nimage = "pic.png"\naudio = "s.wav"\n')
open(P("holdaudio.toml"), "w").write('[[scene]]\naudio = "s.wav"\nhold = 2\n')
open(P("look.toml"), "w").write('[[scene]]\nimage = "pic.png"\ncrop = true\ninvert = true\ndither = "threshold"\nhold = 1\n')
r = run("show", "plan", "typo.toml")
check("show: misspelled key is named in a one-line error", r.returncode == 1 and "imgae" in r.stderr and "Traceback" not in r.stderr, r.stderr[-200:])
r = run("show", "plan", "two.toml")
check("show: two actions in one scene is refused", r.returncode == 1 and "exactly one" in r.stderr)
r = run("show", "plan", "holdaudio.toml")
check("show: hold on an audio scene is refused", r.returncode == 1 and "hold" in r.stderr and "audio scene" in r.stderr)
r = run("show", "plan", "look.toml")
check("show: crop / invert / dither accepted on an image scene", r.returncode == 0 and "HGR page 1" in r.stdout, r.stderr[-200:])
r = run("send", "pic.a2b", "--port", "/dev/null", "--raw", "--addr", "0x0800")
check("send: refuses to overwrite the receiver", r.returncode == 1 and "receiver" in r.stderr)
r = run("send", "pic.a2b", "--port", "/dev/null", "--raw", "--addr", "0xB000")
check("send: refuses to run into I/O and ROM", r.returncode == 1 and "I/O" in r.stderr)
r = run("peek", "C000", "1", "--port", "/dev/null")
check("peek: refuses to read I/O", r.returncode == 1 and "I/O" in r.stderr)
r = run("peek", "BD00"); check("peek without --port fails cleanly", r.returncode == 2 and "--port" in r.stderr)
r = run("crash"); check("crash without --port fails cleanly", r.returncode == 2 and "--port" in r.stderr)
r = run("--help"); check("help lists peek and crash", "a2kit peek" in r.stdout and "a2kit crash" in r.stdout)
r = run("video", "frames", "-o", "x.a2v", "--fps", "0")
check("video: --fps 0 is refused", r.returncode == 1 and "--fps" in r.stderr)
r = run("video", "frames", "-o", "x.a2v", "--fps", "2.5")
check("video: fractional --fps is refused", r.returncode == 2 and "fps" in r.stderr)
r = run("video", "nope", "-o", "x.a2v")
check("video: missing input gives a message, not a traceback", r.returncode == 1 and "no such file" in r.stderr and "Traceback" not in r.stderr)
r = run("audio", "show.toml", "-o", "x.a2b")
check("audio: a non-WAV file gives a message", r.returncode == 1 and "WAV" in r.stderr and "Traceback" not in r.stderr)
os.makedirs(P("nat"))
for n in ("f1.png", "f2.png", "f10.png"):
    Image.new("RGB", (280, 192), (255, 255, 255) if n == "f10.png" else (0, 0, 0)).save(P("nat/" + n))
r = run("video", "nat", "-o", "nat.a2v", "--fps", "6")
_, nf, _ = a2hdr.parse_stream(open(P("nat.a2v"), "rb").read())
check("video: frames sorted naturally (f2 before f10)", r.returncode == 0 and len(nf) == 3 and len(nf[0]) <= 1 and len(nf[1]) <= 1 and len(nf[2]) > 1)
# ---- line speed
r = run("show", "plan", "show.toml")
check("115200 is the default speed", r.returncode == 0 and "115200 baud" in r.stdout, r.stderr[-200:])
r = run("show", "plan", "show.toml", "--baud", "19200")
check("show plan --baud 19200 plans at that speed", r.returncode == 0 and "19200 baud" in r.stdout, r.stderr[-200:])
r = run("show", "plan", "show.toml", "--baud", "57600")
check("--baud the receiver can't use is refused with the list", r.returncode == 2 and "115200" in r.stderr and "19200" in r.stderr)
r = subprocess.run(CLI + ["show", "plan", "show.toml"], capture_output=True, text=True, cwd=tmp, env={**os.environ, "A2KIT_BAUD": "19200"})
check("A2KIT_BAUD=19200 becomes the default speed", r.returncode == 0 and "19200 baud" in r.stdout, r.stderr[-200:])
r = subprocess.run(CLI + ["show", "plan", "show.toml"], capture_output=True, text=True, cwd=tmp, env={**os.environ, "A2KIT_BAUD": "fast"})
check("a bad A2KIT_BAUD gives a message", r.returncode != 0 and "A2KIT_BAUD" in r.stderr and "Traceback" not in r.stderr, r.stderr[-200:])
r = run("video", "frames", "-o", "f.a2v", "--fps", "6")
check("video gets the 115200 byte budget by default", r.returncode == 0 and "budget 1824" in r.stderr, r.stderr[-200:])
r = run("video", "frames", "-o", "s.a2v", "--fps", "6", "--baud", "19200")
check("video --baud 19200 gets a sixth of the byte budget", r.returncode == 0 and "budget 304" in r.stderr, r.stderr[-200:])
r = run("disk", "--baud", "19200", "-o", "slow.dsk")
check("disk --baud 19200 writes the 19200 boot disk", r.returncode == 0 and open(P("slow.dsk"), "rb").read() == open(os.path.join(HERE, "..", "a2kit-boot-19200.dsk"), "rb").read())
r = run("mon", os.path.join(HERE, "..", "receiver-19200.bin"))
check("mon works on the 19200 receiver", r.returncode == 0 and r.stdout.splitlines()[-1] == "0800G")
r = subprocess.run(CLI + ["show", "plan", "nope.toml"], capture_output=True, text=True, cwd=tmp, env={**os.environ, "A2KIT_DEBUG": "1"})
check("A2KIT_DEBUG=1 shows the full traceback", r.returncode != 0 and "Traceback" in r.stderr)

print("CLI: %s" % ("all %d checks passed" % total[0] if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
