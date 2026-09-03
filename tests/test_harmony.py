"""Key and chord estimation, driven by chroma built by hand so it is unambiguous."""

import numpy as np
import pytest

from harmony import QUALITIES, _chroma, _diatonic_fraction, _templates, estimate_key


def chroma_for(pitch_classes, rows: int = 32) -> np.ndarray:
    vector = np.zeros(12)
    for pitch_class in pitch_classes:
        vector[pitch_class % 12] = 1.0
    vector /= np.linalg.norm(vector)
    return np.tile(vector, (rows, 1))


def test_c_major_material_reads_as_c_major():
    estimate = estimate_key(chroma_for([0, 2, 4, 5, 7, 9, 11]))
    assert estimate.tonic == "C"
    assert estimate.mode == "major"
    assert estimate.sharps == 0


def test_g_major_material_reads_as_one_sharp():
    estimate = estimate_key(chroma_for([7, 9, 11, 0, 2, 4, 6]))
    assert estimate.sharps == 1


def test_a_minor_material_reads_as_minor():
    weighted = chroma_for([9, 11, 0, 2, 4, 5, 7])
    weighted[:, 9] *= 2.2          # lean hard on the tonic
    assert estimate_key(weighted).mode == "minor"


def test_every_template_is_unit_length_and_labelled():
    templates, labels = _templates()
    assert templates.shape == (12 * len(QUALITIES), 12)
    assert len(labels) == templates.shape[0]
    assert {label[1] for label in labels} == {q[0] for q in QUALITIES}


def test_a_major_triad_matches_its_own_template_best():
    templates, labels = _templates()
    scores = chroma_for([0, 4, 7])[0] @ templates.T
    root, suffix = labels[int(scores.argmax())]
    assert (root, suffix) == (0, "")


def test_a_minor_seventh_is_not_mistaken_for_a_major_triad():
    templates, labels = _templates()
    scores = chroma_for([0, 3, 7, 10])[0] @ templates.T
    root, suffix = labels[int(scores.argmax())]
    assert root == 0 and suffix in ("m7", "m")


@pytest.mark.parametrize("pitch_classes,expected_suffix", [
    ([0, 5, 7, 10], "7sus4"),
    ([0, 3, 6, 9], "dim7"),
    ([0, 4, 8], "aug"),
])
def test_extended_qualities_match_their_own_template_best(pitch_classes, expected_suffix):
    """These three passed; dominant 9 and half-diminished (m7b5) were tried the
    same way and did not -- see the excluded-on-purpose block in QUALITIES."""
    templates, labels = _templates()
    scores = chroma_for(pitch_classes)[0] @ templates.T
    root, suffix = labels[int(scores.argmax())]
    assert (root, suffix) == (0, expected_suffix)


@pytest.mark.parametrize("suffix", ["6", "m6", "9", "m7b5", "add9"])
def test_ambiguous_qualities_are_not_offered(suffix):
    """C6 (C E G A) and Am7 (A C E G) are the identical four pitch classes --
    chroma has no bass note to break the tie. Dominant 9 and m7b5 looked safer
    but measurably were not: both lost to a simpler chord on their own
    noiseless synthetic chroma (test_extended_qualities_match_their_own_template_best
    once included them, and failed)."""
    assert suffix not in {q[0] for q in QUALITIES}


@pytest.mark.parametrize("root,suffix,mode,in_key", [
    (7, "7sus4", "major", True),    # V7sus4 is exactly as diatonic as V7
    (11, "dim7", "major", True),    # vii-fully-diminished, still the vii chord
    (0, "aug", "major", False),     # augmented is never diatonic
])
def test_extended_qualities_fold_to_their_diatonic_family(root, suffix, mode, in_key):
    fraction = _diatonic_fraction(0, mode, [(root, suffix)])
    assert (fraction > 0) == in_key


# --------------------------------------------------------------------- real audio
#
# Everything above tests the algorithm in isolation. This checks the whole
# pipeline (grid + chroma + chord evidence + key estimate) against real
# recordings whose key is independently documented, not just internally
# consistent. Network + slow, so it is skipped by default; run explicitly with
# `pytest -m network` to re-verify after touching grid.py or harmony.py.
#
# Ground truth was cross-checked against more than one source, since automated
# aggregator sites (Tunebat, SongBPM, GetSongKey) source their key data from
# Spotify's own audio-analysis API — the same class of imperfect tool this is
# validating, not independent ground truth. "Perfect" is a case in point: it is
# widely listed as G major (the transposed easy-piano edition's key, not the
# recording), while SingingCarrots' hand-verified vocal-range data and a music
# press search both confirm the actual studio master is in Ab major.
REAL_SONGS = [
    ("QDYfEBY9NM4", "Let It Be", "The Beatles", "C major"),
    ("hLQl3WQQoQ0", "Someone Like You", "Adele", "A major"),
]


@pytest.mark.network
@pytest.mark.parametrize("video_id,title,artist,truth", REAL_SONGS)
def test_key_detection_matches_documented_real_songs(video_id, title, artist, truth):
    import fetch
    import grid

    audio = fetch.download(video_id)
    g = grid.analyze(audio)
    detected = estimate_key(_chroma(audio, g.beats))
    assert detected.name == truth, f"{artist} - {title}: expected {truth}, got {detected.name}"
