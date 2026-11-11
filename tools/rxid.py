#!/usr/bin/env python3
"""rxid: find out which receiver is running, on first contact.

Every a2kit command that talks to the receiver calls check() before anything
else. It reads the receiver's code back from $0800 and compares it with this
computer's receiver.bin (or receiver-19200.bin at 19200 baud), then:

  - says which it is, on the computer and, unless asked not to, on the Apple
    II's bottom text line ($07D0-$07F7), where ping writes too:
        A2KIT 115200 RX MATCHES THIS PC
        A2KIT 115200 RX OTHER BUILD: RAMLOAD
  - if the receiver doesn't answer at this speed but does at the other one,
    stops with the --baud to use;
  - if it doesn't answer a read at all (no receiver, or a disk from before
    memory reads existed), says so and lets the command go on.

The banner on the Apple II is just A2KIT, so this is where the speed and build
show up. It costs one read of about 360 bytes, a fraction of a second.
"""
import os
import sys

import a2hdr
import send

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RECEIVER = 0x0800
TEXT_LINE = 0x07D0              # text page 1, row 23 (the bottom line)
BUILDS = {115200: "receiver.bin", 19200: "receiver-19200.bin"}
RETRIES = 2                     # per speed: enough for a lost reply, quick when nothing answers


def build_code(baud):
    return open(os.path.join(ROOT, BUILDS[baud]), "rb").read()


def apple_text(msg):
    """40 Apple II screen bytes (normal, upper case) for msg, centred."""
    msg = msg.upper()[:40].center(40)
    return bytes((ord(c) & 0x7F) | 0x80 for c in msg)


def identify(port, baud):
    """'same' if the receiver in memory is this computer's build for this speed,
    the other speed (an int) if it's this computer's build for that one, 'other'
    if it's neither, None if nothing answered a read. (On a real line the wrong
    speed answers nothing; through an emulator's pseudo-terminal it does.)"""
    code = build_code(baud)
    try:
        now = send.read_memory(port, RECEIVER, len(code), retries=RETRIES)
    except IOError:
        return None
    if now == code:
        return "same"
    for other in BUILDS:
        if other != baud and now == build_code(other)[:len(now)]:
            return other
    return "other"


def _stderr(msg):
    print(msg, file=sys.stderr)


def check(port, baud=a2hdr.DEFAULT_BAUD, screen=True, out=_stderr):
    """Identify the receiver and report it; see the module docstring. Returns
    'same', 'other' or None. Raises SystemExit if it answers only at the other speed."""
    found = identify(port, baud)
    if found is None:
        for other in BUILDS:
            if other == baud:
                continue
            port.baudrate = other
            try:
                answers = identify(port, other) is not None
            finally:
                port.baudrate = baud
            if answers:
                raise SystemExit("the receiver answers at %d baud, not %d: use --baud %d, or boot the %d disk"
                                 % (other, baud, other, baud))
        out("could not identify the receiver: it didn't answer a memory read (is it running, "
            "and from a disk built with read support?); going on")
        return None
    if isinstance(found, int):
        raise SystemExit("the receiver is the %d baud one, not %d: use --baud %d, or boot the %d disk"
                         % (found, baud, found, baud))
    if found == "same":
        out("receiver: %s, %d baud, matches this computer's build" % (BUILDS[baud], baud))
        line = "A2KIT %d RX MATCHES THIS PC" % baud
    else:
        out("receiver: answers at %d baud, but isn't this computer's %s; "
            "a2kit ramload --port ... puts the current one in memory" % (baud, BUILDS[baud]))
        line = "A2KIT %d RX OTHER BUILD: RAMLOAD" % baud
    if screen:
        send.Sender(port).send_blob(apple_text(line), TEXT_LINE)
    return found
