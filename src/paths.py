"""Filesystem layout and the small helpers every stage uses to read/write artifacts.

Two roots, mirroring the two-phase pipeline:

    data/sources/<source_id>/   audio + stems + grid + harmony, cached per song
    data/jobs/<job_id>/         one instrument's notation, cheap to rebuild

Every stage writes exactly one artifact and checks for it before running, so
deleting an artifact re-runs only that stage.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SOURCES = DATA / "sources"
JOBS = DATA / "jobs"

STEM_NAMES = ("vocals", "drums", "bass", "guitar", "piano", "other")
PARTS = ("sax", "keys", "drums")


def source_dir(source_id: str) -> Path:
    d = SOURCES / source_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_dir(job_id: str) -> Path:
    d = JOBS / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


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
