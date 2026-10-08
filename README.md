# clefline

Paste a YouTube or Spotify link, pick an instrument, get sheet music.

Alto sax (transposed lead), keyboard (lead sheet with chord symbols), or
drumset (percussion staff) — engraved as PDF, MusicXML, and MIDI, with an
on-screen viewer in between.

A personal practice tool. Output is a strong first draft, not a
publisher-grade engraving — expect to fix a few notes per chart.

## How it works

Two phases, split because the shared work is minutes and the per-instrument
work is seconds:

```
fetch --> separate --> grid --> harmony --> melody      (per song, ~2-3 min)
                                                  |
                                    quantize --> score --> engrave   (per instrument, ~20s)
```

- **fetch** — YouTube via `yt-dlp`. Spotify serves no audio, so a Spotify link
  is resolved to title/artist/duration from its public page, matched against
  YouTube search, and shown to you to confirm before anything downloads.
- **separate** — `demucs` (htdemucs_6s) splits vocals / drums / bass / guitar
  / piano / other.
- **grid** — tempo, beats, and downbeats via `beat_this`, with a `librosa`
  fallback.
- **harmony** — beat-synchronous chroma → chord template matching (Viterbi
  smoothed) and Krumhansl-Schmuckler key estimation, using the detected
  chords to resolve major/minor ambiguity.
- **melody** — `basic-pitch` on the vocal stem, reduced to a single line with
  a Viterbi pass over overlapping candidates.
- **quantize** — snaps notes to a musical grid, merges fragments, and drops
  sub-threshold blips so the chart is readable rather than literal.
- **score / engrave** — `music21` builds the notation (alto sax transposed
  +9 semitones into its written range; keyboard as a lead sheet; drums with
  correct staff positions and noteheads), then `verovio` engraves it to SVG,
  and `svglib` + `reportlab` turn that into a PDF.

Picking a second instrument for an already-transcribed song skips straight to
the quantize/score/engrave stage.

## Setup

**Requires Python 3.11** — not 3.13, not 3.14. `basic-pitch`'s CoreML/TF
backends have no wheels for newer Pythons. Keep the project somewhere that is
**not iCloud-synced** (e.g. `~/Projects/clefline`, not Desktop or Documents):
cold reads of evicted files stall imports for minutes.

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python --no-deps -r requirements.lock   # exact tested versions
```

or, to resolve fresh from `requirements.txt` (dev tools: `requirements-dev.txt`):

```bash
uv pip install --python .venv/bin/python -r requirements.txt --override overrides.txt
```

`--override overrides.txt` is required when resolving: basic-pitch pins an old
`resampy` that breaks on current setuptools (see the file for why). Also needs
`ffmpeg` on PATH (`brew install ffmpeg`).

## Run

```bash
.venv/bin/python src/server.py            # http://127.0.0.1:8104
```

Startup checks run first and stop with the reason if something essential is
missing (wrong Python, no ffmpeg, unwritable data directory).

Installable as a PWA (an icon on your home screen / dock, not offline audio
processing — the transcription itself always needs the network). The **Recent**
panel lists past jobs and links back to their charts.

While a job runs you can **Cancel** it (a running demucs is killed immediately), and
a failed or cancelled job offers **Try again**. Reloading the page — or opening it
in another tab — picks a running job back up; if the server goes away mid-job the page
says it is reconnecting and carries on when it returns. The shell is revalidated on
every load (no stale `app.js` after an edit), fonts are self-hosted, and nothing is
fetched from another origin except thumbnails.

Or transcribe from the terminal:

```bash
.venv/bin/python src/cli.py <youtube-or-spotify-url> --part sax
```

### Always on (launchd)

```bash
scripts/install-service.sh              # install + start the server and a weekly cleanup
scripts/install-service.sh --uninstall  # stop and remove both
```

The server restarts itself after a crash or out-of-memory kill; a job that was
running is **resumed**, not lost (a job that has already crashed the server twice is
failed instead, so it can't loop). Stopping the server cleanly leaves the job
resumable too. The installer refuses an iCloud-synced checkout.

| What | Where |
| --- | --- |
| Health | `curl http://127.0.0.1:8104/healthz` — 200, or 503 naming the failing check |
| Server output (launchd) | `~/Library/Logs/clefline/server.log` |
| App log (rotating, 5 MB × 3) | `data/logs/clefline.log` — job lifecycle, per-stage timings, full tracebacks |
| Restart | `launchctl kickstart -k gui/$(id -u)/com.clefline.server` |

### Settings

All optional, all environment variables (see `.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLEFLINE_HOST` / `CLEFLINE_PORT` | `127.0.0.1` / `8104` | Where it listens. There is **no authentication** — keep it on loopback. |
| `CLEFLINE_DATA_DIR` | `data/` in the repo | Audio, stems, jobs, logs |
| `CLEFLINE_MAX_DURATION_SECONDS` | `900` | Longest recording to transcribe |
| `CLEFLINE_MIN_FREE_GB` | `3` | Refuse to start a download/separation below this much free disk |
| `CLEFLINE_ALLOWED_HOSTS` | *(none)* | Extra Host headers to accept (`127.0.0.1` and `localhost` always are) |
| `CLEFLINE_LOG_LEVEL` | `INFO` | `DEBUG`…`CRITICAL` |

To set them for the service, add them to `EnvironmentVariables` in
`~/Library/LaunchAgents/com.clefline.server.plist` (or edit
`deploy/com.clefline.server.plist` and re-run the installer).

### Keeping it working

- **yt-dlp** is deliberately unpinned: YouTube breaks old builds. If downloads start
  failing, `uv pip install --python .venv/bin/python -U yt-dlp` and restart. Its version
  is logged at startup.
- **Disk:** `data/` has no expiry of its own; the weekly cleanup agent removes jobs older
  than 14 days and songs older than 60 days (never anything queued or running). By hand
  (dry run by default):

```bash
.venv/bin/python scripts/cleanup.py                 # preview
.venv/bin/python scripts/cleanup.py --yes            # delete
```

- **First run** of a model downloads its weights (demucs, beat_this); startup says
  when they aren't cached yet.

## Test

```bash
.venv/bin/python -m pytest tests/ -q      # synthesized audio; network tests need --run-network
.venv/bin/ruff check src tests scripts
```

The suite includes JavaScript unit tests (`tests/js`, Node's built-in runner — no npm
packages; skipped if `node` is missing). CI (GitHub Actions, macOS) runs the same two
commands on every push. If you change `static/icon.svg`, update and re-run
`scripts/make_icons.py` (writes the PNG icons).

## Notes

- Melody comes only from the vocal stem — notes only, never lyrics.
- Detection can be wrong. The UI's **Adjust** panel overrides key, tempo,
  meter, and quantization grid; re-transcribing a song that is already cached
  takes about 20 seconds (only the notation is rebuilt, not the audio analysis).
- Drum classification is a hand-built onset classifier, not a trained model:
  kick/snare/hi-hat are reliable, toms are reasonably reliable, and the
  ride/crash split — a heuristic on top of an already-heuristic cymbal
  detection — is the shakiest of the bunch.
- **Personal use only.** This downloads audio from YouTube for personal
  transcription; the output isn't meant for redistribution. It is built for one
  person on one machine — requests are validated and Host-checked, but there is no
  authentication, so don't expose it beyond localhost. Running it as a public service
  would mean operating a download-and-transcribe service for copyrighted audio.
