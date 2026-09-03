"""Grid is where seconds become musical position; every later stage trusts it."""

import pytest

from grid import Grid, _infer_beats_per_bar


def build(bpm: float = 120.0, count: int = 33, beats_per_bar: int = 4) -> Grid:
    step = 60.0 / bpm
    beats = [i * step for i in range(count)]
    return Grid(beats=beats, downbeats=beats[::beats_per_bar], tempo=bpm,
                beats_per_bar=beats_per_bar)


def test_beat_position_is_exact_on_beats():
    g = build()
    for index in range(0, 10):
        assert g.beat_position(g.beats[index]) == pytest.approx(index)


def test_beat_position_interpolates_between_beats():
    g = build(bpm=120)          # half a second per beat
    assert g.beat_position(0.25) == pytest.approx(0.5)
    assert g.beat_position(1.75) == pytest.approx(3.5)


def test_beat_position_extrapolates_past_the_end():
    g = build(bpm=120, count=5)
    last = g.beats[-1]
    assert g.beat_position(last + 0.5) == pytest.approx(len(g.beats) - 1 + 1.0)


def test_measure_of_splits_on_the_bar():
    g = build(beats_per_bar=4)
    assert g.measure_of(0.0) == (1, 0.0)
    assert g.measure_of(3.5) == (1, 3.5)
    assert g.measure_of(4.0) == (2, 0.0)
    assert g.measure_of(9.0) == (3, 1.0)


@pytest.mark.parametrize("subdivision,value,expected", [
    (4, 0.24, 0.25),        # sixteenths
    (4, 0.26, 0.25),
    (4, 0.51, 0.5),
    (2, 0.4, 0.5),          # eighths
    (2, 0.2, 0.0),
    (3, 0.3, 1 / 3),        # eighth triplets
])
def test_snap_rounds_to_the_named_grid(subdivision, value, expected):
    assert build().snap(value, subdivision) == pytest.approx(expected)


def test_time_signature_reads_compound_six_as_eighths():
    assert build(beats_per_bar=4).time_signature == "4/4"
    assert build(beats_per_bar=3).time_signature == "3/4"
    assert build(beats_per_bar=6).time_signature == "6/8"


@pytest.mark.parametrize("beats_per_bar", [2, 3, 4, 5, 6])
def test_meter_inferred_from_downbeat_spacing(beats_per_bar):
    g = build(count=beats_per_bar * 8 + 1, beats_per_bar=beats_per_bar)
    assert _infer_beats_per_bar(g.beats, g.downbeats) == beats_per_bar


def test_meter_falls_back_to_four_without_enough_downbeats():
    g = build()
    assert _infer_beats_per_bar(g.beats, g.downbeats[:1]) == 4


def test_round_trip_through_dict():
    g = build(bpm=93.5, beats_per_bar=3)
    restored = Grid.from_dict(g.to_dict())
    assert restored.tempo == g.tempo
    assert restored.beats_per_bar == g.beats_per_bar
    assert restored.beats == g.beats


def test_a_pickup_before_the_first_downbeat_stays_non_negative():
    """Regression: real audio opened mid-bar and produced notes at -0.75 quarters,
    which music21 cannot place in any measure."""
    import grid as grid_module
    from grid import analyze

    step = 0.5
    beats = [i * step for i in range(32)]

    def fake_tracker(_audio):
        # First downbeat three beats in: the song starts on beat 4 of a bar.
        return beats, beats[3::4]

    original = grid_module._track_beat_this
    grid_module._track_beat_this = fake_tracker
    try:
        g = analyze("ignored.wav")
    finally:
        grid_module._track_beat_this = original

    assert min(g.quarters(t) for t in g.beats) >= 0
    # The first downbeat must still land on a barline.
    assert g.measure_of(g.quarters(beats[3]))[1] == pytest.approx(0.0)
