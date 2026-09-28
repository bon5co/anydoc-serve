# syntax=docker/dockerfile:1
# anydoc-serve: anydoc + local Tesseract OCR behind an HTTP and MCP API.

# ---- Python dependencies ----------------------------------------------------
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# cysignals ships only an sdist, so the builder needs a C toolchain (the runtime does not).
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- OCR models: tessdata_fast 4.1.0 (see docs/ocr-benchmark.md) -------------
FROM python:3.12-slim AS tessdata
ADD --checksum=sha256:7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2 \
    https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/eng.traineddata /tessdata/eng.traineddata
ADD --checksum=sha256:1f5de9236d2e85f5fdf4b3c500f2d4926f8d9449f28f5394472d9e8d83b91b4d \
    https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/jpn.traineddata /tessdata/jpn.traineddata
ADD --checksum=sha256:294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c \
    https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/tha.traineddata /tessdata/tha.traineddata
ADD --checksum=sha256:9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff \
    https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/osd.traineddata /tessdata/osd.traineddata
RUN chmod 0755 /tessdata && chmod 0644 /tessdata/*

# ---- runtime -----------------------------------------------------------------
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/bon5co/anydoc-serve" \
      org.opencontainers.image.description="HTTP + MCP server for firecrawl/anydoc with local OCR (English, Japanese, Thai)" \
      org.opencontainers.image.licenses="MIT"
RUN useradd --create-home --uid 1000 app
COPY --from=tessdata /tessdata /usr/share/tessdata
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TESSDATA_PREFIX=/usr/share/tessdata \
    PORT=8080
USER app
WORKDIR /app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8080\")}/health', timeout=4)"]
CMD ["anydoc-serve"]
