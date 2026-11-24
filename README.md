# a2kit

[![tests](https://github.com/arguingbananas/a2kit/actions/workflows/test.yml/badge.svg)](https://github.com/arguingbananas/a2kit/actions/workflows/test.yml)

Turn pictures, animations and sound from a modern computer into things an Apple II
can show and play, and send them over a Super Serial Card.

![Hello, Apple: an apple splits open, a walker crosses a hill at sunset, birds turn into falling maple leaves](docs/hello_apple_on_apple2.webp)

*An original animation created with a2kit, running on a real 64K Apple ][ plus (48K and a 16K language card), recorded from its video output.*

> [!NOTE]
> **Made with AI.** This project was built with
> [Claude Code](https://claude.com/claude-code), Anthropic's AI coding
> assistant (Claude), working under my direction: the Python tools, the 6502
> programs, the tests, the example media and the documentation. I directed the
> work, and did what only a person at the Apple II can: fitted the cards and
> chips, booted it, pressed the keys, recovered it after crashes, and judged the
> pictures and sound on the TV. Claude drove the Apple II over the serial link
> for the hardware tests and analysed recordings of its screen. Commits Claude
> wrote or co-wrote name it in a `Co-Authored-By` trailer.

**For:** a 48K Apple II, II+ or IIe with a Super Serial Card, and a computer with an RS-232 serial port (built in, or a USB-to-RS-232 adapter) ([hardware requirements](docs/INSTALL.md#hardware)).

**Status:** working on real hardware: a 1979 Apple II+ with a Super Serial Card II, booted from a Floppy Emu, driven from Linux over a built-in serial port ([tested setup and measurements](docs/INSTALL.md#tested-setup)). Every part is also covered by tests in a 6502 simulator.

## Documentation

| Read | For |
|---|---|
| [Installation Guide](docs/INSTALL.md) | Requirements, per-system setup, finding the serial port |
| [Quick Start](docs/QUICKSTART.md) | Purpose, workflow diagram, first picture on screen |
| [User Manual](docs/MANUAL.md) | Every command and option, formats, memory map, troubleshooting |
| [Roadmap](docs/ROADMAP.md) | Current status, known problems and what comes next |

PDF copies of all three are in `docs/` (keep them in one folder so the links between them work).
Starter assets are in [`examples/`](examples/).

## In one minute

```bash
python3 -m pip install -r requirements.txt      # pillow, numpy, pyserial (Python 3.11+)
./a2kit doctor                                  # checks the install
./a2kit image examples/logo.png -o logo.a2b --preview logo_preview.png
```

| Command | Does |
|---|---|
| `./a2kit image` / `audio` / `video` | Convert a picture, a WAV, or an animation |
| `./a2kit show plan` / `show send` | Build and send a slideshow with sound (`show.toml`) |
| `./a2kit ping` | Check the serial link to the Apple II and measure its speed |
| `./a2kit lcprobe` | Test a language card in slot 0, for about 9 seconds of sound instead of 6 (`--language-card`) |
| `./a2kit peek` / `crash` / `canary` / `ramload` | Read Apple II memory / report or watch for crashes / watch free RAM for changes / start a new receiver build without the disk |
| `./a2kit send` / `stream` | Upload one block / play a video over serial |
| `./a2kit disk` | Make `a2kit-boot.dsk`, a disk that boots straight into the receiver (one is included, plus a 19200-baud one) |
| `./a2kit chargen` | Read the character ROM off the screen with a USB video grabber, for the MAME emulator (Linux) |
| `./a2kit mon receiver.bin` | Print the receiver as Monitor lines, if you'd rather type it in |
| `./a2kit build` / `test` / `doctor` | Rebuild the 6502 programs (cc65) / run the tests / check the setup |
| `./a2kit --version` | Show which version you have ([what changed](CHANGELOG.md)) |

## Layout

| Path | Contents |
|---|---|
| `tools/` | The Python tools; `tools/a2kit.py` is the `./a2kit` command |
| `runtime/` | 6502 sources; `runtime/config.inc` sets the card's slot and line speed |
| `a2kit-boot.dsk` | Boot disk: boots the Apple II straight into the receiver (115200 baud), which restarts itself after each session |
| `a2kit-boot-19200.dsk` | The same at 19200 baud, if your cable or card can't manage 115200 ([Line speed](docs/MANUAL.md#24-line-speed-115200-or-19200-baud)) |
| `receiver.bin`, `receiver-19200.bin`, `sequencer.bin`, `video.bin`, `boot.bin`, `boot-19200.bin` | Built 6502 programs (rebuild with `./a2kit build`) |
| `examples/` | Starter assets and `make_examples.py`, which regenerates them |
| `docs/` | The guides above, the workflow diagram, and `make_pdfs.py` |

## Built with

a2kit stands on these projects; thank you to their authors. None of their code is copied
into this repository: the Python packages are installed with pip, and the tools are run
as separate programs.

| Project | Used for | License |
|---|---|---|
| [Pillow](https://python-pillow.org) | Images, GIFs, previews | MIT-CMU |
| [NumPy](https://numpy.org) | Video and audio conversion | BSD-3-Clause (and others for bundled parts) |
| [pySerial](https://github.com/pyserial/pyserial) | The serial port | BSD |
| [py65](https://github.com/mnaberez/py65) | The 6502 simulator the tests run on | BSD-3-Clause |
| [pypdf](https://github.com/py-pdf/pypdf) | Building the PDF guides | BSD-3-Clause |
| [Ruff](https://github.com/astral-sh/ruff) | Linting (`make lint`) | MIT |
| [cc65](https://cc65.github.io) (`ca65`, `ld65`) | Assembling and linking the 6502 programs | zlib |
| [FFmpeg](https://ffmpeg.org) | Optional: reading video files, and `chargen capture` | LGPL-2.1+ or GPL, as built by your system |
| [pandoc](https://pandoc.org) and [Chrome or Chromium](https://www.chromium.org) | Building the PDF guides | GPL-2.0+; Chromium BSD-3-Clause (Chrome is Google's) |
| [Liberation Sans](https://github.com/liberationfonts/liberation-fonts), [DejaVu Sans Mono](https://dejavu-fonts.github.io) | Fonts embedded in the PDF guides | SIL OFL 1.1; Bitstream Vera license |
| [Aileron](https://dotcolon.net/fonts/aileron) (bundled with Pillow) | The lettering in the example pictures | CC0 (per its author) |

## Working on this with Claude Code

The project was built with [Claude Code](https://claude.com/claude-code) (see
the note at the top), and it's set up for you to carry on the same way. Open the
repository in Claude Code and it reads `CLAUDE.md`: what a2kit is, the commands,
the rules (tests never need an Apple II, a serial port or the network) and when
a change is done. `.claude/settings.json` activates the virtual environment for
each session. Put anything about your own setup (your computer, your serial port,
your Apple II and its cards) in `CLAUDE.local.md`; Claude Code loads it too, and
git ignores it.

## License

MIT; see [LICENSE](LICENSE). The boot disk contains only a2kit's own code (no Apple DOS or ROM), and the example pictures and sounds are generated from scratch by `examples/make_examples.py` (the recording at the top is one of them, filmed off the Apple II), so everything here can be shared under the same terms.
