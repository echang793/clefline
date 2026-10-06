"""The test harness itself: no test may touch real data/, and network tests are opt-in."""

import subprocess
import sys
from pathlib import Path

import paths

REPO = Path(__file__).resolve().parents[1]


def test_data_directories_are_private_to_each_test():
    """Regression: the network harmony tests used to download real audio into
    the production data/sources/ because nothing redirected paths.SOURCES."""
    real = REPO / "data"
    for directory in (paths.DATA, paths.SOURCES, paths.JOBS):
        assert real not in (directory, *directory.parents), f"{directory} is under real data/"


def test_source_dir_writes_land_in_the_private_directory():
    created = paths.source_dir("song-1")
    assert paths.SOURCES in created.parents
    assert REPO / "data" not in created.parents


def test_network_tests_are_skipped_unless_asked_for():
    """pytest.ini always claimed "skipped unless --run-network is passed", but
    nothing implemented it, so offline runs failed on real YouTube calls."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(REPO / "tests" / "test_fetch.py"),
         "-q", "-p", "no:cacheprovider", "-k", "spotify_metadata_is_reachable"],
        capture_output=True, text=True, cwd=REPO, timeout=120,
    )
    assert "1 skipped" in result.stdout, result.stdout + result.stderr
