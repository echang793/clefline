"""pipeline: option threading, crash recovery, dedup, and sparse-melody warnings."""

import json
import queue
import re
import threading
import time
from pathlib import Path

import pytest

import paths
import pipeline
import procs
from grid import Grid
from pipeline import engrave, melody_coverage, new_job, resume_interrupted_jobs


@pytest.fixture(autouse=True)
def _isolated_jobs_dir(tmp_path, monkeypatch):
    """Every test in this file drives _run() by hand for a deterministic
    result, against a private jobs directory.

    `paths.JOBS` is the single binding for the jobs directory (pipeline reads
    it through the module, not a copy), so patching it once redirects
    new_job/get_status/recent/recover alike.

    Also disables the real background worker thread: without this, new_job()
    would start it, and it could pick the same job off the queue and run it
    concurrently with the test's own direct call to _run() -- a race on one
    status.json.
    """
    monkeypatch.setattr(paths, "JOBS", tmp_path)
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)
    monkeypatch.setattr(pipeline, "_work", queue.Queue())
    monkeypatch.setattr(pipeline, "_cancelled", set())
    monkeypatch.setattr(pipeline, "_stop", threading.Event())
    monkeypatch.setattr(pipeline, "_running", None)


def _write_status(jobs_dir, job_id, **fields):
    directory = jobs_dir / job_id
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"job_id": job_id, "created": time.time(), **fields}
    (directory / "status.json").write_text(json.dumps(payload))
    return payload


# ------------------------------------------------------------------- resume

def _queued():
    return list(pipeline._work.queue)


@pytest.mark.parametrize("state,attempts", [("queued", 0), ("running", 1)])
def test_interrupted_jobs_are_resumed_not_failed(tmp_path, state, attempts):
    """The queue is in-memory and dies with the process. Failing every
    in-flight job on restart threw away work the idempotent, artifact-keyed
    stages could simply pick back up -- so resume them."""
    _write_status(tmp_path, "stuck-job", state=state, attempts=attempts,
                  source_id="song-1", part="sax", options={})

    assert resume_interrupted_jobs() == 1

    assert _queued() == ["stuck-job"]
    status = pipeline.get_status("stuck-job")
    assert status["state"] == "queued"
    assert "resum" in status["message"].lower()


def test_a_job_that_already_crashed_the_server_twice_is_failed_not_looped(tmp_path):
    """A job that OOMs the process would otherwise crash it again on every
    restart, forever."""
    _write_status(tmp_path, "poison", state="running", attempts=2,
                  source_id="song-1", part="sax", options={})

    assert resume_interrupted_jobs() == 0

    assert _queued() == []
    status = pipeline.get_status("poison")
    assert status["state"] == "error"
    assert "twice" in status["message"].lower()


def test_resumed_jobs_keep_their_original_order(tmp_path):
    now = time.time()
    for name, created in (("third", now), ("first", now - 200), ("second", now - 100)):
        _write_status(tmp_path, name, state="queued", created=created, source_id="s", part="sax")

    resume_interrupted_jobs()

    assert _queued() == ["first", "second", "third"]


@pytest.mark.parametrize("state", ["done", "error", "cancelled"])
def test_finished_jobs_are_left_alone_by_resume(tmp_path, state):
    _write_status(
        tmp_path, "finished-job", state=state, message="original", source_id="x", part="sax",
    )

    assert resume_interrupted_jobs() == 0

    assert _queued() == []
    assert pipeline.get_status("finished-job")["message"] == "original"


def test_resume_finds_every_orphan_not_just_the_newest_thousand(tmp_path):
    for index in range(1100):
        _write_status(tmp_path, f"job{index:04d}", state="queued", source_id="s", part="sax")
    assert resume_interrupted_jobs() == 1100


def test_resuming_an_empty_jobs_directory_does_nothing():
    assert resume_interrupted_jobs() == 0


def test_starting_a_job_counts_an_attempt(monkeypatch):
    monkeypatch.setattr(pipeline, "prepare", lambda source_id, on_stage: _prepared())
    job_id = new_job("song-1", "sax", {"title": "t"}, {})
    pipeline._run(job_id)
    assert pipeline.get_status(job_id)["attempts"] == 1


# -------------------------------------------------------------------- cancel

def _never_called(*args, **kwargs):
    raise AssertionError("a cancelled job must not run")


