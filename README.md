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
backends have no wheels for newer Pythons.

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Also needs `ffmpeg` on PATH (`brew install ffmpeg`).

## Run

```bash
.venv/bin/python src/server.py            # http://127.0.0.1:8104
```

Installable as a PWA (an icon on your home screen / dock, not offline audio
processing — the transcription itself always needs the network). The **Recent**
panel on the main page lists past jobs and links back to their charts.

Or transcribe from the terminal:

```bash
.venv/bin/python src/cli.py <youtube-or-spotify-url> --part sax
```

`data/` has no automatic expiry, so it grows with every song and job. Clean it
up periodically (dry run by default, `--yes` to actually delete):

```bash
.venv/bin/python scripts/cleanup.py                 # preview
.venv/bin/python scripts/cleanup.py --yes            # delete jobs >14d, songs >60d old
```

## Test

```bash
.venv/bin/python -m pytest tests/ -q      # synthesized audio, no network
.venv/bin/ruff check src tests
```

## Notes

- Melody comes only from the vocal stem — notes only, never lyrics.
- Detection can be wrong. The UI's **Adjust** panel overrides key, tempo,
  meter, and quantization grid, and re-renders from cached MIDI in under a
  second.
- Drum classification is a hand-built onset classifier, not a trained model:
  kick/snare/hi-hat are reliable, toms are reasonably reliable, and the
  ride/crash split — a heuristic on top of an already-heuristic cymbal
  detection — is the shakiest of the bunch.
- Binds `127.0.0.1` by default. This downloads audio for personal
  transcription — the output isn't meant for redistribution.
