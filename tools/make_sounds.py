"""
Makes the add-on's sounds from scratch (no samples), so they are ours to
ship. Run it again after changing a recipe:

    python tools/make_sounds.py

Needs numpy (only here, at build time; the add-on itself doesn't). Every
sound is built from a few small instruments (a kalimba-like pluck, a glass
bell, a soft flute, "noise" made of many quiet sines, a pitch-dropping thump),
placed in the stereo field and put in one shared room (a synthetic reverb),
all in D major pentatonic so any two sounds fit together.

  chime     normal, fighting      two kalimba notes, up a fifth
  water     water                 bubbles rising, then a glass drop
  electric  electric              a crackle, then a quick bright arpeggio
  fire      fire, dragon          a warm swell, a whoosh and crackling embers
  leaf      grass, bug, poison    leaves rustling, two wooden notes
  air       flying, psychic       a breeze rising, a flute gliding up
  sparkle   ice, fairy, steel     a cascade of tiny bells
  shadow    ghost, dark           a low hum, a soft theremin sigh
  earth     ground, rock          a deep thump, pebbles, a low wooden note
  rare      legendary, mythical   a glowing arpeggio over a pad
  search    "looking around..."   a short swish through the grass
  rest      not yet (the wait)    two soft notes, falling
  error     offline, nobody       one soft low note
"""

import os
import sys
import wave

import numpy as np

RATE = 32000
LOUDNESS = -17.0                # dBFS over the loudest 300 ms: under the voice, never over it
PEAK = 0.7                      # and never above -3 dBFS
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "addon", "globalPlugins",
                   "wildEncounters", "sounds")
rng = np.random.default_rng(20261014)


def hz(note):
    """'D6' -> its frequency (A4 = 440)."""
    names = {"C": -9, "C#": -8, "D": -7, "D#": -6, "E": -5, "F": -4, "F#": -3, "G": -2,
             "G#": -1, "A": 0, "A#": 1, "B": 2}
    name, octave = note[:-1], int(note[-1])
    return 440.0 * 2 ** ((names[name] + 12 * (octave - 4)) / 12)


def times(seconds):
    return np.arange(int(seconds * RATE)) / RATE


def attack(t, seconds):
    return np.clip(t / max(seconds, 1e-4), 0, 1)


class Track:
    """A stereo buffer that sounds are placed into."""

    def __init__(self, seconds):
        self.data = np.zeros((2, int(seconds * RATE)))

    def add(self, sound, at=0.0, pan=0.0, gain=1.0):
        """`pan` -1 (left) .. 1 (right), equal power."""
        start = int(at * RATE)
        end = min(start + len(sound), self.data.shape[1])
        if end <= start:
            return
        angle = (pan + 1) * np.pi / 4
        part = sound[:end - start] * gain
        self.data[0, start:end] += part * np.cos(angle)
        self.data[1, start:end] += part * np.sin(angle)


# ------------------------------------------------------------
# Instruments (mono)
# ------------------------------------------------------------

def kalimba(freq, seconds=1.0, decay=0.35):
    t = times(seconds)
    tone = (np.sin(2 * np.pi * freq * t) * np.exp(-t / decay)
            + 0.35 * np.sin(2 * np.pi * freq * 5.4 * t) * np.exp(-t / (decay * 0.12))
            + 0.15 * np.sin(2 * np.pi * freq * 2.0 * t) * np.exp(-t / (decay * 0.5)))
    return tone * attack(t, 0.003)


def glass(freq, seconds=1.5, decay=0.6):
    """A bell: inharmonic partials, the high ones fading first."""
    t = times(seconds)
    partials = ((1.0, 1.0, 1.0), (2.76, 0.4, 0.45), (5.40, 0.2, 0.25), (8.93, 0.08, 0.15))
    tone = sum(a * np.sin(2 * np.pi * freq * r * t) * np.exp(-t / (decay * d))
               for r, a, d in partials)
    return tone * attack(t, 0.002)


def wood(freq, seconds=0.8, decay=0.18):
    """A marimba-like note: a round fundamental and a quick knock."""
    t = times(seconds)
    tone = (np.sin(2 * np.pi * freq * t) * np.exp(-t / decay)
            + 0.5 * np.sin(2 * np.pi * freq * 3.93 * t) * np.exp(-t / (decay * 0.15))
            + 0.25 * np.sin(2 * np.pi * freq * 9.8 * t) * np.exp(-t / (decay * 0.05)))
    return tone * attack(t, 0.002)


