"""Job queue and the two-phase transcription run.

Follows the pattern the sibling projects use: a single worker thread, one
directory per job, every stage writing exactly one artifact and skipping itself
when that artifact already exists. Deleting an artifact re-runs only that stage.

The split that matters:

    prepare()  fetch -> separate -> grid -> harmony -> melody   (per song, minutes)
    engrave()  quantize -> score -> render                      (per part, seconds)

`prepare` output is cached under the song's source id, so asking for a second
instrument skips straight to `engrave`.
"""

import logging
import queue
import threading
import time
import traceback
import uuid
from pathlib import Path

import drums as drums_module
import fetch
import grid as grid_module
import harmony as harmony_module
import notes as notes_module
import paths
import procs
import quantize as quantize_module
import render as render_module
import score as score_module
import separate as separate_module
from paths import InvalidId, job_dir, job_path, read_json, scrub, source_dir, write_json

# Rough share of total runtime, so the progress bar moves at a believable pace.
STAGES = [
    ("fetch", "Downloading audio", 0.10),
    ("separate", "Separating stems", 0.55),
    ("grid", "Finding tempo and beats", 0.10),
    ("harmony", "Working out the key and chords", 0.10),
    ("melody", "Transcribing notes", 0.10),
    ("engrave", "Engraving the score", 0.05),
]

log = logging.getLogger("clefline.pipeline")

_work: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()          # starting the worker thread
_submit_lock = threading.Lock()   # makes "is there already a job for this?" + "create it" atomic
_status_lock = threading.RLock()  # makes a status read-modify-write atomic (see _set_status)
_cancelled: set[str] = set()      # jobs the user cancelled that the worker has not yet wound down
_stop = threading.Event()         # the server is shutting down
_running: str | None = None       # the job the worker is on right now

# A job that was mid-run when the server died is resumed on the next start. One
# that has already been mid-run twice is not: it is what killed the server.
MAX_ATTEMPTS = 2
TERMINAL = ("done", "error", "cancelled")


# --------------------------------------------------------------------------- status

def status_path(job_id: str) -> Path:
    # job_path, not job_dir: asking about a job must never create its directory
    # (write_json makes the parent when a status is actually written).
    return job_path(job_id) / "status.json"


def get_status(job_id: str) -> dict:
    try:
        found = read_json(status_path(job_id))
    except InvalidId:
        found = None
    return found or {"state": "unknown", "job_id": job_id}


def _set_status(job_id: str, **fields) -> None:
    # Read-modify-write on status.json, and both the worker and request threads
    # (cancel) call it. Unlocked, a stale read overwrote a newer write -- a
    # finished job could flip back to "running".
    with _status_lock:
        current = get_status(job_id)
        current.update(fields, job_id=job_id, updated=time.time())
        write_json(status_path(job_id), current)


def _note_cancelling(job_id: str) -> None:
    """Show "Cancelling…" on a running job -- but never over a state the worker
    has already moved on to (it may have finished winding down first)."""
    with _status_lock:
        if get_status(job_id).get("state") == "running":
            _safe_status(job_id, message="Cancelling…")


def _safe_status(job_id: str, **fields) -> None:
    """_set_status for error paths: recording a failure must not itself be able to
    fail (disk full) and take the worker down with it."""
    try:
        _set_status(job_id, **fields)
    except Exception:
        log.exception("could not record status for job %s", job_id)


def _progress_through(stage: str) -> float:
    done = 0.0
    for name, _, weight in STAGES:
        if name == stage:
            return round(done, 3)
        done += weight
    return round(done, 3)


# --------------------------------------------------------------------------- prepare

# A real sung vocal covers on the order of 40-70% of a song, less with long
# instrumental sections. An instrumental track, or one where the vocal
# separation found almost nothing, produces only scattered bleed -- nowhere
# close. Deliberately generous: a false "no vocal" warning on a real vocal
# costs nothing (the chart still renders), so the threshold favors not
# crying wolf.
SPARSE_MELODY_THRESHOLD = 0.15


def melody_coverage(notes: list, song_duration: float) -> float:
    """Fraction of the song's duration actually covered by melody notes."""
    if not song_duration:
        return 0.0
    return sum(n.duration for n in notes) / song_duration


