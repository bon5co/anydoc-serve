"""HTTP API, auth and MCP tool, with a fake OCR engine (no Tesseract needed)."""

from __future__ import annotations

import base64
import json

import pytest
from mcp import Client

from anydoc_serve import app as app_module
from anydoc_serve.convert import ConversionError

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def upload(client, name, data, **fields):
    return client.post("/v1/convert", files={"file": (name, data)}, data=fields)


# ------------------------------------------------------------------ health & docs


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ocr"] == {"enabled": True, "engine": "fake", "langs": ["eng", "jpn", "tha"]}
    assert body["auth"] is False


def test_openapi_documents_convert(client):
    spec = client.get("/openapi.json").json()
    body = spec["paths"]["/v1/convert"]["post"]["requestBody"]["content"]
    assert set(body) == {"multipart/form-data", "application/json"}
    assert "url" in body["application/json"]["schema"]["properties"]
    assert client.get("/docs").status_code == 200


# ------------------------------------------------------------------ born-digital


def test_convert_docx(client, fixture_bytes):
    r = upload(client, "report.docx", fixture_bytes("anydoc-text.docx"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["markdown"].startswith("# Fixture Document")
    meta = body["metadata"]
    assert meta["format"] == "docx"
    assert meta["filename"] == "report.docx"
    assert meta["ocr_pages"] == []
    assert meta["bytes"] == len(fixture_bytes("anydoc-text.docx"))


def test_convert_text_pdf_needs_no_ocr(client, fake_ocr, fixture_bytes):
    r = upload(client, "a.pdf", fixture_bytes("anydoc-text.pdf"))
    assert r.status_code == 200, r.text
    assert r.json()["metadata"]["page_count"] == 2
    assert fake_ocr.calls == []


def test_markdown_output_by_field_query_and_accept(client, fixture_bytes):
    data = fixture_bytes("anydoc-text.docx")
    for response in (
        upload(client, "a.docx", data, output="markdown"),
        client.post("/v1/convert?output=markdown", files={"file": ("a.docx", data)}),
        client.post("/v1/convert", files={"file": ("a.docx", data)}, headers={"Accept": "text/markdown"}),
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert response.text.startswith("# Fixture Document")
        assert json.loads(response.headers["x-anydoc-metadata"])["format"] == "docx"


def test_csv_needs_its_extension(client):
    r = upload(client, "t.csv", b"a,b\n1,2\n")
    assert r.status_code == 200, r.text
    assert "| a | b |" in r.json()["markdown"]
    assert upload(client, "t", b"a,b\n1,2\n", format="csv").status_code == 200


def test_unrecognized_bytes_are_415(client):
    r = upload(client, "blob.bin", b"\x00\x01 definitely not a document")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported"


def test_empty_file_is_400(client):
    assert upload(client, "a.pdf", b"").json()["error"]["code"] == "empty"


# ------------------------------------------------------------------ OCR routing


def test_mixed_pdf_ocrs_only_scanned_pages(client, fake_ocr, fixture_bytes):
    r = upload(client, "mixed.pdf", fixture_bytes("mixed.pdf"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metadata"]["ocr_pages"] == [3, 4]
    assert body["metadata"]["page_count"] == 4
    assert body["metadata"]["ocr_engine"] == "fake"
    assert len(fake_ocr.calls) == 2
    md = body["markdown"]
    # anydoc's text pages first, then the OCR'd pages, in page order
    assert md.index("# Fixture Document") < md.index("OCR TEXT 1") < md.index("OCR TEXT 2")


def test_scanned_pdf_renders_at_ocr_dpi(client, fake_ocr, fixture_bytes):
    r = upload(client, "scanned.pdf", fixture_bytes("scanned.pdf"))
    assert r.status_code == 200, r.text
    assert r.json()["metadata"]["ocr_pages"] == [1, 2, 3]
    # fixture pages are 1654 px wide scans at 200 dpi; rendering at OCR_DPI=200 restores that width
    assert all(abs(w - 1654) <= 2 for w, _ in fake_ocr.calls)
    assert fake_ocr.dpis == [200, 200, 200]


def test_image_goes_to_ocr(client, fake_ocr, fixture_bytes):
    r = upload(client, "scan.jpg", fixture_bytes("scan-eng.jpg"))
    assert r.status_code == 200, r.text
    assert r.json()["markdown"] == "OCR TEXT 1\n"
    assert r.json()["metadata"]["format"] == "jpeg"
    assert len(fake_ocr.calls) == 1


def test_multipage_tiff_ocrs_every_frame(client, fake_ocr):
    import io

    from PIL import Image

    buf = io.BytesIO()
    frames = [Image.new("L", (300, 200), 255), Image.new("L", (400, 200), 255)]
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
    r = upload(client, "scan.tiff", buf.getvalue())
    assert r.status_code == 200, r.text
    assert r.json()["metadata"]["ocr_pages"] == [1, 2]
    assert r.json()["markdown"] == "OCR TEXT 1\n\nOCR TEXT 2\n"
    assert fake_ocr.calls == [(300, 200), (400, 200)]


def test_unreadable_image_is_422(client):
    r = upload(client, "broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "malformed"


def test_ocr_off_reports_pages(client, fixture_bytes):
    r = upload(client, "scanned.pdf", fixture_bytes("scanned.pdf"), ocr="off")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "needs_ocr"
    assert err["pages"] == [1, 2, 3]
    assert err["page_count"] == 3


def test_ocr_disabled_server(make_client, fixture_bytes):
    client = make_client(ocr_enabled=False)
    assert client.get("/health").json()["ocr"]["enabled"] is False
    r = upload(client, "scan.jpg", fixture_bytes("scan-eng.jpg"))
    assert r.status_code == 422
    assert "disabled" in r.json()["error"]["message"]


def test_bad_ocr_option(client, fixture_bytes):
    r = upload(client, "a.docx", fixture_bytes("anydoc-text.docx"), ocr="maybe")
    assert r.status_code == 400


# ------------------------------------------------------------------ limits


def test_upload_limit(make_client, fixture_bytes):
    client = make_client(max_upload_mb=0.05)  # 52 KB; the text PDF is 90 KB
    r = upload(client, "a.pdf", fixture_bytes("anydoc-text.pdf"))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_large"


def test_ocr_page_limit(make_client, fixture_bytes):
    client = make_client(ocr_max_pages=2)
    r = upload(client, "scanned.pdf", fixture_bytes("scanned.pdf"))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_many_ocr_pages"


def test_timeout(make_client, fixture_bytes, fake_ocr, monkeypatch):
    import time

    client = make_client(convert_timeout_s=0.2)
    monkeypatch.setattr(fake_ocr, "recognize", lambda image, dpi=None: time.sleep(1))
    r = upload(client, "scan.jpg", fixture_bytes("scan-eng.jpg"))
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "timeout"


def test_timed_out_conversion_keeps_its_slot_until_it_stops(make_client, fixture_bytes, fake_ocr, monkeypatch):
    import threading
    import time

    release = threading.Event()
    monkeypatch.setattr(fake_ocr, "recognize", lambda image, dpi=None: release.wait(5))
    client = make_client(convert_timeout_s=0.2, max_concurrent_conversions=1)
    service = client.app.state.service
    assert upload(client, "scan.jpg", fixture_bytes("scan-eng.jpg")).status_code == 504
    assert service.slots.locked()  # the OCR thread is still running
    release.set()
    for _ in range(50):
        if not service.slots.locked():
            break
        time.sleep(0.05)
    assert not service.slots.locked()


def test_chunked_body_over_limit_is_413(make_client):
    client = make_client(max_upload_mb=0.01)

    def chunks():
        for _ in range(100):
            yield b"x" * 65536

    r = client.post("/v1/convert", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_large"


def test_animated_gif_reads_first_frame_only(client, fake_ocr):
    import io

    from PIL import Image

    buf = io.BytesIO()
    frames = [Image.new("L", (50 + i, 40), 255) for i in range(3)]
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
    r = upload(client, "a.gif", buf.getvalue())
    assert r.status_code == 200, r.text
    assert fake_ocr.calls == [(50, 40)]


def test_decompression_bomb_is_413(client):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("1", (10000, 10000)).save(buf, "PNG")  # 100 Mpx, a few KB on disk
    r = upload(client, "bomb.png", buf.getvalue())
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "image_too_large"


def test_huge_pdf_page_renders_under_the_pixel_cap(client, fake_ocr):
    import io

    import pypdfium2 as pdfium

    from anydoc_serve.convert import MAX_RENDER_PIXELS

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(14400, 14400)  # 200 x 200 inches, blank: anydoc says it needs OCR
    buf = io.BytesIO()
    pdf.save(buf)
    r = upload(client, "huge.pdf", buf.getvalue())
    assert r.status_code == 200, r.text
    (w, h), dpi = fake_ocr.calls[0], fake_ocr.dpis[0]
    assert w * h <= MAX_RENDER_PIXELS * 1.01
    assert dpi < 200


def test_unsupported_content_type(client):
    r = client.post("/v1/convert", content=b"x", headers={"content-type": "text/plain"})
    assert r.status_code == 415


# ------------------------------------------------------------------ URL sources


def test_url_source(client, fixture_bytes, monkeypatch):
    seen = {}

    async def fake_fetch(url, **kwargs):
        seen.update(url=url, **kwargs)
        return fixture_bytes("anydoc-text.docx"), "remote.docx"

    monkeypatch.setattr(app_module, "fetch", fake_fetch)
    r = client.post("/v1/convert", json={"url": "https://example.com/files/remote.docx"})
    assert r.status_code == 200, r.text
    assert r.json()["metadata"]["filename"] == "remote.docx"
    assert seen["url"] == "https://example.com/files/remote.docx"
    assert seen["allow_private"] is False


def test_url_to_private_address_is_refused(client):
    r = client.post("/v1/convert", json={"url": "http://127.0.0.1:9/secret.pdf"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "url_not_allowed"


def test_url_bad_body(client):
    r = client.post("/v1/convert", json={"link": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_url_fetch_error_passes_through(client, monkeypatch):
    async def failing_fetch(url, **kwargs):
        raise ConversionError(400, "fetch_failed", "GET x answered 404", upstream_status=404)

    monkeypatch.setattr(app_module, "fetch", failing_fetch)
    r = client.post("/v1/convert", json={"url": "https://example.com/missing.pdf"})
    assert r.status_code == 400
    assert r.json()["error"] == {"code": "fetch_failed", "message": "GET x answered 404", "upstream_status": 404}


# ------------------------------------------------------------------ auth


def test_api_key_guards_everything_but_health(make_client, fixture_bytes):
    client = make_client(api_key="s3cret")
    assert client.get("/health").status_code == 200
    assert client.get("/health").json()["auth"] is True
    for method, path in (("get", "/docs"), ("get", "/openapi.json"), ("post", "/v1/convert"), ("post", "/mcp")):
        r = getattr(client, method)(path)
        assert r.status_code == 401, path
        assert r.headers["www-authenticate"] == "Bearer"
    bad = client.post(
        "/v1/convert", files={"file": ("a.docx", fixture_bytes("anydoc-text.docx"))}, headers={"Authorization": "Bearer nope"}
    )
    assert bad.status_code == 401
    lower = client.post(
        "/v1/convert", files={"file": ("a.docx", fixture_bytes("anydoc-text.docx"))}, headers={"Authorization": "bearer s3cret"}
    )
    assert lower.status_code == 200
    good = client.post(
        "/v1/convert", files={"file": ("a.docx", fixture_bytes("anydoc-text.docx"))}, headers={"Authorization": "Bearer s3cret"}
    )
    assert good.status_code == 200


# ------------------------------------------------------------------ MCP


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_tool_converts_base64_and_reports_errors(fixture_bytes, fake_ocr):
    from anydoc_serve.app import create_app
    from anydoc_serve.convert import Converter
    from anydoc_serve.settings import Settings

    app = create_app(Settings(_env_file=None), Converter(fake_ocr))
    async with Client(app.state.mcp, raise_exceptions=True) as mcp:
        tools = (await mcp.list_tools()).tools
        assert [t.name for t in tools] == ["convert_document"]
        props = tools[0].input_schema["properties"]
        assert {"url", "content_base64", "filename", "format", "ocr"} <= set(props)

        docx = base64.b64encode(fixture_bytes("anydoc-text.docx")).decode()
        result = await mcp.call_tool("convert_document", {"content_base64": docx, "filename": "a.docx"})
        assert not result.is_error
        assert result.content[0].text.startswith("# Fixture Document")

        both = await mcp.call_tool("convert_document", {})
        assert both.is_error and "exactly one" in both.content[0].text

        scanned = base64.b64encode(fixture_bytes("scanned.pdf")).decode()
        off = await mcp.call_tool("convert_document", {"content_base64": scanned, "ocr": "off"})
        assert off.is_error and "needs_ocr: 3 of 3 PDF pages" in off.content[0].text
