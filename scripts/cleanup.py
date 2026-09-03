#!/usr/bin/env python3
"""Purge old job and source directories -- data/ has no expiry otherwise.

Jobs are cheap to regenerate (quantize/score/engrave off a cached song, ~20s),
so they get a short grace period. Sources hold the expensive part (download +
demucs separation, minutes), so they get a much longer one -- purging a source
you'll want again next week is a worse mistake than a slightly bigger disk.

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

    stale_jobs = find_stale(JOBS, args.job_days)
    stale_sources = find_stale(SOURCES, args.source_days)

    if not stale_jobs and not stale_sources:
        print("Nothing to clean up.")
        return 0

    verb = "DELETE" if args.yes else "would delete"
    for path in stale_jobs:
        print(f"{verb}  {_describe(path, is_job=True)}")
    for path in stale_sources:
        print(f"{verb}  {_describe(path, is_job=False)}")

    if args.yes:
        for path in (*stale_jobs, *stale_sources):
            shutil.rmtree(path)
        print(f"\nDeleted {len(stale_jobs) + len(stale_sources)} item(s).")
    else:
        print(f"\n{len(stale_jobs) + len(stale_sources)} item(s) would be deleted. "
              "Re-run with --yes to actually delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
