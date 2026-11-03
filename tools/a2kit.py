#!/usr/bin/env python3
"""a2kit: one command for the whole Apple II media kit.

  a2kit image  PNG -o OUT.a2b        convert an image to an HGR block
  a2kit audio  WAV -o OUT.a2b        convert audio to speaker samples
  a2kit video  SRC -o OUT.a2v        convert a clip to a delta stream
  a2kit show   plan|send SHOW.toml   images + audio scene show
  a2kit send   FILE --port DEV       upload one block through the receiver
  a2kit stream OUT.a2v --port DEV    play a video stream
  a2kit ping   --port DEV            check the serial link and measure its speed
  a2kit mon    FILE                  print a binary as Monitor entry lines
  a2kit disk   [-o FILE.dsk]         make a disk that boots straight into the receiver
  a2kit build                        assemble the 6502 programs (needs cc65)
  a2kit test                         run the whole test suite
  a2kit doctor                       check the toolchain and built binaries

`a2kit COMMAND --help` shows each command's options.
"""
import importlib
import importlib.metadata
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

# command -> (module with a main(), one-line summary)
TOOLS = {
    "image": ("imgconv", "image -> HGR block"),
    "audio": ("audioconv", "WAV -> speaker samples"),
    "video": ("videoconv", "clip -> delta stream"),
    "show": ("show", "scene show: plan / send"),
    "send": ("send", "upload a block"),
    "stream": ("stream", "play a video stream"),
    "ping": ("ping", "check and measure the serial link"),
    "mon": ("bin2mon", "binary -> Monitor lines"),
    "disk": ("mkdisk", "bootable receiver disk image"),
}
BINARIES = ("receiver.bin", "sequencer.bin", "video.bin", "boot.bin", "a2kit-boot.dsk")


def usage(code=0):
    print(__doc__.strip(), file=sys.stderr if code else sys.stdout)
    return code


def run_tool(name, args):
    mod = importlib.import_module(TOOLS[name][0])
    sys.argv = ["a2kit " + name] + args
    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit, BrokenPipeError):
        raise
    except Exception as e:
        if os.environ.get("A2KIT_DEBUG"):
            raise
        msg = str(e) or e.__class__.__name__
        print("a2kit %s: error: %s" % (name, msg), file=sys.stderr)
        print("(set A2KIT_DEBUG=1 to see the full traceback)", file=sys.stderr)
        return 1
    return 0


def make(target):
    if not shutil.which("make"):
        print("make not found", file=sys.stderr)
        return 1
    return subprocess.call(["make", "-C", ROOT, target])


def doctor():
    """Report what is installed. Required items affect the exit code; optional ones don't."""
    ok = True

    def line(good, text, required=True):
        nonlocal ok
        if required:
            ok = ok and good
        print(("ok      " if good else ("MISSING " if required else "optional")) + " " + text)

    v = sys.version_info
    line(v >= (3, 11), "python %d.%d.%d%s" % (v[0], v[1], v[2], "" if v >= (3, 11) else "   (3.11 or newer is required)"))
    for mod, pkg, req in (("PIL", "pillow", True), ("numpy", "numpy", True), ("serial", "pyserial", True), ("py65", "py65", False)):
        try:
            importlib.import_module(mod)
            try:
                ver = importlib.metadata.version(pkg)
            except importlib.metadata.PackageNotFoundError:
                ver = ""
            line(True, ("python package %s %s" % (pkg, ver)).strip(), req)
        except ImportError:
            note = "   (python3 -m pip install -r requirements.txt)" if req else "   (only needed for tests: pip install -r requirements-dev.txt)"
            line(False, "python package " + pkg + note, req)
    for exe, why in (("ca65", "cc65 assembler; only to rebuild the 6502 programs"), ("ld65", "cc65 linker; only to rebuild the 6502 programs"),
                     ("make", "needed by 'a2kit build' and 'a2kit test'"), ("ffmpeg", "only for video files like .mp4; GIFs and frame folders don't need it")):
        line(bool(shutil.which(exe)), "%s (%s)" % (exe, why), required=False)
    for b in BINARIES:
        have = os.path.exists(os.path.join(ROOT, b))
        line(have, b + ("" if have else "   (run: a2kit build, or restore the file)"))
    print("\nall required items present" if ok else "\nsomething required is missing (see above)")
    return 0 if ok else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        return usage(0)
    cmd, rest = argv[0], argv[1:]
    if cmd in TOOLS:
        return run_tool(cmd, rest)
    if cmd == "build":
        return make("all")
    if cmd == "test":
        return make("test")
    if cmd == "doctor":
        return doctor()
    print("a2kit: unknown command '%s'\n" % cmd, file=sys.stderr)
    return usage(2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:             # e.g. `a2kit mon x.bin | head`
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
