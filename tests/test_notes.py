"""The melody reducer has to turn a stack of candidates into one singable line."""

import pytest

from notes import NoteEvent, _merge_repeats, monophonic


def test_a_clean_line_survives_untouched():
    played = [NoteEvent(i * 0.5, i * 0.5 + 0.45, 60 + i, 0.9) for i in range(6)]
    out = monophonic(played)
    assert [n.midi for n in out] == [60, 61, 62, 63, 64, 65]


def test_a_held_harmony_note_loses_to_the_moving_line():
    """A quiet pad under a loud melody must not become the melody."""
    melody = [NoteEvent(i * 0.5, i * 0.5 + 0.45, 72 + (i % 3), 0.95) for i in range(8)]
    pad = [NoteEvent(0.0, 4.0, 55, 0.2)]
    out = monophonic(melody + pad)
    assert 55 not in [n.midi for n in out]


def test_output_is_never_polyphonic():
    played = [
        NoteEvent(0.0, 1.0, 60, 0.9),
        NoteEvent(0.1, 1.0, 64, 0.85),
        NoteEvent(0.2, 1.0, 67, 0.8),
    ]
    out = monophonic(played)
    assert all(a.end <= b.start + 1e-9 for a, b in zip(out, out[1:], strict=False))


def test_fragments_shorter_than_the_floor_are_dropped():
    played = [NoteEvent(0.0, 0.01, 60, 0.9)]
    assert monophonic(played, min_duration=0.08) == []


def test_no_notes_in_no_notes_out():
    assert monophonic([]) == []


def test_same_pitch_split_by_a_tiny_gap_is_rejoined():
    line = [NoteEvent(0.0, 0.5, 60, 0.8), NoteEvent(0.52, 1.0, 60, 0.9)]
    merged = _merge_repeats(line, gap=0.05)
    assert len(merged) == 1
    assert merged[0].end == pytest.approx(1.0)


def test_a_real_gap_keeps_two_notes():
    line = [NoteEvent(0.0, 0.5, 60, 0.8), NoteEvent(0.9, 1.4, 60, 0.9)]
    assert len(_merge_repeats(line, gap=0.05)) == 2


def test_note_event_round_trips_through_dict():
    event = NoteEvent(0.5, 1.25, 64, 0.7)
    assert NoteEvent(**event.as_dict()) == event