def prepare(source_id: str, on_stage=lambda name: None) -> dict:
    """Everything that depends only on the song, not on the chosen instrument."""
    directory = source_dir(source_id)

    on_stage("fetch")
    audio = fetch.download(source_id)

    on_stage("separate")
    stems = separate_module.separate(source_id, audio)

    on_stage("grid")
    grid_file = directory / "grid.json"
    if not (payload := read_json(grid_file)):
        payload = grid_module.analyze(audio).to_dict()
        write_json(grid_file, payload)
    g = grid_module.Grid.from_dict(payload)

    on_stage("harmony")
    harmony_file = directory / "harmony.json"
    if not (harmony := read_json(harmony_file)):
        harmony = harmony_module.analyze(audio, g)
        write_json(harmony_file, harmony)

    on_stage("melody")
    melody_file = directory / "melody.json"
    if not (melody := read_json(melody_file)):
        line = notes_module.melody(stems["vocals"])
        melody = {
            "notes": [n.as_dict() for n in line],
            "swing": quantize_module.detect_swing(line, g),
            "coverage": melody_coverage(line, g.beats[-1] if g.beats else 0.0),
        }
        write_json(melody_file, melody)

    hits_file = directory / "hits.json"
    if not (hits := read_json(hits_file)):
        hits = {"hits": [h.as_dict() for h in drums_module.transcribe(stems["drums"])]}
        write_json(hits_file, hits)

    return {"grid": g, "harmony": harmony, "melody": melody, "hits": hits,
            "stems": stems, "audio": audio}


# --------------------------------------------------------------------------- engrave

def _drum_events(raw: list[dict], g, subdivision: int) -> list[tuple[float, float, list[str]]]:
    """Labelled hits -> grid-aligned chords, each lasting until the next stroke.

    Notating every stroke as a fixed short value would litter the part with
    rests; running each one up to the next is how a drum chart actually reads.
    """
    hits = [drums_module.Hit(**h) for h in raw]
    grouped = drums_module.group_simultaneous(hits)

    placed: list[tuple[float, list[str]]] = []
    for onset, voices in grouped:
        offset = g.snap(g.quarters(onset), subdivision)
        if offset < 0:
            continue
        if placed and abs(placed[-1][0] - offset) < 1e-6:
            for voice in voices:
                if voice not in placed[-1][1]:
                    placed[-1][1].append(voice)
        else:
            placed.append((offset, voices))

    step = 1.0 / subdivision
    bar = g.beats_per_bar
    events = []
    for index, (offset, voices) in enumerate(placed):
        following = placed[index + 1][0] if index + 1 < len(placed) else offset + step
        # A drum is struck, not held: a note tied across a barline is nonsense on
        # a percussion staff. Cap each hit at one beat and at the end of its own
        # measure, and let rests carry the gap the way a real chart does.
        room = bar - (offset % bar)
        duration = min(max(following - offset, step), 1.0, room)
        events.append((offset, round(duration, 6), voices))
    return events


def engrave(job_id: str, part: str, prepared: dict, meta: dict, options: dict) -> dict:
    """Build and render one instrument's part from the prepared song data."""
    g = prepared["grid"]
    harmony = prepared["harmony"]
    subdivision = int(options.get("subdivision", 4))

    line = [notes_module.NoteEvent(**n) for n in prepared["melody"]["notes"]]
    quantized = quantize_module.quantize(line, g, subdivision=subdivision)

    sharps = int(options.get("sharps", harmony.get("sharps", 0)))
    time_signature = options.get("time_signature") or g.time_signature
    bpm = float(options.get("bpm") or g.tempo)
    swing = bool(options.get("swing", prepared["melody"].get("swing", False)))
    title = meta.get("title") or "Untitled"
    subtitle = meta.get("uploader") or ""

    if part == "sax":
        built = score_module.build_sax(
            quantized, concert_sharps=sharps, time_signature=time_signature,
            bpm=bpm, swing=swing, title=title, subtitle=subtitle,
        )
    elif part == "keys":
        built = score_module.build_keys(
            quantized, harmony.get("chords", []), concert_sharps=sharps,
            time_signature=time_signature, bpm=bpm, swing=swing,
            title=title, subtitle=subtitle,
        )
    elif part == "drums":
        built = score_module.build_drums(
            _drum_events(prepared["hits"]["hits"], g, subdivision),
            time_signature=time_signature, bpm=bpm, swing=swing,
            title=title, subtitle=subtitle,
        )
    else:
        raise ValueError(f"Unknown part: {part}")

    page_size = options.get("page_size", render_module.DEFAULT_PAGE_SIZE)
    return render_module.render(built, job_dir(job_id), page_size)


# --------------------------------------------------------------------------- jobs

