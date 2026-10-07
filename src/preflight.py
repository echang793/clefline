"""Startup and health checks: say what is wrong in words, now, not minutes into a job.

Each check is a `Check`. A *fatal* one means the server cannot do its job at all
(wrong Python, no ffmpeg, unwritable data directory) and startup stops with the
reason. A non-fatal one (low disk, model weights not downloaded yet, listening
beyond loopback) is logged as a warning and shown by /healthz.

Details never contain a filesystem path: /healthz returns them to the browser.
"""

import importlib.util
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import config
import paths

log = logging.getLogger("clefline.preflight")

REQUIRED_PYTHON = (3, 11)
LOOPBACK = ("127.0.0.1", "localhost", "::1")


# The report from the most recent require(), so startup can tell it already ran.
last: list["Check"] | None = None


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fatal: bool = False


def _python() -> Check:
    version = sys.version_info
    found = f"{version[0]}.{version[1]}"
    ok = tuple(version[:2]) == REQUIRED_PYTHON
    wanted = f"{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}"
    return Check("python", ok, f"Python {found}" if ok else
                 f"Python {wanted} is required (basic-pitch has no wheels for newer versions), "
                 f"but this is {found}.", fatal=True)


def _ffmpeg() -> Check:
    found = shutil.which("ffmpeg") is not None
    return Check("ffmpeg", found, "found" if found else
                 "ffmpeg is not on PATH. Install it with: brew install ffmpeg", fatal=True)


def _ytdlp() -> Check:
    try:
        from yt_dlp.version import __version__
    except ImportError:
        return Check("yt-dlp", False, "yt-dlp is not installed in this environment.", fatal=True)
    return Check("yt-dlp", True, f"version {__version__}")


def _demucs() -> Check:
    found = importlib.util.find_spec("demucs") is not None
    return Check("demucs", found,
                 "installed" if found else "demucs is not installed in this environment.",
                 fatal=True)


def _data_directory() -> Check:
    try:
        paths.DATA.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=paths.DATA):
            pass
    except OSError as error:
        return Check("data directory", False,
                     f"The data directory is not writable: {error.strerror}.", fatal=True)
    return Check("data directory", True, "writable")


def _disk_space() -> Check:
    try:
        paths.require_free_space()
    except paths.LowDisk as error:
        return Check("disk space", False, str(error))
    free = shutil.disk_usage(_existing_ancestor(paths.DATA)).free / 1e9
    return Check("disk space", True, f"{free:.0f} GB free")


def _existing_ancestor(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _weights(name: str, found: bool) -> Check:
    return Check(name, found, "cached" if found else
                 "Not downloaded yet -- the first transcription will download it.")


def _demucs_weights() -> Check:
    cache = os.environ.get("HUGGINGFACE_HUB_CACHE") or str(
        Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    )
    pattern = "models--adefossez--HTDemucs-6s/snapshots/*/5c90dfd2.safetensors"
    return _weights("demucs weights", any(Path(cache).glob(pattern)))


def _beat_this_weights() -> Check:
    torch_home = Path(os.environ.get("TORCH_HOME", Path.home() / ".cache" / "torch"))
    return _weights("beat_this weights",
                    (torch_home / "hub" / "checkpoints" / "beat_this-final0.ckpt").exists())


def _binding() -> Check:
    if config.HOST in LOOPBACK:
        return Check("network binding", True, f"listening on {config.HOST} only")
    return Check(
        "network binding", False,
        f"Listening on {config.HOST}: anyone who can reach this machine can use clefline, "
        "and there is no authentication.",
    )


def live() -> list[Check]:
    """The cheap checks that can change while running: safe to run per request."""
    return [_ffmpeg(), _data_directory(), _disk_space()]


def run() -> list[Check]:
    """Every check, in the order they should be read."""
    return [
        _python(), _ffmpeg(), _ytdlp(), _demucs(), _data_directory(), _disk_space(),
        _demucs_weights(), _beat_this_weights(), _binding(),
    ]


def require() -> list[Check]:
    """Run every check; stop startup if a fatal one failed, else log the rest."""
    global last
    checks = run()
    fatal = [c for c in checks if not c.ok and c.fatal]
    if fatal:
        raise SystemExit(
            "clefline cannot start:\n" + "\n".join(f"  - {c.name}: {c.detail}" for c in fatal)
        )
    for check in checks:
        if check.ok:
            log.info("%s: %s", check.name, check.detail)
        else:
            log.warning("%s: %s", check.name, check.detail)
    last = checks
    return checks
