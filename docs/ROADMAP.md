# a2kit Roadmap

Where a2kit stands and what comes next, most urgent first. Last updated 10 November 2026.

## Where things stand

| Work | Where | State |
|---|---|---|
| Pictures, shows, video and sound at 115200 baud | `main` | Tested on a 1979 Apple II+ ([numbers](INSTALL.md#tested-setup)) |
| Unexplained crashes on that machine | [Section 1](#1-find-out-why-the-apple-ii-crashes-when-idle) | **Shelved.** Clean for 48 hours with extra cooling, which stays in place; heat is the likely trigger |
| Crash reporter, `peek`, `canary` and `ramload` | Branch `feature/crash-reporter` | Tested on hardware, booted from its own disk; `main` merged in; in a PR |
| Clearer speech: `--even-out` for audio, shows and video, and `--gain` for video | `main` | Merged ([PR #3](https://github.com/arguingbananas/a2kit/pull/3)); heard on the Apple II+ |
| Longer sound using a language card | Branch `feature/language-card` | Pushed as work in progress; next after the crash reporter |
| Full memory map for the Manual | [PR #2](https://github.com/arguingbananas/a2kit/pull/2) | Open; conflicts with `main` and has an error |

## 1. Find out why the Apple II crashes when idle

**Shelved on 10 November**, with the machine reliable enough to develop on: with extra cooling it ran clean for 48 hours (below). Keep the extra cooling in place. The tests to pick up later are listed under *When this is picked up again*.

The machine has crashed or hung seven times with the receiver waiting and nobody touching it:

| When | Last known alive | Screen afterwards | Notes |
|---|---|---|---|
| 3 Nov | Unknown | `0002- A=20 X=20 Y=26 P=74 S=E9` | Older disk, before the receiver used the language card. Registers match the idle loop |
| 3–4 Nov | 23:50:26, the final ping of a 30-minute test | `BD02- A=00 X=20 Y=28 P=37 S=F3` | Registers match the idle loop exactly. Keyboard dead until Reset |
| 4–5 Nov | 21:38:37, the final ping of a 30-minute test | `0000- A=80 X=00 Y=89 P=B5 S=26` | Registers don't match the idle loop, and much of the stack is used: it ran wild first |
| 5 Nov | 19:37:58, about 59 minutes into a run | `0000- A=E4 X=20 Y=11 P=B4 S=6D` | Serial port open throughout. Went quiet with no registers showing; they appeared later with no key pressed |
| 6 Nov | 19:10:12, logged by the crash reporter, 22 h 40 min into the run | `0002- A=00 X=20 Y=01 P=37 S=CE` | Registers match the idle loop exactly. `$0000` held `00`, so it stopped there at once. Stack page all zero apart from the handler's bytes |
| 6 Nov | 22:26, 41 min after a power-up and a fresh load | Nothing new | A hang with no `BRK`; the space bar did nothing. Stack page wiped to zero, and 5 bytes at `$8080–$80FF` changed to `00` |
| 7 Nov | 22:02, 5 h 23 min after reseating everything | Nothing new | A hang with no `BRK`; the space bar did nothing. Stack page `$0100–$01C3` zero, and 24 bytes at `$8080–$8546` changed to `00`, `80` or `01` |

Two earlier oddities may be related, but that's unproven: a first picture at 115200 that came up as lo-res blocks with a dead keyboard, and a drop to Applesoft with nothing pressed.

**8 November: the canary found a failing RAM chip.** With the RAMCard removed, free RAM (`$0B00–$BFFF`) held its pattern for 8 h 42 min. Then, from 08:32 until the run was stopped at 10:09, bit 0 of about half the bytes in `$8000–$BFFF` read wrong at every check, in both directions, with different bytes each time. Every listed change was bit 0 alone (xor `01`), and nothing below `$8000` changed, the stack page included. On a II+ each 16K bank is one row of eight 4116 chips, one per data bit, so this points at the bit-0 chip of the `$8000–$BFFF` row, or its socket. It appeared only after hours of running, so heat may bring it on.

**8–10 November: 48 hours clean with extra cooling.** With nothing else changed (the same chip, the RAMCard still out), the canary ran from 22:01 on 8 November to 22:02 on 10 November with better cooling on the Apple II: 565 checks, every one with 0 bytes changed, and no stack wipe, crash or silence. That is more than five times as long as the bit-0 errors took to appear without it (8 h 42 min), and over twice the longest run before any earlier failure (22 h 40 min). So heat is the likely trigger. What it heats is still open: the bit-0 chip itself, or the power supply, whose aging capacitors could let the RAM's supplies sag or ripple as it warms, enough for one weak chip to fail. A sagging supply would also explain the stack wipes in the `$0000` bank better than one bad chip does.

**What the memory dumps show.** Dumps taken after the last three were compared with dumps of the same machine when healthy, and with the reset replayed on its own ROM in the simulator:
- **Memory changes with no program writing it.** The stack page was wiped to zero three times, where a healthy dump shows it full of power-up noise. Booting, `ramload` and an Applesoft cold start don't do this. A failing DRAM chip can lose its contents with no write at all, so this doesn't need a stray write.
- **The changes outside the stack sit in `$8000–$85xx`:** `$8080`, `$80C6`, `$80DF` and `$80FF` changed in both dumps that cover them. That's the bank the canary later caught failing, which fits a bad chip there better than the address fault (a glitch on A15 or in selecting the 16K RAM banks) first suspected. No a2kit code writes there.
- **The receiver's code was never damaged**, and `$D000–$FFFF` always read as ROM, so the RAMCard was never switched in.
- **A wiped stack would itself crash the receiver** the next time its listening routine returns. In the simulator, overwriting its stack did exactly that. So the stray writes may cause the crashes rather than just follow them, though not every crash fits.

**Ruled out so far:**
- **Closing the serial port.** The long hangs happened with the port open. Two quick jumps did follow a close, but only after a Monitor `980G` had left interrupts on: the card's line change raised an interrupt, and the junk IRQ vector sent the CPU to `$0000`. The receiver now keeps interrupts off, and a 30-minute test closing the port 360 times then passed.
- **An NMI going straight to the crash address:** `$3FB` holds junk that would have changed the registers.
- **Reseating the cards and pigtail:** the machine hung again 5 h 23 min later.
- **The RAMCard as the only cause:** the canary run had the card removed and still found the failing bank. Apple-Cillin II's memory test had passed with the card fitted, but on a cool machine; the fault took hours to appear.

**The failing bit is in the language card's socket.** Rows C, D and E of the motherboard's RAM hold `$0000–$3FFF`, `$4000–$7FFF` and `$8000–$BFFF`, and the leftmost chip of each row, column 3, holds bit 0. So bit 0 of `$8000–$BFFF` is chip **E3**, and E3 is the socket a language card's pigtail plugs into: its chip is pulled so the cable can take its place. That matches this machine, whose pigtail goes into the leftmost socket of row E (photographed 10 November). These come from several Apple II forum answers that agree, not from Apple's own documentation ([RAM layout](https://www.applefritter.com/node/33651), [E3 and the language card](https://www.applefritter.com/comment/77315), [Gayler, *The Apple II Circuit Description*](https://www.applefritter.com/files/2023/10/26/W.%20Gayler%20-%20The%20Apple%20II%20Circuit%20Description.pdf)). E3 had been opened and refitted several times: fitting the RAMCard, reseating everything, and taking the card out for the canary run.

**Still possible:** heat is now the likely trigger. **Leading theory:** a marginal contact in E3, from the socket's springs or the chip's legs worked loose by the refitting, or later the pigtail's own connection, that opens as the board warms. It fits one bit in one bank, the hours of warm-up before it failed, and the cure by better cooling. Otherwise: the E3 chip itself, or the power supply. A loose E3 contact doesn't obviously explain the stack wipes, which are in the `$0000–$3FFF` bank, unless the failing chip disturbs the shared data bus; a sagging supply would explain both.

**Restarting after a hang without losing the evidence:** don't switch off. Press Ctrl-Reset, then type `CALL -151`. The first Reset after power-up runs an Applesoft cold start, which zeros `$0800–$0802`, so type `800:78 A9 00` to put the receiver's first bytes back. Type `48:04` so `G` starts with interrupts off, then `980G`, and dump memory before anything else.

**When this is picked up again**, changing one thing at a time:
1. **The reverse test:** run `a2kit canary` without the extra cooling. If the bit-0 errors come back after about 9 hours, heat is confirmed.
2. **Chip or power supply:**
   - Heat only the suspect chip with a hair dryer on low for a few minutes, with `a2kit canary --check 30`. Failing within minutes, with the rest of the machine cool, points at the chip.
   - Or cool only the power supply, then only the motherboard, and see which run stays clean.
   - Measure the supplies at a RAM chip, cold and again after about 9 hours: on a 4116, pin 1 is –5 V, pin 8 +12 V and pin 9 +5 V, with pin 16 ground (check against a datasheet first). Drift beyond about ±5% points at the supply; ripple needs an oscilloscope.
3. **The chip swap:** with the RAMCard out, swap the E3 chip with its neighbour E4, never into the `$0000–$3FFF` row, which holds the receiver. If the errors move to bit 1 (xor `02`), it's the chip; if they stay on bit 0, the E3 socket. With the RAMCard in, E3 is reached through the pigtail, so a bit-0 failure then points at the pigtail's connection or the E3 chip. A chip that fails because of the supply would still move, so this doesn't clear the supply on its own.
4. If it still fails with good RAM and a good supply: a standalone Apple II memory tester with no serial and no a2kit running, then the plain `]` prompt test (the cursor blinks even if the CPU has stopped, so type a key now and then).

**While developing:** if anything odd happens (a hang, garbage on screen, a dead keyboard), don't power off. `a2kit crash`, `peek` and `canary` can capture the evidence first, using the restart procedure above.

## 2. Finish the work in progress

**Suggested merge order** (small and independent first, so conflicts are dealt with once, on the branch with the most to merge):

1. **This roadmap:** merged (PR #4).
2. **Clearer speech:** merged ([PR #3](https://github.com/arguingbananas/a2kit/pull/3)).
3. **The crash reporter:** in a PR, after the 48-hour clean run. `main` is merged in, with `MANUAL.pdf` and `QUICKSTART.pdf` rebuilt (both branches changed them, and Git can't merge PDFs); the tests pass, and `ping` and a show with `even_out` worked on the Apple II with the merged code.
4. **The language card.** `main` is merged in (10 Nov): the language-card lines now sit in the receiver's shared `init` routine, so a restart after a crash re-enables the card too. The combined receiver is exactly the 380 bytes available, so there is no room left, and the simulator tests pass. Its own hardware tests need the RAMCard back in, which changes the machine again, so after refitting it run `a2kit canary` for a few hours, with the extra cooling still on, before testing code on it.
5. **The memory map** (PR #2): carry its content into the Manual after the language card changes the map again, and close it.

### Crash reporter (`feature/crash-reporter`)

Adds `a2kit peek`, `a2kit crash` with `--watch`, `a2kit canary`, and `a2kit ramload`.

- [x] Simulator tests pass
- [x] The Monitor addresses it relies on match the real ROM
- [x] Memory reads work on real hardware
- [x] The final `ramload` works on real hardware, including its check of the code in memory
- [x] A real crash report matched the screen (7 Nov), and the watch now logs every new crash
- [x] Hangs were logged with the right time (6 and 7 Nov)
- [x] The handler keeps to its own part of the stack, so a report shows what the crashed program left
- [x] The receiver keeps interrupts off at its start and after a crash
- [x] The canary, as a scratch script, found a failing RAM bank on real hardware. `a2kit canary` itself then ran on the Apple II over a USB adapter (8 Nov): the fill and checks, a changed byte and its return to the pattern, a stack-page wipe, and a real `BRK` were all logged correctly
- [x] Boot the new `a2kit-boot.dsk` from the SD card (8 Nov: the code in memory matched `receiver.bin`)
- [x] Rerun `ping`, a picture, a show and the video with sound, and compare timings (8 Nov, on both the USB adapter and the built-in port: all clean, timings in the [Installation Guide](INSTALL.md#tested-setup))
- [x] Add the hardware results to the docs and rebuild the PDFs
- [x] Merge `main` in, rebuild the PDFs, retest, and open a PR (10 Nov, after the 48-hour clean run)

**Known limits:** a crash that never reaches `BRK` shows up only as silence. The `BRK` itself and the Monitor's register save overwrite the 5 stack bytes just below S. `peek` above `$D000` reads ROM, not a language card. Read replies go out back-to-back, so these commands may not work on a card refitted with a WDC 65C51.

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

- **Check the player is in memory before starting it.** `show send` and `stream` end their upload by jumping to the sequencer (`$0C00`) or the video player (`$0E00`), which only `--install` puts there. If something has overwritten it since, the Apple II runs whatever is there and hangs. This happened on 10 November: a canary run filled `$0B00–$BFFF` with its pattern, and the next `show send` without `--install` hung the machine with no sound. Now that the receiver answers memory reads, compare the player in memory with `sequencer.bin` or `video.bin` first, and upload it if it doesn't match, saying so. `--install` would then only force a fresh copy.
- **Update the Installation Guide for ADTPro.** ADTPro 2.1.0 works on this machine at 115200 by booting its disk image from the Floppy Emu. Note that it needs the language card; that a2kit and ADTPro can't share the port; and that a `.DSK` on the Emu shouldn't be formatted in-system (write to a blank image, or use the Emu's `Blank.WOZ`).
- **Explain restarting after Ctrl-Reset in the Manual.** It says `980G` restarts a booted receiver, but after the first Reset since power-up an Applesoft cold start has zeroed `$0800–$0802`, so `980G` hits a `BRK`. Give the repair (`800:78 A9 00`), and `48:04` so `G` starts with interrupts off.
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
- **Mockingboard support.** The biggest sound upgrade: the card plays music by itself, so the CPU stops being the sound source. See [the design](#mockingboard-design) below.
- **Two-way messages.** Memory reads add a first Apple II to computer channel. Going further would allow keyboard input, status reports and interactive uses such as a speech-to-text thin client.
- **A stronger checksum:** CRC-16 instead of XOR, if errors ever get through.

### Mockingboard design

*A plan, not yet started. The register-level details are from memory of the card's documentation; check them against it before writing code.*

**Why.** Speaker sound is 1-bit and cycle-timed, and has to be kept in RAM. That caps it at about 6.2 s (9.2 s with the language card), keeps the CPU busy, and brings the whine at the 7,972 Hz sample rate. Longer sound has to be sent in chunks, with a pause for each upload: a 93-second poem took 20 chunks, about 2.9 s apart. Dense music doesn't survive at all. A Hells Bells MIDI rendered for the speaker was muddy with the full band, and its 55–65 Hz bass was inaudible. On a Mockingboard the chips make the sound themselves, so none of these limits apply.

**The card.** A standard Mockingboard, usually in slot 4, has two AY-3-8910 sound chips. Each has three square-wave tone voices, a noise generator, a 4-bit volume per voice and a hardware envelope, so six voices in all, set through 14 registers per chip. The CPU reaches each AY through a 6522 VIA: a register number or value goes on the VIA's port A, then control lines on port B are pulsed, roughly 20–30 cycles per register write. The VIAs' timers can interrupt the CPU at 50 or 60 Hz to drive a player. Music is a few register changes per frame: at most about 700 bytes/s, and usually far less. Some versions add a speech chip, left out here.

**Who keeps time.** There are three ways to play, built in this order:

1. **Streamed, with the computer keeping time** (first). The computer sends each frame's register changes at the right moment, and a small player on the Apple II writes each to the chip as it arrives. There are no replies, just as with `a2kit stream` for video. It needs no interrupts, so the receiver's interrupts-off design and the junk IRQ vector at `$03FE` don't matter. At about 300 bytes/s it uses under 4% of the line. Music can be any length, with no gaps.
2. **Inside the video stream** (next). Video frames already carry up to 1,824 bytes at 115200 baud, and a few register writes per frame would give continuous music under video. Today the speaker's sound has to stop the picture for bursts; with this, sound effects could land on the bounces.
3. **From memory, timed by interrupts** (for shows that run without the computer). The tune is uploaded first, then a VIA timer interrupt writes the registers. This is how most Apple II music software works, and free RAM holds several minutes. The receiver can't take serial bytes while interrupts fire (at 115200 a byte arrives every 89 cycles), so uploads and playback can't overlap. The player must set its own IRQ vector and turn interrupts off again when it's done.

**Where the music comes from.**

| Source | Effort | Notes |
|---|---|---|
| YM files | Low | Register dumps from the same chip (Atari ST, Amstrad); only the 2 MHz clock needs rescaling to the Mockingboard's 1.023 MHz. Thousands of chiptunes exist. |
| VGM files with AY-3-8910 data | Low to medium | The same idea, from a different family of machines. |
| MIDI | Medium to high | Needs arranging into six voices, with drums as noise and short envelopes. Hells Bells would fit, with its bass as a real tone: the AY plays down to about 16 Hz. |
| WAV | Not worth it | Stepping a volume register plays samples, but that is as CPU-heavy as the speaker and no better. |

**In a2kit.**

- **`a2kit mbprobe`:** finds the card, like `lcprobe`, by looking for a 6522 timer that counts down in each slot, and plays a test tone.
- **`a2kit music FILE --port …`:** converts and streams. `--preview out.wav` renders the register stream on the computer with a small AY model, so it can be heard and tested without the Apple II.
- **Shows:** a `music = "tune.ym"` scene, played from memory.
- **Video:** `a2kit video --music tune.ym`, as part of the stream.
- **The receiver doesn't change.** It is at its 380-byte limit with the language card, so the players are separate uploaded programs, like `sequencer.bin` and `video.bin`.
- **Tests:** a VIA and AY model in the py65 simulator records register writes, so tests can check the right writes at the right times; the preview renderer turns them into sound. AppleWin and MAME emulate the card, for checks before real hardware.

**Steps.**

1. Detection and a test tone (`mbprobe`).
2. The streaming player and the YM converter, with the WAV preview.
3. Music inside video.
4. The MIDI converter.
5. The play-from-memory player for shows.

**Before starting.**

- **Hardware:** decide on the card, an original or a modern clone, since the model affects detection and whether a speech chip is present.
- **Timing:** fit it only after the idle crashes are understood. A new card is another device on the bus, and would cloud the canary results. The design, the simulator model and the converters can all be done without the card.

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
- **A USB-to-RS-232 adapter** (8 November 2026, an FTDI FT232R, on `feature/crash-reporter`). On its default 16 ms latency timer, uploads ran at about 8,000 bytes/s with 128- or 255-byte packets, because each packet took a whole number of 16 ms periods. With the timer at 1 ms they ran at 8,546 and 9,119 bytes/s, against 8,743 and 9,157 on the built-in port. Every a2kit command now sets the 1 ms timer itself on Linux, with no `sudo`, and `ping` judges how much a slow port costs from its speed test. A picture, the starter show and all four videos, with and without sound, then ran cleanly over the adapter, looking and sounding the same as on the built-in port.
- **115200 baud** (3 November 2026). Now the default, with `a2kit-boot-19200.dsk` as the fallback. Ping, a picture, the show and the video all ran, including a stress clip at the full byte budget with and without sound.
- **Measured results:** in the [Installation Guide](INSTALL.md#tested-setup).
- **Version control:** the project is on GitHub.
- **ADTPro checked on this machine** (5 November 2026). A disk image went from the computer to the Emu and back byte-for-byte. Nothing in a2kit changed for this.
