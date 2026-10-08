"""Run the JavaScript unit tests (Node's built-in runner; no npm dependencies)."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_unit_tests_pass():
    # Explicit files, not the directory: `node --test <dir>` means different things
    # on different Node versions.
    files = sorted(str(f) for f in (REPO / "tests" / "js").glob("*.test.js"))
    assert files, "no JS tests found"
    result = subprocess.run(
        ["node", "--test", *files],
        capture_output=True, text=True, cwd=REPO, timeout=120,
    )
    # node --test exits non-zero if any test fails; show its output when that happens.
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
