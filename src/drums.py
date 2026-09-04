"""Drum stem -> labelled hits.

There is no pip-installable drum transcriber that works on this stack, so this
is a hand-built classifier: detect onsets on the separated drum stem, then label
each one from where its energy sits in the spectrum and how fast it decays.

That is honest about its limits. Kick, snare and hi-hat — the great majority of
what a drummer reads — come out reliably, because they occupy well-separated
bands. Toms and the ride/crash distinction are much shakier: they overlap in
both frequency and decay, and the stem is already an approximation.

`Classifier` is a protocol so a trained model can replace the heuristic without
touching the rest of the pipeline.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

# Staff position and notehead for each voice, in standard drum-set notation.
VOICES = {
    "kick":       {"display": "F4", "notehead": "normal", "stem": "down"},
    "snare":      {"display": "C5", "notehead": "normal", "stem": "up"},
    "tom":        {"display": "E5", "notehead": "normal", "stem": "up"},
    "hihat":      {"display": "G5", "notehead": "x", "stem": "up"},
    "hihat_open": {"display": "G5", "notehead": "circle-x", "stem": "up"},
    "cymbal":     {"display": "A5", "notehead": "x", "stem": "up"},
}

# Frequency bands the voices separate into, in Hz.
BANDS = {
    "low": (20, 120),        # kick fundamental
    "lowmid": (120, 260),    # snare and tom bodies
    "mid": (260, 2000),      # snare crack
    "high": (2000, 6000),    # cymbal wash
    "vhigh": (6000, 11000),  # hi-hat sizzle
}

WINDOW = 0.07        # seconds of audio examined after each onset
SIMULTANEOUS = 0.035  # hits closer than this are one stroke, played together


@dataclass
class Hit:
    time: float
    voice: str
    strength: float

    def as_dict(self) -> dict:
        return asdict(self)


class Classifier(Protocol):
    def __call__(self, features: dict) -> str: ...


def _band_energies(spectrum: np.ndarray, freqs: np.ndarray) -> dict[str, float]:
    total = float(spectrum.sum()) or 1.0
    return {
        name: float(spectrum[(freqs >= low) & (freqs < high)].sum()) / total
        for name, (low, high) in BANDS.items()
    }


def features_at(y: np.ndarray, sr: int, onset: float) -> dict:
    """Spectral shape and decay of a single stroke."""
    start = int(onset * sr)
    window = y[start : start + int(WINDOW * sr)]
    if len(window) < 64:
        return {}

    spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
    freqs = np.fft.rfftfreq(len(window), 1 / sr)
    energies = _band_energies(spectrum, freqs)

    magnitude = float(spectrum.sum()) or 1.0
    centroid = float((freqs * spectrum).sum() / magnitude)

    # Share of total energy sitting in the loudest bin and its immediate
    # neighbours -- how much of the spectrum one narrow peak explains. A
    # membrane drum (kick, tom) rings at a fundamental and a few harmonics, so
    # a handful of bins carry almost everything; a snare crack or a cymbal is
    # noise-like, energy smeared across the whole band. Measured on synthetic
    # strokes: toms 0.87-0.98, a snare 0.06, a ride 0.07 -- a wide enough gap
    # that the exact threshold barely matters.
    peak = int(spectrum.argmax())
    peakiness = float(spectrum[max(0, peak - 3) : peak + 4].sum() / magnitude)

    # Decay: energy still present a beat-ish later relative to the attack.
    tail = y[start + int(WINDOW * sr) : start + int(0.28 * sr)]
    attack_rms = float(np.sqrt(np.mean(window**2))) or 1e-9
    tail_rms = float(np.sqrt(np.mean(tail**2))) if len(tail) else 0.0

    return {**energies, "centroid": centroid, "peakiness": peakiness,
            "decay": tail_rms / attack_rms, "strength": attack_rms}


def classify(features: dict) -> str:
    """Rule-based labelling. Ordered most-separable first."""
    if not features:
        return "hihat"

    low = features["low"]
    lowmid = features["lowmid"]
    mid = features["mid"]
    high = features["high"]
    vhigh = features["vhigh"]
    decay = features["decay"]
    peakiness = features["peakiness"]
    centroid = features["centroid"]

    # Kick: nearly all the energy is below the range anything else occupies.
    if low > 0.45 and centroid < 350:
        return "kick"

    # Cymbals and hi-hats live at the top of the spectrum. A snare is bright too,
    # so brightness alone is not enough to tell them apart: what separates them is
    # that a snare has real body in the mid band and a hi-hat has almost none.
    # Cymbals then ring where hi-hats stop dead, which is what decay measures.
    if (high + vhigh) > 0.5 and mid < 0.25:
        return "cymbal" if decay > 0.45 else ("hihat_open" if decay > 0.28 else "hihat")

    # Tom: a membrane ringing at a fundamental, not a broadband crack -- checked
    # by peakiness ahead of the snare rule below, or a higher-pitched tom's
    # harmonics landing in the mid band make it indistinguishable from a
    # snare's noisy mid-band crack to that rule alone. Measured gap is wide
    # (0.87-0.98 for a tom's three registers vs 0.06 for a snare), so 0.5 has
    # plenty of margin in both directions.
    if peakiness > 0.5 and centroid < 1500 and (low + lowmid + mid) > 0.5:
        return "tom"

    # Snare: a crack in the mids, with or without much low-mid body left in the stem.
    if mid > 0.22:
        return "snare"

    return "hihat" if vhigh > high else "snare"


def transcribe(audio: Path, classifier: Classifier = classify) -> list[Hit]:
    """Detect and label every stroke in a separated drum stem."""
    import librosa

    y, sr = librosa.load(str(audio), sr=22050, mono=True)
    if not len(y):
        return []

    onsets = librosa.onset.onset_detect(
        y=y, sr=sr, units="time", backtrack=True, hop_length=256,
    )

    hits = [
        Hit(float(onset), classifier(features := features_at(y, sr, float(onset))),
            float(features.get("strength", 0.0)))
        for onset in onsets
    ]
    return [h for h in hits if h.strength > 0]


def group_simultaneous(
    hits: list[Hit], window: float = SIMULTANEOUS
) -> list[tuple[float, list[str]]]:
    """Collapse strokes played together into one event.

    A kick and a hi-hat on the same eighth are one thing the drummer does, and
    must be engraved as a chord rather than as two notes a hair apart.
    """
    grouped: list[tuple[float, list[str]]] = []
    for hit in sorted(hits, key=lambda h: h.time):
        if grouped and hit.time - grouped[-1][0] <= window:
            if hit.voice not in grouped[-1][1]:
                grouped[-1][1].append(hit.voice)
        else:
            grouped.append((hit.time, [hit.voice]))
    return grouped
