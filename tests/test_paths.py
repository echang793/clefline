"""paths: one binding for the data directories, so patching it redirects everything."""

import json

import pytest

import paths
import pipeline


def test_patching_only_paths_jobs_redirects_every_job_accessor(tmp_path, monkeypatch):
    """Regression for the double-binding footgun: pipeline used to do
    `from paths import JOBS`, so recent()/recover saw one object and
    job_dir()/get_status saw another. Patching `paths.JOBS` alone must move
    both -- otherwise a test (or a CLEFLINE_DATA_DIR override) silently splits
    reads and writes across two directories."""
    monkeypatch.setattr(paths, "JOBS", tmp_path)
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)

    job_id = pipeline.new_job("song-1", "sax", {"title": "t"}, {})

    # Written through job_dir() ...
    assert (tmp_path / job_id / "status.json").exists()
    # ... and found again by recent(), which used to read a different binding.
    assert [job["job_id"] for job in pipeline.recent()] == [job_id]


def test_pipeline_has_no_second_binding_of_jobs():
    assert not hasattr(pipeline, "JOBS"), (
        "pipeline.JOBS would be a second binding of paths.JOBS -- use paths.JOBS"
    )


def test_status_files_are_valid_json(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "JOBS", tmp_path)
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)
    job_id = pipeline.new_job("song-1", "keys", {}, {})
    json.loads((tmp_path / job_id / "status.json").read_text())


# ------------------------------------------------------------ id validation


@pytest.mark.parametrize(
    "bad", ["..", "../x", "a/b", "", ".", "a.b", " ", "x" * 65, "a\\b", "a\x00b"]
)
def test_unsafe_ids_are_rejected_and_create_nothing(bad):
    with pytest.raises(paths.InvalidId):
        paths.source_dir(bad)
    with pytest.raises(paths.InvalidId):
        paths.job_dir(bad)
    with pytest.raises(paths.InvalidId):
        paths.job_path(bad)
    assert not paths.SOURCES.exists()
    assert not paths.JOBS.exists()


@pytest.mark.parametrize("good", ["song-1", "QDYfEBY9NM4", "c3526e271fb6", "a_b-C9"])
def test_ordinary_ids_are_accepted(good):
    assert paths.source_dir(good).is_dir()
    assert paths.job_dir(good).is_dir()


def test_job_path_never_creates_a_directory():
    target = paths.job_path("not-there")
    assert target == paths.JOBS / "not-there"
    assert not target.exists()
    assert not paths.JOBS.exists()


def test_a_symlink_escaping_the_data_dir_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    paths.SOURCES.mkdir(parents=True)
    (paths.SOURCES / "sneaky").symlink_to(outside, target_is_directory=True)
    with pytest.raises(paths.InvalidId):
        paths.source_dir("sneaky")
