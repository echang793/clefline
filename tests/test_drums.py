"""The drum classifier, checked against synthesized strokes with known identities."""

import numpy as np
import pytest

from drums import VOICES, Hit, classify, features_at, group_simultaneous, transcribe

SR = 22050


def kick(seconds: float = 0.3) -> np.ndarray:
    """Low sine with a fast pitch drop — the shape of a bass drum."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    sweep = 110 * np.exp(-28 * t) + 45
    return (np.sin(2 * np.pi * np.cumsum(sweep) / SR) * np.exp(-14 * t)).astype(np.float32)


def snare(seconds: float = 0.25) -> np.ndarray:
    """Mid-band body plus broadband noise."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    rng = np.random.default_rng(0)
    body = 0.6 * np.sin(2 * np.pi * 190 * t)
    noise = rng.normal(0, 1, len(t))
    # Roll off the very top so it is not mistaken for a cymbal.
    noise = np.convolve(noise, np.ones(3) / 3, mode="same")
    return ((body + noise) * np.exp(-26 * t)).astype(np.float32)


def hihat(seconds: float = 0.08) -> np.ndarray:
    """Very short, almost all energy at the top of the spectrum."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 1, len(t))
    noise = noise - np.convolve(noise, np.ones(9) / 9, mode="same")   # high-pass
    return (noise * np.exp(-90 * t)).astype(np.float32)


@pytest.mark.parametrize("sample,expected", [
    (kick(), "kick"),
    (snare(), "snare"),
    (hihat(), "hihat"),
])
def test_the_three_voices_that_matter_are_identified(sample, expected):
    padded = np.concatenate([sample, np.zeros(SR // 2, dtype=np.float32)])
    assert classify(features_at(padded, SR, 0.0)) == expected


def test_a_stroke_with_no_audio_does_not_crash_the_classifier():
    assert classify({}) in VOICES


def test_features_of_a_too_short_window_are_empty():
    assert features_at(np.zeros(8, dtype=np.float32), SR, 0.0) == {}


def test_hits_within_a_stroke_of_each_other_become_one_event():
    hits = [Hit(1.00, "kick", 1.0), Hit(1.01, "hihat", 0.5), Hit(2.00, "snare", 1.0)]
    grouped = group_simultaneous(hits)
    assert len(grouped) == 2
    assert sorted(grouped[0][1]) == ["hihat", "kick"]


def test_a_voice_is_not_repeated_within_one_event():
    grouped = group_simultaneous([Hit(1.0, "kick", 1.0), Hit(1.01, "kick", 1.0)])
    assert grouped[0][1] == ["kick"]


def test_every_voice_has_a_staff_position_and_notehead():
    for spec in VOICES.values():
        assert spec["display"] and spec["notehead"]


@pytest.mark.slow
def test_a_simple_groove_is_transcribed_from_audio(tmp_path):
    """Kick on 1 and 3, snare on 2 and 4, at 120bpm."""
    import soundfile as sf

    length = SR * 4
    track = np.zeros(length, dtype=np.float32)
    for beat, sample in enumerate([kick(), snare(), kick(), snare()] * 2):
        start = int(beat * 0.5 * SR)
        end = min(length, start + len(sample))
        track[start:end] += sample[: end - start]

    path = tmp_path / "drums.wav"
    sf.write(str(path), track, SR)

    hits = transcribe(path)
    assert len(hits) >= 6
    voices = [h.voice for h in hits]
    assert "kick" in voices and "snare" in voices
