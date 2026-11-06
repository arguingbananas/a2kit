# a2kit Roadmap

Where a2kit stands and what comes next, most urgent first. Last updated 5 November 2026.

## Where things stand

| Work | Where | State |
|---|---|---|
| Pictures, shows, video and sound at 115200 baud | `main` | Tested on a 1979 Apple II+ ([numbers](INSTALL.md#tested-setup)) |
| Unexplained crashes on that machine | [Section 1](#1-find-out-why-the-apple-ii-crashes-when-idle) | **Open, and blocking hardware tests** |
| Crash reporter, `peek` and `ramload` | Branch `feature/crash-reporter` | Pushed; partly tested on hardware |
| Longer sound using a language card | Branch `feature/language-card` | Pushed as work in progress; long-sound test on hold |
| Full memory map for the Manual | [PR #2](https://github.com/arguingbananas/a2kit/pull/2) | Open; conflicts with `main` and has an error |
| This roadmap | Branch `docs/roadmap` | Docs only; can merge at any time |

## 1. Find out why the Apple II crashes when idle

This comes first, because a crash during a test can't be pinned on either the new code or the machine.

The machine has crashed four times with the receiver waiting and nobody touching it:

| When | Last known alive | Screen afterwards | Notes |
|---|---|---|---|
| 3 Nov | Unknown | `0002- A=20 X=20 Y=26 P=74 S=E9` | Older disk, before the receiver used the language card. Registers match the idle loop |
| 3–4 Nov | 23:50:26, the final ping of a 30-minute test | `BD02- A=00 X=20 Y=28 P=37 S=F3` | Registers match the idle loop. Keyboard dead until Reset |
| 4–5 Nov | 21:38:37, the final ping of a 30-minute test | `0000- A=80 X=00 Y=89 P=B5 S=26` | Registers don't match the idle loop, and much of the stack is used: it ran wild first |
| 5 Nov | 19:37:58, about 59 minutes into a run | `0000- A=E4 X=20 Y=11 P=B4 S=6D` | Serial port open throughout. Went quiet with no registers showing; they appeared later with no key pressed |

Two earlier oddities may be related, but that's unproven: a first picture at 115200 that came up as lo-res blocks with a dead keyboard, and a drop to Applesoft with nothing pressed.

**Ruled out so far:**
- **A closed serial port:** the fourth crash happened with it open.
- **An NMI going straight to the crash address:** `$3FB` holds `FF FF FF`, which would have changed the registers.
- **A serial packet:** the idle registers don't fit one, and the receiver leaves the card's interrupts off.

**Still possible:** a memory, bus or CPU fault; the RAMCard or its pigtail; a poor connection; heat, as the only timed crash came after about an hour; or the power supply. None of these is proven, and a2kit itself isn't fully ruled out until the machine has been tested without it.

**Next steps, changing one thing at a time:**
1. Finish the current `a2kit crash --watch` run, then read its report and compare it with the screen.
2. Reseat the cards and the pigtail, checking the pigtail for bent pins. Photograph how it's attached first, in case a RAM chip has to go back if the card ever comes out.
3. Run the same test again to see whether anything changed.
4. Leave the machine at the plain `]` prompt for several hours, typing a key now and then. The cursor blinks even if the CPU has stopped, so only a typed character shows it's alive. A crash there means a2kit isn't needed to trigger the fault.
5. If it still crashes, repeat with the RAMCard removed, then consider a memory test, the power supply's voltages, and a fan to test the heat idea.

## 2. Finish the work in progress

**Suggested merge order:** this roadmap, then the crash reporter, then the language card, then the memory map. The crash reporter goes first because it helps test everything after it. The language-card branch then has to combine both sets of receiver changes and rerun the tests. The combined build is 375 of the 380 bytes available, but so far that's only been checked for size.

### Crash reporter (`feature/crash-reporter`)

Adds `a2kit peek`, `a2kit crash` with `--watch`, and `a2kit ramload`.

- [x] Simulator tests pass
- [x] The Monitor addresses it relies on match the real ROM
- [x] Memory reads work on real hardware
- [x] An earlier version of the `ramload` loader worked on real hardware
- [ ] Run the final `ramload`, which adds a stack reset and a fuller check
- [ ] See a real crash report that matches the screen, or force one with a small `BRK` program
- [ ] See a hang logged with the right time
- [ ] Boot the new `a2kit-boot.dsk` from the SD card
- [ ] Rerun `ping`, a picture, a show and the video with sound, and compare timings
- [ ] Add the hardware results to the docs, rebuild the PDFs, and open a PR

**Known limits:** a crash that never reaches `BRK` shows up only as silence. `peek` above `$D000` reads ROM, not a language card. Read replies go out back-to-back, so these commands may not work on a card refitted with a WDC 65C51.

### Longer sound (`feature/language-card`)

Raises the sound limit from about 6.2 s to about 9.2 s with `--language-card`. Machines without a card keep today's limit. Includes `a2kit lcprobe`, which reported 16K GOOD on the real Microsoft RAMCard.

- [x] Simulator tests pass, using a language-card model
- [ ] Rerun `a2kit lcprobe`, then play `examples/long_show.toml`, listening for a glitch where the sound reaches the card at 6.2 s
- [ ] Play the stress clip with long sound (`out/stress_long.a2v` locally, which needs regenerating elsewhere)
- [ ] Update the Manual, Quick Start, Install Guide and README, and rebuild the PDFs
- [ ] Correct the Install Guide's Apple II row, which still describes this machine as 48K
- [ ] Open a PR

**After that:** the card's second `$D000` bank could take the limit to about 10.2 s. `peek` could also learn to read the card.

### Memory map (PR #2)

Its content is useful, but it's out of date, conflicts with `main`, and says the card has 12K at `$E000–$FFFF` when it's 8K (the card is 8K there plus two 4K banks at `$D000`). Update it after the language-card branch merges, since that branch changes the map again, or close it and carry the content over.

## 3. Small improvements

- **Update the Installation Guide for ADTPro.** ADTPro 2.1.0 works on this machine at 115200 by booting its disk image from the Floppy Emu. Note that it needs the language card; that a2kit and ADTPro can't share the port; and that a `.DSK` on the Emu shouldn't be formatted in-system (write to a blank image, or use the Emu's `Blank.WOZ`).
- **Document the working cable.** A photo or pinout of the cable and the card's jumper setting is still missing.
- **Support Python 3.10.** Linux Mint 21 ships it, and the only 3.11 feature a2kit uses is `tomllib`. Fall back to the `tomli` package and lower the requirement.
- **Add video and sound previews.** Pictures already have `--preview`. `video --preview out.gif` would show frames as the player draws them, and `audio --preview out.wav` would render what the speaker produces.
- **Correct pixel shape.** Circles come out about 9% narrow on a 4:3 screen. Add an `--aspect` option, on by default once checked on a real monitor.
- **Add a gamma setting** to the converters, if pictures look too dark or too bright on a real monitor.
- **Compress uploads.** A small 6502 decompressor, such as ZX0 or LZSA, could roughly halve picture upload times.
- **Handle a lost final acknowledgement.** If the last reply is lost, the computer reports an error even though the Apple II carried on. Say "probably started" instead.
- **Reduce the sound's whine.** Measured on the Apple II+: notes in tune, no page-change tick, but a whine at the 7,972 Hz sample rate about 5 dB below the notes. Try a faster player, such as 64 cycles a sample (about 16 kHz) with 3-bit samples, and compare by ear. A measured pulse-width table could also even out the loudness steps.
- **Calibrate video timing if needed.** `BURST_MARGIN` in `stream.py` and the 95% frame budget in `videoconv.py` have held up so far, including the stress clip. Adjust them only if a real clip runs late.

## 4. Bigger features

- **Shows that run without the computer.** Save a show to a disk that boots and plays it. This needs a loader beyond track 0: a small custom one, or a distributable ProDOS 8 (check its licence).
- **More pictures per show.** Two hi-res pages limit a show to two pictures. Upload the next picture between scenes, or keep compressed pictures in memory, which the language card would help with.
- **Colour pictures.** The II+ can show six colours in hi-res, so a colour converter is a natural next step.
- **The effects library:** page flipping, mid-screen mode changes, raster-style effects and scrolling text. The II+ has no vertical-blank signal, so timing would use the floating bus.
- **Mockingboard support:** detect the card and play AY music alongside pictures, with no cycle-timing conflict with serial.
- **Two-way messages.** Memory reads add a first Apple II to computer channel. Going further would allow keyboard input, status reports and interactive uses such as a speech-to-text thin client.
- **A stronger checksum:** CRC-16 instead of XOR, if errors ever get through.

## 5. Project upkeep

- **Continuous integration.** The code is on GitHub, but nothing runs `./a2kit test` automatically yet. The suite needs cc65 and py65, and no hardware.
- **Version numbers.** Show a version in the receiver's banner and `./a2kit --version`, so it's clear which build is on a disk or in memory. This matters more now that `ramload` can put a different build in memory.
- **A guided hardware check.** A script that walks through the checklist below and records the results.
- **macOS and Windows.** Run the install guide and the test suite on each.

## Checklist for a new setup

Use this for any setup not yet listed as [tested](INSTALL.md#tested-setup), going from the smallest step to the largest so each one tests one new thing:

1. **Check the disk controller.** An early Apple II may have a 13-sector controller (boot ROM 341-0009) rather than the 16-sector one (341-0027) the boot disk needs. If so, type the receiver in with `./a2kit mon receiver.bin`.
2. **Free the serial port.** Stop anything else using it, such as the ADTPro server. A cable and jumper setting that work with ADTPro are a good sign for a2kit.
3. **Start the receiver.** Boot the disk, or type it in, and confirm the `A2KIT RX READY` banner.
4. **Check the link.** `./a2kit ping` should count to `A2KIT PING 10 OF 10` on the Apple II's bottom line and report `Link OK`. Note the port delay and the speed.
5. **Build up.** Send a full picture, then the starter show, then video without sound, then with sound.
6. **Leave it idle for a while.** Run `./a2kit crash --watch` for at least a couple of hours, once a build with it is available. Short tests missed the crashes in section 1.
7. **Write down the results:** what worked, any retries, real upload times, how the sound and pictures came across, and a photo of the jumper block and cable wiring.

**If something goes wrong:**

| Symptom | Likely cause or fix |
|---|---|
| `no ACK` with the banner showing | Cable, jumper orientation or handshake lines |
| Uploads slower than `show plan` estimates | `./a2kit ping` measures the real rate; try a different `--chunk` |
| Video overruns, late frames, or damage after sound bursts | See the video timing item in section 3 |
| Disk doesn't boot | 13-sector controller: type the receiver in or upgrade the PROMs. Otherwise, try the image in an emulator |
| A key doesn't stop a show or video | Adjust where the programs poll the keyboard |

## Done

- **First hardware test** (2 November 2026, Apple II+). Everything worked after two fixes: a show ending on text page 2, and banners piling up on the booted receiver's screen.
- **`./a2kit ping`:** checks the link, measures the port's delay and the real upload rate, and names the kind of fault.
- **Built-in serial ports:** documented, and part of the tested setup.
- **115200 baud** (3 November 2026). Now the default, with `a2kit-boot-19200.dsk` as the fallback. Ping, a picture, the show and the video all ran, including a stress clip at the full byte budget with and without sound.
- **Measured results:** in the [Installation Guide](INSTALL.md#tested-setup).
- **Version control:** the project is on GitHub.
- **ADTPro checked on this machine** (5 November 2026). A disk image went from the computer to the Emu and back byte-for-byte. Nothing in a2kit changed for this.
