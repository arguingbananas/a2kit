"""Keep the docs honest. Runs the Quick Start's no-hardware commands against
the starter assets in a scratch copy of the project, then checks that the docs
only mention files, commands, options and links that really exist."""
import os, re, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
fails = []

def check(name, cond, detail=""):
    print("  %-62s %s" % (name, "ok" if cond else "FAILED " + detail))
    if not cond: fails.append(name)

quick = open(os.path.join(ROOT, "docs", "QUICKSTART.md")).read()
manual = open(os.path.join(ROOT, "docs", "MANUAL.md")).read()
install = open(os.path.join(ROOT, "docs", "INSTALL.md")).read()
readme = open(os.path.join(ROOT, "README.md")).read()

# ---- 1. run the Quick Start's no-hardware block in a scratch copy
m = re.search(r"<!-- test:no-hardware -->\s*```bash\n(.*?)```", quick, re.S)
check("Quick Start has a tagged no-hardware block", bool(m))
lines = [l.strip() for l in m.group(1).splitlines()] if m else []
cmds = [l for l in lines if l and not l.startswith("#")]
tmp = tempfile.mkdtemp()
work = os.path.join(tmp, "a2kit")
shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("__pycache__", "out", "*.o"))
outputs = {}
for c in cmds:
    r = subprocess.run(c, shell=True, cwd=work, capture_output=True, text=True)
    outputs[c] = r.stdout + r.stderr
    check("runs: " + (c if len(c) < 56 else c[:53] + "..."), r.returncode == 0, r.stderr[-200:])
for f in ("logo.a2b", "logo_preview.png", "sphere.a2b", "sphere_preview.png", "bounce.a2v", "bounce_sound.a2v", "stress.a2v", "stress_sound.a2v", "a2kit-boot.dsk"):
    check("produces out/" + f, os.path.getsize(os.path.join(work, "out", f)) > 0 if os.path.exists(os.path.join(work, "out", f)) else False)
for c, o in outputs.items():
    if "a2kit video" in c:
        check("video has nothing left unsent: " + os.path.basename(c.split("-o")[1].split()[0]), "unsent screen bytes after last frame: 0" in o, o[-160:])
sys.path.insert(0, os.path.join(ROOT, "tools"))
import a2hdr
for f, bursts in (("stress.a2v", 0), ("stress_sound.a2v", 27)):
    _, _fr, _ = a2hdr.parse_stream(open(os.path.join(work, "out", f), "rb").read())
    check(f + " really fills the 115200 byte budget", sum(1800 <= len(x) <= 1824 for x in _fr) >= 50, str([len(x) for x in _fr][:5]))
    check(f + " has %d sound bursts" % bursts, sum(1 for x in _fr if a2hdr.frame_audio_pages(x)) == bursts)
plan = [o for c, o in outputs.items() if "show plan" in c]
check("show plan lists 2 images, 2 sounds and the scene table", bool(plan) and plan[0].count("HGR page") == 2 and plan[0].count(".wav") == 2 and "scene table" in plan[0])

# ---- 2. every file the docs mention exists
for doc, text in (("QUICKSTART", quick), ("MANUAL", manual), ("INSTALL", install), ("README", readme)):
    base = ROOT if doc == "README" else os.path.join(ROOT, "docs")
    missing = []
    for ref in set(re.findall(r"`?(examples/[\w./-]+)`?", text)):
        if "…" in ref or ref.endswith("/"): continue
        if not os.path.exists(os.path.join(ROOT, ref.rstrip(".,)"))): missing.append(ref)
    for ref in set(re.findall(r"\]\(([\w./-]+\.(?:png|svg|md))", text)):
        if not os.path.exists(os.path.normpath(os.path.join(base, ref))): missing.append(ref)
    check("%s: every referenced file exists" % doc, not missing, str(missing))
frames = [f for f in os.listdir(os.path.join(ROOT, "examples", "frames"))]
check("QUICKSTART's frame range f00..f35 matches the folder", len(frames) == 36 and "f00.png … f35.png" in quick)

# ---- 3. every ./a2kit command and --option the docs mention is real
help_text = ""
for sub in ("image", "audio", "video", "show", "send", "stream", "ping", "mon", "disk"):
    help_text += subprocess.run([sys.executable, os.path.join(HERE, "a2kit.py"), sub, "--help"], capture_output=True, text=True).stdout
