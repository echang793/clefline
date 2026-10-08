#!/usr/bin/env python3
"""Render the PWA's PNG icons from the same shapes as static/icon.svg.

    .venv/bin/python scripts/make_icons.py            # writes static/icons/*.png

Android wants 192 and 512 px PNGs in the manifest; iOS wants an apple-touch-icon,
which must be full-bleed because iOS rounds the corners itself (transparent corners
would show up black). Pillow only, so there is no native Cairo dependency. If you
change static/icon.svg, change the shapes below to match and re-run this.
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

NEON = (0x37, 0xF7, 0x12)
DARK = (0x0A, 0x0B, 0x09)
DESIGN = 192          # the SVG's viewBox
SUPERSAMPLE = 4       # draw large, then shrink, for smooth edges

ICONS = [("icon-192.png", 192, False), ("icon-512.png", 512, False),
         ("apple-touch-icon.png", 180, True)]


def render(size: int, full_bleed: bool) -> Image.Image:
    big = size * SUPERSAMPLE
    k = big / DESIGN
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    tile = (*DARK, 255)
    if full_bleed:
        draw.rectangle([0, 0, big, big], fill=tile)
    else:
        draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=24 * k, fill=tile)

    # Staff lines: 7 units thick at 40% opacity over the dark tile.
    line = (*(round(d * 0.6 + n * 0.4) for d, n in zip(DARK, NEON, strict=True)), 255)
    for y in (70, 94, 118):
        draw.rectangle([38 * k, (y - 3.5) * k, 154 * k, (y + 3.5) * k], fill=line)

    glyph = (*NEON, 255)
    for x, y, w, h in ((83, 52, 7, 78), (137, 38, 7, 76)):                 # stems
        draw.rectangle([x * k, y * k, (x + w) * k, (y + h) * k], fill=glyph)
    draw.polygon([(83 * k, 52 * k), (144 * k, 38 * k), (144 * k, 52 * k), (83 * k, 66 * k)],
                 fill=glyph)                                                 # beam

    for cx, cy in ((72, 128), (126, 112)):                                   # note heads
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse(
            [(cx - 15) * k, (cy - 11) * k, (cx + 15) * k, (cy + 11) * k], fill=glyph)
        # SVG rotate(-18) is counter-clockwise on screen, which is +18 in Pillow.
        image.alpha_composite(layer.rotate(18, center=(cx * k, cy * k), resample=Image.BICUBIC))

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1]
                        / "static" / "icons")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, size, full_bleed in ICONS:
        render(size, full_bleed).save(args.out / name, optimize=True)
        print(f"wrote {args.out / name} ({size}x{size}{', full-bleed' if full_bleed else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
