"""PNG icons for installing the PWA (Android needs 192/512 PNGs, iOS an apple-touch-icon).

They are generated from the same shapes as static/icon.svg by scripts/make_icons.py,
so the check is on what is actually drawn, not on file bytes.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server import app

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "static"
client = TestClient(app, base_url="http://127.0.0.1:8104")

NEON = (0x37, 0xF7, 0x12)
DARK = (0x0A, 0x0B, 0x09)


def _near(pixel, wanted, tolerance=12):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], wanted, strict=True))


@pytest.mark.parametrize("name,size", [("icon-192.png", 192), ("icon-512.png", 512),
                                       ("apple-touch-icon.png", 180)])
def test_icons_exist_at_the_right_size(name, size):
    image = Image.open(STATIC / "icons" / name)
    assert image.size == (size, size)


@pytest.mark.parametrize("name", ["icon-192.png", "icon-512.png"])
def test_manifest_icons_are_rounded_dark_tiles_with_a_neon_glyph(name):
    image = Image.open(STATIC / "icons" / name).convert("RGBA")
    width = image.size[0]
    assert image.getpixel((0, 0))[3] == 0, "corner should be transparent (rounded tile)"
    assert _near(image.getpixel((width // 2, int(width * 0.93))), DARK), "tile background"
    # the left note head sits near (72, 128) of the 192-unit design
    assert _near(image.getpixel((int(72 / 192 * width), int(128 / 192 * width))), NEON)


def test_the_apple_touch_icon_is_full_bleed_because_ios_rounds_it_itself():
    image = Image.open(STATIC / "icons" / "apple-touch-icon.png").convert("RGBA")
    assert image.getpixel((0, 0))[3] == 255 and _near(image.getpixel((0, 0)), DARK)
    assert _near(image.getpixel((int(72 / 192 * 180), int(128 / 192 * 180))), NEON)


def test_the_manifest_lists_the_png_icons_and_keeps_the_svg():
    icons = client.get("/manifest.json").json()["icons"]
    sources = {i["src"]: i for i in icons}
    assert sources["/icons/icon-192.png"]["sizes"] == "192x192"
    assert sources["/icons/icon-512.png"]["sizes"] == "512x512"
    assert all(i["type"] == "image/png" for s, i in sources.items() if s.endswith(".png"))
    assert "/icon.svg" in sources


def test_the_page_links_the_apple_touch_icon():
    html = (STATIC / "index.html").read_text()
    assert re.search(r'<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">', html)


def test_the_icons_are_served():
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        response = client.get(f"/icons/{name}")
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"


def test_the_generator_reproduces_the_icons(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "make_icons.py"), "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        assert Image.open(tmp_path / name).size == (size, size)


def test_the_manifest_json_is_valid_json_with_the_expected_keys():
    body = json.loads(client.get("/manifest.json").text)
    assert {"name", "start_url", "display", "icons", "theme_color"} <= set(body)
