"""Local CPU OCR: Tesseract 5 through tesserocr, models held in memory.

Engine choice and settings come from docs/ocr-benchmark.md, measured on
English, Japanese and Thai pages. In short:

* Sauvola (local) binarization, not Tesseract's default global Otsu: on a
  scan with uneven lighting Otsu dropped half of every Thai line.
* One language pack per page, chosen by Tesseract's own script detection
  (OSD), instead of one pass with every configured language loaded: the
  combined `eng+jpn+tha` pass was slower and less accurate.
* OSD also reports page orientation, so upside-down and sideways scans are
  turned upright before recognition.

The tesserocr API objects are not thread-safe, so the engine keeps a pool of
fully loaded workers, one per concurrent conversion.
"""

from __future__ import annotations

import os
import queue
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

LANGUAGES = {"eng": "English", "jpn": "Japanese", "tha": "Thai"}
ALIASES = {"en": "eng", "english": "eng", "ja": "jpn", "jp": "jpn", "japanese": "jpn", "th": "tha", "thai": "tha"}

# Tesseract OSD script names -> the language pack that reads them
SCRIPT_LANG = {
    "Latin": "eng",
    "Japanese": "jpn",
    "Han": "jpn",
    "Hiragana": "jpn",
    "Katakana": "jpn",
    "Thai": "tha",
}

MIN_ORIENT_CONF = 2.0  # below this OSD's orientation guess is noise; leave the page as is
MIN_SCRIPT_CONF = 0.5  # measured confidences on correct detections were 0.6 to 656

# Tesseract needs the scan resolution. Without it (a PIL image carries none
# after any conversion) it guessed wrong on a 200 dpi Japanese scan and lost
# whole phrases: 13.8% CER, against 1.3 to 2.2% with any explicit value from
# 70 to 300 dpi. Images that declare no plausible dpi are assumed to be 300.
DEFAULT_DPI = 300
DPI_RANGE = (70, 1200)

DEFAULT_TESSDATA = "/usr/share/tessdata"


class OcrEngine(Protocol):
    name: str
    langs: list[str]

    def recognize(self, image: Image.Image, dpi: float | None = None) -> OcrResult: ...

    def warm_up(self) -> None: ...


@dataclass
class OcrResult:
    text: str
    lang: str
    script: str | None = None
    rotated: int = 0
    confidence: float | None = None

    def markdown(self) -> str:
        return text_to_markdown(self.text)


def normalize_langs(langs: list[str]) -> list[str]:
    """Validate OCR_LANGS, accepting ISO 639-1 aliases. Order is kept; the
    first language is the fallback when script detection has no answer."""
    out: list[str] = []
    for raw in langs:
        code = ALIASES.get(raw.strip().lower(), raw.strip().lower())
        if code not in LANGUAGES:
            raise ValueError(f"unsupported OCR language {raw!r}; supported: {', '.join(LANGUAGES)}")
        if code not in out:
            out.append(code)
    if not out:
        raise ValueError("OCR_LANGS is empty")
    return out


def select_lang(script: str | None, confidence: float, langs: list[str]) -> str:
    """The Tesseract language string for a page whose script OSD detected.

    A detected script that maps to a configured language wins. Anything else
    (no detection, low confidence, a script nobody configured) falls back to
    every configured language in one pass, which is slower but reads all of
    them."""
    lang = SCRIPT_LANG.get(script or "")
    if lang in langs and confidence >= MIN_SCRIPT_CONF:
        return lang
    return "+".join(langs)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3000 <= cp <= 0x30FF  # CJK punctuation, hiragana, katakana
        or 0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0xFF00 <= cp <= 0xFFEF  # full-width forms
    )


_SPACED = re.compile(r"(?<=\S) (?=\S)")
_MD_LEAD = re.compile(r"^(\s*)([#>*+\-]|\d+[.)])(?=\s)")


def _drop_cjk_spaces(line: str) -> str:
    """Tesseract's Japanese model emits a space between most characters;
    Japanese has none. Remove a single space only when both neighbours are
    CJK, so Latin words and numbers inside Japanese text keep theirs."""
    return _SPACED.sub(
        lambda m: "" if _is_cjk(line[m.start() - 1]) and _is_cjk(line[m.end()]) else " ",
        line,
    )


