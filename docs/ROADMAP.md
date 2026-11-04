# a2kit Roadmap

What to do once the existing code has been tried on real hardware. Section 0 is the test itself; everything after it assumes the test has been run and its results written down.

## 0. The hardware test (first)

**Done on 2 November 2026** on a 1979 Apple II+ (see [Tested setup](INSTALL.md#tested-setup) for the configuration and the measured numbers). Everything worked; two problems were found and fixed (a show ending on text page 2, and banners piling up on the booted receiver's screen). All the sounds played. A recording of the jingle showed the notes within about 0.5% of pitch, no measurable page-change tick, and a clear whine at the 7,972 Hz sample rate (item *Sound* in section 1). The steps below remain the checklist for any new setup.

Go from the smallest step to the largest, so each step tests one new thing:

1. **Check the disk controller.** A 1979 Apple II+ may still have the original 13-sector Disk II controller (boot ROM 341-0009) rather than the 16-sector one (341-0027) that came with DOS 3.3 in 1980. The boot disk needs 16 sectors. If it's 13-sector, type the receiver in instead (`./a2kit mon receiver.bin`).
2. **Free the serial port.** If ADTPro has been using the same card and cable, stop the ADTPro server first: only one program can hold the port. A cable and jumper setting that already work with ADTPro are a good sign for a2kit too.
3. **Receiver.** Boot the disk (or type it in) and confirm the `A2KIT RX READY` banner.
4. **The link.** `./a2kit ping`: the Apple II's bottom line should count to `A2KIT PING 10 OF 10` and the verdict should be `Link OK`. Note the port delay and the speed-test result.
5. **A full picture, then the starter show, then the video without sound, then with sound.**
6. **Write down the results:** what worked; retries; real upload times; how the sound sounded; how pictures looked on your monitor; a photo of the card's jumper block and the cable wiring that worked.

## 1. Fix what the test finds

These are the places most likely to need adjusting, and what to change if they do.

| Area | What to look for | Likely fix |
|---|---|---|
| Wiring and handshake | `no ACK` with the banner showing | Document the working cable, jumper orientation and any handshake-line tie-offs for the Super Serial Card II, with a pinout |
| Upload speed | Real time per picture vs the estimate (`show plan` assumes line rate) | `./a2kit ping` measures the per-packet port delay and the real rate; tune the default `--chunk`; make the estimates use the measured overhead |
| Video timing | Overruns, frames running late, garbage after sound bursts | Calibrate `BURST_MARGIN` (0.05 s) and the 95 % frame budget in `videoconv.py` against the real port |
| Sound | Measured: notes in tune; no audible page-change tick; a whine at the 7,972 Hz sample rate, about 5 dB below the notes in the recording | Try a faster player (for example 64 cycles a sample, about 16 kHz, with 3-bit samples) that moves the whine above most adults' hearing, and compare by ear; separately, a measured pulse-width table could even out the loudness steps. No tick fix needed |
| Pictures | Too dark or too bright; circles look narrow | Add a gamma setting to the converters; correct for non-square pixels (see 2.5) |
| Boot disk | Doesn't boot | 13-sector controller: type the receiver in, upgrade the controller's PROMs, or build a 13-sector boot variant; otherwise check the image in an emulator |
| Keyboard and exits | A key doesn't stop a show or video when expected | Adjust where the programs poll the keyboard |

## 2. Near-term improvements (small, high value)

1. ~~**Document built-in serial ports.**~~ **Done:** built-in ports are in the Hardware and port-finding sections, and are the tested setup.
2. **Support Python 3.10.** Linux Mint 21 ships Python 3.10, and the only 3.11 feature a2kit uses is `tomllib` (in `show`). Fall back to the `tomli` package on 3.10 and lower the requirement.
3. ~~**Add `./a2kit ping`.**~~ **Done:** it checks the link, writes a visible counter to the Apple II's screen, measures the port's delay and the real upload rate, and says in plain words what kind of fault it found.
4. **Add previews for video and sound.** `video --preview out.gif` would render the frames exactly as the player will draw them (including frames that haven't caught up yet). `audio --preview out.wav` would render what the speaker will actually produce. Both let you judge a clip without the hardware.
5. **Add pixel-shape correction.** Apple II pixels aren't square, so circles come out roughly 9 % narrow on a 4:3 screen. Add an `--aspect` option (on by default once checked on a real monitor).
6. **Run the line at 115200 baud.** Built and run on the real machine (3 November 2026; see [Tested setup](INSTALL.md#tested-setup) for the numbers): `a2kit-boot.dsk` runs the Super Serial Card from its crystal (as ADTPro does), six times the line speed. Ping, a picture, the show and the video all ran, including a stress clip at the full byte budget with and without sound. The simulator shows the receiver needs about 63 of the 88.6 cycles each byte allows and the video player about 67. 115200 is now the default, and `a2kit-boot-19200.dsk` is the fallback.
7. **Compress uploads.** A dithered picture compresses well. A small 6502 decompressor (several well-tested ones exist, such as ZX0 or LZSA) could roughly halve picture upload times.
8. **Handle a lost final acknowledgement.** If the reply to the last packet is lost, the computer reports an error even though the Apple II carried on. Treat a missing final ACK as "probably started" and say so, instead of failing.
9. **Update the docs with what was measured.** Mostly done: the Apple II+ is marked tested and the measured numbers are in the Installation Guide. Still to add: a photo or pinout of the working cable and the card's jumper setting.

## 3. Bigger features

1. **Shows that run without the computer.** Save a show to a disk that boots and plays it standalone. That needs a disk loader beyond track 0: either a small custom RWTS, or a freely distributable ProDOS 8 release (such as ProDOS 2.4; check its licence) with an a2kit system file.
2. **More pictures per show.** Two hi-res pages limit a show to two pictures. Either upload the next picture between scenes or keep compressed pictures in the free memory and unpack them in turn.
3. **Colour pictures.** The II+ can show six colours in hi-res. A colour converter (choosing each byte's palette bit and dithering against the artefact colours) is a natural next converter.
4. **The effects library** from the original plan: page-flip animation, mid-screen mode changes, raster-style effects and scrolling text. The II+ has no vertical-blank signal, so the timing would come from the floating-bus technique.
5. **Mockingboard support:** detect the card and play AY music alongside the pictures (no cycle-timing conflict with serial, unlike the speaker).
6. **Two-way messages.** Today the Apple II only sends ACK and NAK. A small Apple II → computer channel would allow keyboard input, confirmations and status reports, and is the base for interactive uses such as a speech-to-text thin client.
7. **A stronger checksum** (CRC-16 instead of XOR), if the test shows errors getting through.

## 4. Project upkeep

- **Version control and CI.** Put the project in a Git repository and run the simulator suite (`./a2kit test`) automatically on every change.
- **A guided hardware check.** A script that walks through section 0 step by step and records the results, so every future hardware change is checked the same way.
- **Version numbers.** Show a version in the receiver's banner and `./a2kit --version`, so it's clear which build is on a disk.
- **macOS and Windows.** Run the install guide and the test suite on each.
