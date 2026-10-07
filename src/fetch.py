"""Link -> audio.

YouTube is the only real audio path. Spotify's API serves metadata and nothing
else, so a Spotify link is resolved to title/artist/duration and then *matched*
to a YouTube upload, which the user confirms before anything downloads.

Nothing here reads or stores lyrics; the audio is the only payload.
"""

import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

import httpx
import soundfile as sf

import config
from paths import source_dir

YT_ID = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/))([A-Za-z0-9_-]{11})"
)
SPOTIFY_TRACK = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

# Audio-only streams are a few MB a minute; this is a backstop, not a budget.
MAX_DOWNLOAD_SIZE = "250M"

# A YouTube upload within this many seconds of the Spotify duration is the same
# recording; anything further out is a live cut, a remix, or an hour-long loop.
DURATION_TOLERANCE = 4.0


class FetchError(RuntimeError):
    pass


@dataclass
class Candidate:
    """One YouTube upload that might be the requested recording."""

    source_id: str          # YouTube video id, and the cache key for the whole song
    title: str
    uploader: str
    duration: float
    thumbnail: str
    url: str
    score: float = 1.0      # 0-1 confidence that this matches the requested track
    note: str = ""          # why it was picked, shown in the confirm step

    def as_dict(self) -> dict:
        return asdict(self)


# Always the venv's yt-dlp, never whatever is on PATH: YouTube breaks older
# builds regularly, and a stale system copy returns HTTP 403 on the media URL
# while still resolving metadata perfectly, which is a confusing way to fail.
YTDLP = [sys.executable, "-m", "yt_dlp"]


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _too_long(seconds: float | None = None) -> FetchError:
    limit = _clock(config.MAX_DURATION_SECONDS)
    how_long = f"is {_clock(seconds)} long -- " if seconds else "is too long -- "
    return FetchError(f"That recording {how_long}the limit is {limit}.")


def _run_ytdlp(args: list[str]) -> dict:
    try:
        proc = subprocess.run(
            [*YTDLP, *args], capture_output=True, text=True, timeout=180
        )
    except subprocess.TimeoutExpired:
        raise FetchError("Looking that up timed out -- try again.") from None
    if proc.returncode != 0:
        lines = proc.stderr.strip().splitlines()
        raise FetchError(f"yt-dlp failed: {lines[-1] if lines else 'no details'}")
    return json.loads(proc.stdout)


