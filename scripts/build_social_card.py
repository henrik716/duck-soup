"""Build docs/assets/social-card.png: the preview image shown when a docs link is shared
(Teams, Slack, LinkedIn, …), referenced by the og:image tag in overrides/main.html.

    python scripts/build_social_card.py

1200×630 is the usual Open Graph size. Some apps (Teams, for one) crop it to a square, so the
duck and the title sit in the middle 630×630.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "docs" / "assets" / "favicon.png"
OUT = ROOT / "docs" / "assets" / "social-card.png"
W, H = 1200, 630

TITLE = "duck soup"
TAGLINE = "geodata ETL on DuckDB"
ACCENT = (157, 133, 255)  # --accent in duck_soup/web/ui/src/styles.css


def _font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def main() -> None:
    logo = Image.open(LOGO).convert("RGBA")
    bg = logo.getpixel((8, 8))[:3]  # the logo's own background, so it blends in
    card = Image.new("RGB", (W, H), bg)

    size = 400
    card.paste(logo.resize((size, size), Image.LANCZOS), ((W - size) // 2, 18), logo.resize((size, size), Image.LANCZOS))

    draw = ImageDraw.Draw(card)
    title_font = _font(["SpaceGrotesk-Bold.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"], 86)
    tag_font = _font(["SpaceGrotesk-Regular.ttf", "segoeui.ttf", "DejaVuSans.ttf"], 34)
    # The editor's wordmark: the name plus a ring (a 。) in the accent colour, centred together,
    # sized like the editor's (.brand .dot in styles.css) and sitting on the baseline.
    ring, gap, stroke = round(0.24 * 86), round(0.07 * 86), round(0.065 * 86)
    title_w = draw.textlength(TITLE, font=title_font) + gap + ring
    x = (W - title_w) / 2
    baseline = 500
    draw.text((x, baseline), TITLE, font=title_font, fill=(255, 255, 255), anchor="ls")
    rx = x + draw.textlength(TITLE, font=title_font) + gap
    draw.ellipse((rx, baseline - ring, rx + ring, baseline), outline=ACCENT, width=stroke)
    draw.text((W // 2, 548), TAGLINE, font=tag_font, fill=(196, 176, 255), anchor="mm")

    card.save(OUT, optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
