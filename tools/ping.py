#!/usr/bin/env python3
"""ping: check the serial link to the Apple II and measure it.

  ping.py --port /dev/ttyS0
  ping.py --port /dev/ttyUSB0 --count 20 --speed 32

The receiver must be running (its banner on screen). ping sends small packets
with the normal upload protocol and times each reply. Each one also writes a
line of text to the bottom row of the Apple II's text screen ("A2KIT PING 3 OF
10"), so you can see on the Apple II that the data really arrived. Then a speed
test sends --speed full packets to hi-res page 2 ($4000, not on screen in text
mode) and reports the real upload rate.

ping never ends the session, so the receiver is still listening afterwards and
you can send straight away. It overwrites $07D0-$07F7 (the bottom text line)
and the first --chunk bytes at $4000.

Exit code: 0 if every packet was acknowledged, 1 if not, 2 for a usage error.
"""
import argparse
import sys
import time

import a2hdr
import send

TEXT_LINE = 0x07D0          # text page 1, row 23 (the bottom line)
SPEED_ADDR = 0x4000         # hi-res page 2: harmless scratch space in text mode
RECEIVER_TIMEOUT = 1.2      # the receiver gives up on a half-received packet after ~1 s
COPY_CYCLES = (18, 40)      # receiver: cycles per byte to copy a good packet, plus fixed cost
GOOD_TURNAROUND = 0.005     # seconds; above this the port itself is adding noticeable delay
PICTURE = 8192


def apple_text(msg):
    """40 Apple II screen bytes (normal, upper case) for msg, centred."""
    msg = msg.upper()[:40].center(40)
    return bytes((ord(c) & 0x7F) | 0x80 for c in msg)


def wire_seconds(nbytes, baud):
    return nbytes * 10.0 / baud


def apple_seconds(n):
    return (COPY_CYCLES[0] * n + COPY_CYCLES[1]) / a2hdr.CPU_HZ


def exchange(port, seq, addr, data, clock):
    """Send one packet; return (kind, seconds, reply byte). kind is ack, nak, other or none."""
    pkt = send.make_packet(seq, addr, data)
    port.reset_input_buffer()
    t0 = clock()
    port.write(pkt)
    reply = port.read(1)
    elapsed = clock() - t0
    if not reply:
        return "none", elapsed, None
    b = reply[0]
    return ("ack" if b == send.ACK else "nak" if b == send.NAK else "other"), elapsed, b


def picture_estimate(turnaround, chunk, baud):
    """Seconds to upload an 8 KB picture, for a given host turnaround per packet."""
    full, rest = divmod(PICTURE, chunk)
    sizes = [chunk] * full + ([rest] if rest else []) + [0]
    return sum(wire_seconds(n + 7, baud) + apple_seconds(n) + turnaround for n in sizes)


def run(port, baud=19200, count=10, speed=16, chunk=128, out=print, clock=time.perf_counter, sleep=time.sleep):
    """Ping, then speed-test. Returns a dict of results (also printed through out)."""
    res = {"sent": 0, "ack": 0, "nak": 0, "other": 0, "none": 0, "others": set(),
           "rtt": [], "turnaround": [], "speed": None, "speed_sent": 0, "speed_ack": 0}
    seq = 0
    for i in range(1, count + 1):
        data = apple_text("A2KIT PING %d OF %d" % (i, count))
        kind, t, b = exchange(port, seq, TEXT_LINE, data, clock)
        seq = (seq + 1) & 0xFF
        res["sent"] += 1
        res[kind] += 1
        if kind == "ack":
            pure = wire_seconds(len(data) + 7, baud) + apple_seconds(len(data))
            res["rtt"].append(t)
            res["turnaround"].append(t - pure)
            out("  %2d  ACK   %6.1f ms   (line and Apple II %5.1f ms, port %5.1f ms)" % (i, t * 1000, pure * 1000, (t - pure) * 1000))
        elif kind == "nak":
            out("  %2d  NAK   %6.1f ms   (the Apple II got it, but the checksum failed)" % (i, t * 1000))
        elif kind == "other":
            res["others"].add(b)
            out("  %2d  ???   %6.1f ms   (unexpected reply byte $%02X)" % (i, t * 1000, b))
            sleep(0.2)
        else:
            out("  %2d  ---   no reply within %.1f s" % (i, t))
            sleep(RECEIVER_TIMEOUT)               # let the receiver give up and resync

    if speed and res["ack"]:
        payload = bytes((k * 37) & 0xFF for k in range(chunk))
        t0 = clock()
        for _ in range(speed):
            kind, _t, _b = exchange(port, seq, SPEED_ADDR, payload, clock)
            seq = (seq + 1) & 0xFF
            res["speed_sent"] += 1
            if kind == "ack":
                res["speed_ack"] += 1
            elif kind == "none":
                sleep(RECEIVER_TIMEOUT)
        elapsed = clock() - t0
        res["speed"] = res["speed_ack"] * chunk / elapsed if elapsed > 0 else None
    return res