def _find_reusable(source_id: str, part: str, options: dict) -> str | None:
    """A job already running, queued, or finished for this exact request.

    Two identical requests are common by accident, not intent -- a double
    click on Find, two browser tabs, a page refresh that resubmits. Reusing
    the existing job instead of starting a second one saves anywhere from
    ~20 seconds (song already cached) to a couple of minutes (it isn't). A
    job that errored or was cancelled is never reused: that request is exactly
    the one to retry, not to hand back the same failure again.
    """
    for job in recent(limit=100):
        if (job.get("source_id") == source_id and job.get("part") == part
                and job.get("options") == options
                and job.get("state") not in ("error", "cancelled")):
            return job.get("job_id")
    return None


def new_job(source_id: str, part: str, meta: dict, options: dict | None = None) -> str:
    options = options or {}
    # Two identical POSTs run in parallel threads; without this they could both
    # miss each other's job and each create their own.
    with _submit_lock:
        if reusable := _find_reusable(source_id, part, options):
            return reusable

        job_id = uuid.uuid4().hex[:12]
        write_json(
            status_path(job_id),
            {
                "job_id": job_id, "source_id": source_id, "part": part, "meta": meta,
                "options": options, "state": "queued", "stage": "queued",
                "message": "Waiting to start", "progress": 0.0, "created": time.time(),
            },
        )
        _work.put(job_id)
    _ensure_worker()
    return job_id


def jobs_ahead(job_id: str) -> int | None:
    """How many jobs will run before this one (the running one included), or
    None if it is not waiting in the queue."""
    with _work.mutex:
        waiting = [j for j in _work.queue if j not in _cancelled or j == job_id]
    if job_id not in waiting:
        return None
    return waiting.index(job_id) + (1 if _running else 0)


def cancel(job_id: str) -> str | None:
    """Ask for a job to stop. Returns its resulting state, or None if unknown.

    A queued job is marked cancelled at once. A running one is flagged and winds
    down within a moment (a subprocess is killed immediately; in-process stages
    stop at their next boundary). A finished job is left as it was.
    """
    status = get_status(job_id)
    state = status.get("state")
    if state == "unknown":
        return None
    if state == "queued":
        _cancelled.add(job_id)   # first, so a worker about to pick it up still sees it
        _safe_status(job_id, state="cancelled", message="Cancelled")
        log.info("job %s cancelled while queued", job_id)
        return "cancelled"
    if state == "running":
        _cancelled.add(job_id)
        _note_cancelling(job_id)
        log.info("job %s: cancel requested while running", job_id)
        return "running"
    return state


def resume_interrupted_jobs() -> int:
    """Put every job that was "queued" or "running" when the last process died
    back on the queue, oldest first, and return how many were resumed.

    The queue is in-memory and dies with the process, which used to leave those
    jobs frozen at whatever percentage they showed (and later, to fail them all).
    Every stage is idempotent and artifact-keyed, so a resumed job picks up where
    it stopped. A job already started MAX_ATTEMPTS times is failed instead: if it
    is what keeps killing the server, resuming it would loop forever.
    """
    orphans = sorted(
        (j for j in recent(limit=None) if j.get("state") in ("queued", "running")),
        key=lambda j: j.get("created", 0),
    )
    resumed = 0
    for job in orphans:
        job_id = job["job_id"]
        if job.get("attempts", 0) >= MAX_ATTEMPTS:
            log.error("job %s not resumed: it has already crashed the server %d times",
                      job_id, job.get("attempts", 0))
            _safe_status(
                job_id, state="error",
                message="This job crashed the server twice, so it was not retried -- "
                        "the recording may be too heavy for this machine.",
            )
            continue
        _safe_status(job_id, state="queued", message="Resuming after a restart")
        _work.put(job_id)
        log.info("job %s resumed after a restart (attempt %d next)", job_id,
                 job.get("attempts", 0) + 1)
        resumed += 1
    return resumed


def worker_status() -> dict:
    """What /healthz needs to know about the worker, without touching its state."""
    worker = _worker
    return {
        "alive": None if worker is None else worker.is_alive(),   # None: not started yet
        "queued": _work.qsize(),
        "running": _running is not None,
    }


def startup() -> int:
    """Called once when the server starts: resume orphans and start the worker."""
    _stop.clear()
    # Before resuming anything: a child left running by a server that was killed
    # would otherwise fight the resumed job over the same directory.
    if reaped := procs.reap_orphans():
        log.warning("Killed %d child process(es) orphaned by the last shutdown.", reaped)
    resumed = resume_interrupted_jobs()
    if resumed:
        _ensure_worker()
    return resumed


def shutdown(timeout: float = 10.0) -> None:
    """Called when the server stops. Kills the running job's subprocess (leaving
    the job "running", so the next start resumes it) and waits for the worker."""
    _stop.set()
    worker = _worker
    if worker is not None and worker.is_alive():
        worker.join(timeout)


