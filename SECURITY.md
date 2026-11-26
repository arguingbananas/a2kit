# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue: on GitHub, open the
repository's **Security** tab and choose **Report a vulnerability**
(<https://github.com/arguingbananas/a2kit/security/advisories/new>). Include what you
found, how to reproduce it, and which commit or version you used.

## Scope

a2kit is a set of local command-line tools. It opens no network ports and makes no
network connections; it reads files you give it and talks to an Apple II over a serial
port. Reports about handling of untrusted input files (images, sound, video, show files),
the serial protocol, or the build and documentation scripts are all welcome.

Only the `main` branch is supported.

## What a2kit defends against, and what it doesn't

- **Files you convert** (images, GIFs, WAV, video through ffmpeg, `show.toml`) are read
  with Pillow, Python's `wave` and `tomllib`. No input is evaluated as code or passed to
  a shell, and external programs (ffmpeg, make, pandoc) get their arguments as a list.
- **Damage on the line is caught**: each upload packet carries a checksum, and the
  receiver copies it into place only once that checks out; `send` refuses, before
  sending, destinations in zero page, the stack, the receiver or I/O. The video player
  refuses runs, skips and fast blocks that would write outside the page being drawn,
  and blocks that would read I/O.
- **The serial link has no authentication.** Anything that can write to the Apple II's
  serial port can upload and run code on it; an Apple II has no memory protection. Keep
  the cable between machines you control.
- **Not in scope:** the Apple II's own ROMs and hardware, other software on the Apple II,
  and the security of the computer a2kit runs on.

GitHub's Dependabot alerts, Dependabot security updates, secret scanning with push
protection, CodeQL code scanning and private vulnerability reporting are turned on for
this repository.
