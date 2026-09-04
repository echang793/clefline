"""music21 Score -> the files you actually take to a music stand.

Four artifacts per part, all from one engraved source:

    score.musicxml   the editable master (MuseScore, Sibelius, Dorico)
    page-N.svg       what the web viewer shows
    score.pdf        printable
    score.mid        play it back against the recording — the real accuracy check

verovio does the engraving; svglib+reportlab turn its SVG into PDF. That pairing
avoids a native cairo dependency, which is the only reason cairosvg is not used.
"""

import base64
import re
from pathlib import Path

import verovio
from music21 import stream

# US Letter in verovio's tenths-of-a-millimetre units, and in PDF points.
PAGE_WIDTH_UNITS = 2159
PAGE_HEIGHT_UNITS = 2794
PAGE_WIDTH_PT = 612.0
PAGE_HEIGHT_PT = 792.0

# Candidate system fonts with broad script coverage, for titles that carry
# CJK, Cyrillic, Greek, or other characters outside Latin-1. reportlab's default
# text font only covers Latin-1: a title like "是你" downloaded correctly and
# rendered fine in the on-screen SVG (the browser draws it), but the PDF path
# silently dropped every glyph and printed nothing. Tried in order; the first
# one that exists on this machine is used. Absence is not an error — a title
# outside the font's coverage still degrades the way it always did.
UNICODE_FONT_NAME = "ClefUnicodeFallback"
UNICODE_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",   # macOS
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",    # common Linux path
    "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
)

VEROVIO_OPTIONS = {
    "pageWidth": PAGE_WIDTH_UNITS,
    "pageHeight": PAGE_HEIGHT_UNITS,
    "pageMarginLeft": 140,
    "pageMarginRight": 140,
    "pageMarginTop": 140,
    "pageMarginBottom": 140,
    "scale": 42,
    "adjustPageHeight": False,
    "footer": "none",
    "breaks": "auto",
    "spacingStaff": 12,
    "spacingSystem": 10,
}


def _toolkit() -> "verovio.toolkit":
    """A toolkit with its font resources wired up explicitly.

    verovio's automatic resource lookup only succeeds on the thread that first
    imported it: constructing a toolkit on the pipeline's worker thread silently
    fails to load Bravura and then refuses to parse anything. Deferring resource
    init and pointing the instance at the packaged data directory works on any
    thread and keeps the setting off global state.

    Also drops verovio's own log level to errors-only. A chord symbol whose
    offset lands inside a sustained note -- ordinary and expected on a lead
    sheet, e.g. a chord change under a note the singer is still holding --
    makes music21 open a second voice to hold it, padded with rests to fill
    the measure. music21 numbers its own primary voice "0", so that auto-
    created second voice becomes "1", and verovio's importer -- which expects
    voices numbered from 1 like every other MusicXML writer -- looks for
    "Layer 0" to attach it to, finds nothing, and logs a warning once per
    occurrence: ~55 of them on one ordinary song's keyboard part.

    The warning is genuinely harmless (verovio still places the chord symbol
    correctly; confirmed by diffing rendered output with and without it) and
    two more surgical fixes were tried and both made things measurably worse
    before this one: renumbering voices to be 1-indexed silenced the warning
    but made verovio treat the padding voice as real and draw its rests as
    visible marks; additionally hiding those with print-object="no" removed
    the marks but changed the piece's page count (3 -> 2), meaning it had
    also altered verovio's spacing/justification pass, not just visibility.
    Turning the log down errors-only changes zero bytes of the MusicXML and
    zero rendering decisions -- confirmed by an unchanged page count and a
    byte-identical MusicXML diff -- it only stops verovio from narrating a
    situation it already handles correctly. loadData()'s own return value,
    not its log output, is what engrave() uses to detect real failures, so
    genuine parse errors are still caught.
    """
    verovio.enableLog(verovio.LOG_ERROR)
    toolkit = verovio.toolkit(False)
    toolkit.setResourcePath(str(Path(verovio.__file__).parent / "data"))
    return toolkit