def _run(job_id: str) -> None:
    global _running
    job = get_status(job_id)
    if job_id in _cancelled or job.get("state") in TERMINAL:
        # Cancelled while it waited (or a stale duplicate queue entry): nothing to do.
        _cancelled.discard(job_id)
        return

    labels = {name: label for name, label, _ in STAGES}
    timings: dict[str, float] = {}
    clock = {"stage": None, "since": 0.0}

    def end_stage() -> None:
        if clock["stage"] is not None:
            timings[clock["stage"]] = round(
                timings.get(clock["stage"], 0.0) + time.monotonic() - clock["since"], 2)

    def on_stage(name: str) -> None:
        procs.check_cancelled()
        end_stage()
        clock.update(stage=name, since=time.monotonic())
        _set_status(job_id, stage=name, message=labels.get(name, name),
                    progress=_progress_through(name))

    _running = job_id
    try:
        with procs.scope(lambda: job_id in _cancelled or _stop.is_set()):
            attempt = job.get("attempts", 0) + 1
            started = time.monotonic()
            log.info("job %s started: %s for %s (attempt %d)",
                     job_id, job.get("part"), job.get("source_id"), attempt)
            _set_status(job_id, state="running", attempts=attempt)
            prepared = prepare(job["source_id"], on_stage)
            on_stage("engrave")
            artifacts = engrave(job_id, job["part"], prepared, job.get("meta", {}),
                                job.get("options", {}))

            detected = {
                "tempo": prepared["grid"].tempo,
                "time_signature": prepared["grid"].time_signature,
                "key": prepared["harmony"].get("key"),
                "sharps": prepared["harmony"].get("sharps"),
                "swing": prepared["melody"].get("swing"),
                "beat_source": prepared["grid"].source,
            }
            # Only sax and keys read the melody; a drum chart has nothing to do
            # with how much of the song the vocal stem covered.
            coverage = prepared["melody"].get("coverage", 1.0)
            if job["part"] in ("sax", "keys") and coverage < SPARSE_MELODY_THRESHOLD:
                detected["sparse_melody"] = True

            end_stage()
            _set_status(job_id, state="done", stage="done", progress=1.0,
                        message="Ready", artifacts=artifacts, detected=detected,
                        timings=timings)
            log.info("job %s done in %.1fs (%s)", job_id, time.monotonic() - started,
                     ", ".join(f"{k} {v}s" for k, v in timings.items()))
    except procs.Cancelled:
        if job_id in _cancelled:
            log.info("job %s cancelled", job_id)
            _safe_status(job_id, state="cancelled", message="Cancelled")
        else:
            # Server shutdown, not the user giving up: leave the job "running" so
            # the next start resumes it.
            log.info("job %s interrupted by shutdown; it will resume on restart", job_id)
    except (fetch.FetchError, separate_module.SeparationError, paths.LowDisk,
            RuntimeError, ValueError) as error:
        # These already read as a clear "what and why" -- fetch/separation
        # failures and the page-size/verovio-parse checks are all raised with
        # a human-facing message on purpose. Shown as-is.
        log.warning("job %s failed: %s", job_id, error)
        _safe_status(job_id, state="error",
                     message=scrub(str(error)) or type(error).__name__,
                     traceback=traceback.format_exc()[-2000:])
    except Exception as error:
        # Anything else is a genuine surprise -- something inside torch,
        # librosa or music21 that this pipeline doesn't have a specific
        # message for. Framed as unexpected rather than shown bare, so it
        # doesn't read as if the app understood exactly what went wrong.
        log.error("job %s failed unexpectedly: %s", job_id, error, exc_info=True)
        _safe_status(
            job_id, state="error",
            message="Unexpected error during transcription: "
                    + (scrub(str(error)) or type(error).__name__),
            traceback=traceback.format_exc()[-2000:],
        )
    finally:
        _cancelled.discard(job_id)
        _running = None


def _loop() -> None:
    while not _stop.is_set():
        try:
            job_id = _work.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            _run(job_id)
        except Exception:
            # _run records its own failures; this is the last line of defence so
            # that nothing a job does can kill the thread every later job needs.
            log.exception("job %s crashed the worker loop", job_id)
        finally:
            _work.task_done()


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, daemon=True, name="clefline-worker")
            _worker.start()


def recent(limit: int | None = 25) -> list[dict]:
    jobs = [read_json(p / "status.json") for p in paths.JOBS.glob("*") if p.is_dir()]
    jobs = [j for j in jobs if j]
    jobs.sort(key=lambda j: j.get("created", 0), reverse=True)
    return jobs[:limit]
