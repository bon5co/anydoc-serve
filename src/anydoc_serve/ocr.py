"""Local CPU OCR: Tesseract 5 through tesserocr, models held in memory.

Engine choice and settings come from docs/ocr-benchmark.md, measured on
English, Japanese, Thai and mixed-script pages. In short:

* `tessdata_fast` 4.1.0 models with Sauvola (local) binarization, not
  Tesseract's default global Otsu: on a scan with uneven lighting Otsu dropped
  half of every Thai line.
* Every configured language in one pass, in the fixed order jpn, tha, eng.
  Real pages mix scripts (a Japanese invoice with English lines, Thai with
  English terms). Picking one language per page from Tesseract's script
  detection turned the Japanese line of a mixed page into garbage, and the
  order is load-bearing: with eng ahead of tha, Thai came back 79 to 83%
  wrong.
* Script detection (OSD) is still run, for page orientation only: sideways
  and upside-down scans are turned upright before recognition.
* Tesseract writes Thai SARA AM decomposed (NIKHAHIT + SARA AA); it is
  recomposed so the text matches what people type and search for.

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

# Order languages are loaded in: measured, see module docstring.
LANG_ORDER = ["jpn", "tha", "eng"]

MIN_ORIENT_CONF = 2.0  # below this OSD's orientation guess is noise; leave the page as is

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
    rotated: int = 0
    confidence: float | None = None

    def markdown(self) -> str:
        return text_to_markdown(self.text)


def normalize_langs(langs: list[str]) -> list[str]:
    """Validate OCR_LANGS, accepting ISO 639-1 aliases and dropping repeats."""
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


def combined_lang(langs: list[str]) -> str:
    """The one Tesseract language string every page is read with: all
    configured languages, in LANG_ORDER."""
    return "+".join(sorted(langs, key=LANG_ORDER.index))


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


def _thai_sara_am(text: str) -> str:
    return text.replace("\u0e4d\u0e32", "\u0e33")


def text_to_markdown(text: str) -> str:
    """Tesseract text -> Markdown paragraphs. Lines keep their breaks (joining
    them would need word-boundary rules per language); a line that would read
    as Markdown syntax (heading, list, quote) gets its marker escaped."""
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", _thai_sara_am(text).strip()):
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
        self.lang = combined_lang(langs)
        self.api = self._api(tessdata, self.lang, tesserocr.PSM.AUTO)

    def _api(self, tessdata: str, lang: str, psm: int):  # -> tesserocr.PyTessBaseAPI
        api = self._tesserocr.PyTessBaseAPI(path=tessdata, lang=lang, psm=psm)
        api.SetVariable("thresholding_method", "2")  # Sauvola, see module docstring
        return api

    def recognize(self, image: Image.Image, dpi: float | None) -> OcrResult:
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
        api = self.api
        api.SetImage(image)
        api.SetSourceResolution(resolution)
        text = api.GetUTF8Text()
        return OcrResult(text=text, lang=self.lang, rotated=rotated, confidence=float(api.MeanTextConf()))


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
            return worker.recognize(image, dpi)

    def warm_up(self) -> None:
        blank = Image.new("L", (64, 64), 255)
        for _ in range(self._pool.qsize()):
            self.recognize(blank)


def build_engine(langs: list[str], *, workers: int = 1, tessdata: str | None = None) -> OcrEngine:
    return TesseractEngine(langs, tessdata=tessdata, workers=workers)