def _norm(text: str) -> str:
    """Fold a title down to comparable words: no accents, no punctuation, no noise."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\((?:official|lyric|audio|video|hd|4k)[^)]*\)", " ", text)
    text = re.sub(r"\[(?:official|lyric|audio|video|hd|4k)[^\]]*\]", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def spotify_metadata(url: str) -> dict:
    """Track name, artist and duration from Spotify's public page. No API key.

    `og:description` is formatted "Artist / Album / Song / Year"; the first
    segment is the artist. `music:duration` is whole seconds.
    """
    match = SPOTIFY_TRACK.search(url)
    if not match:
        raise FetchError("Not a Spotify track link.")
    with httpx.Client(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as client:
        html = client.get(f"https://open.spotify.com/track/{match.group(1)}").text

    def meta(attr: str, value: str) -> str:
        found = re.search(
            rf'<meta[^>]+{attr}="{re.escape(value)}"[^>]+content="([^"]*)"', html
        ) or re.search(
            rf'<meta[^>]+content="([^"]*)"[^>]+{attr}="{re.escape(value)}"', html
        )
        return found.group(1) if found else ""

    title = meta("property", "og:title")
    description = meta("property", "og:description")
    artist = description.split("·")[0].strip() if description else ""
    raw_duration = meta("name", "music:duration")
    if not title:
        raise FetchError("Spotify page gave no track title — is the link public?")
    return {
        "title": title,
        "artist": artist,
        "duration": float(raw_duration) if raw_duration else 0.0,
        "thumbnail": meta("property", "og:image"),
    }


# Words that mark an upload as something other than the studio recording. A
# live take or cover can have a near-identical title and a coincidentally
# close duration, which used to be enough to win -- Spotify's own track is the
# studio single, so its title carries none of these words, and a candidate
# whose title does is almost never the right match.
_ALTERNATE_VERSION_WORDS = frozenset({
    "live", "cover", "remix", "acoustic", "instrumental", "karaoke", "tribute",
    "reaction", "slowed", "sped up", "nightcore", "8d audio", "type beat",
    "parody", "piano version", "reverb", "extended", "mashup", "megamix",
})
_ALTERNATE_VERSION_PENALTY = 0.55

# YouTube auto-generates a "<Artist> - Topic" channel from official audio
# delivered to streaming services -- the same master Spotify plays. It is the
# single strongest signal available that a candidate is the real recording
# rather than a fan upload, cover, or reaction video.
_OFFICIAL_CHANNEL_BONUS = 0.10


def _matched_version_words(text: str) -> frozenset[str]:
    normalized = f" {_norm(text)} "
    return frozenset(word for word in _ALTERNATE_VERSION_WORDS if f" {word} " in normalized)


def _score_candidate(
    *, wanted: str, entry_title: str, uploader: str, artist: str,
    duration: float, entry_duration: float,
) -> tuple[float, str]:
    """The ranking formula, factored out so it can be tested without a network call."""
    text_score = SequenceMatcher(
        None, wanted, _norm(f"{uploader} {entry_title}")
    ).ratio()

    if duration and entry_duration:
        delta = abs(entry_duration - duration)
        # Full credit inside the tolerance, then falling off to nothing at 30s.
        duration_score = (
            1.0 if delta <= DURATION_TOLERANCE
            else max(0.0, 1 - (delta - DURATION_TOLERANCE) / 30)
        )
        note = f"{delta:.0f}s from the Spotify duration"
    else:
        duration_score, note = 0.5, "duration unknown"

    # Duration is the stronger signal: titles are noisy, length is not.
    score = 0.4 * text_score + 0.6 * duration_score

    # A word like "live" only counts against a candidate if the requested
    # track itself isn't already that version -- if you asked Spotify for the
    # live recording, a YouTube upload saying so is the right match, not a
    # mismatch.
    flagged = _matched_version_words(entry_title) - _matched_version_words(wanted)
    if flagged:
        score *= _ALTERNATE_VERSION_PENALTY
        note = f"{note}, titled \"{next(iter(flagged))}\""

    if _norm(uploader).replace(" topic", "") == _norm(artist) and uploader.endswith("- Topic"):
        score += _OFFICIAL_CHANNEL_BONUS
        note = f"{note}, official audio channel"

    return round(score, 3), note


def search_youtube(title: str, artist: str, duration: float, limit: int = 5) -> list[Candidate]:
    """Rank YouTube uploads against a known track. Best match first."""
    query = f"{artist} {title} audio".strip()
    data = _run_ytdlp(["-J", "--flat-playlist", "--no-warnings", f"ytsearch{limit}:{query}"])
    wanted = _norm(f"{artist} {title}")

    candidates: list[Candidate] = []
    for entry in data.get("entries") or []:
        if not entry.get("id"):
            continue
        entry_title = entry.get("title") or ""
        uploader = entry.get("uploader") or entry.get("channel") or ""
        entry_duration = float(entry.get("duration") or 0)

        score, note = _score_candidate(
            wanted=wanted, entry_title=entry_title, uploader=uploader, artist=artist,
            duration=duration, entry_duration=entry_duration,
        )

        candidates.append(
            Candidate(
                source_id=entry["id"],
                title=entry_title,
                uploader=uploader,
                duration=entry_duration,
                thumbnail=(entry.get("thumbnails") or [{}])[-1].get("url", ""),
                url=f"https://www.youtube.com/watch?v={entry['id']}",
                score=score,
                note=note,
            )
        )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def youtube_metadata(video_id: str) -> Candidate:
    data = _run_ytdlp(["-J", "--no-warnings", f"https://www.youtube.com/watch?v={video_id}"])
    return Candidate(
        source_id=video_id,
        title=data.get("title") or "",
        uploader=data.get("uploader") or data.get("channel") or "",
        duration=float(data.get("duration") or 0),
        thumbnail=data.get("thumbnail") or "",
        url=f"https://www.youtube.com/watch?v={video_id}",
        note="direct link",
    )


def resolve(url: str) -> dict:
    """Link -> the candidate to transcribe, plus alternatives for a Spotify match.

    Returns a dict the UI shows in the confirm step; nothing is downloaded yet.
    """
    url = url.strip()
    if match := YT_ID.search(url):
        chosen = youtube_metadata(match.group(1))
        if chosen.duration > config.MAX_DURATION_SECONDS:
            raise _too_long(chosen.duration)
        return {
            "kind": "youtube", "chosen": chosen.as_dict(),
            "alternatives": [], "requested": None,
        }

    if SPOTIFY_TRACK.search(url):
        requested = spotify_metadata(url)
        candidates = search_youtube(requested["title"], requested["artist"], requested["duration"])
        if not candidates:
            raise FetchError("No YouTube upload found for that Spotify track.")
        within_cap = [c for c in candidates if c.duration <= config.MAX_DURATION_SECONDS]
        if not within_cap:
            raise _too_long(candidates[0].duration)
        candidates = within_cap
        return {
            "kind": "spotify",
            "requested": requested,
            "chosen": candidates[0].as_dict(),
            "alternatives": [c.as_dict() for c in candidates[1:]],
        }

    raise FetchError("Paste a YouTube or Spotify track link.")


def download(source_id: str, force: bool = False):
    """Fetch the audio as 44.1k mono FLAC. Cached — the second call is free.

    FLAC over WAV is a pure disk-space win (lossless, ~40-50% smaller) that
    every downstream reader (librosa, soundfile, demucs) handles transparently.

    The length cap is enforced here, not only in resolve(): POST /api/jobs can
    skip the resolve step, so this is the gate that actually protects the disk
    and the (hour-long) separation stage.
    """
    directory = source_dir(source_id)
    audio_path = directory / "audio.flac"
    if audio_path.exists() and not force:
        return audio_path

    try:
        proc = subprocess.run(
            [
                *YTDLP, "-f", "bestaudio/best", "--no-warnings", "--no-playlist",
                "--match-filter", f"duration<={config.MAX_DURATION_SECONDS}",
                "--max-filesize", MAX_DOWNLOAD_SIZE,
                "-x", "--audio-format", "flac",
                "--postprocessor-args", "ExtractAudio:-ac 1 -ar 44100",
                "-o", str(directory / "audio.%(ext)s"),
                f"https://www.youtube.com/watch?v={source_id}",
            ],
            capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        raise FetchError("The download timed out after 15 minutes -- try again.") from None

    if not audio_path.exists():
        # yt-dlp exits 0 and prints this when --match-filter skips the video.
        if "does not pass filter" in proc.stdout + proc.stderr:
            raise _too_long()
        raise FetchError(f"Download produced no audio: {proc.stderr.strip()[-400:]}")

    # Belt and braces: a filter on metadata can be wrong about the real stream.
    duration = sf.info(str(audio_path)).duration
    if duration > config.MAX_DURATION_SECONDS:
        audio_path.unlink()
        raise _too_long(duration)
    return audio_path