def _escape_marker(m: re.Match[str]) -> str:
    marker = m.group(2)
    if marker[0].isdigit():  # "1." -> "1\.", the only escape CommonMark honours there
        return m.group(1) + marker[:-1] + "\\" + marker[-1]
    return m.group(1) + "\\" + marker


def text_to_markdown(text: str) -> str:
    """Tesseract text -> Markdown paragraphs. Lines keep their breaks (joining
    them would need word-boundary rules per language); a line that would read
    as Markdown syntax (heading, list, quote) gets its marker escaped."""
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = []
        for line in block.splitlines():
            line = _drop_cjk_spaces(line.strip())
            if line:
                lines.append(_MD_LEAD.sub(_escape_marker, line))
        if lines:
            paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs) + "\n" if paragraphs else ""


class _Worker:
    """One thread's worth of loaded Tesseract instances."""

    def __init__(self, tessdata: str, langs: list[str]) -> None:
        import tesserocr

        self._tesserocr = tesserocr
        self.osd = self._api(tessdata, "osd", tesserocr.PSM.OSD_ONLY)
        codes = list(langs) + (["+".join(langs)] if len(langs) > 1 else [])
        self.apis = {code: self._api(tessdata, code, tesserocr.PSM.AUTO) for code in codes}

    def _api(self, tessdata: str, lang: str, psm: int):  # -> tesserocr.PyTessBaseAPI
        api = self._tesserocr.PyTessBaseAPI(path=tessdata, lang=lang, psm=psm)
        api.SetVariable("thresholding_method", "2")  # Sauvola, see module docstring
        return api

    def recognize(self, image: Image.Image, langs: list[str], dpi: float | None) -> OcrResult:
        image = image.convert("L")
        resolution = int(dpi) if dpi and DPI_RANGE[0] <= dpi <= DPI_RANGE[1] else DEFAULT_DPI
        self.osd.SetImage(image)
        self.osd.SetSourceResolution(resolution)
        found = self.osd.DetectOrientationScript() or {}
        rotated = 0
        if found.get("orient_deg") and found.get("orient_conf", 0) >= MIN_ORIENT_CONF:
            rotated = int(found["orient_deg"])
            # OSD reports the page's rotation clockwise; PIL rotates counterclockwise
            image = image.rotate(rotated, expand=True)
        script = found.get("script_name")
        lang = select_lang(script, float(found.get("script_conf", 0.0)), langs)
        api = self.apis[lang]
        api.SetImage(image)
        api.SetSourceResolution(resolution)
        text = api.GetUTF8Text()
        return OcrResult(text=text, lang=lang, script=script, rotated=rotated, confidence=float(api.MeanTextConf()))


class TesseractEngine:
    name = "tesseract"

    def __init__(self, langs: list[str], *, tessdata: str | None = None, workers: int = 1) -> None:
        self.langs = normalize_langs(langs)
        self.tessdata = tessdata or os.environ.get("TESSDATA_PREFIX") or DEFAULT_TESSDATA
        missing = [
            f"{code}.traineddata"
            for code in ["osd", *self.langs]
            if not (Path(self.tessdata) / f"{code}.traineddata").is_file()
        ]
        if missing:
            raise FileNotFoundError(f"missing Tesseract models in {self.tessdata}: {', '.join(missing)}")
        self._pool: queue.Queue[_Worker] = queue.Queue()
        for _ in range(max(1, workers)):
            self._pool.put(_Worker(self.tessdata, self.langs))

    @contextmanager
    def _worker(self) -> Iterator[_Worker]:
        worker = self._pool.get()
        try:
            yield worker
        finally:
            self._pool.put(worker)

    def recognize(self, image: Image.Image, dpi: float | None = None) -> OcrResult:
        """OCR one page. `dpi` is the scan resolution when known (PDF render
        resolution, or the image's own dpi tag)."""
        with self._worker() as worker:
            return worker.recognize(image, self.langs, dpi)

    def warm_up(self) -> None:
        blank = Image.new("L", (64, 64), 255)
        for _ in range(self._pool.qsize()):
            self.recognize(blank)


def build_engine(langs: list[str], *, workers: int = 1, tessdata: str | None = None) -> OcrEngine:
    return TesseractEngine(langs, tessdata=tessdata, workers=workers)
