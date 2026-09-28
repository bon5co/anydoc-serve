"""Document to Markdown: anydoc first, local OCR for what anydoc cannot read.

anydoc converts every born-digital format itself. It does no OCR: a PDF with
scanned or image-only pages raises `NeedsOcrError` naming those pages, and an
image is not a format it knows. This module fills exactly that gap:

* image upload -> OCR the image
* PDF with `NeedsOcrError(pages=...)` -> OCR only the listed pages; every other
  page still goes through anydoc (as contiguous page ranges cut out of the
  original PDF), so the text pages keep anydoc's structure
* everything else -> anydoc's Markdown, untouched
"""

from __future__ import annotations

import io
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import anydoc
import pypdfium2 as pdfium
from PIL import Image, ImageSequence

from .ocr import OcrEngine

OcrMode = Literal["auto", "off"]

# PDFium is not thread-safe (pypdfium2 documents this); conversions run on a
# thread pool, so every PDFium call goes through this lock. OCR and anydoc
# run outside it.
PDFIUM_LOCK = threading.RLock()

# Pillow only warns between MAX_IMAGE_PIXELS (89 Mpx) and twice that; the
# converter refuses anything over it outright, frame by frame, before decoding,
# so a small file cannot expand into gigabytes of pixels.
MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS or 89_478_485

# Largest bitmap a PDF page is rendered to for OCR (A4 at 200 dpi is 3.9 Mpx).
# A page with a huge MediaBox is rendered at a lower resolution instead.
MAX_RENDER_PIXELS = 50_000_000

IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"BM", "bmp"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)
IMAGE_EXTENSIONS = {
    "png": "png", "jpg": "jpeg", "jpeg": "jpeg", "tif": "tiff", "tiff": "tiff",
    "bmp": "bmp", "gif": "gif", "webp": "webp",
}  # fmt: skip


