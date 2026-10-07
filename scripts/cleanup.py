#!/usr/bin/env python3
"""Purge old job and source directories -- data/ has no expiry otherwise.

Jobs are cheap to regenerate (quantize/score/engrave off a cached song, ~20s),
so they get a short grace period. Sources hold the expensive part (download +
demucs separation, minutes), so they get a much longer one -- purging a source
you'll want again next week is a worse mistake than a slightly bigger disk.

Never touches a job that is queued or running, nor a song such a job is working
on. Also clears two kinds of litter: `_demucs` scratch directories left by a
separation that was killed, and empty job directories.

Defaults to a dry run: prints what it would delete and touches nothing. Pass
--yes to actually delete.

    .venv/bin/python scripts/cleanup.py                  # preview, default ages
    .venv/bin/python scripts/cleanup.py --yes             # actually delete
    .venv/bin/python scripts/cleanup.py --job-days 3 --yes
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paths import JOBS, SOURCES, read_json  # noqa: E402

DEFAULT_JOB_DAYS = 14.0
DEFAULT_SOURCE_DAYS = 60.0

ACTIVE_STATES = ("queued", "running")
# A job directory exists for a moment before its status.json does; don't call
# one empty (and delete it) until it has clearly been abandoned.
EMPTY_GRACE_HOURS = 1.0


def age_days(path: Path, now: float | None = None) -> float:
    """Days since the newest file anywhere under `path` was last modified.

    Using the newest file rather than the directory's own mtime means a source
    that was only re-touched for a second instrument (a new file dropped into
    an existing directory) counts as recently used, not stale.
    """
    now = time.time() if now is None else now
    newest = max(
        (f.stat().st_mtime for f in path.rglob("*") if f.is_file()),
        default=path.stat().st_mtime,
    )
    return (now - newest) / 86400


def find_stale(root: Path, max_age_days: float, now: float | None = None) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        d for d in root.iterdir() if d.is_dir() and age_days(d, now) > max_age_days
    )


def active_work() -> tuple[set[str], set[str]]:
    """(job ids, source ids) of everything currently queued or running."""
    jobs, sources = set(), set()
    if JOBS.is_dir():
        for directory in JOBS.iterdir():
            status = read_json(directory / "status.json") if directory.is_dir() else None
            if status and status.get("state") in ACTIVE_STATES:
                jobs.add(directory.name)
                if status.get("source_id"):
                    sources.add(status["source_id"])
    return jobs, sources


def find_empty_jobs(now: float | None = None) -> list[Path]:
    now = time.time() if now is None else now
    if not JOBS.is_dir():
        return []
    return sorted(
        d for d in JOBS.iterdir()
        if d.is_dir() and not any(f.is_file() for f in d.rglob("*"))
        and (now - d.stat().st_mtime) / 3600 > EMPTY_GRACE_HOURS
    )


def find_scratch(active_sources: set[str]) -> list[Path]:
    """Leftover `_demucs` work directories of separations that are not running."""
    if not SOURCES.is_dir():
        return []
    return sorted(
        d / "_demucs" for d in SOURCES.iterdir()
        if d.is_dir() and (d / "_demucs").is_dir() and d.name not in active_sources
    )


def _describe(path: Path, is_job: bool) -> str:
    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    title = ""
    if is_job and (status := read_json(path / "status.json")):
        title = status.get("meta", {}).get("title", "")
    label = "job" if is_job else "source"
    suffix = f"  -- {title}" if title else ""
    return f"{label:6s} {path.name}  ({size / 1e6:.1f} MB, {age_days(path):.0f}d old){suffix}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--job-days", type=float, default=DEFAULT_JOB_DAYS,
        help=f"purge jobs untouched this many days (default {DEFAULT_JOB_DAYS:g})",
    )
    parser.add_argument(
        "--source-days", type=float, default=DEFAULT_SOURCE_DAYS,
        help=f"purge sources untouched this many days (default {DEFAULT_SOURCE_DAYS:g})",
    )
    parser.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    args = parser.parse_args(argv)

    active_jobs, active_sources = active_work()
    stale_jobs = [d for d in find_stale(JOBS, args.job_days) if d.name not in active_jobs]
    stale_sources = [
        d for d in find_stale(SOURCES, args.source_days) if d.name not in active_sources
    ]
    empty_jobs = [d for d in find_empty_jobs() if d.name not in active_jobs]
    scratch = find_scratch(active_sources)

    if not (stale_jobs or stale_sources or empty_jobs or scratch):
        print("Nothing to clean up.")
        return 0

    verb = "DELETE" if args.yes else "would delete"
    for path in stale_jobs:
        print(f"{verb}  {_describe(path, is_job=True)}")
    for path in stale_sources:
        print(f"{verb}  {_describe(path, is_job=False)}")
    for path in empty_jobs:
        print(f"{verb}  empty job directory {path.name}")
    for path in scratch:
        print(f"{verb}  leftover separation scratch {path.parent.name}/_demucs")

    everything = (*stale_jobs, *stale_sources, *empty_jobs, *scratch)
    if args.yes:
        for path in everything:
            shutil.rmtree(path, ignore_errors=True)
        print(f"\nDeleted {len(everything)} item(s).")
    else:
        print(f"\n{len(everything)} item(s) would be deleted. "
              "Re-run with --yes to actually delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
