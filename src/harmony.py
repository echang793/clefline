"""Key and chord symbols from the audio itself.

Two jobs, both driven by chroma (energy per pitch class):

  * the key, via Krumhansl-Schmuckler correlation against major/minor profiles,
    which decides the key signature of every part including the sax;
  * the chord under each beat, template-matched and then smoothed with Viterbi
    so the chart shows one chord per bar rather than a new one every beat.

Harmonic rhythm is the reason for the smoothing: without a switching penalty,
passing notes in the melody drag the estimate around inside a single chord.
"""

from dataclasses import dataclass

import numpy as np

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "D-", "D", "E-", "E", "F", "G-", "G", "A-", "A", "B-", "B"]

# Krumhansl-Kessler key profiles.
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# Chord templates as pitch-class offsets from the root, with the suffix music21
# expects in a ChordSymbol figure.
QUALITIES: list[tuple[str, tuple[int, ...], float]] = [
    ("", (0, 4, 7), 1.00),          # major
    ("m", (0, 3, 7), 1.00),         # minor
    ("7", (0, 4, 7, 10), 0.98),
    ("m7", (0, 3, 7, 10), 0.98),
    ("maj7", (0, 4, 7, 11), 0.95),
    ("sus4", (0, 5, 7), 0.90),
    ("dim", (0, 3, 6), 0.88),
]

CHANGE_PENALTY = 0.32   # cost of switching chord between beats
MIN_STRENGTH = 0.55     # below this the bar is called N.C. rather than guessed


@dataclass
class KeyEstimate:
    tonic: str
    mode: str
    sharps: int

    @property
    def name(self) -> str:
        return f"{self.tonic} {self.mode}"


def _chroma(audio, beats: list[float]):
    """Beat-synchronous chroma: one 12-vector per beat, L2-normalised."""
    import librosa

    y, sr = librosa.load(str(audio), sr=22050, mono=True)
    # CQT chroma tracks pitch better than STFT chroma on full mixes.
    raw = librosa.feature.chroma_cqt(y=y, sr=sr)
    frames = librosa.time_to_frames(beats, sr=sr)
    frames = np.clip(frames, 0, raw.shape[1] - 1)

    columns = []
    for start, end in zip(frames, list(frames[1:]) + [raw.shape[1]], strict=True):
        segment = raw[:, start:max(start + 1, end)]
        vector = segment.mean(axis=1)
        norm = np.linalg.norm(vector)
        columns.append(vector / norm if norm else vector)
    return np.array(columns)


# Scale degrees and the triad quality built on each, used to ask "do the chords
# we heard actually belong to this key?". The minor row allows both the natural
# minor v and the harmonic-minor V, since pop songs use either.
DIATONIC = {
    "major": {0: ("",), 2: ("m", "m7"), 4: ("m", "m7"), 5: ("", "maj7"),
              7: ("", "7"), 9: ("m", "m7"), 11: ("dim",)},
    "minor": {0: ("m", "m7"), 2: ("dim",), 3: ("", "maj7"), 5: ("m", "m7"),
              7: ("m", "m7", "", "7"), 8: ("", "maj7"), 10: ("", "7")},
}
# Weight on chord agreement relative to the chroma correlation. Chords are the
# stronger signal for mode — a major and its relative minor share a chroma
# profile almost exactly, and only the harmony tells them apart.
CHORD_WEIGHT = 0.8
# Nudge towards the enharmonic spelling with fewer accidentals: C# major and
# D-flat major sound identical, but one costs the reader seven sharps.
ACCIDENTAL_PENALTY = 0.02


def _diatonic_fraction(tonic: int, mode: str, chords: list[tuple[int, str]]) -> float:
    """Share of the detected chords that belong to this key."""
    if not chords:
        return 0.0
    table = DIATONIC[mode]
    hits = 0
    for root, suffix in chords:
        allowed = table.get((root - tonic) % 12)
        if allowed and (suffix in allowed or suffix.rstrip("7") in
                        {a.rstrip("7") for a in allowed}):
            hits += 1
    return hits / len(chords)


