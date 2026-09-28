"""End to end against a running container, real Tesseract included.

    docker build -t anydoc-serve . && docker run -d -p 8080:8080 -e API_KEY=test-key anydoc-serve
    ANYDOC_SERVE_URL=http://127.0.0.1:8080 ANYDOC_SERVE_API_KEY=test-key \
        uv run pytest -m integration
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
from metric import cer  # noqa: E402
from texts import PAGES  # noqa: E402

pytestmark = pytest.mark.integration

URL = os.environ.get("ANYDOC_SERVE_URL", "http://127.0.0.1:8080")
KEY = os.environ.get("ANYDOC_SERVE_API_KEY", "")
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def http():
    headers = {"Authorization": f"Bearer {KEY}"} if KEY else {}
    with httpx.Client(base_url=URL, headers=headers, timeout=120) as client:
        yield client


def convert(http, name: str, **data) -> dict:
    r = http.post("/v1/convert", files={"file": (name, (FIXTURES / name).read_bytes())}, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def test_health_is_open():
    r = httpx.get(f"{URL}/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["ocr"]["engine"] == "tesseract"
    assert body["ocr"]["langs"] == ["eng", "jpn", "tha"]


@pytest.mark.skipif(not KEY, reason="container started without API_KEY")
def test_auth_required():
    r = httpx.post(f"{URL}/v1/convert", timeout=10)
    assert r.status_code == 401


def test_docx(http):
    body = convert(http, "anydoc-text.docx")
    assert body["markdown"].startswith("# Fixture Document")
    assert "| Tall | B2 | C2 |" in body["markdown"]


def test_markdown_output(http):
    r = http.post(
        "/v1/convert",
        files={"file": ("a.docx", (FIXTURES / "anydoc-text.docx").read_bytes())},
        headers={"Accept": "text/markdown"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.text.startswith("# Fixture Document")


def test_mixed_pdf_text_pages_via_anydoc_scanned_pages_via_ocr(http):
    body = convert(http, "mixed.pdf")
    meta, md = body["metadata"], body["markdown"]
    assert meta["ocr_pages"] == [3, 4]
    assert meta["ocr_engine"] == "tesseract"
    assert md.startswith("# Fixture Document")
    jp, th = md.index("名前はまだ無い"), md.index("ประกาศบริษัท")
    assert md.index("## Lists") < jp < th


@pytest.mark.parametrize("lang", ["eng", "jpn", "tha"])
def test_scanned_image_cer(http, lang):
    body = convert(http, f"scan-{lang}.jpg")
    assert body["metadata"]["ocr_pages"] == [1]
    error = cer(PAGES[lang][0], body["markdown"])
    assert error < 0.05, f"{lang} CER {error:.3f}:\n{body['markdown']}"


def test_scanned_pdf(http):
    body = convert(http, "scanned.pdf")
    md = body["markdown"]
    assert body["metadata"]["ocr_pages"] == [1, 2, 3]
    assert "Four score and seven years ago" in md
    assert "名前はまだ無い" in md
    assert "ประกาศบริษัท" in md


def test_rotated_scan_is_turned_upright(http):
    body = convert(http, "rotated.jpg")
    assert "Four score and seven years ago" in body["markdown"]


NOTO_SANS_JP = (
    "https://github.com/notofonts/noto-cjk/raw/Sans2.004/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf",
    "68a3fc98800b2a27b371f2fb79991daf3633bd89309d4ffaa6946fd587f375b5",
)


def noto_sans_jp() -> Path:
    """Noto Sans CJK JP, downloaded once (16 MB) and checked against its sha256."""
    import hashlib

    url, sha = NOTO_SANS_JP
    path = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "anydoc-serve-tests" / Path(url).name
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = httpx.get(url, follow_redirects=True, timeout=120).raise_for_status().content
        assert hashlib.sha256(data).hexdigest() == sha, "font download does not match its pinned sha256"
        path.write_bytes(data)
    return path


def test_mixed_japanese_english_page(http):
    """The page from the 2026-09-28 launch review: script detection called it
    Latin, and the Japanese line came back as `BRB BS 2026-0928 Aste Sq 12,800`."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(noto_sans_jp()), 48)
    image = Image.new("RGB", (1400, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 60), "請求書番号 2026-0928 合計金額 12,800円", font=font, fill="black")
    draw.text((40, 170), "Invoice total due by October 31", font=font, fill="black")
    buf = io.BytesIO()
    image.save(buf, "PNG")
    r = http.post("/v1/convert", files={"file": ("invoice.png", buf.getvalue())})
    assert r.status_code == 200, r.text
    md = r.json()["markdown"]
    assert "請求書番号" in md, md
    assert "12,800円" in md, md
    assert "Invoice total due by October 31" in md, md


def test_ocr_off_refuses_scans(http):
    r = http.post("/v1/convert", files={"file": ("s.pdf", (FIXTURES / "scanned.pdf").read_bytes())}, data={"ocr": "off"})
    assert r.status_code == 422
    assert r.json()["error"]["pages"] == [1, 2, 3]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_over_streamable_http():
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {KEY}"} if KEY else {}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30.0, read=300.0)) as http_client:
        async with Client(streamable_http_client(f"{URL}/mcp", http_client=http_client)) as mcp:
            tools = (await mcp.list_tools()).tools
            assert [t.name for t in tools] == ["convert_document"]
            data = base64.b64encode((FIXTURES / "scan-tha.jpg").read_bytes()).decode()
            result = await mcp.call_tool("convert_document", {"content_base64": data, "filename": "scan.jpg"})
            assert not result.is_error, result.content
            assert "ประกาศบริษัท" in result.content[0].text
