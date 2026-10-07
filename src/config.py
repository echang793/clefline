"""Runtime settings, read from the environment once at import.

Every knob is a `CLEFLINE_`-prefixed variable with a default that suits one person
running this on their own machine. There are no secrets here, so there is nothing
to keep out of the repo; `.env.example` lists them all.

A bad value stops startup with a message naming the variable, rather than
surfacing later as a confusing failure.
"""

import os

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _int(name: str, default: int, low: int | None = None, high: int | None = None) -> int:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a whole number, got {raw!r}") from None
    if (low is not None and value < low) or (high is not None and value > high):
        raise SystemExit(f"{name} must be between {low} and {high}, got {value}")
    return value


def _list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def _level(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip().upper() or default
    if value not in LOG_LEVELS:
        raise SystemExit(f"{name} must be one of {', '.join(LOG_LEVELS)}, got {value!r}")
    return value


# Where the server listens. Loopback by default: there is no authentication, so
# anything wider is a deliberate choice (and preflight says so loudly).
HOST = os.environ.get("CLEFLINE_HOST", "").strip() or "127.0.0.1"
PORT = _int("CLEFLINE_PORT", 8104, low=1, high=65535)

LOG_LEVEL = _level("CLEFLINE_LOG_LEVEL", "INFO")

# Where audio, stems, jobs and logs live. Empty means `data/` inside the repo.
DATA_DIR = os.environ.get("CLEFLINE_DATA_DIR", "").strip()

# Longest recording we will download and transcribe. A 10-hour upload would
# otherwise download, then run demucs until its own timeout.
MAX_DURATION_SECONDS = _int("CLEFLINE_MAX_DURATION_SECONDS", 15 * 60, low=1)

# Refuse to start a download or separation with less than this much free disk:
# a run fills the disk with FLAC stems, and a full disk corrupts everything
# else the machine is doing. Whole gigabytes.
MIN_FREE_GB = _int("CLEFLINE_MIN_FREE_GB", 3, low=0)

# Host headers the API answers to. The server binds 127.0.0.1, but a hostile web
# page can still point its own DNS name at 127.0.0.1 and drive the API from the
# user's browser (DNS rebinding); refusing foreign Host headers closes that.
# IPv6 (`[::1]`) is not listed: Starlette's host check splits on ":" and cannot
# match a bracketed address. Add more with the CLEFLINE_ALLOWED_HOSTS variable
# (comma separated).
ALLOWED_HOSTS = ["127.0.0.1", "localhost", *_list("CLEFLINE_ALLOWED_HOSTS")]