def flute(freq_from, freq_to, seconds, glide=0.25, vibrato=0.006):
    t = times(seconds)
    curve = np.clip(t / glide, 0, 1)
    curve = curve * curve * (3 - 2 * curve)                     # smoothstep
    freq = freq_from + (freq_to - freq_from) * curve
    freq = freq * (1 + vibrato * np.sin(2 * np.pi * 5.2 * t) * np.clip(t / 0.3, 0, 1))
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    tone = np.sin(phase) + 0.18 * np.sin(2 * phase) + 0.06 * np.sin(3 * phase)
    env = attack(t, 0.08) * np.clip((seconds - t) / 0.35, 0, 1)
    return tone * env


def pad(freqs, seconds, swell=0.4, release=0.6, brightness=4):
    """Soft saw-ish voices, slightly detuned."""
    t = times(seconds)
    tone = np.zeros_like(t)
    for f in freqs:
        for detune in (-0.003, 0.003):
            for n in range(1, brightness + 1):
                tone += np.sin(2 * np.pi * f * (1 + detune) * n * t + rng.uniform(0, 6.28)) / n ** 1.6
    env = attack(t, swell) * np.clip((seconds - t) / release, 0, 1)
    return tone * env / (len(freqs) * 2)


def airy(seconds, low_from, low_to, high_from, high_to, voices=260):
    """Noise made of many quiet sines whose band moves over time (a breeze,
    a swish, rustling)."""
    t = times(seconds)
    progress = t / seconds
    low = low_from * (low_to / low_from) ** progress
    high = high_from * (high_to / high_from) ** progress
    out = np.zeros_like(t)
    for _ in range(voices):
        f = np.exp(rng.uniform(np.log(min(low_from, low_to) * 0.7),
                               np.log(max(high_from, high_to) * 1.3)))
        inside = np.clip(np.minimum(f - low, high - f) / (0.25 * f) + 0.5, 0, 1)
        out += inside * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    return out / np.sqrt(voices)


def bubble(freq, seconds=0.12):
    """A bubble: a sine whose pitch rises as it fades."""
    t = times(seconds)
    f = freq * (1 + 1.4 * t / seconds)
    phase = 2 * np.pi * np.cumsum(f) / RATE
    return np.sin(phase) * np.exp(-t / (seconds * 0.35)) * attack(t, 0.003)


def thump(seconds=0.6, f_from=110.0, f_to=42.0):
    t = times(seconds)
    f = f_to + (f_from - f_to) * np.exp(-t / 0.05)
    phase = 2 * np.pi * np.cumsum(f) / RATE
    return np.sin(phase) * np.exp(-t / 0.18) * attack(t, 0.004)


def click(freq, seconds=0.012):
    t = times(seconds)
    return np.sin(2 * np.pi * freq * t) * np.exp(-t / (seconds * 0.25))


def flutter(n, rate_hz, depth=0.7):
    """A random, smooth flicker of loudness (leaves, embers)."""
    points = int(n / RATE * rate_hz) + 3
    values = 1 - depth * rng.random(points)
    return np.interp(np.arange(n) / RATE * rate_hz, np.arange(points), values)


# ------------------------------------------------------------
# The room
# ------------------------------------------------------------

