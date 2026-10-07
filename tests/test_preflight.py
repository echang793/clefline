"""preflight: say what is wrong at startup, in words, instead of minutes into a job."""

import shutil
import sys

import pytest

import config
import paths
import preflight


def _by_name(checks):
    return {c.name: c for c in checks}


def test_everything_passes_on_a_healthy_machine():
    checks = _by_name(preflight.run())
    for name in ("python", "ffmpeg", "yt-dlp", "demucs", "data directory", "disk space"):
        assert checks[name].ok, f"{name}: {checks[name].detail}"


def test_a_missing_ffmpeg_is_a_fatal_failure(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    check = _by_name(preflight.run())["ffmpeg"]
    assert not check.ok and check.fatal
    assert "brew install ffmpeg" in check.detail


def test_the_wrong_python_version_is_fatal(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 13, 0, "final", 0))
    check = _by_name(preflight.run())["python"]
    assert not check.ok and check.fatal
    assert "3.11" in check.detail


def test_an_unwritable_data_directory_is_fatal(monkeypatch, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    monkeypatch.setattr(paths, "DATA", blocker / "data")
    check = _by_name(preflight.run())["data directory"]
    assert not check.ok and check.fatal


def test_low_disk_space_fails_the_check_but_is_not_fatal_at_startup(monkeypatch):
    """The server can still show history and downloads; only new jobs are refused."""
    monkeypatch.setattr(config, "MIN_FREE_GB", 10**6)
    check = _by_name(preflight.run())["disk space"]
    assert not check.ok and not check.fatal


def test_missing_model_weights_warn_that_the_first_job_will_download_them(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch"))
    checks = _by_name(preflight.run())
    for name in ("demucs weights", "beat_this weights"):
        assert not checks[name].ok and not checks[name].fatal
        assert "download" in checks[name].detail.lower()


def test_cached_model_weights_pass(monkeypatch, tmp_path):
    hf = tmp_path / "hf" / "hub" / "models--adefossez--HTDemucs-6s" / "snapshots" / "abc"
    hf.mkdir(parents=True)
    (hf / "5c90dfd2.safetensors").write_bytes(b"x")
    torch = tmp_path / "torch" / "hub" / "checkpoints"
    torch.mkdir(parents=True)
    (torch / "beat_this-final0.ckpt").write_bytes(b"x")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch"))
    checks = _by_name(preflight.run())
    assert checks["demucs weights"].ok and checks["beat_this weights"].ok


def test_binding_beyond_localhost_is_flagged_because_there_is_no_auth(monkeypatch):
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    check = _by_name(preflight.run())["network binding"]
    assert not check.ok and not check.fatal
    assert "no authentication" in check.detail.lower()


def test_the_default_localhost_binding_is_fine():
    assert _by_name(preflight.run())["network binding"].ok


def test_require_exits_listing_every_fatal_problem(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "version_info", (3, 13, 0, "final", 0))
    with pytest.raises(SystemExit) as caught:
        preflight.require()
    message = str(caught.value)
    assert "ffmpeg" in message and "3.11" in message


def test_require_returns_the_report_when_nothing_fatal_is_wrong():
    checks = preflight.require()
    assert any(c.name == "ffmpeg" and c.ok for c in checks)


def test_a_non_fatal_failure_does_not_stop_startup(monkeypatch):
    monkeypatch.setattr(config, "MIN_FREE_GB", 10**6)
    checks = preflight.require()
    assert not _by_name(checks)["disk space"].ok


def test_no_check_detail_exposes_a_filesystem_path():
    for check in preflight.run():
        assert str(paths.ROOT) not in check.detail
        assert "/Users/" not in check.detail, f"{check.name}: {check.detail}"


def test_require_remembers_its_report_so_startup_does_not_run_it_twice(monkeypatch):
    """`python src/server.py` runs preflight before binding the port (for a clean
    failure message); the app's own startup must not repeat it and log every line again."""
    monkeypatch.setattr(preflight, "last", None)
    report = preflight.require()
    assert preflight.last is report


def test_the_lifespan_reuses_an_earlier_report(monkeypatch, caplog):
    import server

    report = preflight.require()
    monkeypatch.setattr(preflight, "last", report)
    calls = []
    monkeypatch.setattr(preflight, "require", lambda: calls.append(1) or report)
    assert server.ensure_preflight() is report
    assert calls == []

    monkeypatch.setattr(preflight, "last", None)
    server.ensure_preflight()
    assert calls == [1]
