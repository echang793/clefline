"""pipeline: option threading, crash recovery, dedup, and sparse-melody warnings."""

import json
import re
import time
from pathlib import Path

import pytest

import pipeline
from grid import Grid
from pipeline import engrave, melody_coverage, new_job, recover_interrupted_jobs


@pytest.fixture(autouse=True)
def _isolated_jobs_dir(tmp_path, monkeypatch):
    """Every test in this file drives _run() by hand for a deterministic
    result, against a private jobs directory.

    Two separate things need patching, not one: recover_interrupted_jobs()
    and recent() resolve the bare name JOBS in pipeline's own module globals,
    but new_job()/get_status()/_set_status() go through paths.job_dir(),
    which resolves JOBS in *paths*' module globals -- the same object at
    import time, but two independent bindings, so patching only one leaves
    half the code paths still writing to the real data/jobs/.

    Also disables the real background worker thread: without this, new_job()
    would start it, and it could pick the same job off the queue and run it
    concurrently with the test's own direct call to _run() -- a race on one
    status.json.
    """
    import paths

    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    monkeypatch.setattr(paths, "JOBS", tmp_path)
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)


def _write_status(jobs_dir, job_id, **fields):
    directory = jobs_dir / job_id
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"job_id": job_id, "created": time.time(), **fields}
    (directory / "status.json").write_text(json.dumps(payload))
    return payload


# --------------------------------------------------------------- crash recovery

@pytest.mark.parametrize("state", ["queued", "running"])
def test_interrupted_jobs_are_marked_as_errors(tmp_path, monkeypatch, state):
    """Regression: the job queue is in-memory and dies with the process. A job
    still "running" when the server restarts used to stay that way forever --
    a progress bar that never moves again, no error, no retry path."""
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    _write_status(tmp_path, "stuck-job", state=state, source_id="x", part="sax")

    count = recover_interrupted_jobs()

    assert count == 1
    status = pipeline.get_status("stuck-job")
    assert status["state"] == "error"
    assert "restart" in status["message"].lower()


@pytest.mark.parametrize("state", ["done", "error"])
def test_finished_jobs_are_left_alone_by_recovery(tmp_path, monkeypatch, state):
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    _write_status(
        tmp_path, "finished-job", state=state, message="original", source_id="x", part="sax",
    )

    count = recover_interrupted_jobs()

    assert count == 0
    assert pipeline.get_status("finished-job")["message"] == "original"


def test_recovery_on_an_empty_jobs_directory_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    assert recover_interrupted_jobs() == 0


# --------------------------------------------------------------------- job dedup

def test_an_identical_request_reuses_the_existing_job(tmp_path, monkeypatch):
    """A double click on Find, two tabs, a resubmitted page -- the same
    request happening twice by accident should not pay for two full runs."""
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)

    first = new_job("song-1", "sax", {"title": "t"}, {"subdivision": 4})
    second = new_job("song-1", "sax", {"title": "t"}, {"subdivision": 4})

    assert first == second
    assert len(list(tmp_path.iterdir())) == 1


def test_a_different_option_is_not_treated_as_a_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)

    first = new_job("song-1", "sax", {"title": "t"}, {"subdivision": 4})
    second = new_job("song-1", "sax", {"title": "t"}, {"subdivision": 2})

    assert first != second


def test_a_failed_job_is_never_reused(tmp_path, monkeypatch):
    """The one request that most needs a fresh attempt is exactly the one a
    naive dedup would hand back the same failure for."""
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)

    _write_status(tmp_path, "job-a", state="error", source_id="song-1", part="sax", options={})
    second = new_job("song-1", "sax", {"title": "t"}, {})

    assert second != "job-a"


# ------------------------------------------------------------- melody coverage

def test_melody_coverage_is_the_fraction_of_the_song_with_notes():
    notes = [type("N", (), {"duration": 1.0})(), type("N", (), {"duration": 1.5})()]
    assert melody_coverage(notes, song_duration=10.0) == pytest.approx(0.25)


def test_melody_coverage_of_a_zero_length_song_is_zero_not_a_crash():
    assert melody_coverage([], song_duration=0.0) == 0.0


def test_melody_coverage_of_no_notes_is_zero():
    assert melody_coverage([], song_duration=10.0) == 0.0


# ---------------------------------------------------------- sparse-melody flag

def test_sparse_melody_flags_when_coverage_is_low(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    prepared = _prepared()
    prepared["melody"]["coverage"] = 0.02
    monkeypatch.setattr(pipeline, "prepare", lambda source_id, on_stage: prepared)

    job_id = new_job("song-1", "sax", {"title": "t"}, {})
    pipeline._run(job_id)

    status = pipeline.get_status(job_id)
    assert status["state"] == "done"
    assert status["detected"]["sparse_melody"] is True


def test_sparse_melody_is_not_flagged_for_drums(tmp_path, monkeypatch):
    """Drums never reads the melody -- how much vocal the song has is
    irrelevant to a drum chart."""
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    prepared = _prepared()
    prepared["melody"]["coverage"] = 0.02
    monkeypatch.setattr(pipeline, "prepare", lambda source_id, on_stage: prepared)

    job_id = new_job("song-1", "drums", {"title": "t"}, {})
    pipeline._run(job_id)

    assert "sparse_melody" not in pipeline.get_status(job_id)["detected"]


def test_a_healthy_melody_is_not_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)
    prepared = _prepared()
    prepared["melody"]["coverage"] = 0.6
    monkeypatch.setattr(pipeline, "prepare", lambda source_id, on_stage: prepared)

    job_id = new_job("song-1", "sax", {"title": "t"}, {})
    pipeline._run(job_id)

    assert "sparse_melody" not in pipeline.get_status(job_id)["detected"]


# -------------------------------------------------------------- error framing

def test_a_known_error_message_is_shown_as_is(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)

    def raise_fetch_error(source_id, on_stage):
        raise pipeline.fetch.FetchError("Paste a YouTube or Spotify link.")

    monkeypatch.setattr(pipeline, "prepare", raise_fetch_error)

    job_id = new_job("song-1", "sax", {"title": "t"}, {})
    pipeline._run(job_id)

    status = pipeline.get_status(job_id)
    assert status["state"] == "error"
    assert status["message"] == "Paste a YouTube or Spotify link."


def test_an_unexpected_error_is_framed_as_a_surprise(tmp_path, monkeypatch):
    """A domain error (FetchError, SeparationError, a page-size ValueError)
    already reads clearly and is shown verbatim. Anything else -- a crash
    somewhere inside torch or music21 this pipeline has no specific message
    for -- should not read as if the app understood exactly what broke."""
    monkeypatch.setattr(pipeline, "JOBS", tmp_path)

    def raise_surprise(source_id, on_stage):
        raise KeyError("some_unexpected_internal_key")

    monkeypatch.setattr(pipeline, "prepare", raise_surprise)

    job_id = new_job("song-1", "sax", {"title": "t"}, {})
    pipeline._run(job_id)

    status = pipeline.get_status(job_id)
    assert status["state"] == "error"
    assert "unexpected" in status["message"].lower()
    assert "some_unexpected_internal_key" in status["message"]


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
