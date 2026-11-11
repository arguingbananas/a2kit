# a2kit

Converts pictures, animations and sound on a modern computer into formats a 48K Apple II
(II, II+ or IIe with a Super Serial Card) can show and play, and sends them over RS-232.
Inputs: PNG/JPEG/GIF images, WAV sound, GIFs or frame folders (video files via ffmpeg),
and `show.toml` scripts. Outputs: `.a2b` blocks, `.a2v` video streams, bootable `.dsk`
images, and uploads to a running Apple II. Users are Apple II hobbyists on Linux, macOS
or Windows. See README.md, docs/INSTALL.md, docs/QUICKSTART.md, docs/MANUAL.md.

## Environment

- Python 3.11 or newer (last tested on 3.12). Dependencies: `requirements.txt`
  (runtime), `requirements-dev.txt` (tests, docs, lint), `constraints.txt` (exact
  tested versions). Use a virtual environment: `.venv/`. `./a2kit` and `make` use it
  by themselves; `.claude/settings.json` activates it for every Claude Code session
  and links it into new worktrees.
- System tools: cc65 (`ca65`, `ld65`) and make to build the 6502 programs and run the
  tests; ffmpeg optional for video files; pandoc and Chrome or Chromium to rebuild PDFs.
- 6502 sources are in `runtime/`; the Python tools are in `tools/`, and `./a2kit` runs
  `tools/a2kit.py`.

## Commands

    python3 -m pip install -r requirements-dev.txt -c constraints.txt   # set up
    ./a2kit build            # rebuild the 6502 programs and boot disks (make all)
    ./a2kit doctor           # check the toolchain and built files
    make smoke               # sanity check after every change, about 10 s
    make lint                # ruff, correctness rules only (pyproject.toml)
    ./a2kit test             # full suite (make test), about three minutes
    python3 -m pip_audit -r requirements-dev.txt    # dependency vulnerabilities
    python3 docs/make_pdfs.py                       # rebuild the PDFs after changing a guide

There is no formatter; don't reformat files wholesale.

## Files

Required, and kept current: README.md, LICENSE (MIT), SECURITY.md, CONTRIBUTING.md,
requirements*.txt, constraints.txt, pyproject.toml, Makefile, docs/INSTALL.md,
docs/QUICKSTART.md, docs/MANUAL.md and their PDFs, docs/ROADMAP.md.
Generated but committed: `*.bin`, `*.dsk`, `video.lbl` (rebuild with `./a2kit build`,
never edit by hand) and `docs/*.pdf` (rebuild with `docs/make_pdfs.py`).
Optional, not present: CHANGELOG, CI workflow, packaging.

## Rules

- Never put secrets, credentials or personal data in code, logs, docs or commits.
- Tests must not need an Apple II, a serial port or the network: use the py65
  simulator, fake ports and pseudo-terminals. Commands that use `--port` talk to real
  hardware; only run them when asked.
- a2kit uses no paid APIs or online services. Don't add example media from outside
  sources; `examples/make_examples.py` generates them, so everything stays MIT.
- The boot image leaves exactly 380 bytes for the receiver (`runtime/boot.s`); new
  receiver code must fit or something else must shrink. Players are uploaded separately.
- Branches, commit messages and PR merging: see CONTRIBUTING.md.

## Definition of done

- `make smoke` and `./a2kit test` pass.
- `make lint` is clean.
- No new high-severity `pip-audit` findings.
- README and the guides updated (and PDFs rebuilt) if behaviour changed.
