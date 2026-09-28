"""OCR engine benchmark: character error rate and latency per page.

Runs inside bench/Dockerfile (linux/amd64) so the numbers are the ones the
server image will see. Every engine is constructed once and warmed on one
page before timing, which is how the server runs it (models stay loaded).

CER is defined in metric.py (NFC, full-width ASCII folded, layout line breaks
and spaces between Japanese characters ignored; spaces inside Thai words and
circled digits count as errors).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from typing import Callable

from PIL import Image
from metric import cer, norm

SAMPLES = Path(sys.argv[1] if len(sys.argv) > 1 else "/samples")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/out")
REPEATS = int(os.environ.get("BENCH_REPEATS", "3"))

TESSDATA = {kind: f"/opt/{kind}" for kind in ("tessdata", "tessdata_fast", "tessdata_best")}
# Page language as an oracle would name it. Mixed pages get the combined
# models of the scripts they contain, primary script first.
TESS_LANG = {
    "jpn": "jpn", "tha": "tha", "eng": "eng",
    "mix-je": "jpn+eng", "mix-te": "tha+eng", "mix-jte": "jpn+tha+eng", "review": "jpn+eng",
}  # fmt: skip
# The resolution the server passes to Tesseract: the render resolution for
# pages (200 dpi), and 300 for an image with no dpi tag (the review page).
DPI = {"review": 300}


# ---------------------------------------------------------------- engines


def tesseract(tessdata: str, lang_for: Callable[[str], str], sauvola: bool = False):
    import tesserocr

    apis: dict[str, tesserocr.PyTessBaseAPI] = {}

    def run(img: Image.Image, lang: str, code: str | None = None) -> str:
        code = code or lang_for(lang)
        if code not in apis:
            apis[code] = tesserocr.PyTessBaseAPI(path=tessdata, lang=code, psm=tesserocr.PSM.AUTO)
            if sauvola:  # local (adaptive) binarization instead of global Otsu
                apis[code].SetVariable("thresholding_method", "2")
        api = apis[code]
        api.SetImage(img.convert("L"))
        api.SetSourceResolution(DPI.get(lang, 200))
        return api.GetUTF8Text()

    return run


OSD_SCRIPT = {"Latin": "eng", "Japanese": "jpn", "Han": "jpn", "Hiragana": "jpn", "Katakana": "jpn", "Thai": "tha"}


def tesseract_osd(tessdata: str, mode: str):
    """Pick the language string per page from Tesseract's own script detection
    (OSD). mode: "single" = the detected language alone; "plus_eng" = detected
    + eng; "primary" = detected first, then every other configured language."""
    import tesserocr

    osd = tesserocr.PyTessBaseAPI(path=tessdata, lang="osd", psm=tesserocr.PSM.OSD_ONLY)
    osd.SetVariable("thresholding_method", "2")
    recognize = tesseract(tessdata, lambda code: code, sauvola=True)
    every = ["eng", "jpn", "tha"]

    def run(img: Image.Image, lang: str) -> str:
        osd.SetImage(img.convert("L"))
        osd.SetSourceResolution(DPI.get(lang, 200))
        found = osd.DetectOrientationScript() or {}
        code = OSD_SCRIPT.get(found.get("script_name", ""))
        if code is None:
            codes = every
        elif mode == "single":
            codes = [code]
        elif mode == "plus_eng":
            codes = [code] if code == "eng" else [code, "eng"]
        else:
            codes = [code] + [c for c in every if c != code]
        return recognize(img, lang, "+".join(codes))

    return run


def rapid(version: str, model_type: str, rec_for: Callable[[str], tuple[str, str, str]]):
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    engines: dict[tuple[str, str, str], RapidOCR] = {}

    def run(img: Image.Image, lang: str) -> str:
        key = rec_for(lang)
        if key not in engines:
            rec_lang, rec_version, rec_type = key
            engines[key] = RapidOCR(
                params={
                    "Global.log_level": "error",
                    "Det.ocr_version": OCRVersion(version),
                    "Det.model_type": ModelType(model_type),
                    "Rec.lang_type": LangRec(rec_lang),
                    "Rec.ocr_version": OCRVersion(rec_version),
                    "Rec.model_type": ModelType(rec_type),
                }
            )
        out = engines[key](img.convert("RGB"))
        return "\n".join(out.txts or ())

    return run


# RapidOCR recognizes with one model per page, so it is given the page's
# primary script as an oracle: Japanese model for any page with Japanese on it,
# Thai for Thai + English. Thai on the jpn+tha+eng page has no model that reads
# it alongside Japanese.
RAPID_PRIMARY = {"jpn": "jpn", "tha": "tha", "eng": "eng", "mix-je": "jpn", "mix-te": "tha", "mix-jte": "jpn", "review": "jpn"}


def v6(model_type: str):
    def rec_for(lang: str) -> tuple[str, str, str]:
        lang = RAPID_PRIMARY[lang]
        if lang == "tha":  # PP-OCRv6 has no Thai recognizer; fall back to v5
            return ("th", "PP-OCRv5", "mobile")
        return ({"jpn": "japan", "eng": "en"}[lang], "PP-OCRv6", model_type)

    return rapid("PP-OCRv6", model_type, rec_for)


def v5(lang: str) -> tuple[str, str, str]:
    return ({"jpn": "ch", "tha": "th", "eng": "en"}[RAPID_PRIMARY[lang]], "PP-OCRv5", "mobile")


ENGINES: dict[str, Callable[[], Callable[[Image.Image, str], str]]] = {
    "Tesseract 5.5 tessdata_fast, page language": lambda: tesseract(TESSDATA["tessdata_fast"], TESS_LANG.get),
    "Tesseract 5.5 tessdata, page language": lambda: tesseract(TESSDATA["tessdata"], TESS_LANG.get),
    "Tesseract 5.5 tessdata_best, page language": lambda: tesseract(TESSDATA["tessdata_best"], TESS_LANG.get),
    "Tesseract 5.5 tessdata_fast, eng+jpn+tha": lambda: tesseract(TESSDATA["tessdata_fast"], lambda _: "eng+jpn+tha"),
    "Tesseract 5.5 tessdata_fast + Sauvola, page language": lambda: tesseract(
        TESSDATA["tessdata_fast"], TESS_LANG.get, sauvola=True
    ),
    "Tesseract 5.5 tessdata + Sauvola, page language": lambda: tesseract(TESSDATA["tessdata"], TESS_LANG.get, sauvola=True),
    "Tesseract 5.5 tessdata_best + Sauvola, page language": lambda: tesseract(
        TESSDATA["tessdata_best"], TESS_LANG.get, sauvola=True
    ),
    "Tesseract 5.5 tessdata_fast + Sauvola, eng+jpn+tha": lambda: tesseract(
        TESSDATA["tessdata_fast"], lambda _: "eng+jpn+tha", sauvola=True
    ),
    "Tesseract 5.5 tessdata_fast + Sauvola, jpn+tha+eng": lambda: tesseract(
        TESSDATA["tessdata_fast"], lambda _: "jpn+tha+eng", sauvola=True
    ),
    "Tesseract 5.5 tessdata_fast + Sauvola, script detection, single language": lambda: tesseract_osd(
        TESSDATA["tessdata_fast"], "single"
    ),
    "Tesseract 5.5 tessdata_fast + Sauvola, script detection + eng": lambda: tesseract_osd(
        TESSDATA["tessdata_fast"], "plus_eng"
    ),
    "Tesseract 5.5 tessdata_fast + Sauvola, script detection first, then all": lambda: tesseract_osd(
        TESSDATA["tessdata_fast"], "primary"
    ),
    "Tesseract 5.5 tessdata + Sauvola, eng+jpn+tha": lambda: tesseract(
        TESSDATA["tessdata"], lambda _: "eng+jpn+tha", sauvola=True
    ),
    "Tesseract 5.5 tessdata + Sauvola, script detection first, then all": lambda: tesseract_osd(
        TESSDATA["tessdata"], "primary"
    ),
    "RapidOCR PP-OCRv5 mobile": lambda: rapid("PP-OCRv5", "mobile", v5),
    "RapidOCR PP-OCRv6 small": lambda: v6("small"),
    "RapidOCR PP-OCRv6 medium": lambda: v6("medium"),
}


def main() -> None:
    manifest = json.loads((SAMPLES / "manifest.json").read_text())
    only = os.environ.get("BENCH_ENGINES")  # substring filter, e.g. "Sauvola"
    rows = []
    for name, factory in ENGINES.items():
        if only and not any(o in name for o in only.split(",")):
            continue
        run = factory()
        # warm every language path once so model load is not in the timing
        for lang in dict.fromkeys(m["lang"] for m in manifest):
            first = next(m for m in manifest if m["lang"] == lang)
            run(Image.open(SAMPLES / first["file"]), lang)
        if os.environ.get("BENCH_WARM_ONLY"):
            continue
        for m in manifest:
            img = Image.open(SAMPLES / m["file"])
            img.load()
            times, hyp = [], ""
            for _ in range(REPEATS):
                t0 = time.perf_counter()
                hyp = run(img, m["lang"])
                times.append(time.perf_counter() - t0)
            row = {
                "engine": name,
                "file": m["file"],
                "lang": m["lang"],
                "variant": m["variant"],
                "chars": len(norm(m["text"])),
                "cer": cer(m["text"], hyp),
                "seconds": statistics.median(times),
                "hyp": hyp,
            }
            rows.append(row)
            print(f"{name:50s} {m['file']:22s} CER {row['cer']:.3f}  {row['seconds']:.2f}s", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / os.environ.get("BENCH_RESULTS", "results.json")).write_text(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
