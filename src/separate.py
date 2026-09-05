"""Mixdown -> six stems, via demucs.

htdemucs_6s splits piano and guitar out of `other`, which is what makes a
keyboard part possible at all. The vocal stem is used purely as a pitch source
for the melody — no words are read from it anywhere in this app.

Slowest stage in the pipeline (~1-2 min on MPS for a 4-minute song) and the
reason songs are cached: transcribing a second instrument reuses these files.

Stems are FLAC, not WAV -- lossless, ~40-50% smaller, and every downstream
reader (librosa, soundfile) handles it transparently, so this was a pure
disk-space win. A four-minute song's six stems ran close to 200MB as WAV.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from paths import STEM_NAMES, source_dir

MODEL = "htdemucs_6s"


class SeparationError(RuntimeError):
    pass


def stem_paths(source_id: str) -> dict[str, Path]:
    directory = source_dir(source_id) / "stems"
    return {name: directory / f"{name}.flac" for name in STEM_NAMES}


def is_separated(source_id: str) -> bool:
    return all(path.exists() for path in stem_paths(source_id).values())


def _device() -> str:
    """MPS when the Metal backend is actually usable, else CPU (slow but correct)."""
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def separate(source_id: str, audio: Path, force: bool = False) -> dict[str, Path]:
    targets = stem_paths(source_id)
    if is_separated(source_id) and not force:
        return targets

    directory = source_dir(source_id)
    work = directory / "_demucs"
    work.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        [sys.executable, "-m", "demucs", "-n", MODEL, "-d", _device(), "--flac",
         "--out", str(work), str(audio)],
        capture_output=True, text=True, timeout=3600,
    )

    produced = work / MODEL / audio.stem
    if not produced.is_dir():
        raise SeparationError(
            f"demucs produced no stems (device={_device()}): {proc.stderr.strip()[-500:]}"
        )

    stems_dir = directory / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    for name, target in targets.items():
        made = produced / f"{name}.flac"
        if not made.exists():
            raise SeparationError(f"demucs did not emit a {name} stem")
        shutil.move(str(made), target)

    shutil.rmtree(work, ignore_errors=True)
    return targets
