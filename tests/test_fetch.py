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


def _score(**kwargs):
    from fetch import _score_candidate
    defaults = dict(
        wanted="the beatles let it be", entry_title="Let It Be (Remastered 2009)",
        uploader="The Beatles - Topic", artist="The Beatles",
        duration=243.0, entry_duration=243.0,
    )
    return _score_candidate(**{**defaults, **kwargs})


def test_a_live_recording_loses_to_the_studio_match_despite_a_close_duration():
    """Regression: a live take with a near-identical duration and a title that
    still overlaps heavily on text used to be scored as good as the real thing."""
    studio_score, _ = _score()
    live_score, note = _score(
        entry_title="Let It Be (Live at Wembley)", uploader="Some Fan Channel",
        entry_duration=241.0,
    )
    assert live_score < studio_score
    assert "live" in note


@pytest.mark.parametrize("word", ["cover", "remix", "karaoke", "acoustic", "8d audio"])
def test_alternate_version_words_are_penalized(word):
    score, note = _score(entry_title=f"Let It Be ({word})", uploader="Some Channel")
    baseline, _ = _score()
    assert score < baseline
    assert word in note


def test_a_version_word_is_not_penalized_when_it_was_actually_requested():
    """If the Spotify track itself is the live/acoustic/etc. version, a
    YouTube upload saying so is the right match, not a red flag."""
    plain, _ = _score(entry_title="Let It Be (Live at Wembley)", uploader="Some Channel")
    requested, note = _score(
        wanted="the beatles let it be live", entry_title="Let It Be (Live at Wembley)",
        uploader="Some Channel",
    )
    assert requested > plain
    assert "titled" not in note


def test_the_auto_generated_topic_channel_gets_a_bonus():
    fan, _ = _score(uploader="Some Random Beatles Fan Page")
    topic, note = _score(uploader="The Beatles - Topic")
    assert topic > fan
    assert "official" in note


@pytest.mark.network
def test_spotify_metadata_is_reachable():
    from fetch import spotify_metadata
    data = spotify_metadata("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    assert data["title"] and data["duration"] > 0
