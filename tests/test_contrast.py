"""WCAG contrast for every text/background and focus/background pair in style.css.

The audit measured several pairs far below AA (warning text ~1.8:1, hover text
neon-green-on-white ~1.5:1) and the design system's own "text" colour is only ~4:1
on its own surface. Neon green is for fills, rings and borders -- never for text
on a light panel -- and this test fails the build if that regresses.
"""

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parents[1] / "static" / "style.css").read_text()


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))


def _themes() -> dict[str, dict[str, str]]:
    root = re.search(r":root\s*\{(.*?)\}", CSS, re.S).group(1)
    dark = re.search(r"prefers-color-scheme: dark\)\s*\{\s*:root\s*\{(.*?)\}", CSS, re.S).group(1)
    light = _tokens(root)
    return {"light": light, "dark": {**light, **_tokens(dark)}}


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def ratio(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


TEXT_PAIRS = [   # (foreground token, background token): body text, needs 4.5:1
    ("ink", "bg"), ("ink", "panel"),
    ("muted", "bg"), ("muted", "panel"),
    ("danger", "danger-bg"), ("warning", "warning-bg"),
    ("on-primary", "primary"),
]
UI_PAIRS = [     # focus rings and selected state: need 3:1 against what they sit on
    ("focus", "bg"), ("focus", "panel"),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg", TEXT_PAIRS)
def test_text_meets_wcag_aa(theme, fg, bg):
    tokens = _themes()[theme]
    assert ratio(tokens[fg], tokens[bg]) >= 4.5, (
        f"{theme}: {fg} {tokens[fg]} on {bg} {tokens[bg]} is {ratio(tokens[fg], tokens[bg]):.2f}:1"
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg", UI_PAIRS)
def test_focus_indicators_meet_three_to_one(theme, fg, bg):
    tokens = _themes()[theme]
    assert ratio(tokens[fg], tokens[bg]) >= 3.0, f"{theme}: {fg} on {bg}"


def test_the_neon_primary_is_never_used_as_text_colour():
    """Neon green on a light panel is ~1.4:1. It is a fill/ring/border colour."""
    for rule in re.findall(r"[^{}]+\{[^{}]*\}", CSS):
        body = rule.split("{", 1)[1]
        assert not re.search(r"(?<![-\w])color:\s*var\(--primary\)", body), rule.strip()[:120]


def test_every_colour_token_the_pairs_use_is_defined_in_both_themes():
    themes = _themes()
    needed = {t for pair in TEXT_PAIRS + UI_PAIRS for t in pair}
    for name, tokens in themes.items():
        assert needed <= set(tokens), f"{name} is missing {sorted(needed - set(tokens))}"
