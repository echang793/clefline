"""Resource limits and clean failures: duration cap, timeouts, no paths in messages."""

import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

import config
import fetch
import paths
import pipeline
import procs
import separate
from fetch import Candidate, FetchError


def _candidate(duration, source_id="QDYfEBY9NM4"):
    return Candidate(source_id=source_id, title="t", uploader="u", duration=duration,
                     thumbnail="", url="https://www.youtube.com/watch?v=" + source_id)


# --------------------------------------------------------------------- config

def test_the_duration_cap_defaults_to_fifteen_minutes():
    assert config.MAX_DURATION_SECONDS == 900


def test_the_duration_cap_can_be_set_from_the_environment(monkeypatch):
    import importlib

    monkeypatch.setenv("CLEFLINE_MAX_DURATION_SECONDS", "120")
    try:
        assert importlib.reload(config).MAX_DURATION_SECONDS == 120
    finally:
        monkeypatch.delenv("CLEFLINE_MAX_DURATION_SECONDS")
        importlib.reload(config)


# ------------------------------------------------------------ resolve: too long

def test_a_youtube_link_over_the_cap_is_refused_with_the_reason(monkeypatch):
    monkeypatch.setattr(fetch, "youtube_metadata", lambda vid: _candidate(60 * 60 * 10))
    with pytest.raises(FetchError, match="limit"):
        fetch.resolve("https://youtu.be/QDYfEBY9NM4")


def test_a_song_within_the_cap_resolves(monkeypatch):
    monkeypatch.setattr(fetch, "youtube_metadata", lambda vid: _candidate(240))
    assert fetch.resolve("https://youtu.be/QDYfEBY9NM4")["chosen"]["duration"] == 240


def test_spotify_matches_over_the_cap_are_dropped_in_favour_of_ones_within_it(monkeypatch):
    monkeypatch.setattr(fetch, "spotify_metadata",
                        lambda url: {"title": "t", "artist": "a", "duration": 240})
    monkeypatch.setattr(fetch, "search_youtube", lambda *a: [
        _candidate(60 * 60 * 3, "AAAAAAAAAAA"), _candidate(241, "BBBBBBBBBBB"),
    ])
    result = fetch.resolve("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    assert result["chosen"]["source_id"] == "BBBBBBBBBBB"
    assert all(c["duration"] <= config.MAX_DURATION_SECONDS for c in result["alternatives"])


def test_spotify_with_only_over_cap_matches_is_refused(monkeypatch):
    monkeypatch.setattr(fetch, "spotify_metadata",
                        lambda url: {"title": "t", "artist": "a", "duration": 240})
    monkeypatch.setattr(fetch, "search_youtube", lambda *a: [_candidate(60 * 60 * 3)])
    with pytest.raises(FetchError, match="limit"):
        fetch.resolve("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")


# ---------------------------------------------------------------- download

def _fake_run_writing(seconds, captured=None):
    """A stand-in for procs.run that "downloads" a real FLAC."""
    def run(cmd, **kwargs):
        if captured is not None:
            captured.append(cmd)
        out = next(a for a in cmd if "audio.%(ext)s" in a).replace("%(ext)s", "flac")
        sf.write(out, np.zeros(int(8000 * seconds), dtype="float32"), 8000, format="FLAC")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def test_download_asks_ytdlp_to_enforce_the_cap_itself(monkeypatch):
    """POST /api/jobs can skip /api/resolve, so the download is the real gate."""
    captured = []
    monkeypatch.setattr(procs, "run", _fake_run_writing(1, captured))
    fetch.download("QDYfEBY9NM4")
    cmd = captured[0]
    assert "--match-filter" in cmd
    assert f"duration<={config.MAX_DURATION_SECONDS}" in cmd[cmd.index("--match-filter") + 1]
    assert "--max-filesize" in cmd


def test_a_download_longer_than_the_cap_is_refused_and_deleted(monkeypatch):
    monkeypatch.setattr(config, "MAX_DURATION_SECONDS", 2)
    monkeypatch.setattr(procs, "run", _fake_run_writing(5))
    with pytest.raises(FetchError, match="limit"):
        fetch.download("QDYfEBY9NM4")
    assert not (paths.SOURCES / "QDYfEBY9NM4" / "audio.flac").exists()


def test_a_download_within_the_cap_is_kept(monkeypatch):
    monkeypatch.setattr(procs, "run", _fake_run_writing(1))
    assert fetch.download("QDYfEBY9NM4").exists()


def test_a_video_filtered_out_by_ytdlp_reports_the_limit_not_a_missing_file(monkeypatch):
    def skipped(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, "[download] X does not pass filter (duration<=900), skipping ..", "")

    monkeypatch.setattr(procs, "run", skipped)
    with pytest.raises(FetchError, match="limit"):
        fetch.download("QDYfEBY9NM4")


def test_a_download_timeout_is_a_clean_error_without_the_command_line(monkeypatch):
    def hang(cmd, **kwargs):
        raise procs.TimedOut(f"{cmd} ran too long")

    monkeypatch.setattr(procs, "run", hang)
    with pytest.raises(FetchError) as caught:
        fetch.download("QDYfEBY9NM4")
    assert sys.executable not in str(caught.value)
    assert "timed out" in str(caught.value).lower()


def test_a_metadata_timeout_is_a_clean_error(monkeypatch):
    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 180)

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(FetchError) as caught:
        fetch._run_ytdlp(["-J", "x"])
    assert sys.executable not in str(caught.value)


