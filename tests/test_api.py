"""The HTTP API: input validation, no side effects on reads, no internals leaked."""

import json
import queue
import time

import pytest
from fastapi.testclient import TestClient

import paths
import pipeline
import server

client = TestClient(server.app, base_url="http://127.0.0.1:8104")

VALID = {"source_id": "QDYfEBY9NM4", "part": "sax", "meta": {"title": "t"}, "options": {}}


@pytest.fixture(autouse=True)
def _no_worker(monkeypatch):
    monkeypatch.setattr(pipeline, "_ensure_worker", lambda: None)
    monkeypatch.setattr(pipeline, "_work", queue.Queue())
    monkeypatch.setattr(pipeline, "_cancelled", set())


def _post(**changes):
    return client.post("/api/jobs", json={**VALID, **changes})


# ------------------------------------------------------------------ source_id

@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "../x", "QDYfEBY9NM", "QDYfEBY9NM4x", "QDYfEBY9NM!", "", "a b c d e f g",
    "QDYfEBY9NM4&list=PL", "QDYfEBY9/M4",
])
def test_a_malformed_source_id_is_rejected_and_touches_nothing(bad):
    """Regression: only /api/resolve checked the id; POST /api/jobs accepted
    anything, and paths.source_dir() mkdir'd SOURCES/<id> -- so "../../x"
    created directories outside data/."""
    response = _post(source_id=bad)
    assert response.status_code == 422
    assert not paths.SOURCES.exists()
    assert not paths.JOBS.exists()


def test_a_valid_request_creates_a_queued_job():
    response = _post()
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["state"] == "queued"
    assert status["source_id"] == "QDYfEBY9NM4"


# --------------------------------------------------------------------- options

@pytest.mark.parametrize("options", [
    {"subdivision": 0},          # used to be a ZeroDivisionError minutes into a job
    {"subdivision": 5},
    {"bpm": 5},
    {"bpm": 9999},
    {"sharps": 12},
    {"sharps": -8},
    {"time_signature": "7/8"},
    {"time_signature": "garbage"},
    {"page_size": "legal"},      # used to be rejected only after the whole transcription
    {"swing": "maybe"},
    {"unknown_key": 1},
])
def test_out_of_range_options_are_rejected_up_front(options):
    assert _post(options=options).status_code == 422


def test_the_options_the_ui_actually_sends_are_accepted():
    options = {"subdivision": 4, "page_size": "letter", "sharps": -3, "bpm": 118.5,
               "time_signature": "6/8"}
    assert _post(options=options).status_code == 200


def test_an_invalid_part_is_rejected():
    assert _post(part="trombone").status_code == 422


def test_meta_keeps_only_known_fields_and_caps_their_size():
    response = _post(meta={
        "title": "Song", "uploader": "Band", "duration": 200.0,
        "thumbnail": "https://i.ytimg.com/x.jpg", "score": 0.9, "note": "n",
        "evil": "<script>", "title_extra": "x",
    })
    assert response.status_code == 200
    meta = client.get(f"/api/jobs/{response.json()['job_id']}").json()["meta"]
    assert set(meta) <= {"title", "uploader", "duration", "thumbnail", "url"}
    assert meta["title"] == "Song"

    assert _post(meta={"title": "x" * 5000}).status_code == 422


def test_a_non_https_thumbnail_is_dropped_not_stored():
    response = _post(meta={"title": "t", "thumbnail": "javascript:alert(1)"})
    assert response.status_code == 200
    meta = client.get(f"/api/jobs/{response.json()['job_id']}").json()["meta"]
    assert not meta.get("thumbnail")


# ------------------------------------------------------ reads have no side effects

@pytest.mark.parametrize("path", [
    "/api/jobs/zzzzzzzzzzzz",
    "/api/jobs/zzzzzzzzzzzz/page/1",
    "/api/jobs/zzzzzzzzzzzz/file/pdf",
    "/api/jobs/a.b",
    "/api/jobs/a.b/page/1",
])
def test_reading_an_unknown_job_is_a_404_and_creates_no_directory(path):
    """Regression: every GET called job_dir(), which mkdir'd data/jobs/<id>."""
    assert client.get(path).status_code == 404
    assert not paths.JOBS.exists() or not any(paths.JOBS.iterdir())


# ----------------------------------------------------------- nothing leaks out

def test_tracebacks_and_absolute_paths_never_reach_the_client():
    paths.JOBS.mkdir(parents=True)
    (paths.JOBS / "broken").mkdir()
    (paths.JOBS / "broken" / "status.json").write_text(json.dumps({
        "job_id": "broken", "state": "error", "created": time.time(),
        "message": "boom", "traceback": f"Traceback ... {paths.ROOT}/src/pipeline.py line 1",
    }))
    for url in ("/api/jobs/broken", "/api/jobs"):
        body = client.get(url).text
        assert "traceback" not in body.lower()
        assert str(paths.ROOT) not in body


def test_resolve_hides_internal_errors(monkeypatch):
    def boom(url):
        raise OSError(f"{paths.ROOT}/secret/path exploded")

    monkeypatch.setattr(server.fetch, "resolve", boom)
    response = client.post("/api/resolve", json={"url": "https://youtu.be/QDYfEBY9NM4"})
    assert response.status_code == 502
    assert str(paths.ROOT) not in response.text


