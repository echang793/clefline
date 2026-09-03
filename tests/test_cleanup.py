"""scripts/cleanup.py -- all against synthetic tmp directories, never real data."""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cleanup import age_days, find_stale, main  # noqa: E402


def make(root: Path, name: str, age_hours: float, content: str = "x") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "file.txt").write_text(content)
    stamp = time.time() - age_hours * 3600
    import os
    os.utime(directory / "file.txt", (stamp, stamp))
    return directory


def test_age_days_reads_the_newest_file_not_the_directory(tmp_path):
    directory = make(tmp_path, "song", age_hours=48)
    assert age_days(directory) == pytest.approx(2.0, abs=0.05)


def test_a_second_file_dropped_in_later_resets_the_age(tmp_path):
    """A source re-touched for a second instrument's job must not read as stale
    just because its first file is old."""
    directory = make(tmp_path, "song", age_hours=200)
    (directory / "newer.txt").write_text("y")  # untouched utime == now
    assert age_days(directory) < 1.0


def test_find_stale_only_returns_directories_past_the_threshold(tmp_path):
    make(tmp_path, "old", age_hours=48)
    make(tmp_path, "new", age_hours=1)
    stale = find_stale(tmp_path, max_age_days=1.0)
    assert [d.name for d in stale] == ["old"]


def test_find_stale_on_a_missing_root_is_empty_not_an_error(tmp_path):
    assert find_stale(tmp_path / "does-not-exist", max_age_days=1.0) == []


def test_dry_run_deletes_nothing(tmp_path, monkeypatch, capsys):
    jobs = tmp_path / "jobs"
    sources = tmp_path / "sources"
    make(jobs, "job1", age_hours=48)
    make(sources, "src1", age_hours=2000)

    import cleanup
    monkeypatch.setattr(cleanup, "JOBS", jobs)
    monkeypatch.setattr(cleanup, "SOURCES", sources)

    exit_code = main(["--job-days", "1"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert (jobs / "job1").exists()
    assert (sources / "src1").exists()
    assert "would delete" in out
    assert "job1" in out


def test_yes_actually_deletes(tmp_path, monkeypatch, capsys):
    jobs = tmp_path / "jobs"
    sources = tmp_path / "sources"
    make(jobs, "job1", age_hours=48)
    make(jobs, "job2", age_hours=1)   # too recent, must survive
    make(sources, "src1", age_hours=2000)

    import cleanup
    monkeypatch.setattr(cleanup, "JOBS", jobs)
    monkeypatch.setattr(cleanup, "SOURCES", sources)

    exit_code = main(["--job-days", "1", "--source-days", "30", "--yes"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert not (jobs / "job1").exists()
    assert (jobs / "job2").exists()
    assert not (sources / "src1").exists()
    assert "Deleted 2 item(s)" in out


def test_a_job_directorys_title_is_shown_when_available(tmp_path, monkeypatch, capsys):
    import os

    jobs = tmp_path / "jobs"
    directory = make(jobs, "job1", age_hours=48)
    status = directory / "status.json"
    status.write_text(json.dumps({"meta": {"title": "Some Song"}}))
    stamp = time.time() - 48 * 3600
    os.utime(status, (stamp, stamp))  # backdate, or "newest file" logic keeps this job fresh

    import cleanup
    monkeypatch.setattr(cleanup, "JOBS", jobs)
    monkeypatch.setattr(cleanup, "SOURCES", tmp_path / "sources")

    main(["--job-days", "1"])
    assert "Some Song" in capsys.readouterr().out


def test_nothing_stale_says_so_and_exits_clean(tmp_path, monkeypatch, capsys):
    jobs = tmp_path / "jobs"
    make(jobs, "job1", age_hours=1)

    import cleanup
    monkeypatch.setattr(cleanup, "JOBS", jobs)
    monkeypatch.setattr(cleanup, "SOURCES", tmp_path / "sources")

    exit_code = main(["--job-days", "14"])
    assert exit_code == 0
    assert "Nothing to clean up" in capsys.readouterr().out
