# Owner: Mac owner. Generates the five HUD sound cues (spec 9.2) as short 16-bit mono WAV files.
"""Run: python3 scripts/make_sounds.py  -> writes helper/BeaconHelper/HUD/Sounds/*.wav

Python 3 standard library only. The output files are committed; rerun only to change the sounds.
"""
import math
import os
import struct
import wave

RATE = 44100
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "helper", "BeaconHelper", "HUD", "Sounds")


def envelope(i, n, attack=0.008, release=0.03):
    t = i / RATE
    dur = n / RATE
    a = min(1.0, t / attack) if attack > 0 else 1.0
    r = min(1.0, (dur - t) / release) if release > 0 else 1.0
    return max(0.0, min(a, r))


def sweep(f0, f1, dur, amp=0.45):
    """Sine sweep from f0 to f1 Hz with a soft envelope and a quiet octave overtone."""
    n = int(RATE * dur)
    out, phase = [], 0.0
    for i in range(n):
        f = f0 + (f1 - f0) * (i / n)
        phase += 2 * math.pi * f / RATE
        s = math.sin(phase) + 0.2 * math.sin(2 * phase)
        out.append(amp * s / 1.2 * envelope(i, n))
    return out


def tone(f, dur, amp=0.45):
    return sweep(f, f, dur, amp)


def silence(dur):
    return [0.0] * int(RATE * dur)


def click(dur=0.025, f=2200, amp=0.6, decay=180.0):
    """Short exponentially decaying burst: reads as a mechanical click."""
    n = int(RATE * dur)
    out = []
    for i in range(n):
        t = i / RATE
        s = math.sin(2 * math.pi * f * t) * 0.7 + math.sin(2 * math.pi * f * 1.7 * t) * 0.3
        out.append(amp * s * math.exp(-decay * t) * min(1.0, i / 20.0))
    return out


def write(name, samples):
    path = os.path.join(OUT, name + ".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))
    print("wrote", os.path.relpath(path), f"{len(samples) / RATE * 1000:.0f} ms")


def main():
    os.makedirs(OUT, exist_ok=True)
    write("listen_start", sweep(520, 880, 0.14))
    write("listen_stop", sweep(880, 520, 0.14))
    write("tick", click(dur=0.03, f=1400, amp=0.25, decay=250.0))
    write("action", click(dur=0.04, f=2200, amp=0.6, decay=160.0))
    write("error", tone(220, 0.12, 0.5) + silence(0.06) + tone(180, 0.16, 0.5))


if __name__ == "__main__":
    main()
