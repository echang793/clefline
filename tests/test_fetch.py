"""Link parsing and match ranking. Network calls are marked and skipped."""

import pytest

from fetch import SPOTIFY_TRACK, YT_ID, Candidate, _norm, resolve


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?list=RD&v=dQw4w9WgXcQ&index=2", "dQw4w9WgXcQ"),
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
])
def test_youtube_ids_are_extracted(url, expected):
    assert YT_ID.search(url).group(1) == expected


@pytest.mark.parametrize("url", [
    "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
    "https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT?si=abc",
])
def test_spotify_ids_are_extracted(url):
    assert SPOTIFY_TRACK.search(url).group(1) == "4cOdK2wGLETKBW3PvgPWqT"


def test_an_unrelated_link_is_rejected_clearly():
    with pytest.raises(Exception) as caught:
        resolve("https://example.com/song.mp3")
    assert "YouTube or Spotify" in str(caught.value)


@pytest.mark.parametrize("raw,expected", [
    ("Song Name (Official Video)", "song name"),
    ("Song Name [Official Audio]", "song name"),
    ("Sóng Nàme!!", "song name"),
    ("Song   Name  4K", "song name 4k"),
])
def test_titles_normalise_for_comparison(raw, expected):
    assert _norm(raw) == expected


def test_candidate_serialises_for_the_ui():
    candidate = Candidate("abc12345678", "T", "U", 200.0, "", "url")
    assert candidate.as_dict()["source_id"] == "abc12345678"


@pytest.mark.network
def test_spotify_metadata_is_reachable():
    from fetch import spotify_metadata
    data = spotify_metadata("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    assert data["title"] and data["duration"] > 0
