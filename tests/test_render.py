"""Engraving: the SVG text pass, and that a score produces every artifact."""

from pathlib import Path

from music21 import clef, meter, note, stream

from render import _flatten_text, _hide_metronome, render

SVG = ('<svg xmlns="http://www.w3.org/2000/svg">'
       '<text font-size="0px"><tspan x="10" y="20" text-anchor="middle" '
       'font-size="40px">{body}</tspan></text></svg>')


def flattened(body: str) -> str:
    return _flatten_text(SVG.format(body=body))


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
