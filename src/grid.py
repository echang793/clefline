"""Tempo, beats, downbeats — the timing backbone every other stage snaps to.

`beat_this` is a transformer beat/downbeat tracker and is markedly better at
downbeats than onset-autocorrelation methods; librosa is the fallback when its
checkpoint cannot be fetched. Downbeat spacing is what gives us the meter, so a
bad downbeat track shows up as a wrong time signature, not as wrong notes.

The Grid is the one place seconds become musical position. Everything downstream
speaks in (measure, offset-in-quarter-notes).
"""

import bisect
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Meters we are willing to infer. Anything else falls back to 4/4, which is the
# right guess often enough that a wrong exotic meter is the worse error.
SUPPORTED_BEATS_PER_BAR = (2, 3, 4, 5, 6)


@dataclass
class Grid:
    beats: list[float]           # beat times, seconds, extended back to cover t=0
    downbeats: list[float]       # subset of `beats` that start a measure
    tempo: float                 # BPM, median of the beat intervals
    beats_per_bar: int
    source: str = "beat_this"    # which tracker produced this
    _origin: int = field(default=0, repr=False)  # beat index of the first downbeat

    @property
    def time_signature(self) -> str:
        # Beat unit is a quarter for everything we infer; 6 reads as 6/8 in
        # practice but notating it 6/4 would be wrong, so treat it as compound.
        return "6/8" if self.beats_per_bar == 6 else f"{self.beats_per_bar}/4"

    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.tempo if self.tempo else 0.5

    def beat_position(self, t: float) -> float:
        """Seconds -> continuous beat number, interpolating between beat times.

        Extrapolates at both ends at the local tempo so notes that fall outside
        the tracked region still land somewhere sensible.
        """
        beats = self.beats
        if not beats:
            return t / self.beat_seconds
        if t <= beats[0]:
            span = (beats[1] - beats[0]) if len(beats) > 1 else self.beat_seconds
            return (t - beats[0]) / span
        if t >= beats[-1]:
            span = (beats[-1] - beats[-2]) if len(beats) > 1 else self.beat_seconds
            return (len(beats) - 1) + (t - beats[-1]) / span
        i = bisect.bisect_right(beats, t) - 1
        span = beats[i + 1] - beats[i]
        return i + ((t - beats[i]) / span if span > 0 else 0.0)

    def quarters(self, t: float) -> float:
        """Seconds -> quarter notes since the downbeat of measure 1."""
        return self.beat_position(t) - self._origin

    def measure_of(self, quarters: float) -> tuple[int, float]:
        """Quarter position -> (1-based measure number, offset within the measure)."""
        bar = int(np.floor(quarters / self.beats_per_bar))
        return bar + 1, quarters - bar * self.beats_per_bar

    def snap(self, quarters: float, subdivision: int = 4) -> float:
        """Round to the nearest grid step.

        `subdivision` is steps per quarter note: 4 = sixteenths, 2 = eighths,
        3 = eighth-note triplets.
        """
        step = 1.0 / subdivision
        return round(quarters / step) * step

    def to_dict(self) -> dict:
        return {
            "beats": self.beats, "downbeats": self.downbeats, "tempo": self.tempo,
            "beats_per_bar": self.beats_per_bar, "source": self.source, "origin": self._origin,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Grid":
        return cls(
            beats=payload["beats"], downbeats=payload["downbeats"], tempo=payload["tempo"],
            beats_per_bar=payload["beats_per_bar"], source=payload.get("source", "?"),
            _origin=payload.get("origin", 0),
        )


def _infer_beats_per_bar(beats: list[float], downbeats: list[float]) -> int:
    """Most common number of beats between consecutive downbeats."""
    if len(downbeats) < 3:
        return 4
    counts: list[int] = []
    for a, b in zip(downbeats, downbeats[1:], strict=False):
        n = sum(1 for t in beats if a - 1e-6 <= t < b - 1e-6)
        if n in SUPPORTED_BEATS_PER_BAR:
            counts.append(n)
    if not counts:
        return 4
    values, frequencies = np.unique(counts, return_counts=True)
    return int(values[int(np.argmax(frequencies))])


def _extend_to_start(beats: list[float], downbeats: list[float]) -> tuple[list[float], int]:
    """Pad beats backwards to t=0 so a pickup has somewhere to live.

    Returns the padded beat list and the index of the first downbeat within it,
    which becomes the origin of measure 1.
    """
    if not beats:
        return beats, 0
    interval = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    pad: list[float] = []
    t = beats[0] - interval
    while t > 0:
        pad.append(t)
        t -= interval
    pad.reverse()
    padded = pad + beats
    first = downbeats[0] if downbeats else beats[0]
    origin = min(range(len(padded)), key=lambda i: abs(padded[i] - first))
    return padded, origin


def _track_beat_this(audio: Path) -> tuple[list[float], list[float]] | None:
    try:
        import torch
        from beat_this.inference import File2Beats

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        beats, downbeats = File2Beats(device=device)(str(audio))
        return [float(b) for b in beats], [float(d) for d in downbeats]
    except Exception:
        return None


def _track_librosa(audio: Path) -> tuple[list[float], list[float]]:
    import librosa

    y, sr = librosa.load(str(audio), sr=22050, mono=True)
    _, frames = librosa.beat.beat_track(y=y, sr=sr, trim=False)
    beats = [float(t) for t in librosa.frames_to_time(frames, sr=sr)]
    # librosa gives no downbeats. Assume 4/4 from the first beat, which is a
    # coin flip on phase but keeps bar lines evenly spaced.
    return beats, beats[::4]


def analyze(audio: Path) -> Grid:
    tracked = _track_beat_this(audio)
    source = "beat_this"
    if tracked is None or len(tracked[0]) < 4:
        tracked = _track_librosa(audio)
        source = "librosa"

    beats, downbeats = tracked
    if len(beats) < 2:
        raise RuntimeError("No beats detected — is the audio silent?")

    beats_per_bar = _infer_beats_per_bar(beats, downbeats)
    tempo = 60.0 / float(np.median(np.diff(beats)))
    padded, origin = _extend_to_start(beats, downbeats)

    # Measure 1 must begin at or before the earliest beat, or a pickup ends up at
    # a negative position and cannot be engraved. Stepping back whole bars keeps
    # the downbeat phase intact: the anacrusis simply occupies the bars before it.
    if origin > 0:
        origin -= math.ceil(origin / beats_per_bar) * beats_per_bar

    return Grid(
        beats=padded, downbeats=downbeats, tempo=round(tempo, 2),
        beats_per_bar=beats_per_bar, source=source, _origin=origin,
    )
