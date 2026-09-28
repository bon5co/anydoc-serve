# anydoc-serve

**Office documents, PDFs and scans to Markdown over HTTP and MCP, in one Docker container, with local OCR for English, Japanese and Thai.**

anydoc-serve wraps [firecrawl/anydoc](https://github.com/firecrawl/anydoc), the fast Rust converter for Word, PowerPoint, Excel, OpenDocument, RTF, EPUB, CSV and PDF, in a server you can run anywhere:

```bash
docker run -p 8080:8080 ghcr.io/bon5co/anydoc-serve
curl -F file=@report.docx http://localhost:8080/v1/convert
```

## What it adds over plain anydoc

| | anydoc | anydoc-serve |
| --- | --- | --- |
| Office formats, text PDFs to Markdown | yes | yes, through anydoc, output unchanged |
| Run as a service | library and CLI | HTTP API, OpenAPI docs, Docker image ([anydoc#11](https://github.com/firecrawl/anydoc/issues/11)) |
| Scanned or image-only PDF pages | `NeedsOcr` error, or upload the whole file to Firecrawl's hosted OCR | OCR'd **locally**, on CPU; nothing leaves the container |
| Images (`.png`, `.jpg`, `.tiff`, `.webp`, ...) | not supported | OCR'd locally ([anydoc#146](https://github.com/firecrawl/anydoc/issues/146)) |
| Mixed PDFs (some pages scanned) | the whole file fails | text pages go through anydoc, **only the scanned pages** go through OCR ([anydoc#157](https://github.com/firecrawl/anydoc/issues/157)) |
| AI agents | agent skill (CLI) | MCP server at `/mcp` with a `convert_document` tool |
| Sideways or upside-down scans | | turned upright before OCR |

OCR is Tesseract 5 with language detection per page. It was picked by measuring it against RapidOCR/PaddleOCR on English, Japanese and Thai pages: see [the benchmark](#ocr-benchmark).

## Quickstart

```bash
docker run -d -p 8080:8080 -e API_KEY=change-me ghcr.io/bon5co/anydoc-serve
```

or `docker compose up -d` with the [`docker-compose.yml`](docker-compose.yml) in this repo.

Convert an upload:

```bash
curl -H "Authorization: Bearer change-me" -F file=@scan.pdf http://localhost:8080/v1/convert
```

```json
{
  "markdown": "# Quarterly report\n\n...",
  "metadata": {
    "filename": "scan.pdf", "bytes": 460689, "format": "pdf", "page_count": 4,
    "ocr_pages": [3, 4], "ocr_engine": "tesseract", "ocr_langs": ["eng", "jpn", "tha"], "duration_ms": 1735.0
  }
}
```

`ocr_pages` lists the 1-indexed pages that went through OCR; every other page came from anydoc.

Just the Markdown:

```bash
curl -H "Authorization: Bearer change-me" -H "Accept: text/markdown" -F file=@report.docx http://localhost:8080/v1/convert
```

Have the server fetch a URL:

```bash
curl -H "Authorization: Bearer change-me" -H "Content-Type: application/json" \
     -d '{"url": "https://example.com/report.pdf"}' http://localhost:8080/v1/convert
```

Interactive docs are at `/docs` (OpenAPI JSON at `/openapi.json`).

## API

`POST /v1/convert`

- `multipart/form-data`: `file` (required), `format`, `ocr`, `output`
- `application/json`: `{"url": "...", "format": ..., "ocr": ..., "output": ...}`

| Option | Values | Default | Meaning |
| --- | --- | --- | --- |
| `format` | `docx`, `pdf`, `csv`, ... | detected | Force the format. Needed for CSV sent without a `.csv` file name. |
| `ocr` | `auto`, `off` | `auto` | `off` refuses scanned input with `422 needs_ocr` and the page list, like anydoc. |
| `output` | `json`, `markdown` | `json` | `markdown` answers `text/markdown`, with the metadata as JSON in the `X-Anydoc-Metadata` header. Also accepted as a query parameter, or send `Accept: text/markdown`. |

Errors answer `{"error": {"code": ..., "message": ...}}`: `400` bad request or unfetchable URL, `401` missing or wrong key, `413` over `MAX_UPLOAD_MB` or `OCR_MAX_PAGES`, `415` unknown format, `422` encrypted, malformed, or needs OCR with OCR off, `504` over `CONVERT_TIMEOUT_S`.

`GET /health` answers `{"status": "ok", "ocr": {...}, "auth": true}` and never needs the key.

## MCP

The server speaks MCP over streamable HTTP at `/mcp`, with one tool, `convert_document(url | content_base64, filename?, format?, ocr?)`, which returns the Markdown.

Claude Code:

```bash
claude mcp add --transport http anydoc http://localhost:8080/mcp --header "Authorization: Bearer change-me"
```

Clients configured with an `mcpServers` JSON block that supports remote HTTP servers (Cursor, for example):

```json
{
  "mcpServers": {
    "anydoc": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": { "Authorization": "Bearer change-me" }
    }
  }
}
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8080` | Port to listen on. Railway and most platforms set it. |
| `API_KEY` | empty | When set, every endpoint except `/health` requires `Authorization: Bearer <API_KEY>`. Empty means open, and the server logs a warning at startup. |
| `OCR_LANGS` | `eng,jpn,tha` | OCR languages, comma-separated: `eng`, `jpn`, `tha` (`en`, `ja`, `th` also accepted). Each page is read with the language its script is detected as; a page in no configured script is read with all of them. |
| `OCR_ENABLED` | `true` | `false` never OCRs: scanned input fails with `422`, as in plain anydoc. |
| `OCR_DPI` | `200` | Resolution scanned PDF pages are rendered at for OCR. |
| `OCR_MAX_PAGES` | `200` | A document needing OCR on more pages than this is refused with `413`. |
| `MAX_UPLOAD_MB` | `50` | Largest document accepted, uploaded or fetched. |
| `CONVERT_TIMEOUT_S` | `120` | Wall-clock limit per conversion, OCR included; over it answers `504`. |
| `MAX_CONCURRENT_CONVERSIONS` | `2` | Conversions that run at once (the rest wait). Each slot holds its own loaded OCR models. |
| `FETCH_TIMEOUT_S` | `30` | Limit for downloading a `{"url": ...}` source, redirects included. |
| `ALLOW_PRIVATE_URLS` | `false` | Allow URL sources that resolve to private, loopback or link-local addresses. Off by default so the server cannot be used to reach its own network. |

## Resource use

Measured on `linux/amd64`, 2026-09-28:

- **Image:** 376 MB on disk (94 MB compressed download). Tesseract models for English, Japanese, Thai and script detection are 18 MB of that.
- **Memory:** 321 MiB RSS idle after startup (OCR models loaded for both conversion slots), 399 to 408 MiB after a round of conversions (`docker stats`, two runs).
- **Speed:** a born-digital `.docx` converts in about 2 ms; OCR takes about 1 s per page (median, 4 cores of an Intel i5-10500T). A 4-page PDF with 2 scanned pages took 1.5 to 1.7 s.

## OCR benchmark

Character error rate on synthetic 200 dpi pages (degraded "scan" and clean render), two pages per language, and median seconds per page. Full method, per-engine rows and reproduction steps: [docs/ocr-benchmark.md](docs/ocr-benchmark.md).

| Engine | eng scan | jpn scan | tha scan | eng clean | jpn clean | tha clean | s/page |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **anydoc-serve as shipped** (Tesseract 5.5 `tessdata_fast`, Sauvola, per-page script detection) | 0.0% | 4.7% | 0.0% | 0.0% | 2.3% | 0.0% | 1.00 |
| Tesseract 5.5 `tessdata_fast`, defaults, page language given | 0.0% | 2.8% | 33.4% | 0.0% | 2.8% | 0.0% | 0.36 |
| Tesseract 5.5 `tessdata_fast`, defaults, `eng+jpn+tha` in one pass | 0.1% | 3.0% | 35.0% | 0.0% | 12.9% | 0.0% | 1.11 |
| RapidOCR 3.9 PP-OCRv5 mobile | 7.8% | 10.5% | 0.4% | 24.7% | 1.0% | 0.0% | 5.68 |
| RapidOCR 3.9 PP-OCRv6 small (Thai: PP-OCRv5) | 7.6% | 0.2% | 1.5% | 8.1% | 0.2% | 10.7% | 7.39 |

RapidOCR reads Japanese better, but was 6 to 7 times slower and dropped whole lines of English. Synthetic pages give exact ground truth but are not your documents: treat these as a comparison between engines, not an accuracy promise.

## Deploy on Railway

TODO: Railway template link.

## Development

```bash
uv sync
uv run pytest                      # unit tests, no Tesseract needed
docker build -t anydoc-serve .
docker run -d -p 8080:8080 -e API_KEY=test-key anydoc-serve
ANYDOC_SERVE_URL=http://127.0.0.1:8080 ANYDOC_SERVE_API_KEY=test-key uv run pytest -m integration
```

Test fixtures `anydoc-text.docx` and `anydoc-text.pdf` are copied from anydoc's own test corpus (MIT); the scanned fixtures are generated by `tests/fixtures/make_fixtures.py`.

## Credits and license

The document conversion is all [anydoc](https://github.com/firecrawl/anydoc) by [Firecrawl](https://firecrawl.dev) (MIT). OCR is [Tesseract](https://github.com/tesseract-ocr/tesseract) (Apache 2.0) through [tesserocr](https://github.com/sirfz/tesserocr) (MIT); PDF pages are rendered with [pypdfium2](https://github.com/pypdfium2-team/pypdfium2). anydoc-serve is not affiliated with Firecrawl.

MIT, see [LICENSE](LICENSE).