known = {"image", "audio", "video", "show", "send", "stream", "ping", "mon", "disk", "build", "test", "doctor", "COMMAND"}   # COMMAND is a placeholder
bad = {c for t in (quick, manual, install, readme) for c in re.findall(r"\./a2kit (\w+)", t) if c not in known}
check("docs only use real ./a2kit commands", not bad, str(bad))
flags = {f.rstrip("-") for t in (quick, manual) for f in re.findall(r"`(--[a-z][a-z-]+)", t)} | \
        {f.rstrip("-") for t in (quick, manual) for f in re.findall(r"(?<=\s)(--[a-z][a-z-]+)", re.sub(r"[^\n]*<!--.*", "", t))}
unknown = sorted(f for f in flags if f not in help_text and f not in ("--help",))
check("docs only mention real --options", not unknown, str(unknown))

# ---- 4. links between the docs resolve (GitHub-style heading anchors)
def slug(h):
    h = re.sub(r"[`*]", "", h.strip().lower())
    h = re.sub(r"[^\w\s-]", "", h)
    return re.sub(r"\s", "-", h)
heads = {name: {slug(h) for h in re.findall(r"^#{1,6}\s+(.*)$", t, re.M)} for name, t in
         (("QUICKSTART.md", quick), ("MANUAL.md", manual), ("INSTALL.md", install))}
broken = []
for own, t in (("QUICKSTART.md", quick), ("MANUAL.md", manual), ("INSTALL.md", install)):
    for m_ in re.finditer(r"\]\((QUICKSTART\.md|MANUAL\.md|INSTALL\.md)?#([\w-]+)\)", t):
        target, frag = m_.group(1) or own, m_.group(2)
        if frag not in heads[target]: broken.append((own, target, frag))
check("docs' #anchor links all resolve (all three docs)", not broken, str(broken))

stale = ["frame N is", "30 ms", "SLOT = 2", "confirm it in the chip", "don't target that range", "builds receiver.bin",
         "II+ or later", "II+ (or later)", "Apple II or II+", "USB-serial adapter",
         "not yet been run on real", "simulators only", "not yet on real", "None of it has been tested"]
found = [(d, p_) for d, t in (("README", readme), ("QUICKSTART", quick), ("MANUAL", manual), ("INSTALL", install)) for p_ in stale if p_ in t]
check("no stale phrasing left in the docs", not found, str(found))
hw = install[install.index("### Hardware"):install.index("### Pick your level")] if "### Hardware" in install else ""
check("INSTALL has one Hardware section covering memory, speed, card, cable, models",
      all(k in hw for k in ("48K", "1.02 MHz", "Super Serial Card", "6551", "RS-232", "DB-25", "DB-9", "IIc", "IIgs", "65C02", "16-sector")))
check("Quick Start, Manual and README link to the Hardware section",
      all("INSTALL.md#hardware" in t for t in (quick, manual, readme)))
check("ping is documented in the Manual and used in the Quick Start and Install guide",
      "### `a2kit ping`" in manual and "./a2kit ping" in quick and "./a2kit ping" in install)
check("README, Quick Start and Manual link to the tested-setup record",
      all("INSTALL.md#tested-setup" in t for t in (readme, quick, manual)) and "### Tested setup" in install)
check("README points at all three guides", all(g in readme for g in ("docs/INSTALL.md", "docs/QUICKSTART.md", "docs/MANUAL.md")))
import filecmp
boot_out = os.path.join(work, "out", "a2kit-boot.dsk")
check("the shipped a2kit-boot.dsk matches a freshly made one", os.path.exists(boot_out) and filecmp.cmp(boot_out, os.path.join(ROOT, "a2kit-boot.dsk"), shallow=False))
_boot = open(os.path.join(ROOT, "boot.bin"), "rb").read()
restart = _boot[0x180:_boot.index(bytes([0x4C, 0x69, 0xFF]), 0x180) + 3]      # up to JMP MONZ
check("the manual's restart-loop bytes match boot.bin", "0980:" + " ".join("%02X" % b for b in restart) in manual)
req = open(os.path.join(ROOT, "requirements.txt")).read().lower()
check("requirements.txt lists pillow, numpy and pyserial", all(p_ in req for p_ in ("pillow", "numpy", "pyserial")))
check("requirements-dev.txt adds py65 on top of requirements.txt", "py65" in open(os.path.join(ROOT, "requirements-dev.txt")).read() and "-r requirements.txt" in open(os.path.join(ROOT, "requirements-dev.txt")).read())
check("INSTALL.md names every required package", all(p_ in install for p_ in ("pillow", "numpy", "pyserial", "py65", "cc65", "ffmpeg", "3.11")))
print("docs: %s" % ("all checks passed" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