def test_ytdlp_failure_shows_the_last_stderr_line_not_a_python_list(monkeypatch):
    def fail(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "WARNING: x\nERROR: Video unavailable\n")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(FetchError) as caught:
        fetch._run_ytdlp(["-J", "x"])
    assert "[" not in str(caught.value)
    assert "Video unavailable" in str(caught.value)


# ---------------------------------------------------------------- separation

def test_a_demucs_timeout_is_a_clean_error_without_the_command_line(monkeypatch, tmp_path):
    def hang(cmd, **kwargs):
        raise procs.TimedOut(f"{cmd} ran too long")

    monkeypatch.setattr(procs, "run", hang)
    with pytest.raises(separate.SeparationError) as caught:
        separate.separate("QDYfEBY9NM4", tmp_path / "audio.flac")
    assert sys.executable not in str(caught.value)
    assert "timed out" in str(caught.value).lower()


# ------------------------------------------------- no filesystem paths in messages

def test_scrub_hides_the_repo_and_home_directories():
    text = f"failed at {paths.ROOT}/src/pipeline.py and {paths.Path.home()}/x"
    scrubbed = paths.scrub(text)
    assert str(paths.ROOT) not in scrubbed
    assert str(paths.Path.home()) not in scrubbed
    assert "pipeline.py" in scrubbed


def test_an_unexpected_failure_message_contains_no_absolute_paths(monkeypatch):
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)
    job_id = pipeline.new_job("song-1", "sax", {}, {})

    def explode(*args, **kwargs):
        raise OSError(f"cannot read {paths.ROOT}/data/sources/song-1/audio.flac")

    monkeypatch.setattr(pipeline, "prepare", explode)
    pipeline._run(job_id)

    status = pipeline.get_status(job_id)
    assert status["state"] == "error"
    assert str(paths.ROOT) not in status["message"]
    assert "audio.flac" in status["message"]


def test_a_domain_error_message_contains_no_absolute_paths(monkeypatch):
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)
    job_id = pipeline.new_job("song-1", "sax", {}, {})

    def refuse(*args, **kwargs):
        raise FetchError(f"yt-dlp failed in {paths.ROOT}/data")

    monkeypatch.setattr(pipeline, "prepare", refuse)
    pipeline._run(job_id)
    assert str(paths.ROOT) not in pipeline.get_status(job_id)["message"]


# ------------------------------------------------------------------- disk guard

def _must_not_run(cmd, **kwargs):
    raise AssertionError("should have refused before starting a subprocess")


def test_low_disk_space_is_a_clean_error_before_downloading(monkeypatch):
    monkeypatch.setattr(config, "MIN_FREE_GB", 10**6)
    monkeypatch.setattr(procs, "run", _must_not_run)
    with pytest.raises(paths.LowDisk, match="free"):
        fetch.download("QDYfEBY9NM4")


def test_low_disk_space_is_a_clean_error_before_separating(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MIN_FREE_GB", 10**6)
    monkeypatch.setattr(procs, "run", _must_not_run)
    with pytest.raises(paths.LowDisk):
        separate.separate("QDYfEBY9NM4", tmp_path / "audio.flac")


def test_a_cached_download_needs_no_free_space(monkeypatch):
    """The guard is for work about to be done; a cache hit does none."""
    cached = paths.source_dir("QDYfEBY9NM4") / "audio.flac"
    cached.write_bytes(b"x")
    monkeypatch.setattr(config, "MIN_FREE_GB", 10**6)
    assert fetch.download("QDYfEBY9NM4") == cached


def test_the_default_free_space_floor_is_three_gigabytes():
    assert config.MIN_FREE_GB == 3


def test_a_low_disk_message_does_not_expose_the_data_path(monkeypatch):
    monkeypatch.setattr(config, "MIN_FREE_GB", 10**6)
    with pytest.raises(paths.LowDisk) as caught:
        paths.require_free_space()
    assert str(paths.ROOT) not in str(caught.value)


# --------------------------------------------------------- stale separation dir

def test_a_stale_demucs_work_dir_is_cleared_before_a_new_run(monkeypatch, tmp_path):
    """A killed run leaves data/sources/<id>/_demucs behind; the next run must
    not write into (and mix with) it."""
    stale = paths.source_dir("QDYfEBY9NM4") / "_demucs" / "leftover.txt"
    stale.parent.mkdir()
    stale.write_text("old")
    seen = {}

    def fake(cmd, **kwargs):
        seen["stale_present"] = stale.exists()
        return subprocess.CompletedProcess(cmd, 1, "", "demucs failed")

    monkeypatch.setattr(procs, "run", fake)
    with pytest.raises(separate.SeparationError):
        separate.separate("QDYfEBY9NM4", tmp_path / "audio.flac")
    assert seen["stale_present"] is False
