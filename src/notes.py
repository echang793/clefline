"""Stem -> note events, and polyphonic mush -> a single singable line.

basic-pitch returns everything it hears, including harmonies, breath artifacts
and octave ghosts. A melody is one note at a time, so the second half of this
module picks a path through those candidates with Viterbi: the emission score is
how confident basic-pitch was, and the transition cost punishes leaping around,
which is what separates a melody from a stack of harmony notes.

Pitch only. No words are extracted from the vocal stem here or anywhere else.
"""

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

FRAME = 0.01           # seconds per Viterbi frame
REST = -1              # the "nothing sounding" state
SWITCH_COST = 3.2      # flat penalty for changing note, in log-score units
LEAP_COST = 0.16       # extra penalty per semitone of leap
REST_EMISSION = 0.22   # how loud a note must be to beat silence


@dataclass
class NoteEvent:
    start: float
    end: float
    midi: int
    amplitude: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_dict(self) -> dict:
        return asdict(self)


def transcribe(
    audio: Path, *, onset_threshold: float = 0.5, frame_threshold: float = 0.3,
    minimum_note_length: float = 90.0, minimum_frequency: float | None = None,
    maximum_frequency: float | None = None,
) -> list[NoteEvent]:
    """Run basic-pitch over one stem. Returns every note it heard, unfiltered."""
    import contextlib
    import io

    from basic_pitch.inference import predict

    # basic-pitch prints a tensor summary per audio chunk; a four-minute song
    # buries everything else in the log.
    with contextlib.redirect_stdout(io.StringIO()):
        _, _, events = predict(
            str(audio),
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            minimum_note_length=minimum_note_length,
            minimum_frequency=minimum_frequency,
            maximum_frequency=maximum_frequency,
        )
    notes = [
        NoteEvent(float(start), float(end), int(pitch), float(amplitude))
        for start, end, pitch, amplitude, *_ in events
        if end > start
    ]
    notes.sort(key=lambda n: (n.start, n.midi))
    return notes


def monophonic(notes: list[NoteEvent], *, min_duration: float = 0.12) -> list[NoteEvent]:
    """Reduce overlapping candidates to one line via Viterbi over time frames.

    States are the candidate notes active in a frame, plus a rest. Staying put is
    free; switching costs a constant plus a per-semitone leap penalty, so the
    path prefers long stepwise lines over flitting between harmony notes.
    """
    notes = [n for n in notes if n.duration > 0]
    if not notes:
        return []

    span_start = min(n.start for n in notes)
    span_end = max(n.end for n in notes)
    frame_count = max(1, int(np.ceil((span_end - span_start) / FRAME)))

    # Which candidates sound in each frame.
    active: list[list[int]] = [[] for _ in range(frame_count)]
    for index, note in enumerate(notes):
        first = int((note.start - span_start) / FRAME)
        last = min(frame_count - 1, int((note.end - span_start) / FRAME))
        for f in range(max(0, first), last + 1):
            active[f].append(index)

    # Viterbi. Scores are "higher is better"; costs are subtracted.
    best: dict[int, float] = {REST: 0.0}
    back: list[dict[int, int]] = []

    for frame in range(frame_count):
        states = [*active[frame], REST]
        scored: dict[int, float] = {}
        pointer: dict[int, int] = {}
        for state in states:
            emission = REST_EMISSION if state == REST else notes[state].amplitude
            top_score, top_prev = -1e18, REST
            for previous, previous_score in best.items():
                cost = 0.0
                if previous != state:
                    cost = SWITCH_COST
                    if previous != REST and state != REST:
                        cost += LEAP_COST * abs(notes[state].midi - notes[previous].midi)
                    # Entering or leaving silence is cheaper than swapping notes.
                    else:
                        cost = SWITCH_COST * 0.5
                candidate = previous_score + emission - cost
                if candidate > top_score:
                    top_score, top_prev = candidate, previous
            scored[state] = top_score
            pointer[state] = top_prev
        back.append(pointer)
        best = scored

    state = max(best, key=lambda s: best[s])
    path = [state]
    for pointer in reversed(back[1:]):
        state = pointer[state]
        path.append(state)
    path.reverse()

    # Runs of the same state become notes, clipped to the candidate's own extent.
    line: list[NoteEvent] = []
    index = 0
    while index < len(path):
        state = path[index]
        run_end = index
        while run_end + 1 < len(path) and path[run_end + 1] == state:
            run_end += 1
        if state != REST:
            note = notes[state]
            start = max(note.start, span_start + index * FRAME)
            end = min(note.end, span_start + (run_end + 1) * FRAME)
            if end - start >= min_duration:
                line.append(NoteEvent(start, end, note.midi, note.amplitude))
        index = run_end + 1

    return _merge_repeats(line)


def _merge_repeats(line: list[NoteEvent], gap: float = 0.12) -> list[NoteEvent]:
    """Glue same-pitch notes split by a tiny gap back into one sustained note."""
    merged: list[NoteEvent] = []
    for note in line:
        if merged and merged[-1].midi == note.midi and note.start - merged[-1].end <= gap:
            merged[-1] = NoteEvent(
                merged[-1].start, max(merged[-1].end, note.end), note.midi,
                max(merged[-1].amplitude, note.amplitude),
            )
        else:
            merged.append(note)
    return merged


# A sung line lives inside the human vocal range. Demucs leaves kick and bass
# bleed in the vocal stem, and basic-pitch happily reports it as notes an octave
# below any singer, so the transcriber is told not to look there at all.
VOCAL_MIN_HZ = 80.0      # ~E2, below any sung note
VOCAL_MAX_HZ = 1200.0    # ~D6, above any sung note
VOCAL_MIN_MIDI = 40
VOCAL_MAX_MIDI = 84
VOCAL_MIN_NOTE_MS = 120.0


def melody(audio: Path, **kwargs) -> list[NoteEvent]:
    """Transcribe a vocal stem and reduce it to one singable line.

    The defaults are tuned for singing rather than for general transcription:
    a narrower frequency window and a longer minimum note kill the breath
    artefacts and instrument bleed that otherwise triple the note count and
    bury the tune in unreadable sixteenths.
    """
    settings = {
        "minimum_frequency": VOCAL_MIN_HZ,
        "maximum_frequency": VOCAL_MAX_HZ,
        "minimum_note_length": VOCAL_MIN_NOTE_MS,
        **kwargs,
    }
    line = monophonic(transcribe(audio, **settings))
    return [note for note in line if VOCAL_MIN_MIDI <= note.midi <= VOCAL_MAX_MIDI]
