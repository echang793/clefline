"""Filesystem layout and the small helpers every stage uses to read/write artifacts.

Two roots, mirroring the two-phase pipeline:

    data/sources/<source_id>/   audio + stems + grid + harmony, cached per song
    data/jobs/<job_id>/         one instrument's notation, cheap to rebuild

Every stage writes exactly one artifact and checks for it before running, so
deleting an artifact re-runs only that stage.

Ids arrive from the network, so they are validated before they become a path:
`source_dir`/`job_dir`/`job_path` accept only `[A-Za-z0-9_-]{1,64}` and refuse
anything that resolves outside its root (a symlink, say). Only `source_dir` and
`job_dir` create directories; `job_path` is for reads, which must never do so.
"""

import json
import re
import shutil
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(config.DATA_DIR).expanduser().resolve() if config.DATA_DIR else ROOT / "data"
SOURCES = DATA / "sources"
JOBS = DATA / "jobs"

STEM_NAMES = ("vocals", "drums", "bass", "guitar", "piano", "other")
PARTS = ("sax", "keys", "drums")

SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class InvalidId(ValueError):
    """An id that is not safe to use as a directory name."""


class LowDisk(RuntimeError):
    """Not enough free space to start work that writes gigabytes."""


def _checked(root: Path, ident: str) -> Path:
    if not isinstance(ident, str) or not SAFE_ID.fullmatch(ident):
        raise InvalidId("That id is not valid.")
    path = root / ident
    if path.resolve().parent != root.resolve():
        raise InvalidId("That id is not valid.")
    return path


def source_dir(source_id: str) -> Path:
    d = _checked(SOURCES, source_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_dir(job_id: str) -> Path:
    d = _checked(JOBS, job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_path(job_id: str) -> Path:
    """Where a job's directory is (or would be). Never creates it."""
    return _checked(JOBS, job_id)


def require_free_space() -> None:
    """Raise LowDisk unless the data disk has config.MIN_FREE_GB free."""
    probe = DATA
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free_gb = shutil.disk_usage(probe).free / 1e9
    if free_gb < config.MIN_FREE_GB:
        raise LowDisk(
            f"Only {free_gb:.1f} GB free on the data disk; at least {config.MIN_FREE_GB} GB "
            "is needed to transcribe a song. Free some space (scripts/cleanup.py) and try again."
        )


def scrub(text: str) -> str:
    """Hide this machine's filesystem layout in text bound for the browser."""
    return text.replace(str(ROOT), "<clefline>").replace(str(Path.home()), "~")


def read_json(path: Path) -> dict | None:
    """Return the parsed artifact, or None if it is missing or half-written."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: dict) -> None:
    """Write atomically so a crashed stage never leaves a readable-but-wrong artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)
