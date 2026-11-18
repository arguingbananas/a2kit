# Changelog

What changed in each version of a2kit, newest first. `./a2kit --version` shows
which one you have; the receiver check in every command (`VERSION MATCH` on the
Apple II's screen) shows whether the Apple II is running the receiver from the
same version. How to add an entry: see [CONTRIBUTING.md](CONTRIBUTING.md#versions-and-the-changelog).

## Unreleased

- Video without tearing: `video --flip` draws each frame on the hidden hi-res
  page and flips to it when it's complete, at the cost of up to twice the bytes
  a frame. `video.bin` is rebuilt (`stream --install` uploads it); older players
  can't play flipped streams. Older streams play as before. Each flip waits for
  the screen's vertical blank, so it never lands mid-screen; that keeps 20 ms of
  each frame quiet (875 bytes a frame instead of 1,094 at 10 frames a second).
- New example: `examples/hello_apple.gif`, "Hello, Apple", a 32-second black
  and white shadow-play animation (an apple, a walker on a hill at sunset, birds
  that turn into leaves) with full-screen wipes, at 10 frames a second.
- Colour video: `video --colour` converts an animation in the six hi-res colours.
  New example: `examples/balloon.gif`, a balloon drifting across the colour
  landscape. `video.bin` is rebuilt (`stream --install` uploads it); older
  players can't show colour streams.
- `a2kit chargen` reads the Apple II's character ROM off its screen with a USB
  video grabber (Linux), for the MAME emulator, and checks it against the real
  ROM's checksum; on the tested II+ it matched exactly. The ROM is never part of
  a2kit. New built file: `screenhold.bin`.
- Shows can have more than two pictures: the third and later are kept packed in
  the space sound uses and unpacked onto the hidden page just before they show
  (it takes 0.1-0.4 s). The starter show now ends on the colour landscape.
  `sequencer.bin` is rebuilt; `show send` uploads the new one by itself.
- Colour pictures: `image --colour` (and `colour = true` in a show's image scene)
  uses the Apple II's six hi-res colours, for a colour monitor or TV, and its
  `--preview` shows the colours as the MAME emulator draws them. New example:
  `examples/colour.png`.
- Colour pictures are cleaner: areas already in one of the six colours no longer
  get stray dots from the dither.
- The colour example is redrawn in the six colours, as Apple II art was, so it
  converts cleanly instead of grainy (and packs to 1,264 bytes instead of 6,076).
- Pictures upload packed when that's quicker: `send` and `show send` pack them,
  and a 155-byte unpacker on the Apple II unpacks them (`--no-pack` turns it off).
  Line art packs best: the starter show uploads in 2.5 s instead of 2.9 s at
  115200 baud, and in 13.9 s instead of 18.5 at 19200. The receiver and boot disk
  are unchanged; `unpack.bin` is a new built file.
- `audio --preview out.wav` writes what the Apple II's speaker will produce, from the
  player's exact timing; `video --preview out.gif` shows the screen as the player
  draws it, frame by frame, at the speed it plays.
- a2kit now runs on Python 3.10 as well as 3.11 and newer: on 3.10 it reads show
  scripts with the `tomli` package, which `requirements.txt` installs there. Tested
  with the full suite on Python 3.10.22.

## 1.0.0 (14 November 2026)

The first numbered release. Everything below works on a 1979 Apple II+ with a
Super Serial Card at 115200 baud, and in the test suite.

**Pictures, sound and video**
- `image`, `audio` and `video` convert pictures, WAV sound and animations (GIFs,
  frame folders, and video files through ffmpeg) for a 48K Apple II.
- `show plan` / `show send` build and play picture-and-sound shows from a
  `show.toml`; `stream` plays a video with sound between frames.
- `--even-out` makes speech much clearer, for audio, shows and video; `--gain`
  for video ([#3](https://github.com/arguingbananas/a2kit/pull/3)).
- About 9 seconds of sound instead of 6 with a 16K language card and
  `--language-card`; `lcprobe` tests the card
  ([#11](https://github.com/arguingbananas/a2kit/pull/11)).

**The link**
- 115200 baud by default, six times faster than the 19200 fallback
  ([#1](https://github.com/arguingbananas/a2kit/pull/1)); a bootable disk for
  each speed; low-latency mode for USB adapters
  ([#6](https://github.com/arguingbananas/a2kit/pull/6)).
- `ping` checks the link, measures it and names the likely fault.
- Every command identifies the receiver first: it says whether the Apple II is
  running this version's receiver (`VERSION MATCH` or `MISMATCH`) and names the
  `--baud` to use if the speeds differ; the banner is just `A2KIT`, centred
  ([#15](https://github.com/arguingbananas/a2kit/pull/15),
  [#16](https://github.com/arguingbananas/a2kit/pull/16),
  [#17](https://github.com/arguingbananas/a2kit/pull/17)).
- `show send` uploads the sequencer by itself when it's missing, so a show after
  a power cycle no longer needs `--install` to be safe
  ([#12](https://github.com/arguingbananas/a2kit/pull/12)).
- A lost final acknowledgement no longer resends into a program that has
  started; it says "most likely started" instead of failing
  ([#20](https://github.com/arguingbananas/a2kit/pull/20)).

**When things go wrong**
- `crash` reports a crash the receiver caught, or watches for one; `peek` reads
  Apple II memory, slot ROMs included; `canary` watches free RAM for changes;
  `ramload` starts a new receiver without the disk
  ([#6](https://github.com/arguingbananas/a2kit/pull/6),
  [#10](https://github.com/arguingbananas/a2kit/pull/10)).
- A stray interrupt is reported like a crash instead of running wild
  ([#15](https://github.com/arguingbananas/a2kit/pull/15)).

**Docs and upkeep**
- Quick Start, Installation Guide and Manual, with PDFs; a full memory map
  ([#2](https://github.com/arguingbananas/a2kit/pull/2)); how to restart after
  Ctrl-Reset ([#13](https://github.com/arguingbananas/a2kit/pull/13)).
- `make smoke`, `make lint`, pinned tested versions, and contributor and
  security guides ([#7](https://github.com/arguingbananas/a2kit/pull/7)).
