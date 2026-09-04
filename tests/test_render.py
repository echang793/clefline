"""Engraving: the SVG text pass, and that a score produces every artifact."""

import contextlib
import os
from pathlib import Path

from music21 import clef, harmony, meter, note, stream

from render import _flatten_text, _hide_metronome, engrave, render, to_musicxml

SVG = ('<svg xmlns="http://www.w3.org/2000/svg">'
       '<text font-size="0px"><tspan x="10" y="20" text-anchor="middle" '
       'font-size="40px">{body}</tspan></text></svg>')


def flattened(body: str) -> str:
    return _flatten_text(SVG.format(body=body))


@contextlib.contextmanager
def captured_fd2(tmp_path: Path):
    """Capture the OS-level stderr file descriptor, not just sys.stderr.

    verovio is a C++ binding and writes its own log lines straight to fd 2,
    bypassing anything Python-level redirection (contextlib.redirect_stderr,
    capsys) can see -- confirmed empirically while diagnosing this, not
    assumed. Only a real fd-level dup2 catches it.
    """
    capture_path = tmp_path / "fd2.txt"
    with open(capture_path, "w") as capture_file:
        saved_fd = os.dup(2)
        os.dup2(capture_file.fileno(), 2)
        try:
            yield capture_path
        finally:
            os.dup2(saved_fd, 2)
            os.close(saved_fd)


def test_a_chord_symbol_mid_note_does_not_warn_to_stderr(tmp_path):
    """Regression: a chord change under a note the singer is still holding is
    ordinary lead-sheet content, and it made music21's exporter open a second,
    rest-only voice to carry it. verovio's importer expects voices numbered
    from 1, not music21's 0, so it logged "Layer 0 cannot be found" once per
    occurrence -- ~55 of them on one real song's keyboard part. Two fixes that
    touched the MusicXML (renumbering voices, then also hiding the padding
    voice's rests) were tried and both changed real output -- one made the
    padding voice's rests visibly render, the other changed the page count.
    The actual fix (verovio.enableLog in render._toolkit) changes zero bytes
    of the MusicXML; this only has to prove the noise is gone.
    """
    part = stream.Part()
    part.insert(0, clef.TrebleClef())
    part.insert(0, meter.TimeSignature("4/4"))
    part.insert(0.0, note.Note(midi=60, quarterLength=2.0))  # sounds 0.0-2.0
    symbol = harmony.ChordSymbol("Cmaj7")
    symbol.writeAsChord = False
    part.insert(1.0, symbol)  # lands inside the note above -- the trigger
    part.append(note.Note(midi=62, quarterLength=2.0))

    score = stream.Score()
    score.append(part.makeNotation())
    musicxml = to_musicxml(score, tmp_path / "score.musicxml")

    with captured_fd2(tmp_path) as captured:
        engrave(musicxml, tmp_path / "out")
    assert "Layer 0" not in captured.read_text()


def test_real_font_size_is_hoisted_onto_the_text_element():
    """svglib measures with the outer size; 0px made every anchored string misplace."""
    out = flattened("Title")
    assert 'font-size="40px"' in out
    assert 'font-size="0px"' not in out


def test_position_and_anchor_are_hoisted_too():
    out = flattened("Title")
    assert 'x="10"' in out and 'y="20"' in out and 'text-anchor="middle"' in out


def test_tooltip_metadata_is_not_printed():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg"><text font-size="0px">'
           '<tspan x="1" y="2" font-size="30px"><title>title</title>Real Title</tspan>'
           '</text></svg>')
    assert "title</text>" not in _flatten_text(svg)
    assert "Real Title" in _flatten_text(svg)


def test_chord_accidentals_survive_as_ascii():
    """Regression: verovio writes a chord sharp as SMuFL U+EA66, in its own tspan.

    That is the chord-symbol accidental range, not the U+E26x notation range.
    Dropping it printed C#m7 as "Cm7" — a different chord, not a cosmetic loss.
    """
    assert "C#m7" in flattened("C\uea66m7")
    assert "Bbmaj7" in flattened("B\uea64maj7")


def test_runs_split_across_tspans_are_joined_without_gaps():
    """verovio pretty-prints each run, so a naive join printed "C # m7"."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg"><text font-size="0px">'
           '<tspan x="1" y="2" font-size="30px">\n  C\n  </tspan>'
           '<tspan font-size="30px">\n  \uea66\n  </tspan>'
           '<tspan font-size="30px">\n  m7\n  </tspan></text></svg>')
    assert ">C#m7<" in _flatten_text(svg)


def test_music_glyphs_without_a_text_equivalent_are_dropped():
    assert "" not in flattened(" = 120")


def test_metronome_is_removed_but_playback_tempo_survives():
    xml = ('<direction><direction-type><metronome><beat-unit>quarter</beat-unit>'
           '<per-minute>120</per-minute></metronome></direction-type>'
           '<sound tempo="120" /></direction>')
    out = _hide_metronome(xml)
    assert "<metronome" not in out
    assert 'sound tempo="120"' in out


def test_render_produces_every_artifact(tmp_path):
    part = stream.Part()
    part.insert(0, clef.TrebleClef())
    part.insert(0, meter.TimeSignature("4/4"))
    for midi in (60, 62, 64, 65):
        part.append(note.Note(midi=midi, quarterLength=1))
    score = stream.Score()
    score.append(part.makeNotation())

    artifacts = render(score, tmp_path)
    assert artifacts["page_count"] >= 1
    for name in ("pdf", "midi", "musicxml"):
        assert Path(artifacts[name]).stat().st_size > 0


def test_ascii_text_keeps_verovios_own_font():
    """Ordinary titles must render exactly as before this existed — no fallback
    font swapped in where the default already covers everything."""
    out = flattened("Plain English Title")
    assert "font-family" not in out.split("</text>")[-2].rsplit("<text", 1)[-1]


def test_non_latin1_text_gets_a_fallback_font_when_one_is_available():
    """Regression: a title like "是你" downloaded fine and rendered correctly
    in the on-screen SVG, but reportlab's default PDF font has no CJK glyphs
    and silently dropped every character — the page just had a blank gap where
    the title should be. font-family only needs setting when content actually
    needs it; whether one is available on this machine is not something the
    test can assume, so it only checks the two are consistent."""
    from render import _unicode_font

    out = flattened("是你")
    has_font_attr = 'font-family="' in out
    assert has_font_attr == (_unicode_font() is not None)
