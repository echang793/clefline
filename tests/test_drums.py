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


def tom(freq: float, seconds: float = 0.4) -> np.ndarray:
    """A membrane ringing at `freq` with a fast pitch drop, like a real tom."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    sweep = freq * 1.3 * np.exp(-10 * t) + freq * 0.85
    return (np.sin(2 * np.pi * np.cumsum(sweep) / SR) * np.exp(-6 * t)).astype(np.float32)


def ride(seconds: float = 0.9) -> np.ndarray:
    """A defined metallic ping plus shimmer -- moderate sustain, some pitch."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    rng = np.random.default_rng(2)
    ping = 0.5 * np.sin(2 * np.pi * 550 * t) + 0.3 * np.sin(2 * np.pi * 1100 * t)
    shimmer = rng.normal(0, 1, len(t))
    shimmer = shimmer - np.convolve(shimmer, np.ones(15) / 15, mode="same")
    return ((ping * np.exp(-2.2 * t)) + 0.35 * shimmer * np.exp(-3.5 * t)).astype(np.float32)


def crash(seconds: float = 1.8) -> np.ndarray:
    """Broadband and long -- no defined pitch, unlike a ride."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 1, len(t))
    return (noise * np.exp(-1.3 * t)).astype(np.float32)


@pytest.mark.parametrize("sample,expected", [
    (kick(), "kick"),
    (snare(), "snare"),
    (hihat(), "hihat"),
])
def test_the_three_voices_that_matter_are_identified(sample, expected):
    padded = np.concatenate([sample, np.zeros(SR // 2, dtype=np.float32)])
    assert classify(features_at(padded, SR, 0.0)) == expected


@pytest.mark.parametrize("freq", [90, 150, 220])  # low, mid, high tom
def test_toms_across_their_pitch_range_are_not_called_snare(freq):
    """Regression: a tom's harmonics leaking into the mid band used to lose to
    the snare rule before the tom rule (positioned after it) ever ran -- a
    high or mid tom classified as "snare" outright, not just uncertainly.
    Confirmed on synthetic audio before this existed: high_tom and mid_tom
    both misclassified, only low_tom happened to survive."""
    padded = np.concatenate([tom(freq), np.zeros(SR, dtype=np.float32)])
    assert classify(features_at(padded, SR, 0.0)) == "tom"


def test_a_toms_narrowband_ring_is_measurably_peakier_than_a_snares_crack():
    tom_features = features_at(
        np.concatenate([tom(150), np.zeros(SR, dtype=np.float32)]), SR, 0.0
    )
    snare_features = features_at(
        np.concatenate([snare(), np.zeros(SR, dtype=np.float32)]), SR, 0.0
    )
    assert tom_features["peakiness"] > 0.8
    assert snare_features["peakiness"] < 0.2


def test_ride_and_crash_are_not_confused_with_a_tonal_drum():
    """Both are cymbals in this app's vocabulary (no separate ride/crash voice
    exists), but neither should ever be peaky enough to trip the tom rule."""
    for sample in (ride(), crash()):
        padded = np.concatenate([sample, np.zeros(SR, dtype=np.float32)])
        assert classify(features_at(padded, SR, 0.0)) in ("cymbal", "hihat_open", "hihat")


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