def to_musicxml(score: stream.Score, target: Path) -> Path:
    """Write MusicXML. music21 writes to a temp file, so move it into place."""
    written = Path(score.write("musicxml"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_hide_metronome(written.read_text()))
    written.unlink(missing_ok=True)
    return target


def _hide_metronome(musicxml: str) -> str:
    """Strip the printed metronome mark, keeping the MIDI tempo intact.

    verovio engraves <metronome> with a quarter-note glyph from the Leipzig
    music font, which neither the PDF writer nor a browser has, so it prints as
    a blank followed by a bare "= 120". The visible tempo is a plain <words>
    direction instead. print-object="no" does not work here — verovio ignores it
    on directions — so the <direction-type> is removed outright. The sibling
    <sound tempo="..."> element is left alone and still drives playback.
    """
    return re.sub(
        r"\s*<direction-type>\s*<metronome.*?</metronome>\s*</direction-type>",
        "",
        musicxml,
        flags=re.S,
    )


def engrave(musicxml: Path, out_dir: Path) -> dict:
    """MusicXML -> SVG pages + PDF + MIDI. Returns the artifact paths and page count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    toolkit = _toolkit()
    toolkit.setOptions(VEROVIO_OPTIONS)
    if not toolkit.loadData(musicxml.read_text()):
        raise RuntimeError("verovio could not parse the MusicXML")

    pages = []
    for number in range(1, toolkit.getPageCount() + 1):
        svg_path = out_dir / f"page-{number}.svg"
        svg_path.write_text(toolkit.renderToSVG(number))
        pages.append(svg_path)

    midi_path = out_dir / "score.mid"
    midi_path.write_bytes(base64.b64decode(toolkit.renderToMIDI()))

    pdf_path = _svgs_to_pdf(pages, out_dir / "score.pdf")
    return {
        "pages": [str(p) for p in pages],
        "page_count": len(pages),
        "pdf": str(pdf_path),
        "midi": str(midi_path),
    }


SVG_NS = "http://www.w3.org/2000/svg"
PRIVATE_USE = range(0xE000, 0xF900)

# verovio draws accidentals inside chord symbols as SMuFL glyphs in the Unicode
# private use area. Dropping them silently turned C#m7 into Cm7 on the printed
# page — a wrong chord, not a cosmetic loss — so these are transliterated rather
# than stripped. Everything else in that range is a music glyph with no textual
# equivalent (note heads in tempo marks and the like) and is dropped.
SMUFL_TEXT = {
    # Chord-symbol accidentals (csymAccidental*) — the ones that actually appear
    # above the staff on a lead sheet.
    "\uea64": "b", "\uea65": "n", "\uea66": "#", "\uea63": "bb", "\uea67": "##",
    # Standard notation accidentals, in case they reach a text run.
    "\ue260": "b", "\ue261": "n", "\ue262": "#", "\ue264": "bb", "\ue263": "##",
}


def _unicode_font() -> str | None:
    """Register the first available broad-coverage font. Cached after the first call."""
    if not hasattr(_unicode_font, "_name"):
        from svglib.svglib import register_font

        _unicode_font._name = None
        for path in UNICODE_FONT_CANDIDATES:
            if Path(path).exists():
                registered, ok = register_font(UNICODE_FONT_NAME, path)
                if ok:
                    _unicode_font._name = registered
                break
    return _unicode_font._name


def _flatten_text(svg: str) -> str:
    """Collapse verovio's nested tspans into plain <text>, for the PDF writer.

    verovio nests the actual string, its font size and often its x/y/anchor
    inside <tspan> layers, leaving font-size="0px" on the <text> itself. svglib
    reads the outer element, measures every string as zero wide, and drops
    anchored text in the wrong place — which is what clipped the title off the
    left edge. Hoisting position, size and content onto the <text> fixes it.

    Browsers resolve the nesting themselves and are served the original SVG.
    """
    import xml.etree.ElementTree as ET

    ET.register_namespace("", SVG_NS)
    root = ET.fromstring(svg)
    skip = {f"{{{SVG_NS}}}title", f"{{{SVG_NS}}}desc"}

    for text in root.iter(f"{{{SVG_NS}}}text"):
        pieces, sizes = [], []
        for node in text.iter():
            if node.tag in skip:
                continue  # tooltip metadata, never rendered
            if node is not text:
                if size := node.get("font-size"):
                    sizes.append(size)
                for attribute in ("x", "y", "text-anchor"):
                    if text.get(attribute) is None and node.get(attribute) is not None:
                        text.set(attribute, node.get(attribute))
            # Each tspan is one logical run. Strip it before joining: verovio
            # pretty-prints the markup, so a chord symbol arrives as the three
            # runs "C", the sharp glyph and "m7", and keeping that indentation
            # would print "C # m7".
            if node.text and node.text.strip():
                pieces.append(node.text.strip())
            for child in node:
                if child.tag in skip and child.tail and child.tail.strip():
                    pieces.append(child.tail.strip())

        raw = "".join(pieces)
        content = "".join(
            SMUFL_TEXT.get(c, "") if ord(c) in PRIVATE_USE else c for c in raw
        ).strip()
        for child in list(text):
            text.remove(child)
        text.text = content
        if sizes:
            text.set("font-size", max(sizes, key=lambda v: float(v.rstrip("px") or 0)))

        # reportlab's default text font only covers Latin-1. A title in Chinese,
        # Cyrillic, Greek etc. needs a real font or its glyphs are silently
        # dropped — not garbled, just gone, which is worse than a visible box.
        # Ordinary titles are left on verovio's own font-family so the page
        # looks exactly as it did before this existed.
        if any(ord(c) > 0xFF for c in content) and (font := _unicode_font()):
            text.set("font-family", font)

    return ET.tostring(root, encoding="unicode")


def _svgs_to_pdf(pages: list[Path], target: Path) -> Path:
    """One SVG per PDF page, scaled to fit Letter and centred."""
    import io

    from reportlab.graphics import renderPDF
    from reportlab.pdfgen import canvas
    from svglib.svglib import svg2rlg

    surface = canvas.Canvas(str(target), pagesize=(PAGE_WIDTH_PT, PAGE_HEIGHT_PT))
    for page in pages:
        drawing = svg2rlg(io.StringIO(_flatten_text(page.read_text())))
        if drawing is None:
            continue
        if drawing.width and drawing.height:
            scale = min(PAGE_WIDTH_PT / drawing.width, PAGE_HEIGHT_PT / drawing.height)
            drawing.scale(scale, scale)
            drawing.width *= scale
            drawing.height *= scale
        x = (PAGE_WIDTH_PT - drawing.width) / 2
        y = PAGE_HEIGHT_PT - drawing.height - (PAGE_HEIGHT_PT - drawing.height) / 2
        renderPDF.draw(drawing, surface, x, max(0, y))
        surface.showPage()
    surface.save()
    return target


def render(score: stream.Score, out_dir: Path) -> dict:
    """Full engraving pass for one part."""
    musicxml = to_musicxml(score, out_dir / "score.musicxml")
    artifacts = engrave(musicxml, out_dir)
    artifacts["musicxml"] = str(musicxml)
    return artifacts
