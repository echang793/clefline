"""Synthetic audio with known ground truth, so the suite needs no network.

A plucked-string-ish tone: strong attack so onset and beat trackers have
something to latch onto, a few harmonics so pitch detection is not fed a bare
sine, and an exponential decay so notes do not smear into each other.
"""

import numpy as np

SR = 22050


def tone(midi: int, seconds: float, sr: int = SR, amplitude: float = 0.5) -> np.ndarray:
    frequency = 440.0 * 2 ** ((midi - 69) / 12)
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    wave = sum(
        amplitude * weight * np.sin(2 * np.pi * frequency * partial * t)
        for partial, weight in ((1, 1.0), (2, 0.45), (3, 0.2), (4, 0.1))
    )
    envelope = np.exp(-3.0 * t)
    envelope[: int(sr * 0.004)] *= np.linspace(0, 1, int(sr * 0.004))
    return wave * envelope


def melody(midis: list[int], bpm: float = 120.0, beats: float = 1.0,
           sr: int = SR) -> np.ndarray:
    """Even notes at a fixed tempo. Returns mono float32 in [-1, 1]."""
    step = 60.0 / bpm * beats
    length = int(sr * step * len(midis)) + sr
    out = np.zeros(length, dtype=np.float64)
    for index, midi in enumerate(midis):
        start = int(index * step * sr)
        note = tone(midi, step * 1.6, sr)
        end = min(length, start + len(note))
        out[start:end] += note[: end - start]
    peak = np.max(np.abs(out)) or 1.0
    return (out / peak * 0.9).astype(np.float32)


def write(path, samples, sr: int = SR) -> None:
    import soundfile as sf

    sf.write(str(path), samples, sr)
