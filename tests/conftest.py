from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from anydoc_serve.app import create_app
from anydoc_serve.convert import Converter
from anydoc_serve.ocr import OcrResult
from anydoc_serve.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"


class FakeOcr:
    """Stands in for Tesseract in unit tests: records what it was asked to read."""

    name = "fake"
    langs = ["eng", "jpn", "tha"]

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.dpis: list[float | None] = []

    def recognize(self, image: Image.Image, dpi: float | None = None) -> OcrResult:
        self.calls.append(image.size)
        self.dpis.append(dpi)
        return OcrResult(text=f"OCR TEXT {len(self.calls)}", lang="eng")

    def warm_up(self) -> None:
        pass


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def fake_ocr() -> FakeOcr:
    return FakeOcr()


@pytest.fixture
def make_client(fake_ocr):
    clients = []

    def make(**overrides) -> TestClient:
        settings = Settings(_env_file=None, **overrides)
        converter = Converter(fake_ocr if settings.ocr_enabled else None, ocr_max_pages=settings.ocr_max_pages)
        client = TestClient(create_app(settings, converter))
        client.__enter__()  # run the lifespan (MCP session manager)
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()
