"""Quantization is what makes the chart readable; getting it wrong is silent."""

import pytest

from grid import Grid
from notes import NoteEvent
from quantize import detect_swing, quantize


def build(bpm: float = 120.0, count: int = 65) -> Grid:
    step = 60.0 / bpm
    beats = [i * step for i in range(count)]
    return Grid(beats=beats, downbeats=beats[::4], tempo=bpm, beats_per_bar=4)


def test_slightly_late_onsets_snap_back_to_the_beat():
    g = build()             # 0.5 s per beat
    played = [NoteEvent(0.02, 0.48, 60, 0.9), NoteEvent(0.53, 0.99, 62, 0.9)]
    out = quantize(played, g, subdivision=4)
    assert [n.offset for n in out] == [0.0, 1.0]
    assert [n.midi for n in out] == [60, 62]


def test_durations_land_on_the_grid():
    g = build()
    played = [NoteEvent(0.0, 0.51, 60, 0.9)]
    out = quantize(played, g, subdivision=4)
    assert out[0].duration == pytest.approx(1.0)


def test_blips_below_the_grid_are_dropped():
    g = build()
    # 6 ms: far shorter than a sixteenth at 120bpm (125 ms), and not a real note.
    out = quantize([NoteEvent(0.0, 0.006, 60, 0.4)], g, subdivision=4)
    assert out == []


def test_overlapping_notes_become_one_line():
    g = build()
    played = [NoteEvent(0.0, 1.6, 60, 0.9), NoteEvent(0.5, 1.6, 64, 0.8)]
    out = quantize(played, g, subdivision=4, monophonic=True)
    assert all(a.end <= b.offset for a, b in zip(out, out[1:], strict=False))


def test_a_note_with_no_room_left_yields_to_the_next_onset():
    g = build()
    played = [NoteEvent(0.0, 2.0, 60, 0.9), NoteEvent(0.02, 2.0, 67, 0.95)]
    out = quantize(played, g, subdivision=4)
    assert len(out) == 1


def test_same_pitch_landing_on_one_slot_is_a_single_note():
    g = build()
    played = [NoteEvent(0.0, 0.5, 60, 0.5), NoteEvent(0.03, 1.0, 60, 0.9)]
    out = quantize(played, g, subdivision=4)
    assert len(out) == 1


def test_straight_eighths_are_not_called_swing():
    g = build()
    played = [NoteEvent(i * 0.25, i * 0.25 + 0.2, 60, 0.9) for i in range(40)]
    assert detect_swing(played, g) is False


def test_two_to_one_offbeats_are_called_swing():
    g = build()
    played = []
    for beat in range(24):
        base = beat * 0.5
        played.append(NoteEvent(base, base + 0.3, 60, 0.9))
        played.append(NoteEvent(base + 0.5 * 2 / 3, base + 0.5 * 2 / 3 + 0.15, 62, 0.9))
    assert detect_swing(played, g) is True


def test_swing_needs_evidence_before_it_is_claimed():
    g = build()
    played = [NoteEvent(0.5 * 2 / 3, 0.9, 60, 0.9)]
    assert detect_swing(played, g) is False
