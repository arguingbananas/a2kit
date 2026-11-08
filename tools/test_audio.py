"""Test audioconv's --compress on synthetic signals: speech-like bursts of very
different loudness, a bass tone the speaker can't play, and silence."""
import os, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import a2hdr, audioconv
from testlib import expect, finish

RATE = a2hdr.SPEAKER_RATE
N = int(RATE)               # samples in one second
t = np.arange(N) / RATE


def levels(data, n):
    """The 4-bit levels in a packed block, first n samples."""
    b = np.frombuffer(data, np.uint8)
    out = np.empty(2 * len(b))
    out[0::2], out[1::2] = b >> 4, b & 15
    return out[:n]


def loudness(n):
    return np.sqrt(np.mean(((n - 7.5) / 7.5) ** 2))


# a loud second, then a second 30 dB quieter: like a shouted word then a quiet one
tone = np.sin(2 * np.pi * 700 * t)
x = np.concatenate([0.9 * tone, 0.9 * 10 ** (-30 / 20) * tone])
y = audioconv.compress(x, RATE)
loud, quiet = np.abs(y[:N]).max(), np.abs(y[N + N // 5:]).max()     # skip the release
print("loud/quiet: %.0f dB before, %.1f dB after" % (30, 20 * np.log10(loud / quiet)))
expect(20 * np.log10(loud / quiet) < 6, "a 30 dB gap between loud and quiet parts shrinks to under 6 dB")
expect(len(y) == len(x), "compress keeps the length")

# the level jumps up at once, so a sudden peak can't slip through and set the scale
burst = np.zeros(N); burst[N // 2: N // 2 + 40] = 0.9 * np.sin(2 * np.pi * 700 * t[:40])
burst += 0.9 * 10 ** (-30 / 20) * tone
y = audioconv.compress(burst, RATE)
spike, rest = np.abs(y[N // 2: N // 2 + 40]).max(), np.abs(y[: N // 2]).max()
expect(20 * np.log10(spike / rest) < 6, "a 5 ms spike is turned down too, not left to set the scale (%.1f dB above the rest)" % (20 * np.log10(spike / rest)))

# bass the speaker can't play is cut, so it doesn't use up the levels
bass = 0.9 * np.sin(2 * np.pi * 50 * t)
expect(np.abs(audioconv.compress(bass + 0.05 * tone, RATE)[1000:-1000]).max() < 0.2,
       "a 50 Hz tone is mostly removed by the high-pass")

expect(not np.any(audioconv.compress(np.zeros(1000), RATE)), "silence stays silent")
expect(len(audioconv.compress(np.zeros(0), RATE)) == 0, "an empty clip is fine")

# end to end, on something shaped like speech: mostly quiet, with one brief loud syllable
speech = 0.9 * 10 ** (-30 / 20) * tone
speech[N // 2: N // 2 + N // 20] *= 10 ** (30 / 20)
fd, path = tempfile.mkstemp(suffix=".wav"); os.close(fd)
with wave.open(path, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
    w.writeframes((speech * 32767).astype("<i2").tobytes())
plain = levels(audioconv.convert(path, dither=False), N)
squashed = levels(audioconv.convert(path, dither=False, compress_speech=True), N)
print("block loudness: %.0f%% plain, %.0f%% compressed" % (loudness(plain) * 100, loudness(squashed) * 100))
expect(loudness(squashed) > 2 * loudness(plain), "a compressed block of speech-like sound is much louder overall")
short = audioconv.convert(path, max_seconds=0.5, compress_speech=True)
expect(len(short) == -(-int(0.5 * RATE) // 2 // 256) * 256, "--max-seconds still trims a compressed clip")
os.remove(path)
finish("test_audio")
