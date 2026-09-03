"""Detected notes -> notation-ready rhythm.

Raw onsets are never on the grid: a singer lands a few tens of milliseconds
early or late and holds the note as long as the phrase wants. Engraving that
literally produces a page of 32nds and rests that nobody can sight-read, so this
stage snaps everything to a musical grid and throws away detail below it.

Swing is detected rather than notated as triplets: if the off-beat eighths sit
near a 2:1 ratio the part is marked "Swing" and the eighths stay straight, which
is how a real chart is written.
"""

from dataclasses import asdict, dataclass

import numpy as np

from grid import Grid
from notes import NoteEvent

SWING_RATIO = 2 / 3          # where an off-beat eighth sits in a swung feel
SWING_TOLERANCE = 0.055
MIN_SWING_ONSETS = 12


@dataclass
class QuantizedNote:
    """A note placed on the grid. `offset` is quarter notes from measure 1, beat 1."""

    offset: float
    duration: float
    midi: int

    @property
    def end(self) -> float:
        return self.offset + self.duration

    def as_dict(self) -> dict:
        return asdict(self)


def detect_swing(notes: list[NoteEvent], g: Grid) -> bool:
    """True when off-beat onsets cluster near 2:1 instead of even eighths."""
    fractions = []
    for note in notes:
        fraction = g.beat_position(note.start) % 1.0
        if 0.25 < fraction < 0.85:
            fractions.append(fraction)
    if len(fractions) < MIN_SWING_ONSETS:
        return False

    # A median near 2:1 is not enough on its own: a busy straight-sixteenth line
    # scatters onsets across the whole beat and averages out to roughly the same
    # place. Real swing puts most off-beats in a tight cluster, so require that
    # the bulk of them actually sit there.
    near_swing = sum(1 for f in fractions if abs(f - SWING_RATIO) < SWING_TOLERANCE)
    if near_swing / len(fractions) < 0.5:
        return False
    return abs(float(np.median(fractions)) - SWING_RATIO) < SWING_TOLERANCE


def quantize(
    notes: list[NoteEvent], g: Grid, *, subdivision: int = 4,
    min_steps: int = 1, monophonic: bool = True,
) -> list[QuantizedNote]:
    """Snap notes to a `subdivision`-per-quarter grid and clean up the wreckage.

    `min_steps` is the shortest note kept, in grid steps. Notes that quantize to
    nothing are dropped rather than rounded up, so a stray blip does not become a
    sixteenth note the player has to read.
    """
    step = 1.0 / subdivision
    minimum = min_steps * step

    placed: list[QuantizedNote] = []
    for note in notes:
        start = g.snap(g.quarters(note.start), subdivision)
        end = g.snap(g.quarters(note.end), subdivision)
        duration = end - start
        if duration < minimum:
            # Keep the onset — it was heard — but give it the shortest legal value.
            duration = minimum if note.duration >= (minimum * g.beat_seconds * 0.5) else 0.0
        if duration <= 0 or start < 0:
            # Anything landing before measure 1 cannot be engraved. The grid
            # normally leaves room for a pickup; this is the backstop.
            continue
        placed.append(QuantizedNote(round(start, 6), round(duration, 6), note.midi))

    placed.sort(key=lambda n: (n.offset, n.midi))
    placed = _dedupe(placed)
    if monophonic:
        placed = _resolve_overlaps(placed, minimum)
    return _join_sustains(placed)


def _join_sustains(notes: list[QuantizedNote], limit: float = 8.0) -> list[QuantizedNote]:
    """Rejoin a held note that the transcriber chopped into consecutive pieces.

    basic-pitch breaks sustained and vibrato-heavy singing into several short
    notes at the same pitch. Once quantized those pieces sit end to end with no
    gap, which prints as a stream of repeated sixteenths where the singer held
    one note. A genuine repeated note is re-articulated and leaves a gap, so
    only exactly-touching pieces are joined, and never past `limit` quarters.
    """
    joined: list[QuantizedNote] = []
    for note in notes:
        previous = joined[-1] if joined else None
        if (previous and previous.midi == note.midi
                and abs(previous.end - note.offset) < 1e-6
                and previous.duration + note.duration <= limit):
            joined[-1] = QuantizedNote(
                previous.offset, round(previous.duration + note.duration, 6), note.midi
            )
        else:
            joined.append(note)
    return joined


def _dedupe(notes: list[QuantizedNote]) -> list[QuantizedNote]:
    """Two candidates that snapped onto the same slot and pitch are one note."""
    out: list[QuantizedNote] = []
    for note in notes:
        if out and out[-1].offset == note.offset and out[-1].midi == note.midi:
            out[-1] = QuantizedNote(note.offset, max(out[-1].duration, note.duration), note.midi)
        else:
            out.append(note)
    return out


def _resolve_overlaps(notes: list[QuantizedNote], minimum: float) -> list[QuantizedNote]:
    """Enforce one note at a time: truncate whatever is still sounding.

    A note squeezed below the minimum by the next onset is dropped, not shrunk —
    two notes fighting for one slot means one of them was wrong.
    """
    out: list[QuantizedNote] = []
    for note in notes:
        if out:
            previous = out[-1]
            if note.offset < previous.end:
                trimmed = note.offset - previous.offset
                if trimmed < minimum:
                    # The earlier note has no room left; the later onset wins.
                    out.pop()
                else:
                    out[-1] = QuantizedNote(previous.offset, round(trimmed, 6), previous.midi)
        if not out or note.offset >= out[-1].end:
            out.append(note)
    return out
