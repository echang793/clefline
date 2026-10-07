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

_work: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()


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
    current = get_status(job_id)
    current.update(fields, job_id=job_id, updated=time.time())
    write_json(status_path(job_id), current)


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
    job that errored is never reused: that request is exactly the one to
    retry, not to hand back the same failure again.
    """
    for job in recent(limit=100):
        if (job.get("source_id") == source_id and job.get("part") == part
                and job.get("options") == options and job.get("state") != "error"):
            return job.get("job_id")
    return None


def new_job(source_id: str, part: str, meta: dict, options: dict | None = None) -> str:
    options = options or {}
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


def recover_interrupted_jobs() -> int:
    """Mark anything left "queued" or "running" from before this process
    started as interrupted, and return how many were found.

    The job queue is an in-memory queue.Queue: it dies with the process. A
    crash or restart mid-job leaves that job's status.json frozen at whatever
    percentage it was showing, forever -- no error, no retry path, just a
    progress bar in the UI that never moves again. Called once at server
    startup, before anything can be queued against the (now-empty) new queue.
    """
    count = 0
    for job in recent(limit=1000):
        if job.get("state") in ("queued", "running"):
            _set_status(
                job["job_id"], state="error",
                message="Interrupted by a server restart -- try again.",
            )
            count += 1
    return count


def _run(job_id: str) -> None:
    job = get_status(job_id)
    labels = {name: label for name, label, _ in STAGES}

    def on_stage(name: str) -> None:
        _set_status(job_id, stage=name, message=labels.get(name, name),
                    progress=_progress_through(name))

    try:
        _set_status(job_id, state="running")
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

        _set_status(job_id, state="done", stage="done", progress=1.0,
                    message="Ready", artifacts=artifacts, detected=detected)
    except (fetch.FetchError, separate_module.SeparationError, RuntimeError, ValueError) as error:
        # These already read as a clear "what and why" -- fetch/separation
        # failures and the page-size/verovio-parse checks are all raised with
        # a human-facing message on purpose. Shown as-is.
        _set_status(job_id, state="error",
                    message=scrub(str(error)) or type(error).__name__,
                    traceback=traceback.format_exc()[-2000:])
    except Exception as error:
        # Anything else is a genuine surprise -- something inside torch,
        # librosa or music21 that this pipeline doesn't have a specific
        # message for. Framed as unexpected rather than shown bare, so it
        # doesn't read as if the app understood exactly what went wrong.
        _set_status(
            job_id, state="error",
            message="Unexpected error during transcription: "
                    + (scrub(str(error)) or type(error).__name__),
            traceback=traceback.format_exc()[-2000:],
        )


def _loop() -> None:
    while True:
        _run(_work.get())
        _work.task_done()


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, daemon=True, name="clefline-worker")
            _worker.start()


def recent(limit: int = 25) -> list[dict]:
    jobs = [read_json(p / "status.json") for p in paths.JOBS.glob("*") if p.is_dir()]
    jobs = [j for j in jobs if j]
    jobs.sort(key=lambda j: j.get("created", 0), reverse=True)
    return jobs[:limit]