def test_resolve_still_shows_the_reason_for_a_bad_link(monkeypatch):
    def refuse(url):
        raise server.fetch.FetchError("Paste a YouTube or Spotify track link.")

    monkeypatch.setattr(server.fetch, "resolve", refuse)
    response = client.post("/api/resolve", json={"url": "nope"})
    assert response.status_code == 400
    assert "YouTube" in response.json()["detail"]


def test_resolve_rejects_an_absurdly_long_url():
    assert client.post("/api/resolve", json={"url": "x" * 5000}).status_code == 422


def test_resolve_is_capped_to_a_few_concurrent_lookups(monkeypatch):
    """Each resolve forks a yt-dlp process (up to 3 minutes); a flurry must
    not fork unboundedly."""
    held = []
    while server._resolve_slots.acquire(blocking=False):
        held.append(1)
    try:
        response = client.post("/api/resolve", json={"url": "https://youtu.be/QDYfEBY9NM4"})
        assert response.status_code == 429
    finally:
        for _ in held:
            server._resolve_slots.release()


# --------------------------------------------------------- host + headers

def test_a_foreign_host_header_is_refused():
    """DNS rebinding: a hostile page resolving evil.example to 127.0.0.1 could
    otherwise drive this API from the user's browser."""
    assert client.get("/healthz", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/healthz", headers={"host": "127.0.0.1:8104"}).status_code == 200
    assert client.get("/healthz", headers={"host": "localhost:8104"}).status_code == 200


def test_security_headers_are_set():
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_a_finished_jobs_status_exposes_no_filesystem_paths():
    """Regression: render() records absolute paths for every artifact, and the
    status API passed them straight through. The UI only needs the page count
    (files are fetched through /api/jobs/<id>/file/<name>)."""
    paths.JOBS.mkdir(parents=True)
    (paths.JOBS / "finished").mkdir()
    root = paths.ROOT / "data" / "jobs" / "finished"
    (paths.JOBS / "finished" / "status.json").write_text(json.dumps({
        "job_id": "finished", "state": "done", "created": time.time(), "message": "Ready",
        "artifacts": {
            "pages": [f"{root}/page-1.svg", f"{root}/page-2.svg"], "page_count": 2,
            "pdf": f"{root}/score.pdf", "midi": f"{root}/score.mid",
            "musicxml": f"{root}/score.musicxml",
        },
    }))
    for url in ("/api/jobs/finished", "/api/jobs"):
        response = client.get(url)
        assert str(paths.ROOT) not in response.text
        assert "/Users/" not in response.text
    assert client.get("/api/jobs/finished").json()["artifacts"] == {"page_count": 2}


# ------------------------------------------------------------------- cancelling

def test_cancelling_a_queued_job_over_http():
    job_id = _post().json()["job_id"]
    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert client.get(f"/api/jobs/{job_id}").json()["state"] == "cancelled"


def test_cancelling_an_unknown_job_is_a_404_and_creates_nothing():
    assert client.post("/api/jobs/zzzzzzzzzzzz/cancel").status_code == 404
    assert client.post("/api/jobs/a.b/cancel").status_code == 404
    assert not paths.JOBS.exists() or not any(paths.JOBS.iterdir())


def test_cancelling_a_finished_job_is_a_409():
    paths.JOBS.mkdir(parents=True)
    (paths.JOBS / "finished").mkdir()
    (paths.JOBS / "finished" / "status.json").write_text(json.dumps({
        "job_id": "finished", "state": "done", "created": time.time(),
    }))
    response = client.post("/api/jobs/finished/cancel")
    assert response.status_code == 409
    assert client.get("/api/jobs/finished").json()["state"] == "done"


def test_a_queued_status_says_how_many_jobs_are_ahead():
    first = _post(source_id="AAAAAAAAAAA").json()["job_id"]
    second = _post(source_id="BBBBBBBBBBB").json()["job_id"]
    assert client.get(f"/api/jobs/{first}").json()["ahead"] == 0
    assert client.get(f"/api/jobs/{second}").json()["ahead"] == 1


# --------------------------------------------------------------------- healthz

def test_healthz_reports_each_check_and_the_queue():
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    for name in ("ffmpeg", "data directory", "disk space", "worker"):
        assert body["checks"][name]["ok"] is True, body["checks"][name]
    assert body["queued"] == 0 and body["running"] is False


def test_healthz_counts_waiting_jobs():
    _post(source_id="AAAAAAAAAAA")
    _post(source_id="BBBBBBBBBBB")
    assert client.get("/healthz").json()["queued"] == 2


def test_healthz_is_503_when_ffmpeg_is_missing(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["checks"]["ffmpeg"]["ok"] is False


def test_healthz_is_503_when_the_disk_is_full(monkeypatch):
    import config

    monkeypatch.setattr(config, "MIN_FREE_GB", 10**6)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["checks"]["disk space"]["ok"] is False


def test_healthz_is_503_when_the_worker_died_with_jobs_waiting(monkeypatch):
    class Dead:
        def is_alive(self):
            return False

    monkeypatch.setattr(pipeline, "_worker", Dead())
    _post()
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["checks"]["worker"]["ok"] is False


def test_healthz_is_fine_when_the_worker_has_not_started_and_nothing_waits():
    assert client.get("/healthz").json()["checks"]["worker"]["ok"] is True


def test_healthz_exposes_no_paths():
    assert "/Users/" not in client.get("/healthz").text
