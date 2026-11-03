# a2kit

Turn pictures, animations and sound from a modern computer into things an Apple II
can show and play, and send them over a Super Serial Card.

**For:** a 48K Apple II, II+ or IIe with a Super Serial Card, and a computer with an RS-232 serial port (built in, or a USB-to-RS-232 adapter) ([hardware requirements](docs/INSTALL.md#hardware)).

**Status:** working on real hardware: a 1979 Apple II+ with a Super Serial Card II, booted from a Floppy Emu, driven from Linux over a built-in serial port ([tested setup and measurements](docs/INSTALL.md#tested-setup)). Every part is also covered by tests in a 6502 simulator.

## Documentation

| Read | For |
|---|---|
| [Installation Guide](docs/INSTALL.md) | Requirements, per-system setup, finding the serial port |
| [Quick Start](docs/QUICKSTART.md) | Purpose, workflow diagram, first picture on screen |
| [User Manual](docs/MANUAL.md) | Every command and option, formats, memory map, troubleshooting |
| [Roadmap](docs/ROADMAP.md) | What to do after the first real-hardware test |

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
| `./a2kit send` / `stream` | Upload one block / play a video over serial |
| `./a2kit disk` | Make `a2kit-boot.dsk`, a disk that boots straight into the receiver (one is included) |
| `./a2kit mon receiver.bin` | Print the receiver as Monitor lines, if you'd rather type it in |
| `./a2kit build` / `test` / `doctor` | Rebuild the 6502 programs (cc65) / run the tests / check the setup |

## Layout

| Path | Contents |
|---|---|
| `tools/` | The Python tools; `tools/a2kit.py` is the `./a2kit` command |
| `runtime/` | 6502 sources; `runtime/config.inc` sets the card's slot and baud rate |
| `a2kit-boot.dsk` | Boot disk: boots the Apple II straight into the receiver, which restarts itself after each session |
| `receiver.bin`, `sequencer.bin`, `video.bin`, `boot.bin` | Built 6502 programs (rebuild with `./a2kit build`) |
| `examples/` | Starter assets and `make_examples.py`, which regenerates them |
| `docs/` | The guides above, the workflow diagram, and `make_pdfs.py` |