def reverb(track, wet=0.22, seconds=1.6, damping=3.0):
    """A synthetic room: decaying noise, darker as it fades, a little
    different on each side so the tail is wide. Applied by FFT convolution."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    out = np.zeros((2, track.shape[1] + n))
    shared = rng.standard_normal(n)          # partly the same on both sides: wide, but
    for side in range(2):                    # still full on one speaker
        noise = (0.7 * shared + 0.7 * rng.standard_normal(n)) * np.exp(-t * 6.9 / seconds)
        spectrum = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(n, 1 / RATE)
        noise = np.fft.irfft(spectrum * np.exp(-freqs / (RATE / damping / 2)), n)
        noise[: int(0.012 * RATE)] *= np.linspace(0, 1, int(0.012 * RATE))   # a pre-delay
        # Unity gain where the notes are (200 Hz to 4 kHz), so `wet` means what it says.
        band = (freqs >= 200) & (freqs <= 4000)
        noise /= np.sqrt(np.mean(np.abs(np.fft.rfft(noise)[band]) ** 2))
        size = track.shape[1] + n
        conv = np.fft.irfft(np.fft.rfft(track[side], size) * np.fft.rfft(noise, size), size)
        out[side] = conv * wet * 2
        out[side, :track.shape[1]] += track[side] * (1 - wet)
    return out


def loudness(data):
    """dBFS of the loudest 300 ms."""
    power = np.mean(data ** 2, axis=0)
    window = int(0.3 * RATE)
    return 10 * np.log10(np.convolve(power, np.ones(window) / window, "valid").max())


def finish(track, wet=0.22, room=1.6, level=LOUDNESS):
    data = reverb(track.data, wet, room)
    # Trim the silent tail, then fade the end.
    envelope = np.max(np.abs(data), axis=0)
    loud = np.nonzero(envelope > envelope.max() * 0.01)[0]
    data = data[:, : loud[-1] + 1] if len(loud) else data
    fade = min(int(0.15 * RATE), data.shape[1])
    data[:, -fade:] *= np.linspace(1, 0, fade) ** 2
    data[:, : int(0.002 * RATE)] *= np.linspace(0, 1, int(0.002 * RATE))
    data = data * 10 ** ((level - loudness(data)) / 20)
    peak = np.max(np.abs(data))
    return data * (PEAK / peak) if peak > PEAK else data


def save(name, data):
    os.makedirs(OUT, exist_ok=True)
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2").T.copy()
    with wave.open(os.path.join(OUT, name + ".wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    print(f"{name}.wav  {data.shape[1] / RATE:.2f} s")


# ------------------------------------------------------------
# The sounds
# ------------------------------------------------------------

def chime():
    tr = Track(1.6)
    tr.add(kalimba(hz("D6")), 0.0, -0.25, 0.8)
    tr.add(kalimba(hz("A6")), 0.13, 0.25, 0.7)
    tr.add(glass(hz("D7"), 1.2, 0.4), 0.13, 0.0, 0.12)
    return finish(tr)


def water():
    tr = Track(1.8)
    for at, f, pan in ((0.00, 520, -0.5), (0.07, 690, 0.3), (0.13, 610, -0.1),
                       (0.20, 820, 0.5), (0.24, 760, -0.35)):
        tr.add(bubble(f), at, pan, 0.55)
    tr.add(glass(hz("F#6"), 1.4, 0.5), 0.36, 0.15, 0.55)
    tr.add(glass(hz("A6"), 1.2, 0.4), 0.48, -0.2, 0.3)
    return finish(tr, wet=0.28)


def electric():
    tr = Track(1.5)
    for _ in range(26):
        tr.add(click(rng.uniform(2500, 6500)), rng.uniform(0, 0.22), rng.uniform(-0.8, 0.8),
               rng.uniform(0.15, 0.4))
    t = times(0.35)
    for i, note in enumerate(("D6", "F#6", "A6", "D7")):
        f = hz(note)
        tone = sum(np.sin(2 * np.pi * f * n * t) / n for n in (1, 3, 5))
        tone *= np.exp(-t / 0.09) * attack(t, 0.002) * (0.75 + 0.25 * np.sin(2 * np.pi * 32 * t))
        tr.add(tone, 0.2 + i * 0.05, -0.4 + i * 0.27, 0.4)
    tr.add(glass(hz("D7"), 1.0, 0.35), 0.38, 0.4, 0.25)
    return finish(tr, wet=0.2)


def fire():
    tr = Track(1.9)
    tr.add(pad((hz("D3"), hz("A3")), 1.3, swell=0.35, release=0.6, brightness=6), 0.0, 0.0, 0.7)
    whoosh = airy(0.7, 250, 900, 700, 2600)
    whoosh *= np.sin(np.pi * np.linspace(0, 1, len(whoosh))) ** 1.5
    tr.add(whoosh, 0.05, 0.0, 0.55)
    for _ in range(18):
        tr.add(click(rng.uniform(1800, 4200), 0.006), rng.uniform(0.15, 1.2),
               rng.uniform(-0.7, 0.7), rng.uniform(0.15, 0.5))
    tr.add(kalimba(hz("A5"), 1.0, 0.3), 0.45, 0.2, 0.35)
    return finish(tr, wet=0.18)


def leaf():
    tr = Track(1.6)
    rustle = airy(0.55, 2200, 2000, 7000, 6000, voices=320)
    rustle *= flutter(len(rustle), 28, 0.85) * np.sin(np.pi * np.linspace(0, 1, len(rustle)))
    tr.add(rustle, 0.0, -0.3, 0.6)
    tr.add(wood(hz("A5")), 0.28, 0.1, 0.7)
    tr.add(wood(hz("D6")), 0.42, 0.35, 0.6)
    return finish(tr, wet=0.2)


def air():
    tr = Track(1.9)
    breeze = airy(1.1, 400, 1200, 1500, 4200)
    breeze *= np.sin(np.pi * np.linspace(0, 1, len(breeze))) ** 2
    tr.add(breeze, 0.0, -0.4, 0.55)
    tr.add(flute(hz("A5"), hz("E6"), 1.2, glide=0.35), 0.25, 0.25, 0.4)
    return finish(tr, wet=0.3, room=1.9)


def sparkle():
    tr = Track(1.8)
    notes = ("B7", "A7", "F#7", "E7", "D7", "B6", "A6", "F#6")
    for i, note in enumerate(notes):
        tr.add(glass(hz(note), 0.9, 0.25), i * 0.055 + rng.uniform(0, 0.01),
               np.sin(i * 1.7) * 0.7, 0.45 - i * 0.02)
    tr.add(glass(hz("D7"), 1.2, 0.5), 0.5, 0.0, 0.25)
    return finish(tr, wet=0.3, room=1.8)


def shadow():
    tr = Track(2.0)
    tr.add(pad((hz("D3"), hz("F3")), 1.6, swell=0.5, release=0.7, brightness=2), 0.0, 0.0, 0.8)
    tr.add(flute(hz("A4"), hz("F4"), 1.2, glide=0.6, vibrato=0.012), 0.3, 0.3, 0.45)
    return finish(tr, wet=0.35, room=2.0)


def earth():
    tr = Track(1.5)
    tr.add(thump(), 0.0, 0.0, 1.0)
    for _ in range(14):
        tr.add(click(rng.uniform(500, 1400), 0.02), rng.uniform(0.05, 0.4),
               rng.uniform(-0.6, 0.6), rng.uniform(0.1, 0.3))
    tr.add(wood(hz("D4"), 1.0, 0.3), 0.22, -0.15, 0.6)
    tr.add(wood(hz("A4"), 0.8, 0.25), 0.36, 0.2, 0.4)
    return finish(tr, wet=0.18)


def rare():
    tr = Track(2.6)
    tr.add(pad((hz("D4"), hz("A4"), hz("F#5")), 2.1, swell=0.6, release=0.9, brightness=3),
           0.0, 0.0, 0.5)
    for i, note in enumerate(("D5", "F#5", "A5", "D6", "F#6", "A6")):
        tr.add(glass(hz(note), 1.4, 0.55), 0.1 + i * 0.11, -0.6 + i * 0.24, 0.5)
    for i in range(8):
        tr.add(glass(hz(("D7", "E7", "F#7", "A7")[i % 4]), 0.6, 0.15), 0.8 + i * 0.07,
               rng.uniform(-0.8, 0.8), 0.15)
    return finish(tr, wet=0.32, room=2.2)


def search():
    tr = Track(0.8)
    swish = airy(0.45, 900, 2400, 3000, 7000, voices=220)
    swish *= np.sin(np.pi * np.linspace(0, 1, len(swish))) ** 2
    tr.add(swish, 0.0, -0.3, 0.6)
    return finish(tr, wet=0.15, room=0.9, level=-21.0)


def rest():
    tr = Track(1.3)
    tr.add(kalimba(hz("A5"), 1.0, 0.3), 0.0, 0.15, 0.7)
    tr.add(kalimba(hz("D5"), 1.0, 0.35), 0.16, -0.15, 0.7)
    return finish(tr, wet=0.2)


def error():
    tr = Track(1.0)
    tr.add(wood(hz("F#4"), 0.8, 0.22), 0.0, 0.0, 1.0)
    return finish(tr, wet=0.18)


SOUNDS = (chime, water, electric, fire, leaf, air, sparkle, shadow, earth, rare, search, rest,
          error)

if __name__ == "__main__":
    wanted = set(sys.argv[1:])
    for make in SOUNDS:
        if not wanted or make.__name__ in wanted:
            save(make.__name__, make())