def estimate_key(chroma: np.ndarray, chords: list[tuple[int, str]] | None = None) -> KeyEstimate:
    """Pick the key from chroma correlation plus, when available, the chords.

    Krumhansl-Schmuckler alone confuses a key with its relative minor, because
    the two share a pitch-class profile. Feeding in the chords that were
    actually detected resolves the mode, which matters a great deal on the page:
    the wrong mode means the wrong key signature and an accidental on almost
    every note.
    """
    from music21 import key as m21key

    average = chroma.mean(axis=0)
    chords = chords or []

    best, best_score = ("C", "major", 0), -1e9
    for tonic in range(12):
        rotated = np.roll(average, -tonic)
        for profile, mode in ((MAJOR_PROFILE, "major"), (MINOR_PROFILE, "minor")):
            correlation = float(np.corrcoef(rotated, profile)[0, 1])
            agreement = _diatonic_fraction(tonic, mode, chords)
            sharps = m21key.Key(SHARP_NAMES[tonic], mode).sharps
            score = (correlation + CHORD_WEIGHT * agreement
                     - ACCIDENTAL_PENALTY * abs(sharps))
            if score > best_score:
                best_score, best = score, (SHARP_NAMES[tonic], mode, tonic)

    tonic_name, mode, tonic_index = best
    sharps = m21key.Key(tonic_name, mode).sharps

    # Re-spell into the enharmonic that needs fewer accidentals (C# -> D-flat).
    if abs(sharps) > 6:
        flat_name = FLAT_NAMES[tonic_index]
        flat_sharps = m21key.Key(flat_name, mode).sharps
        if abs(flat_sharps) < abs(sharps):
            tonic_name, sharps = flat_name, flat_sharps
    elif sharps < 0:
        tonic_name = FLAT_NAMES[tonic_index]

    return KeyEstimate(tonic_name, mode, sharps)


def _templates() -> tuple[np.ndarray, list[str]]:
    rows, labels = [], []
    for root in range(12):
        for suffix, offsets, weight in QUALITIES:
            vector = np.zeros(12)
            for index, offset in enumerate(offsets):
                # Root and third carry the identity; upper extensions matter less.
                vector[(root + offset) % 12] = 1.0 if index < 2 else 0.85
            vector *= weight / np.linalg.norm(vector)
            rows.append(vector)
            labels.append((root, suffix))
    return np.array(rows), labels


def estimate_chords(chroma: np.ndarray, sharps: int) -> list[int]:
    """Viterbi over beats. Returns one template index per beat, or -1 for N.C."""
    templates, _ = _templates()
    scores = chroma @ templates.T                      # (beats, templates)
    if not len(scores):
        return []

    count = templates.shape[0]
    best = scores[0].copy()
    back = np.zeros((len(scores), count), dtype=int)

    for step in range(1, len(scores)):
        stay = best
        switch = best.max() - CHANGE_PENALTY
        winner = int(best.argmax())
        chosen = np.where(stay >= switch, np.arange(count), winner)
        back[step] = chosen
        best = np.maximum(stay, switch) + scores[step]

    path = [int(best.argmax())]
    for step in range(len(scores) - 1, 0, -1):
        path.append(int(back[step][path[-1]]))
    path.reverse()

    return [
        index if scores[beat][index] >= MIN_STRENGTH else -1
        for beat, index in enumerate(path)
    ]


def chord_symbols(chroma: np.ndarray, g, sharps: int) -> list[dict]:
    """Chroma + beat grid -> [{offset in quarters, symbol}], emitted on changes only."""
    if not len(chroma):
        return []
    _, labels = _templates()
    path = estimate_chords(chroma, sharps)
    names = FLAT_NAMES if sharps < 0 else SHARP_NAMES

    symbols: list[dict] = []
    previous = None
    for beat_index, template_index in enumerate(path):
        if template_index < 0:
            continue
        root, suffix = labels[template_index]
        symbol = f"{names[root]}{suffix}"
        if symbol == previous:
            continue
        previous = symbol
        offset = g.quarters(g.beats[beat_index]) if beat_index < len(g.beats) else None
        if offset is None or offset < 0:
            continue
        symbols.append({"offset": round(offset, 4), "symbol": symbol})
    return symbols


def analyze(audio, g) -> dict:
    """One chroma pass feeds the chord track, and the chords feed the key."""
    chroma = _chroma(audio, g.beats)
    if not len(chroma):
        return {"key": "C major", "tonic": "C", "mode": "major", "sharps": 0, "chords": []}

    _, labels = _templates()
    # Chord identity does not depend on spelling, so these can be found before
    # the key is known and then used as evidence for it.
    path = estimate_chords(chroma, 0)
    detected = [labels[i] for i in path if i >= 0]

    estimate = estimate_key(chroma, detected)
    return {
        "key": estimate.name,
        "tonic": estimate.tonic,
        "mode": estimate.mode,
        "sharps": estimate.sharps,
        "chords": chord_symbols(chroma, g, estimate.sharps),
    }
