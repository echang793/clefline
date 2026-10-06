
## Gotchas that cost real time

These are all load-bearing. Each one failed silently or misleadingly first time.

- **verovio's fonts only load on the thread that imported it.** `verovio.toolkit()`
  built on the pipeline's worker thread cannot load Bravura and then refuses to
  parse any MusicXML, reporting only "could not parse". `render._toolkit()` uses
  `verovio.toolkit(False)` + `setResourcePath(...)`, which works on any thread.
- **Use the venv's yt-dlp, never the one on PATH.** A stale system yt-dlp resolves
  metadata perfectly and then returns HTTP 403 on the media URL. `fetch.YTDLP` is
  `[sys.executable, "-m", "yt_dlp"]` so the version tracks requirements.txt.
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
