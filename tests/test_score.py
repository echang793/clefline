"""The sax part is the only place this app does music theory it must get exactly right."""

import pytest
from music21 import key as m21key

from score import ALTO_SAX_HIGH, ALTO_SAX_LOW, ALTO_SAX_TRANSPOSE, fit_to_range, written_key_sharps


@pytest.mark.parametrize("concert_sharps", range(-7, 8))
def test_written_key_stays_in_notatable_range(concert_sharps):
    """A key signature can never need more than seven accidentals."""
    assert -7 <= written_key_sharps(concert_sharps) <= 7


@pytest.mark.parametrize("tonic", ["C", "G", "D", "A", "E", "B", "F", "B-", "E-", "A-", "D-", "G-"])
def test_written_key_matches_a_major_sixth_transposition(tonic):
    """Written key must equal the concert key transposed up a major sixth."""
    concert = m21key.Key(tonic, "major")
    expected = concert.tonic.transpose("M6")
    written = written_key_sharps(concert.sharps)
    # Compare pitch classes: the spelling may be enharmonic (G# major -> A-flat).
    assert m21key.KeySignature(written).asKey("major").tonic.pitchClass == expected.pitchClass


def test_transposition_interval_is_exactly_nine_semitones():
    assert ALTO_SAX_TRANSPOSE == 9


@pytest.mark.parametrize("midis", [
    [36, 38, 40],           # far below the horn
    [96, 98, 100],          # far above it
    [58, 74, 90],           # already spanning the full range
    [20, 60, 110],          # absurdly wide, needs per-note folding
    [69],
])
def test_range_fold_never_leaves_the_horn(midis):
    fitted = fit_to_range(midis)
    assert len(fitted) == len(midis)
    assert all(ALTO_SAX_LOW <= m <= ALTO_SAX_HIGH for m in fitted)


def test_range_fold_preserves_contour_when_a_single_shift_suffices():
    """A line that fits after one octave shift keeps every interval intact."""
    original = [48, 50, 52, 53, 55]
    fitted = fit_to_range(original)
    steps = [b - a for a, b in zip(original, original[1:], strict=False)]
    assert [b - a for a, b in zip(fitted, fitted[1:], strict=False)] == steps


def test_range_fold_of_empty_line():
    assert fit_to_range([]) == []


@pytest.mark.parametrize("concert_sharps,expected", [
    (4, -5),    # concert C# minor -> A# minor (7 sharps) is spelled B-flat minor
    (5, -4),    # concert B major -> G# major (8) is spelled A-flat major
    (0, 3),     # concert C major -> A major, already easy
    (-3, 0),    # concert E-flat major -> C major
])
def test_written_key_prefers_the_easier_enharmonic(concert_sharps, expected):
    """Seven sharps on a stand is a bug, not a key signature."""
    assert written_key_sharps(concert_sharps) == expected


@pytest.mark.parametrize("concert_sharps", range(-7, 8))
def test_written_key_never_exceeds_five_accidentals_where_avoidable(concert_sharps):
    written = written_key_sharps(concert_sharps)
    alternative = written - 12 if written > 0 else written + 12
    assert abs(written) <= abs(alternative) or abs(alternative) > 7


@pytest.mark.parametrize("sharps,midi,expected", [
    (-5, 61, "D-"),     # flat key spells the black note as D-flat
    (4, 61, "C#"),      # sharp key spells the same note as C-sharp
    (0, 60, "C"),
    (-2, 70, "B-"),
    (3, 66, "F#"),
])
def test_notes_are_spelled_to_fit_the_key(sharps, midi, expected):
    """Notes built from MIDI default to sharps; in a flat key every altered note
    then printed an accidental that contradicted the signature."""
    from score import spell
    assert spell(midi, sharps).name == expected


@pytest.mark.parametrize("raw,expected", [
    ("\U0001f3a7 Song Title \U0001f1e8\U0001f1e6", "Song Title"),
    ("Normal Title", "Normal Title"),
    ("", "Untitled"),
    ("\U0001f600\U0001f600", "Untitled"),
    ("  spaced   out  ", "spaced out"),
])
def test_titles_drop_glyphs_the_engraver_cannot_draw(raw, expected):
    """Emoji have no glyph in the music text font and print as solid boxes."""
    from score import clean_title
    assert clean_title(raw) == expected


@pytest.mark.parametrize("title", [
    "是你",                        # regression: this exact title printed "Untitled"
    "Тест Кириллица",
    "Ünïcödé Café",
    "日本語のタイトル",
    "제목",
])
def test_non_latin_titles_are_kept_intact(title):
    """CJK, Cyrillic, Greek and accented Latin all render correctly — verovio
    emits real <text>, not glyph paths, so the browser or PDF font draws it.
    An earlier version filtered by codepoint range instead of by actual emoji
    ranges and stripped every non-Latin-alphabet title down to nothing."""
    from score import clean_title
    assert clean_title(title) == title


def test_emoji_stripped_from_a_non_latin_title_leaves_the_rest():
    from score import clean_title
    assert clean_title("是你 \U0001f3a7") == "是你"
