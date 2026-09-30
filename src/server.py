"""FastAPI app: paste a link, pick an instrument, get a score.

Binds 127.0.0.1 by default. This app downloads audio and engraves it for
personal practice; it is not meant to be exposed, and is deliberately absent
from the vantage funnel.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import fetch  # noqa: E402
import pipeline  # noqa: E402
from paths import PARTS, ROOT, job_dir  # noqa: E402

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
STATIC = ROOT / "static"

# Only these names are servable from a job directory, and each maps to one file.
ARTIFACTS = {
    "pdf": ("score.pdf", "application/pdf"),
    "musicxml": ("score.musicxml", "application/vnd.recordare.musicxml+xml"),
    "midi": ("score.mid", "audio/midi"),
}


class ResolveRequest(BaseModel):
    url: str


class JobRequest(BaseModel):
    source_id: str
    part: str
    meta: dict = {}
    options: dict = {}


@app.post("/api/resolve")
def resolve(request: ResolveRequest):
    try:
        return fetch.resolve(request.url)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/jobs")
def create_job(request: JobRequest):
    if request.part not in PARTS:
        raise HTTPException(status_code=400, detail=f"part must be one of {PARTS}")
    if not request.source_id.strip():
        raise HTTPException(status_code=400, detail="source_id is required")
    job_id = pipeline.new_job(request.source_id, request.part, request.meta, request.options)
    return {"job_id": job_id}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": pipeline.recent()}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    status = pipeline.get_status(job_id)
    if status.get("state") == "unknown":
        raise HTTPException(status_code=404, detail="No such job")
    return status


@app.get("/api/jobs/{job_id}/page/{number}")
def job_page(job_id: str, number: int):
    """One engraved SVG page. This is what the on-screen viewer renders."""
    page = job_dir(job_id) / f"page-{number}.svg"
    if not page.exists():
        raise HTTPException(status_code=404, detail="No such page")
    return FileResponse(page, media_type="image/svg+xml")


@app.get("/api/jobs/{job_id}/file/{name}")
def job_file(job_id: str, name: str):
    if name not in ARTIFACTS:
        raise HTTPException(status_code=404, detail="Unknown artifact")
    filename, media_type = ARTIFACTS[name]
    path = job_dir(job_id) / filename
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
