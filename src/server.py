"""FastAPI app: paste a link, pick an instrument, get a score.

Binds 127.0.0.1 by default. This app downloads audio and engraves it for
personal practice; it is not meant to be exposed beyond this machine. Requests
are still validated and Host-checked, because a hostile web page open in the
same browser can reach a localhost server.
"""

import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator  # noqa: E402

import config  # noqa: E402
import fetch  # noqa: E402
import paths  # noqa: E402
import pipeline  # noqa: E402

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The job queue is in-memory and dies with the process. Anything left
    # "queued" or "running" from before this startup is a job a prior crash
    # or restart orphaned -- mark it interrupted before the (now-empty) queue
    # can be asked to work on anything new.
    if recovered := pipeline.recover_interrupted_jobs():
        log.warning("Marked %d interrupted job(s) from a previous run as failed.", recovered)
    yield


app = FastAPI(title="clefline", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)
STATIC = paths.ROOT / "static"

# Only these names are servable from a job directory, and each maps to one file.
ARTIFACTS = {
    "pdf": ("score.pdf", "application/pdf"),
    "musicxml": ("score.musicxml", "application/vnd.recordare.musicxml+xml"),
    "midi": ("score.mid", "audio/midi"),
}

# Until the web fonts are self-hosted (a later change) the stylesheet and font
# files come from Google; nothing else is allowed off-origin except thumbnails.
CSP = "; ".join([
    "default-src 'self'",
    "img-src 'self' data: https:",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
])


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# --------------------------------------------------------------------- schemas

SourceId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{11}$")]


class ResolveRequest(BaseModel):
    url: str = Field(max_length=500)


class JobMeta(BaseModel):
    """What the UI may tell us about a recording. Anything else is dropped --
    meta is stored verbatim and later rendered, so it is kept small and known."""

    title: str | None = Field(None, max_length=300)
    uploader: str | None = Field(None, max_length=300)
    duration: float | None = Field(None, ge=0, le=86400)
    thumbnail: str | None = Field(None, max_length=600)
    url: str | None = Field(None, max_length=600)

    @field_validator("thumbnail", "url")
    @classmethod
    def _https_only(cls, value):
        # Rendered into an <img src> / link: never anything but https.
        return value if value and value.startswith("https://") else None


class JobOptions(BaseModel):
    """The Adjust panel. Bounds mirror what the pipeline can actually do, so a
    bad value is a 422 now rather than an error minutes into a transcription."""

    model_config = ConfigDict(extra="forbid")

    subdivision: Literal[2, 3, 4] | None = None
    sharps: int | None = Field(None, ge=-7, le=7)
    bpm: float | None = Field(None, ge=30, le=260)
    time_signature: Literal["2/4", "3/4", "4/4", "5/4", "6/8"] | None = None
    page_size: Literal["letter", "a4"] | None = None
    swing: bool | None = None


class JobRequest(BaseModel):
    source_id: SourceId
    part: Literal["sax", "keys", "drums"]
    meta: JobMeta = Field(default_factory=JobMeta)
    options: JobOptions = Field(default_factory=JobOptions)


# ------------------------------------------------------------------------ API

# Each resolve forks a yt-dlp process that can run for minutes.
_resolve_slots = threading.BoundedSemaphore(2)


def _public(status: dict) -> dict:
    """A job status as the browser may see it: no traceback, no machine paths."""
    shown = {k: v for k, v in status.items() if k != "traceback"}
    if isinstance(shown.get("message"), str):
        shown["message"] = paths.scrub(shown["message"])
    if isinstance(shown.get("artifacts"), dict):
        # render() records absolute file paths; the browser fetches files through
        # /api/jobs/<id>/file/<name> and needs only to know how many pages exist.
        shown["artifacts"] = {"page_count": shown["artifacts"].get("page_count", 0)}
    return shown


def _job_directory(job_id: str) -> Path:
    try:
        return paths.job_path(job_id)
    except paths.InvalidId:
        raise HTTPException(status_code=404, detail="No such job") from None


@app.post("/api/resolve")
def resolve(request: ResolveRequest):
    if not _resolve_slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Still looking up other links -- try again.")
    try:
        return fetch.resolve(request.url)
    except fetch.FetchError as error:
        raise HTTPException(status_code=400, detail=paths.scrub(str(error))) from error
    except Exception:
        log.exception("resolve failed for %r", request.url)
        raise HTTPException(
            status_code=502, detail="Could not look that link up right now -- try again."
        ) from None
    finally:
        _resolve_slots.release()


@app.post("/api/jobs")
def create_job(request: JobRequest):
    job_id = pipeline.new_job(
        request.source_id, request.part,
        request.meta.model_dump(exclude_none=True),
        request.options.model_dump(exclude_none=True),
    )
    return {"job_id": job_id}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [_public(job) for job in pipeline.recent()]}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    status = pipeline.get_status(job_id)
    if status.get("state") == "unknown":
        raise HTTPException(status_code=404, detail="No such job")
    return _public(status)


@app.get("/api/jobs/{job_id}/page/{number}")
def job_page(job_id: str, number: int):
    """One engraved SVG page. This is what the on-screen viewer renders."""
    page = _job_directory(job_id) / f"page-{number}.svg"
    if number < 1 or not page.exists():
        raise HTTPException(status_code=404, detail="No such page")
    return FileResponse(page, media_type="image/svg+xml")


@app.get("/api/jobs/{job_id}/file/{name}")
def job_file(job_id: str, name: str):
    if name not in ARTIFACTS:
        raise HTTPException(status_code=404, detail="Unknown artifact")
    filename, media_type = ARTIFACTS[name]
    path = _job_directory(job_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not rendered yet")

    status = pipeline.get_status(job_id)
    title = (status.get("meta", {}).get("title") or "score")[:80]
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip() or "score"
    return FileResponse(path, media_type=media_type,
                        filename=f"{safe} ({status.get('part', 'part')}){path.suffix}")


@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------- PWA
#
# Registered before the catch-all static mount below so these exact paths are
# served here rather than falling through to it — Starlette tries routes in
# registration order, and only /manifest.json, /favicon.ico and /sw.js need a
# route of their own; everything else in static/ is served by the mount as-is.

@app.get("/manifest.json")
def manifest():
    return JSONResponse({
        "name": "clefline",
        "short_name": "clefline",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#e7e5e4",
        "theme_color": "#37f712",
        "description": "Paste a song, pick an instrument, get sheet music.",
        "icons": [
            {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
             "purpose": "any maskable"},
        ],
    })


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("CLEFLINE_HOST", "127.0.0.1"),
        port=int(os.environ.get("CLEFLINE_PORT", "8104")),
        log_level="info",
    )
