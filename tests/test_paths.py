"""paths: one binding for the data directories, so patching it redirects everything."""

import json

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
