"""pipeline.engrave: option threading from the job request through to render."""

import re
from pathlib import Path

import pytest

from grid import Grid
from pipeline import engrave


def _prepared():
    beats = [i * 0.5 for i in range(16)]
    return {
        "grid": Grid(beats=beats, downbeats=beats[::4], tempo=120.0, beats_per_bar=4),
        "harmony": {"sharps": 0, "chords": []},
        "melody": {
            "notes": [
                {"start": i * 0.5, "end": i * 0.5 + 0.4, "midi": 60 + i, "amplitude": 0.8}
                for i in range(6)
            ],
            "swing": False,
        },
        "hits": {"hits": []},
    }


def _media_box(pdf_path):
    match = re.search(
        rb"/MediaBox\s*\[\s*[\d.]+\s+[\d.]+\s+([\d.]+)\s+([\d.]+)", Path(pdf_path).read_bytes()
    )
    return float(match.group(1)), float(match.group(2))


def test_page_size_option_reaches_the_pdf(tmp_path, monkeypatch):
    """The Adjust panel's page-size choice has to survive the trip through
    pipeline.engrave into render.render, or picking A4 in the UI does nothing."""
    import paths

    monkeypatch.setattr(paths, "JOBS", tmp_path)

    artifacts = engrave(
        "job-a4-test", "sax", _prepared(), meta={"title": "t"}, options={"page_size": "a4"},
    )
    assert _media_box(artifacts["pdf"]) == (595.28, 841.89)


def test_page_size_defaults_to_letter_when_not_given(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "JOBS", tmp_path)

    artifacts = engrave("job-default-test", "sax", _prepared(), meta={"title": "t"}, options={})
    assert _media_box(artifacts["pdf"]) == (612.0, 792.0)


@pytest.mark.parametrize("part", ["sax", "keys", "drums"])
def test_page_size_option_works_for_every_part(tmp_path, monkeypatch, part):
    import paths

    monkeypatch.setattr(paths, "JOBS", tmp_path)

    artifacts = engrave(
        f"job-{part}-test", part, _prepared(), meta={"title": "t"}, options={"page_size": "a4"},
    )
    assert _media_box(artifacts["pdf"]) == (595.28, 841.89)