def test_cancelling_a_queued_job_marks_it_cancelled_and_the_worker_skips_it(monkeypatch):
    job_id = new_job("song-1", "sax", {"title": "t"}, {})

    assert pipeline.cancel(job_id) == "cancelled"
    assert pipeline.get_status(job_id)["state"] == "cancelled"

    monkeypatch.setattr(pipeline, "prepare", _never_called)
    pipeline._run(job_id)  # what the worker does when it later dequeues it
    assert pipeline.get_status(job_id)["state"] == "cancelled"


def test_cancelling_a_running_job_stops_it(monkeypatch):
    started = threading.Event()

    def endless(source_id, on_stage):
        started.set()
        while True:
            on_stage("separate")  # a stage boundary: where a cancel is noticed
            time.sleep(0.01)

    monkeypatch.setattr(pipeline, "prepare", endless)
    job_id = new_job("song-1", "sax", {"title": "t"}, {})
    worker = threading.Thread(target=pipeline._run, args=(job_id,))
    worker.start()
    assert started.wait(5)

    pipeline.cancel(job_id)
    worker.join(5)

    assert not worker.is_alive()
    assert pipeline.get_status(job_id)["state"] == "cancelled"


def test_cancelling_a_running_job_kills_its_subprocess(monkeypatch, tmp_path):
    """The cancel flag reaches a blocking subprocess through procs' scope, not
    just at stage boundaries -- demucs runs for minutes in one stage."""
    import sys

    pidfile = tmp_path / "pid"
    code = f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(60)"

    def separating(source_id, on_stage):
        procs.run([sys.executable, "-c", code], timeout=120)

    monkeypatch.setattr(pipeline, "prepare", separating)
    job_id = new_job("song-1", "sax", {"title": "t"}, {})
    worker = threading.Thread(target=pipeline._run, args=(job_id,))
    worker.start()
    deadline = time.time() + 10
    while not pidfile.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert pidfile.exists()

    pipeline.cancel(job_id)
    worker.join(10)

    assert not worker.is_alive()
    assert pipeline.get_status(job_id)["state"] == "cancelled"
    import os

    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_cancelling_a_finished_job_changes_nothing(tmp_path):
    _write_status(tmp_path, "done-job", state="done", message="Ready", source_id="s", part="sax")
    assert pipeline.cancel("done-job") == "done"
    assert pipeline.get_status("done-job")["message"] == "Ready"


def test_cancelling_an_unknown_job_is_none():
    assert pipeline.cancel("nope-nope") is None
    assert pipeline.cancel("../etc") is None


def test_a_shutdown_leaves_the_running_job_resumable_not_cancelled(monkeypatch):
    """Shutdown interrupts the work but is not the user giving up on it: the
    job must stay "running" so the next start resumes it."""
    started = threading.Event()

    def endless(source_id, on_stage):
        started.set()
        while True:
            on_stage("separate")
            time.sleep(0.01)

    monkeypatch.setattr(pipeline, "prepare", endless)
    job_id = new_job("song-1", "sax", {"title": "t"}, {})
    worker = threading.Thread(target=pipeline._run, args=(job_id,))
    worker.start()
    assert started.wait(5)

    pipeline.shutdown()
    worker.join(5)

    assert not worker.is_alive()
    assert pipeline.get_status(job_id)["state"] == "running"


# ------------------------------------------------------------ worker survival

def test_a_failing_status_write_does_not_escape_run(monkeypatch):
    """Disk full: the handler that records the error can fail too. That used to
    kill the worker thread and leave the job "running" forever."""
    def refuse(job_id):
        raise FetchErrorStandIn("download failed")

    monkeypatch.setattr(pipeline, "prepare", lambda source_id, on_stage: refuse("x"))
    job_id = new_job("song-1", "sax", {"title": "t"}, {})

    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pipeline, "_set_status", full_disk)
    pipeline._run(job_id)  # must not raise


class FetchErrorStandIn(RuntimeError):
    pass


def test_the_worker_loop_survives_a_job_that_blows_up(monkeypatch):
    first = new_job("song-1", "sax", {"title": "a"}, {})
    second = new_job("song-2", "sax", {"title": "b"}, {})
    calls = []

    def flaky(job_id):
        calls.append(job_id)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        pipeline._stop.set()

    monkeypatch.setattr(pipeline, "_run", flaky)
    worker = threading.Thread(target=pipeline._loop)
    worker.start()
    worker.join(10)

    assert not worker.is_alive()
    assert calls == [first, second]
    assert pipeline._work.unfinished_tasks == 0


