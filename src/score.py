"""Quantized notes -> a music21 Score, one builder per instrument.

The sax builder is the only one doing real music theory: an alto saxophone is an
Eb instrument, so what the player reads is the sounding pitch transposed up a
major sixth, in a key signature three sharps further round the circle. Both are
exact arithmetic and are unit-tested across all twelve keys.

Melody is notes only. Nothing here writes text under the staff.
"""

from music21 import (
    bar,
    clef,
    expressions,
    harmony,
    instrument,
    key,
    layout,
    metadata,
    meter,
    note,
    percussion,
    stream,
    tempo,
)

from quantize import QuantizedNote

# Alto sax sounds a major sixth below what is written.
ALTO_SAX_TRANSPOSE = 9
ALTO_SAX_KEY_SHIFT = 3
# Written range of a standard alto: low Bb3 up to high F#6.
ALTO_SAX_LOW = 58
ALTO_SAX_HIGH = 90


def written_key_sharps(concert_sharps: int) -> int:
    """Concert key signature -> the one an Eb alto reads, in its easiest spelling.

    Transposing up a major sixth pushes plenty of ordinary concert keys off the
    end of the circle: concert C# minor becomes A# minor, seven sharps, which no
    one wants on a stand. The enharmonic equivalent is the same set of pitches
    written with five flats, so the spelling with fewer accidentals wins.
    """
    sharps = concert_sharps + ALTO_SAX_KEY_SHIFT
    alternative = sharps - 12 if sharps > 0 else sharps + 12
    if abs(alternative) < abs(sharps) and -7 <= alternative <= 7:
        return alternative
    return max(-7, min(7, sharps))


def spell(midi: int, sharps: int):
    """A music21 Pitch spelled to fit the key signature.

    Notes built straight from a MIDI number default to sharp spellings, so in a
    flat key every altered note printed as an accidental that contradicted the
    signature — the single biggest source of clutter on the page. Scale tones
    take their spelling from the key itself; everything else follows the
    signature's direction.
    """
    from music21 import key as m21key
    from music21 import pitch as m21pitch

    signature = m21key.KeySignature(sharps)
    in_key = {p.pitchClass: p.name for p in signature.getScale().getPitches()[:7]}
    name = in_key.get(midi % 12) or (
        SHARP_SPELLING if sharps >= 0 else FLAT_SPELLING
    )[midi % 12]
    return m21pitch.Pitch(f"{name}{midi // 12 - 1}")


SHARP_SPELLING = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_SPELLING = ["C", "D-", "D", "E-", "E", "F", "G-", "G", "A-", "A", "B-", "B"]


def fit_to_range(midis: list[int], low: int = ALTO_SAX_LOW, high: int = ALTO_SAX_HIGH) -> list[int]:
    """Shift the whole line by octaves to fit, then fold any stragglers.

    Shifting everything together first preserves the melodic contour; folding a
    single note is a last resort for whatever the global shift couldn't reach.

    A straggler is folded to whichever in-range octave of its own pitch class
    sits closest to the previous fitted note, not to the first octave a blind
    "step up until in range" walk happens to land on. The old approach always
    folded a note below the floor down to the bottom of the horn and one above
    the ceiling up to the top, regardless of where the phrase actually was --
    a low outlier next to a run of high notes got yanked to the opposite end of
    the range, a 20+ semitone leap the singer never sang.
    """
    if not midis:
        return []

    best_shift, best_cost = 0, None
    for octaves in range(-4, 5):
        shift = octaves * 12
        cost = sum(
            max(0, low - (m + shift)) + max(0, (m + shift) - high) for m in midis
        )
        if best_cost is None or cost < best_cost:
            best_shift, best_cost = shift, cost

    fitted: list[int] = []
    for m in midis:
        value = m + best_shift
        if low <= value <= high:
            fitted.append(value)
            continue
        anchor = fitted[-1] if fitted else (low + high) // 2
        fitted.append(_nearest_octave_in_range(value, low, high, anchor))
    return fitted


def _nearest_octave_in_range(value: int, low: int, high: int, anchor: int) -> int:
    """Every octave of `value`'s pitch class that falls in [low, high], closest to `anchor`.

    The range spans more than an octave, so at least one candidate always
    exists for any pitch class.
    """
    base = low + ((value - low) % 12)
    candidates = []
    while base <= high:
        candidates.append(base)
        base += 12
    return min(candidates, key=lambda c: abs(c - anchor))


def _add_header(part: stream.Part, *, clef_obj, sharps: int, time_signature: str,
                bpm: float, swing: bool) -> None:
    part.insert(0, clef_obj)
    part.insert(0, key.KeySignature(sharps))
    part.insert(0, meter.TimeSignature(time_signature))

    # The metronome mark carries the tempo into MusicXML and MIDI, but is hidden:
    # verovio draws its quarter-note glyph in the Leipzig music font, which is
    # not available to either the PDF writer or a browser, so it comes out blank.
    # The visible tempo is plain text that renders anywhere.
    mark = tempo.MetronomeMark(number=round(bpm))
    mark.style.hideObjectOnPrint = True
    part.insert(0, mark)

    caption = f"{round(bpm)} BPM" + (" — Swing" if swing else "")
    part.insert(0, expressions.TextExpression(caption))