class ConversionError(Exception):
    """A conversion that cannot produce Markdown. `status` is the HTTP status
    the API answers with, `code` a stable machine-readable name."""

    def __init__(self, status: int, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class ConvertResult:
    markdown: str
    metadata: dict[str, Any] = field(default_factory=dict)


def detect_image(data: bytes, filename: str | None) -> str | None:
    """Image kind from the magic bytes, else from the extension when the bytes
    are not a document anydoc recognizes."""
    for signature, kind in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if filename and "." in filename and anydoc.format_from_bytes(data) is None:
        return IMAGE_EXTENSIONS.get(filename.rsplit(".", 1)[1].lower())
    return None


def detect_format(data: bytes, filename: str | None, format_hint: str | None) -> str:
    if format_hint:
        fmt = anydoc.format_from_extension(format_hint)
        if fmt is None:
            raise ConversionError(415, "unsupported", f"unknown format {format_hint!r}")
        return fmt
    fmt = anydoc.format_from_bytes(data)
    if fmt is None and filename:
        fmt = anydoc.format_from_path(filename)
    if fmt is None:
        raise ConversionError(
            415,
            "unsupported",
            "unrecognized document format; name it with `format` (e.g. csv) or send a supported file",
        )
    return fmt


# anydoc exception -> (HTTP status, code)
_ERRORS: tuple[tuple[type[Exception], int, str], ...] = (
    (anydoc.UnsupportedError, 415, "unsupported"),
    (anydoc.EncryptedError, 422, "encrypted"),
    (anydoc.MalformedError, 422, "malformed"),
    (anydoc.MissingPartError, 422, "missing_part"),
    (anydoc.ResourceLimitError, 413, "resource_limit"),
)


def _anydoc_error(error: Exception) -> ConversionError:
    for kind, status, code in _ERRORS:
        if isinstance(error, kind):
            return ConversionError(status, code, str(error))
    return ConversionError(422, "convert_failed", str(error))


def _page_count(data: bytes) -> int:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            return len(pdf)
        finally:
            pdf.close()


class Converter:
    def __init__(self, ocr: OcrEngine | None, *, ocr_dpi: int = 200, ocr_max_pages: int = 200) -> None:
        self.ocr = ocr
        self.ocr_dpi = ocr_dpi
        self.ocr_max_pages = ocr_max_pages
        self._local = threading.local()  # per-thread cancel flag: one Converter serves every conversion

    def convert(
        self,
        data: bytes,
        *,
        filename: str | None = None,
        format_hint: str | None = None,
        ocr: OcrMode = "auto",
        cancel: threading.Event | None = None,
    ) -> ConvertResult:
        """Convert one document. `cancel`, when set from another thread, stops
        the conversion at the next page boundary (used after a timeout)."""
        try:
            return self._convert(data, filename, format_hint, ocr, cancel or threading.Event())
        except ConversionError:
            raise
        except pdfium.PdfiumError as error:
            raise ConversionError(422, "malformed", f"unreadable PDF: {error}") from error
        except Exception as error:  # a bug or an engine failure: still a JSON error, not a bare 500
            raise ConversionError(500, "internal", f"{type(error).__name__}: {error}") from error

    def _convert(
        self, data: bytes, filename: str | None, format_hint: str | None, ocr: OcrMode, cancel: threading.Event
    ) -> ConvertResult:
        self._local.cancel = cancel
        started = time.perf_counter()
        if not data:
            raise ConversionError(400, "empty", "the document is empty")
        meta: dict[str, Any] = {"filename": filename, "bytes": len(data)}

        image_kind = None if format_hint else detect_image(data, filename)
        if image_kind:
            meta["format"] = image_kind
            markdown = self._ocr_image(data, ocr, meta)
        else:
            fmt = detect_format(data, filename, format_hint)
            meta["format"] = fmt
            try:
                markdown = anydoc.to_markdown_bytes(data, fmt)
                meta["ocr_pages"] = []
            except anydoc.NeedsOcrError as error:
                markdown = self._ocr_pdf(data, list(error.pages), error.page_count, ocr, meta)
            except anydoc.ConvertError as error:
                raise _anydoc_error(error) from error
            if fmt == "pdf" and "page_count" not in meta:
                meta["page_count"] = _page_count(data)

        meta["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return ConvertResult(markdown=markdown, metadata=meta)

    # ------------------------------------------------------------ OCR paths

    def _require_ocr(self, mode: OcrMode, why: str, **detail: Any) -> OcrEngine:
        if mode == "off" or self.ocr is None:
            reason = "OCR is off for this request" if mode == "off" else "OCR is disabled on this server"
            raise ConversionError(422, "needs_ocr", f"{why}, and {reason}", **detail)
        return self.ocr

    def _check_cancel(self) -> None:
        cancel = getattr(self._local, "cancel", None)
        if cancel is not None and cancel.is_set():
            raise ConversionError(504, "timeout", "conversion cancelled after the time limit")

    def _ocr_image(self, data: bytes, mode: OcrMode, meta: dict[str, Any]) -> str:
        engine = self._require_ocr(mode, "images need OCR")
        try:
            image = Image.open(io.BytesIO(data))
            # an animated GIF is one picture; a multi-page TIFF is a multi-page scan
            count = 1 if image.format == "GIF" else int(getattr(image, "n_frames", 1))
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ConversionError(413, "image_too_large", str(error)) from error
        except Exception as error:  # PIL raises a zoo of types for bad input
            raise ConversionError(422, "malformed", f"unreadable image: {error}") from error
        if count > self.ocr_max_pages:
            raise ConversionError(413, "too_many_ocr_pages", f"{count} image frames; the limit is {self.ocr_max_pages}")
        dpi = image.info.get("dpi")
        dpi = float(dpi[0]) if isinstance(dpi, tuple) and dpi else None
        parts = []
        for index in range(count):  # decode and OCR one frame at a time
            self._check_cancel()
            try:
                image.seek(index)
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ConversionError(
                        413,
                        "image_too_large",
                        f"frame {index + 1} is {image.width}x{image.height}, over {MAX_IMAGE_PIXELS} pixels",
                    )
                frame = image.copy()
            except ConversionError:
                raise
            except Image.DecompressionBombError as error:
                raise ConversionError(413, "image_too_large", str(error)) from error
            except Exception as error:
                raise ConversionError(422, "malformed", f"unreadable image frame {index + 1}: {error}") from error
            parts.append(engine.recognize(frame, dpi).markdown())
        meta.update(
            ocr_engine=engine.name, ocr_langs=engine.langs, ocr_pages=list(range(1, count + 1)), page_count=count
        )
        return "\n".join(part for part in parts if part.strip())

    def _ocr_pdf(self, data: bytes, pages: list[int], page_count: int, mode: OcrMode, meta: dict[str, Any]) -> str:
        detail = {"pages": pages, "page_count": page_count}
        engine = self._require_ocr(mode, f"{len(pages)} of {page_count} PDF pages are scanned or image-only", **detail)
        if len(pages) > self.ocr_max_pages:
            raise ConversionError(
                413, "too_many_ocr_pages", f"{len(pages)} pages need OCR; the limit is {self.ocr_max_pages}", **detail
            )
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(data)
        try:
            markdown = self._ocr_pdf_pages(engine, pdf, set(pages), page_count, meta)
        finally:
            with PDFIUM_LOCK:
                pdf.close()
        return markdown

    def _ocr_pdf_pages(
        self, engine: OcrEngine, pdf: pdfium.PdfDocument, need: set[int], page_count: int, meta: dict[str, Any]
    ) -> str:
        parts: list[str] = []
        ocr_done: list[int] = []
        # Walk pages in order, as runs of text pages (anydoc) and scanned pages (OCR).
        page = 1
        while page <= page_count:
            self._check_cancel()
            if page in need:
                parts.append(self._ocr_page(engine, pdf, page))
                ocr_done.append(page)
                page += 1
                continue
            end = page
            while end + 1 <= page_count and end + 1 not in need:
                end += 1
            text = self._anydoc_range(pdf, page, end)
            if text is None:  # anydoc changed its mind on the cut-out range: OCR it
                for p in range(page, end + 1):
                    self._check_cancel()
                    parts.append(self._ocr_page(engine, pdf, p))
                    ocr_done.append(p)
            else:
                parts.append(text)
            page = end + 1
        meta.update(ocr_engine=engine.name, ocr_langs=engine.langs, ocr_pages=ocr_done, page_count=page_count)
        return "\n\n".join(part.strip("\n") for part in parts if part.strip()) + "\n"

    def _ocr_page(self, engine: OcrEngine, pdf: pdfium.PdfDocument, page: int) -> str:
        with PDFIUM_LOCK:
            pdf_page = pdf[page - 1]
            width, height = pdf_page.get_size()  # points
            scale = self.ocr_dpi / 72
            if width * height * scale * scale > MAX_RENDER_PIXELS:
                scale = math.sqrt(MAX_RENDER_PIXELS / (width * height))
            bitmap = pdf_page.render(scale=scale, grayscale=True)
            image = bitmap.to_pil().copy()  # detach from PDFium-owned memory before closing
            bitmap.close()
            pdf_page.close()
        return engine.recognize(image, scale * 72).markdown()

    @staticmethod
    def _anydoc_range(pdf: pdfium.PdfDocument, first: int, last: int) -> str | None:
        buf = io.BytesIO()
        with PDFIUM_LOCK:
            sub = pdfium.PdfDocument.new()
            try:
                sub.import_pages(pdf, list(range(first - 1, last)))
                sub.save(buf)
            finally:
                sub.close()
        try:
            return anydoc.to_markdown_bytes(buf.getvalue(), "pdf")
        except (anydoc.NeedsOcrError, anydoc.UnsupportedError):
            return None
        except anydoc.ConvertError as error:
            raise _anydoc_error(error) from error
