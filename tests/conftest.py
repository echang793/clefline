import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def pytest_addoption(parser):
    parser.addoption(
        "--run-network", action="store_true", default=False,
        help="run tests that hit YouTube or Spotify (skipped by default)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-network"):
        return
    skip = pytest.mark.skip(reason="needs network; pass --run-network to run")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _private_data_dir(tmp_path, monkeypatch):
    """Point every data directory at a per-test temp dir.

    Without this, anything that reaches paths.source_dir()/job_dir() writes
    into the real data/ -- the network harmony tests did exactly that, and a
    forgotten monkeypatch once left stray job directories in production.
    """
    import paths

    data = tmp_path / "_data"
    monkeypatch.setattr(paths, "DATA", data)
    monkeypatch.setattr(paths, "SOURCES", data / "sources")
    monkeypatch.setattr(paths, "JOBS", data / "jobs")