def _place_notes(part: stream.Part, notes: list[QuantizedNote], midis: list[int],
                 sharps: int) -> None:
    for quantized, midi in zip(notes, midis, strict=True):
        part.insert(
            quantized.offset,
            note.Note(spell(midi, sharps), quarterLength=quantized.duration),
        )


def _finish(part: stream.Part) -> stream.Part:
    """Fill gaps with rests, then let music21 bar, tie and beam it."""
    part.makeRests(fillGaps=True, inPlace=True, hideRests=False)
    finished = part.makeNotation()
    measures = finished.getElementsByClass(stream.Measure)
    if measures:
        measures[-1].rightBarline = bar.Barline("final")
    return finished


# Emoji, symbol and flag ranges: verovio's text font has no glyph for these and
# they print as solid black boxes. This is deliberately narrow \u2014 it must not
# catch CJK, Cyrillic, Greek, or accented Latin, all of which render correctly
# (verovio emits real <text> for titles; the browser or PDF font draws it, not
# verovio's own glyph set). An earlier version filtered by codepoint instead of
# by range and stripped every non-Latin-alphabet title down to nothing, which is
# how a song titled "\u662f\u4f60" ended up printed as "Untitled".
_EMOJI_RANGES = (
    (0x1F1E6, 0x1F1FF),   # regional indicator letters (flag emoji)
    (0x1F300, 0x1FAFF),   # misc symbols/pictographs through symbols-and-pictographs-extended-A
    (0x2600, 0x27BF),     # misc symbols, dingbats
    (0x2190, 0x21FF),     # arrows (used as decoration in some upload titles)
    (0x2B00, 0x2BFF),     # misc symbols and arrows
    (0xFE00, 0xFE0F),     # variation selectors (emoji presentation)
    (0x200D, 0x200D),     # zero-width joiner (combines emoji sequences)
)


def _is_emoji(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _EMOJI_RANGES)


def clean_title(text: str) -> str:
    """Drop the glyphs the engraver actually can't draw, keep everything else."""
    kept = [c for c in (text or "") if not _is_emoji(c)]
    return " ".join("".join(kept).split()).strip(" -\u2013\u2014") or "Untitled"


def _score(title: str, subtitle: str, part: stream.Part) -> stream.Score:
    score = stream.Score()
    score.metadata = metadata.Metadata(title=clean_title(title), composer=clean_title(subtitle))
    score.insert(0, layout.ScoreLayout())
    score.append(part)
    return score


def build_sax(notes: list[QuantizedNote], *, concert_sharps: int, time_signature: str,
              bpm: float, swing: bool, title: str, subtitle: str) -> stream.Score:
    """Alto sax lead: transposed to written pitch and folded into the horn's range."""
    written = fit_to_range([n.midi + ALTO_SAX_TRANSPOSE for n in notes])
    written_sharps = written_key_sharps(concert_sharps)

    part = stream.Part()
    part.insert(0, instrument.AltoSaxophone())
    _add_header(part, clef_obj=clef.TrebleClef(), sharps=written_sharps,
                time_signature=time_signature, bpm=bpm, swing=swing)
    _place_notes(part, notes, written, written_sharps)
    return _score(title, f"{subtitle} — Alto Sax in E♭", _finish(part))


def build_keys(notes: list[QuantizedNote], chords: list[dict], *, concert_sharps: int,
               time_signature: str, bpm: float, swing: bool, title: str,
               subtitle: str) -> stream.Score:
    """Keyboard lead sheet: concert-pitch melody with chord symbols above."""
    part = stream.Part()
    part.insert(0, instrument.Piano())
    _add_header(part, clef_obj=clef.TrebleClef(), sharps=concert_sharps,
                time_signature=time_signature, bpm=bpm, swing=swing)
    _place_notes(part, notes, [n.midi for n in notes], concert_sharps)

    for entry in chords:
        try:
            symbol = harmony.ChordSymbol(entry["symbol"])
        except Exception:
            continue
        symbol.writeAsChord = False
        part.insert(entry["offset"], symbol)

    return _score(title, f"{subtitle} — Lead Sheet", _finish(part))


def build_drums(events: list[tuple[float, float, list[str]]], *, time_signature: str,
                bpm: float, swing: bool, title: str, subtitle: str) -> stream.Score:
    """Drum set on a percussion staff.

    Each event is (offset in quarters, duration, voices played together). Voices
    map to fixed staff positions and noteheads — x for anything struck with a
    stick on metal — which is what makes a drum part readable at a glance.
    Simultaneous voices become one chord, because they are one stroke of the kit.
    """
    from drums import VOICES

    part = stream.Part()
    kit = instrument.UnpitchedPercussion()
    kit.partName, kit.partAbbreviation = "Drum Set", "Dr."
    part.insert(0, kit)
    _add_header(part, clef_obj=clef.PercussionClef(), sharps=0,
                time_signature=time_signature, bpm=bpm, swing=swing)

    for offset, duration, voices in events:
        heads = []
        for voice in voices:
            spec = VOICES.get(voice)
            if not spec:
                continue
            head = note.Unpitched(displayName=spec["display"])
            head.notehead = spec["notehead"]
            heads.append(head)
        if not heads:
            continue

        element = heads[0] if len(heads) == 1 else percussion.PercussionChord(heads)
        element.quarterLength = duration
        part.insert(offset, element)

    return _score(title, f"{subtitle} — Drums", _finish(part))