def report(res, baud, chunk, out=print):
    """Print a summary and a plain-language diagnosis. Returns the exit code."""
    out("")
    out("%d of %d answered with ACK" % (res["ack"], res["sent"]) +
        ("; %d NAK" % res["nak"] if res["nak"] else "") +
        ("; %d unexpected" % res["other"] if res["other"] else "") +
        ("; %d no reply" % res["none"] if res["none"] else ""))
    if res["rtt"]:
        r, p = res["rtt"], res["turnaround"]
        out("round trip  min/avg/max  %.1f / %.1f / %.1f ms" % (min(r) * 1000, sum(r) / len(r) * 1000, max(r) * 1000))
        out("port delay  avg %.1f ms per packet (round trip minus time on the line and in the Apple II)" % (sum(p) / len(p) * 1000))
    line_rate = baud / 10.0
    if res["speed"] is not None:
        out("speed test  %d x %d-byte packets: %d of them acknowledged, %.0f bytes/s = %.0f%% of the line rate (%.0f bytes/s)" % (
            res["speed_sent"], chunk, res["speed_ack"], res["speed"], res["speed"] / line_rate * 100, line_rate))
        if res["speed"] > 0:
            out("            an 8 KB picture would take about %.1f s" % (PICTURE / res["speed"]))
    out("")

    ok = res["ack"] == res["sent"] and res["speed_ack"] == res["speed_sent"]
    if res["sent"] and res["none"] == res["sent"]:
        out("No reply at all. Check, in order:")
        out("  - the receiver is running: its banner A2KIT RX READY is on the Apple II's screen")
        out("  - --baud (%d) is the number in that banner (19200, or 115200 with a2kit-boot-115200.dsk)" % baud)
        out("  - --port names the right port, and nothing else (such as ADTPro) has it open")
        out("  - the cable: a null-modem adapter may be needed, or the card's jumper block turned round")
        out("  - the adapter is RS-232, not a 3.3 V / 5 V USB-TTL board")
        out("  - the card is in the slot set in runtime/config.inc (2 unless you changed it)")
    elif res["other"] and not res["ack"]:
        out("Bytes come back, but not the receiver's replies (%s)." % ", ".join("$%02X" % b for b in sorted(res["others"])))
        out("The speed probably doesn't match: --baud (%d) must be the number in the receiver's" % baud)
        out("banner, A2KIT RX READY 19200 or 115200. Otherwise suspect the wiring or the signal levels.")
    elif res["nak"] or res["other"]:
        out("The link works but some packets arrive damaged: noise, a long or poor cable, or a")
        out("marginal adapter. Uploads will retry, but slowly. Try --chunk 64 or 32 for uploads,")
        out("a shorter cable, or a lower baud rate (User Manual, Troubleshooting).")
        if baud > 19200:
            out("At %d baud, the 19200 disk (a2kit-boot.dsk, --baud 19200) is the first thing to try." % baud)
    elif res["none"]:
        out("Some packets got no reply: a loose connection, or the receiver stopped (a key pressed?).")
    if ok:
        out("Link OK. The Apple II's bottom line should read: A2KIT PING %d OF %d" % (res["sent"], res["sent"]))
        if res["turnaround"]:
            p = sum(res["turnaround"]) / len(res["turnaround"])
            if p > GOOD_TURNAROUND:
                now, best = picture_estimate(p, 128, baud), picture_estimate(0.0005, 128, baud)
                out("Your port adds about %.0f ms to every packet. Uploads would be about %.0f%% faster without it;" % (
                    p * 1000, (now / best - 1) * 100))
                out("see 'a2kit ping' in the User Manual for how to reduce it.")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. /dev/ttyS0, /dev/ttyUSB0, COM3")
    a2hdr.add_baud_arg(ap)
    ap.add_argument("--count", type=int, default=10, help="pings to send (default 10)")
    ap.add_argument("--speed", type=int, default=16, help="packets in the speed test (default 16; 0 skips it)")
    ap.add_argument("--chunk", type=int, default=128, help="bytes per speed-test packet, 1-255 (default 128)")
    ap.add_argument("--timeout", type=float, default=1.0, help="seconds to wait for each reply (default 1.0)")
    a = ap.parse_args()
    if not 1 <= a.count <= 999:
        sys.exit("--count must be 1-999")
    if not 0 <= a.speed <= 999:
        sys.exit("--speed must be 0-999")
    if not 1 <= a.chunk <= 255:
        sys.exit("--chunk must be 1-255")
    if a.timeout <= 0:
        sys.exit("--timeout must be positive")

    import serial
    port = serial.Serial(a.port, a.baud, timeout=a.timeout)
    print("pinging the Apple II on %s at %d baud" % (a.port, a.baud))
    res = run(port, a.baud, a.count, a.speed, a.chunk)
    port.close()
    sys.exit(report(res, a.baud, a.chunk))


if __name__ == "__main__":
    main()
