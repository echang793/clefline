"""The frontend's structure: accessibility hooks, same-origin assets, ids the script uses.

There is no JS test framework here, so these read index.html / app.js / style.css and
check the things that would otherwise only break in a browser.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text()
JS = (STATIC / "app.js").read_text()
CSS = (STATIC / "style.css").read_text()


class _Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, {k: (v if v is not None else "") for k, v in attrs}))


def _parse():
    parser = _Elements()
    parser.feed(HTML)
    return parser.elements


def _by_id():
    return {a["id"]: (tag, a) for tag, a in _parse() if "id" in a}


def test_no_duplicate_ids():
    ids = [a["id"] for _, a in _parse() if "id" in a]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


def test_every_element_the_script_looks_up_exists():
    wanted = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', JS))
    missing = sorted(wanted - set(_by_id()))
    assert not missing, f"app.js looks up ids that index.html does not have: {missing}"


def test_the_progress_bar_is_exposed_as_a_progressbar():
    tag, bar = _by_id()["progress-bar"]
    assert bar.get("role") == "progressbar"
    assert (bar.get("aria-valuemin"), bar.get("aria-valuemax")) == ("0", "100")
    assert "aria-valuenow" in bar and bar.get("aria-label")


def test_job_status_text_is_a_live_region():
    assert _by_id()["stage"][1].get("aria-live") == "polite"


def test_errors_are_announced_as_alerts():
    for ident in ("input-error", "job-error"):
        assert _by_id()[ident][1].get("role") == "alert", ident
    assert _by_id()["sparse-warning"][1].get("role") == "status"


def test_the_progress_card_can_cancel_and_a_failed_job_can_be_retried():
    ids = _by_id()
    assert ids["cancel-job"][0] == "button" and ids["retry-job"][0] == "button"


def test_headings_that_take_focus_when_a_card_appears_are_focusable():
    for ident in ("confirm-title", "result-title", "progress-title"):
        assert _by_id()[ident][1].get("tabindex") == "-1", ident


def test_the_find_button_reports_when_it_is_busy():
    assert 'aria-busy' in JS and "find" in JS


def test_scripts_load_in_dependency_order():
    scripts = [a["src"] for tag, a in _parse() if tag == "script" and "src" in a]
    assert scripts == ["/poll.js", "/app.js"]


def test_every_script_and_stylesheet_is_same_origin():
    for tag, attrs in _parse():
        url = attrs.get("src") if tag == "script" else (
            attrs.get("href") if tag == "link" and attrs.get("rel") == "stylesheet" else None)
        if url:
            assert url.startswith("/") and not url.startswith("//"), url
    assert "googleapis" not in HTML and "gstatic" not in HTML and "googleapis" not in CSS


def test_reduced_motion_is_respected():
    block = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", CSS, re.S)
    assert block, "no prefers-reduced-motion block"
    assert "transition" in block.group(1) and "none" in block.group(1)


def test_the_fonts_are_self_hosted_and_declared():
    for family in ("Space Mono", "JetBrains Mono"):
        assert f'font-family: "{family}"' in CSS
    for name in ("SpaceMono-Regular", "SpaceMono-Bold", "JetBrainsMono-Regular"):
        assert f"/fonts/{name}.woff2" in CSS
        assert (STATIC / "fonts" / f"{name}.woff2").stat().st_size > 1000
    licence = (STATIC / "fonts" / "OFL.txt").read_text()
    assert "SIL Open Font License" in licence
    assert "Space Mono Project Authors" in licence and "JetBrains Mono Project Authors" in licence


def test_the_script_builds_dom_not_html_from_untrusted_text():
    """Titles and thumbnails come from yt-dlp/Spotify. Nothing may be assigned to
    innerHTML except a plain string literal (no template, no interpolation)."""
    statements = re.findall(r"\.innerHTML\s*=\s*([^;]+);", JS)
    for value in statements:
        assert re.fullmatch(r"""\s*("[^"]*"|'[^']*')\s*""", value), (
            f"innerHTML assigned a non-literal: {value.strip()[:80]}"
        )
    assert "insertAdjacentHTML" not in JS and "outerHTML" not in JS and "document.write" not in JS
