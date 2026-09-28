"""Render the benchmark texts to page images: a clean scan and a degraded one.

Pages are A4 at 200 dpi (1654 x 2339 px), 12 pt type. The degraded variant
imitates an office flatbed scan: a small skew, blur, sensor noise, uneven
contrast, and JPEG at quality 60. The randomness is seeded, so the files are
the same on every run.

    uv run --no-project --with pillow --with numpy bench/render_samples.py OUT_DIR
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from texts import PAGES

DPI = 200
PAGE_W, PAGE_H = int(8.27 * DPI), int(11.69 * DPI)
MARGIN = int(0.9 * DPI)
FONT_PX = int(12 / 72 * DPI)  # 12 pt
LINE_GAP = int(FONT_PX * 0.7)

SEEDS = {"jpn": 1, "tha": 2, "eng": 3}

FONTS = {
    "jpn": ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 0),
    "tha": ("/usr/share/fonts/truetype/tlwg/Norasi.ttf", 0),  # Thai + Latin digits in one face
    "eng": ("/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf", 0),
}


def _units(text: str, lang: str) -> list[str]:
    """Break points: words for English, grapheme-ish clusters otherwise
    (a combining mark never starts a unit, so Thai vowels and tones stay on
    their base consonant)."""
    if lang == "eng":
        out: list[str] = []
        for i, word in enumerate(text.split(" ")):
            out.append(word if i == 0 else " " + word)
        return out
    units: list[str] = []
    for ch in text:
        if units and unicodedata.category(ch) in ("Mn", "Mc"):
            units[-1] += ch
        else:
            units.append(ch)
    return units


def wrap(text: str, lang: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for unit in _units(para, lang):
            candidate = line + unit
            if line and font.getlength(candidate) > width:
                lines.append(line)
                line = unit.lstrip(" ")
            else:
                line = candidate
        lines.append(line)
        lines.append("")  # paragraph gap
    return lines[:-1]


def render(text: str, lang: str) -> Image.Image:
    path, index = FONTS[lang]
    layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    font = ImageFont.truetype(path, FONT_PX, index=index, layout_engine=layout)
    img = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(img)
    y = MARGIN
    for line in wrap(text, lang, font, PAGE_W - 2 * MARGIN):
        if line:
            draw.text((MARGIN, y), line, font=font, fill=0)
        y += FONT_PX + LINE_GAP
    return img


def degrade(img: Image.Image, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    img = img.rotate(rng.uniform(-1.2, 1.2), resample=Image.BICUBIC, fillcolor=255, expand=False)
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    arr = np.asarray(img, dtype=np.float32)
    # uneven illumination: a soft gradient across the page
    gy, gx = np.mgrid[0 : arr.shape[0], 0 : arr.shape[1]]
    shade = 1.0 - 0.12 * (gx / arr.shape[1]) - 0.06 * (gy / arr.shape[0])
    arr = arr * shade
    # ink fade + paper tone
    arr = 35 + arr * (225 - 35) / 255
    arr += rng.normal(0, 9, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for lang, pages in PAGES.items():
        for n, text in enumerate(pages, start=1):
            clean = render(text, lang)
            base = f"{lang}-p{n}"
            clean.save(out / f"{base}-clean.png", optimize=True)
            degrade(clean, seed=SEEDS[lang] * 10 + n).save(
                out / f"{base}-scan.jpg", quality=60
            )
            for variant, ext in (("clean", "png"), ("scan", "jpg")):
                manifest.append(
                    {"file": f"{base}-{variant}.{ext}", "lang": lang, "page": n, "variant": variant, "text": text}
                )
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"raqm={features.check('raqm')} wrote {len(manifest)} images to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
