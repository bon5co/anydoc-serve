"""Build the scanned test fixtures from the benchmark pages.

Needs the fonts in bench/Dockerfile, so run it there:

    docker build -t anydoc-serve-bench bench/
    docker run --rm -v $PWD:/repo -w /repo anydoc-serve-bench \
        sh -c 'pip install -q pypdfium2 && python tests/fixtures/make_fixtures.py'

Outputs (committed):
    scan-eng.jpg, scan-jpn.jpg, scan-tha.jpg   one degraded scan each, cropped to the text
    mixed.pdf    anydoc-text.pdf (2 text pages) + a scanned Japanese page + a scanned Thai page
    scanned.pdf  the three scans, image-only
    rotated.jpg  the English scan turned 90 degrees clockwise, for orientation correction
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "bench"))

from render_samples import DPI, degrade, render  # noqa: E402
from texts import PAGES  # noqa: E402

SEEDS = {"eng": 31, "jpn": 11, "tha": 21}


def scan(lang: str) -> Image.Image:
    page = degrade(render(PAGES[lang][0], lang), seed=SEEDS[lang])
    return page.crop((0, 0, page.width, int(page.height * 0.30)))


def jpeg(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def image_pdf(images: list[Image.Image]) -> pdfium.PdfDocument:
    """One page per image, the image embedded as JPEG at DPI: what a scanner writes."""
    pdf = pdfium.PdfDocument.new()
    for image in images:
        w, h = image.width * 72 / DPI, image.height * 72 / DPI
        page = pdf.new_page(w, h)
        obj = pdfium.PdfImage.new(pdf)
        obj.load_jpeg(io.BytesIO(jpeg(image)), inline=True)
        obj.set_matrix(pdfium.PdfMatrix().scale(w, h))
        page.insert_obj(obj)
        page.gen_content()
    return pdf


def main() -> None:
    scans = {lang: scan(lang) for lang in ("eng", "jpn", "tha")}
    for lang, image in scans.items():
        (HERE / f"scan-{lang}.jpg").write_bytes(jpeg(image))
    (HERE / "rotated.jpg").write_bytes(jpeg(scans["eng"].rotate(-90, expand=True)))

    image_pdf([scans["eng"], scans["jpn"], scans["tha"]]).save(HERE / "scanned.pdf")

    mixed = pdfium.PdfDocument(HERE / "anydoc-text.pdf")
    mixed.import_pages(image_pdf([scans["jpn"], scans["tha"]]))
    mixed.save(HERE / "mixed.pdf")
    print("fixtures written to", HERE)


if __name__ == "__main__":
    main()