# ---------------------------------------------------------------- dedup race

def test_concurrent_identical_submissions_create_one_job(tmp_path, monkeypatch):
    """Two identical POSTs run in parallel threads; both used to miss the
    other's job in _find_reusable and each create their own."""
    real = pipeline._find_reusable

    def slow(*args, **kwargs):
        found = real(*args, **kwargs)
        time.sleep(0.05)   # widen the check-then-create window
        return found

    monkeypatch.setattr(pipeline, "_find_reusable", slow)
    results, barrier = [], threading.Barrier(6)

    def submit():
        barrier.wait()
        results.append(new_job("song-1", "sax", {"title": "t"}, {"subdivision": 4}))

    threads = [threading.Thread(target=submit) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert len(set(results)) == 1
    assert len([d for d in tmp_path.iterdir() if d.is_dir()]) == 1


# ------------------------------------------------------------- queue position

def test_queued_jobs_report_how_many_are_ahead(monkeypatch):
    a = new_job("song-1", "sax", {"title": "a"}, {})
    b = new_job("song-2", "sax", {"title": "b"}, {})
    c = new_job("song-3", "sax", {"title": "c"}, {})

    assert [pipeline.jobs_ahead(x) for x in (a, b, c)] == [0, 1, 2]

    monkeypatch.setattr(pipeline, "_running", "someone-else")
    assert [pipeline.jobs_ahead(x) for x in (a, b, c)] == [1, 2, 3]


def test_a_job_that_is_not_queued_has_no_position():
    assert pipeline.jobs_ahead("not-queued") is None


# --------------------------------------------------------------------- job dedup

def test_an_identical_request_reuses_the_existing_job(tmp_path, monkeypatch):
    """A double click on Find, two tabs, a resubmitted page -- the same
    request happening twice by accident should not pay for two full runs."""
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)

    first = new_job("song-1", "sax", {"title": "t"}, {"subdivision": 4})
    second = new_job("song-1", "sax", {"title": "t"}, {"subdivision": 4})

    assert first == second
    assert len(list(tmp_path.iterdir())) == 1


def test_a_different_option_is_not_treated_as_a_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)

    first = new_job("song-1", "sax", {"title": "t"}, {"subdivision": 4})
    second = new_job("song-1", "sax", {"title": "t"}, {"subdivision": 2})

    assert first != second


def test_a_failed_job_is_never_reused(tmp_path, monkeypatch):
    """The one request that most needs a fresh attempt is exactly the one a
    naive dedup would hand back the same failure for."""
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)

    _write_status(tmp_path, "job-a", state="error", source_id="song-1", part="sax", options={})
    second = new_job("song-1", "sax", {"title": "t"}, {})

    assert second != "job-a"


def test_a_cancelled_job_is_never_reused(tmp_path):
    """Resubmitting a cancelled request means "do it after all", not "hand me
    back the cancelled job"."""
    _write_status(tmp_path, "job-c", state="cancelled", source_id="song-1", part="sax", options={})
    assert new_job("song-1", "sax", {"title": "t"}, {}) != "job-c"


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
    prepared = _prepared()
    prepared["melody"]["coverage"] = 0.02
    monkeypatch.setattr(pipeline, "prepare", lambda source_id, on_stage: prepared)

    job_id = new_job("song-1", "drums", {"title": "t"}, {})
    pipeline._run(job_id)

    assert "sparse_melody" not in pipeline.get_status(job_id)["detected"]


def test_a_healthy_melody_is_not_flagged(tmp_path, monkeypatch):
    prepared = _prepared()
    prepared["melody"]["coverage"] = 0.6
    monkeypatch.setattr(pipeline, "prepare", lambda source_id, on_stage: prepared)

    job_id = new_job("song-1", "sax", {"title": "t"}, {})
    pipeline._run(job_id)

    assert "sparse_melody" not in pipeline.get_status(job_id)["detected"]


# -------------------------------------------------------------- error framing

def test_a_known_error_message_is_shown_as_is(tmp_path, monkeypatch):

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
