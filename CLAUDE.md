# clefline

Song link (YouTube/Spotify) → sheet music for alto sax, keyboard or drums. FastAPI backend
(`src/server.py`), vanilla-JS frontend (`static/`), one worker thread running a two-phase
pipeline (`src/pipeline.py`): `prepare` (fetch → separate → grid → harmony → melody, cached
per song) then `engrave` (per instrument). Personal tool: one person, one machine, loopback only.

## Commands

```bash
.venv/bin/python src/server.py                  # run (preflight, then http://127.0.0.1:8104)
.venv/bin/python -m pytest tests/ -q            # offline; --run-network adds the 3 live tests
.venv/bin/ruff check src tests scripts
scripts/install-service.sh                      # launchd: server (KeepAlive) + weekly cleanup
curl localhost:8104/healthz                     # 200, or 503 naming the failing check
```

Install from `requirements.lock` (`uv pip install --no-deps -r requirements.lock`), or resolve
`requirements.txt` **with** `--override overrides.txt`. Settings are `CLEFLINE_*` env vars
(`src/config.py`, listed in `.env.example`). The project lives in `~/Projects/clefline` on
purpose: not iCloud-synced (cold reads there stalled imports and a test run for 12 minutes).

## Architecture rules that are easy to break

- **Every id from the network goes through `paths`** (`source_dir`/`job_dir`/`job_path`):
  `[A-Za-z0-9_-]{1,64}`, resolved inside its root. Reads use `job_path`, which never creates
  directories. The API layer is stricter (`source_id` must be an 11-char YouTube id).
- **Every child process goes through `procs.run`** (never bare `subprocess.run` for demucs or
  yt-dlp): it kills the whole process group on timeout/cancel, drains pipes so chatty output
  can't deadlock, and records the child under `data/run` so a restart reaps orphans left by
  `kill -9`/OOM. Cancellation reaches it through `procs.scope`, not function arguments.
- **`_set_status` is locked** (`pipeline._status_lock`): the worker and request threads both do
  read-modify-write on `status.json`; unlocked, a stale write flipped a finished job back to
  `running`. Error paths use `_safe_status` so recording a failure cannot itself crash the worker.
- **Nothing internal reaches the browser:** `server._public` strips tracebacks, scrubs paths
  from messages, and reduces `artifacts` to `page_count` (render records absolute paths).
- **A restart resumes jobs** (stages are idempotent); a job already started twice is failed
  instead (`MAX_ATTEMPTS`), so one that kills the server cannot loop.


## Gotchas that cost real time

These are all load-bearing. Each one failed silently or misleadingly first time.

- **verovio's fonts only load on the thread that imported it.** `verovio.toolkit()`
  built on the pipeline's worker thread cannot load Bravura and then refuses to
  parse any MusicXML, reporting only "could not parse". `render._toolkit()` uses
  `verovio.toolkit(False)` + `setResourcePath(...)`, which works on any thread.
- **Use the venv's yt-dlp, never the one on PATH.** A stale system yt-dlp resolves
  metadata perfectly and then returns HTTP 403 on the media URL. `fetch.YTDLP` is
  `[sys.executable, "-m", "yt_dlp"]`. It is deliberately *unpinned* in requirements.txt
  (YouTube breaks old builds); upgrade with `uv pip install -U yt-dlp`. The lock pins
  whatever was current when it was generated.
- **setuptools 81+ removed `pkg_resources`,** which resampy < 0.4.3 imports at
  module load, breaking basic-pitch. Hence the `resampy>=0.4.3` pin.
- **verovio writes chord-symbol accidentals as SMuFL U+EA64–EA67,** a different
  range from notation accidentals (U+E26x). They arrive in their own `<tspan>`.
  Dropping them printed C#m7 as "Cm7" — a different chord. `render.SMUFL_TEXT`
  transliterates them; the runs are joined without whitespace or it reads "C # m7".
- **verovio ignores `print-object="no"` on directions.** To hide the metronome
  glyph (drawn in a music font neither the PDF writer nor a browser has) the
  `<direction-type>` is stripped outright, keeping `<sound tempo>` for playback.
- **music21 notes built from a MIDI number default to sharp spellings.** In a flat
  key that put a contradicting accidental on nearly every note. `score.spell()`
  spells to the key signature; this was the single biggest readability fix.
- **A pickup before the first downbeat produced negative offsets** and music21
  refused to place the notes. `grid.analyze` steps the origin back whole bars.
- **Patch `paths.JOBS`/`paths.SOURCES`, never a copy.** The data directories have one
  binding, in `paths`; `pipeline` reads `paths.JOBS` through the module. (It used to do
  `from paths import JOBS`, a second binding: patching one half split reads from writes
  and once left stray job directories in production.) `tests/conftest.py` redirects them
  to a per-test temp dir for every test, so nothing touches real `data/`.
- **requirements.txt alone cannot be installed from scratch:** basic-pitch 0.4.0 declares
  `resampy<0.4.3`, which we must override (`overrides.txt`) because resampy<0.4.3 needs
  the `pkg_resources` that setuptools 81+ removed. Always install with `--override`.

- **launchd's PATH has no `/opt/homebrew/bin`,** so ffmpeg is "not found" under the service
  unless the plist sets PATH (`deploy/com.clefline.server.plist` does).
- **demucs weights live in the HuggingFace cache** (`~/.cache/huggingface/hub/models--adefossez--
  HTDemucs-6s/...`), not torch hub; beat_this weights are in torch hub. `preflight` checks both.
- **`kill -9` skips graceful shutdown,** so demucs survives as an orphan; `procs.reap_orphans()`
  runs at startup, before resuming jobs, and only kills a pid that still runs the recorded program.

## Known limits

- Melody comes out around 2.5 notes/sec on a busy pop vocal, which is denser than
  a hand-written chart. Use the Adjust panel's eighth-note grid to simplify.
- Drum classification is heuristic: kick, snare and hi-hat are reliable. Toms are
  now too (peakiness-based, see `drums.classify`). Ride and crash are split by the
  same peakiness feature (ride's bell gives it a defined pitch a crash's wash
  lacks) but on a much thinner synthetic margin (0.066-0.074 vs 0.014-0.017) --
  the shakiest voice in the classifier. `cymbal` stays in `VOICES` as a legacy
  fallback so hits cached before this split still render, but `classify()` no
  longer produces it.
- Chord symbols are sparse — the Viterbi smoothing favours holding a chord.
- The vocal stem is whatever demucs separates as "vocals" — lead and backing
  vocals are not split further, so a song with prominent harmony can occasionally
  pull it into the melody instead of the lead line. Not fixable without a harder
  source-separation model than this stack has.
