"""Runtime settings, read from the environment once at import.

Every knob is a CLEFLINE_* variable with a default that suits one person running
this on their own machine. There are no secrets here, so there is nothing to keep
out of the repo; `.env.example` lists them all.
"""

import os


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a whole number, got {raw!r}") from None


def _list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


# Longest recording we will download and transcribe. A 10-hour upload would
# otherwise download, then run demucs until its own timeout.
MAX_DURATION_SECONDS = _int("CLEFLINE_MAX_DURATION_SECONDS", 15 * 60)

# Refuse to start a download or separation with less than this much free disk:
# a run fills the disk with FLAC stems, and a full disk corrupts everything
# else the machine is doing. Whole gigabytes.
MIN_FREE_GB = _int("CLEFLINE_MIN_FREE_GB", 3)

# Host headers the API answers to. The server binds 127.0.0.1, but a hostile web
# page can still point its own DNS name at 127.0.0.1 and drive the API from the
# user's browser (DNS rebinding); refusing foreign Host headers closes that.
# IPv6 (`[::1]`) is not listed: Starlette's host check splits on ":" and cannot
# match a bracketed address. Extend with CLEFLINE_ALLOWED_HOSTS=a.example,b.example.
ALLOWED_HOSTS = ["127.0.0.1", "localhost", *_list("CLEFLINE_ALLOWED_HOSTS")]
