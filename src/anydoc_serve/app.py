"""FastAPI application: /v1/convert, /health, OpenAPI docs, and MCP at /mcp."""

from __future__ import annotations

import asyncio
import hmac
import threading
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field, HttpUrl, ValidationError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .convert import ConversionError, Converter, ConvertResult, OcrMode
from .fetch import fetch
from .ocr import build_engine
from .settings import Settings, get_settings

log = logging.getLogger("anydoc_serve")

Output = Literal["json", "markdown"]


class ConvertUrlRequest(BaseModel):
    url: HttpUrl = Field(description="http(s) URL of the document to fetch and convert.")
    format: str | None = Field(None, description="Force a format, e.g. `csv`. Default: detected from the bytes.")
    ocr: OcrMode = Field("auto", description="`auto` OCRs only what anydoc cannot read; `off` refuses scanned input.")
    output: Output | None = Field(
        None, description="`markdown` answers `text/markdown` instead of JSON. Default: `json`, or `Accept`."
    )


class ConvertResponse(BaseModel):
    markdown: str
    metadata: dict[str, Any] = Field(
        description="filename, bytes, format, page_count, ocr_pages (1-indexed pages that went through OCR), "
        "ocr_engine, ocr_langs, duration_ms."
    )


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class Service:
    """Holds the converter (and the loaded OCR model) plus the limits."""

    def __init__(self, settings: Settings, converter: Converter) -> None:
        self.settings = settings
        self.converter = converter
        self.slots = asyncio.Semaphore(settings.max_concurrent_conversions)
        self.fetches = asyncio.Semaphore(settings.max_concurrent_conversions * 2)

    async def convert(
        self, data: bytes, *, filename: str | None, format_hint: str | None, ocr: OcrMode
    ) -> ConvertResult:
        if len(data) > self.settings.max_upload_bytes:
            raise ConversionError(413, "too_large", f"document is larger than {self.settings.max_upload_mb:g} MB")
        # The slot is released when the worker thread finishes, not when the
        # request gives up: a timed-out conversion is told to stop at the next
        # page, and until it has, it still counts against the limit.
        await self.slots.acquire()
        cancel = threading.Event()
        try:
            task = asyncio.ensure_future(
                asyncio.to_thread(
                    self.converter.convert, data, filename=filename, format_hint=format_hint, ocr=ocr, cancel=cancel
                )
            )
        except BaseException:
            self.slots.release()
            raise
        task.add_done_callback(self._finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=self.settings.convert_timeout_s)
        except TimeoutError as error:
            cancel.set()
            raise ConversionError(
                504, "timeout", f"conversion took longer than {self.settings.convert_timeout_s:g}s"
            ) from error

    def _finished(self, task: asyncio.Future[ConvertResult]) -> None:
        self.slots.release()
        if not task.cancelled():
            task.exception()  # mark retrieved; a timed-out task's error has nobody left to report to

    async def convert_url(self, url: str, *, format_hint: str | None, ocr: OcrMode) -> ConvertResult:
        async with self.fetches:
            data, filename = await fetch(
                url,
                max_bytes=self.settings.max_upload_bytes,
                timeout=self.settings.fetch_timeout_s,
                allow_private=self.settings.allow_private_urls,
            )
        return await self.convert(data, filename=filename, format_hint=format_hint, ocr=ocr)


class _BodyTooLarge(Exception):
    pass


class BodyLimit:
    """ASGI middleware: refuse request bodies over `limit` bytes as they
    stream in, chunked or not, before anything buffers them."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        received = 0
        started = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if started:
                raise
            response = JSONResponse(
                {"error": {"code": "too_large", "message": f"request body is larger than {self.limit} bytes"}},
                status_code=413,
            )
            await response(scope, receive, send)


class BearerAuth:
    """ASGI middleware: when API_KEY is set, everything but /health needs
    `Authorization: Bearer <API_KEY>`. Pure ASGI so it also guards the
    mounted MCP app and streaming responses."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.expected = api_key.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # only lifespan passes unchecked, so a future websocket route cannot be open by accident
        if not self.expected or scope["type"] == "lifespan" or scope.get("path") == "/health":
            return await self.app(scope, receive, send)
        scheme, _, token = dict(scope["headers"]).get(b"authorization", b"").partition(b" ")
        if scheme.lower() == b"bearer" and hmac.compare_digest(token.strip(), self.expected):
            return await self.app(scope, receive, send)
        response = JSONResponse(
            {"error": {"code": "unauthorized", "message": "missing or wrong `Authorization: Bearer <API_KEY>`"}},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


def _error(error: ConversionError) -> JSONResponse:
    return JSONResponse({"error": {"code": error.code, "message": error.message, **error.detail}}, status_code=error.status)


def _wants_markdown(request: Request, output: str | None) -> bool:
    if output:
        return output == "markdown"
    accept = request.headers.get("accept", "")
    return "text/markdown" in accept and "application/json" not in accept


def _respond(result: ConvertResult, as_markdown: bool) -> Response:
    if as_markdown:
        return PlainTextResponse(
            result.markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"X-Anydoc-Metadata": json.dumps(result.metadata, ensure_ascii=True)},
        )
    return JSONResponse({"markdown": result.markdown, "metadata": result.metadata})


CONVERT_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {"type": "string", "format": "binary", "description": "The document or image."},
                    "format": {"type": "string", "description": "Force a format, e.g. `csv`."},
                    "ocr": {"type": "string", "enum": ["auto", "off"], "default": "auto"},
                    "output": {"type": "string", "enum": ["json", "markdown"], "default": "json"},
                },
            }
        },
        "application/json": {"schema": ConvertUrlRequest.model_json_schema()},
    },
}


def create_app(settings: Settings | None = None, converter: Converter | None = None) -> FastAPI:
    settings = settings or get_settings()
    if converter is None:
        engine = (
            build_engine(settings.ocr_langs, workers=settings.max_concurrent_conversions)
            if settings.ocr_enabled
            else None
        )
        converter = Converter(engine, ocr_dpi=settings.ocr_dpi, ocr_max_pages=settings.ocr_max_pages)
    service = Service(settings, converter)

    from .mcp_server import build_mcp  # after Service exists; MCP tools call into it

    mcp = build_mcp(service)
    mcp_app = mcp.streamable_http_app(
        stateless_http=True,
        # a document travels base64-encoded inside the JSON-RPC body
        max_request_body_size=settings.max_upload_bytes * 4 // 3 + 64 * 1024,
        json_response=True,
        # Served behind arbitrary hostnames (Railway, reverse proxies); access
        # control is the API_KEY bearer check, not a Host allowlist.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if not settings.api_key:
            log.warning("API_KEY is not set: every endpoint is open to anyone who can reach this port")
        if converter.ocr is not None:
            await asyncio.to_thread(converter.ocr.warm_up)
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="anydoc-serve",
        version=__version__,
        summary="Documents and scans to Markdown over HTTP and MCP: anydoc plus local OCR.",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.mcp = mcp

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, Any]:
        ocr = converter.ocr
        return {
            "status": "ok",
            "version": __version__,
            "ocr": {"enabled": ocr is not None, "engine": ocr.name if ocr else None, "langs": ocr.langs if ocr else []},
            "auth": bool(settings.api_key),
        }

    @app.post(
        "/v1/convert",
        tags=["convert"],
        summary="Convert a document or image to Markdown",
        description=(
            "Send the file as `multipart/form-data` (`file` field), or JSON `{\"url\": ...}` to have the server "
            "fetch it. Scanned PDF pages and images are OCRed locally; every other page goes through anydoc. "
            "Answers `{markdown, metadata}`, or the bare Markdown as `text/markdown` with `output=markdown` "
            "(form field, JSON field, or query) or `Accept: text/markdown`."
        ),
        response_model=ConvertResponse,
        responses={
            200: {"content": {"text/markdown": {"schema": {"type": "string"}}}},
            **{
                code: {"model": ErrorResponse, "description": text}
                for code, text in {
                    400: "Bad request or URL could not be fetched",
                    401: "Missing or wrong API key",
                    413: "Document over MAX_UPLOAD_MB, or too many pages need OCR",
                    415: "Format not recognized",
                    422: "Encrypted, malformed, or needs OCR with OCR off",
                    504: "Conversion over CONVERT_TIMEOUT_S",
                }.items()
            },
        },
        openapi_extra={"requestBody": CONVERT_BODY},
    )
    async def convert(request: Request, output: Output | None = None) -> Response:
        declared = int(request.headers.get("content-length") or 0)
        if declared > settings.max_upload_bytes + 1024 * 1024:  # multipart framing overhead
            return _error(ConversionError(413, "too_large", f"document is larger than {settings.max_upload_mb:g} MB"))
        content_type = request.headers.get("content-type", "")
        try:
            if content_type.startswith("multipart/form-data"):
                form = await request.form(max_files=1, max_fields=10)
                upload = form.get("file")
                if upload is None or isinstance(upload, str):
                    raise ConversionError(400, "no_file", "multipart body needs a `file` field")
                ocr = str(form.get("ocr") or "auto")
                if ocr not in ("auto", "off"):
                    raise ConversionError(400, "bad_option", "`ocr` must be `auto` or `off`")
                output = output or (str(form.get("output")) if form.get("output") else None)
                result = await service.convert(
                    await upload.read(),
                    filename=upload.filename,
                    format_hint=str(form.get("format")) if form.get("format") else None,
                    ocr=ocr,  # type: ignore[arg-type]
                )
            elif content_type.startswith("application/json"):
                try:
                    body = ConvertUrlRequest.model_validate_json(await request.body())
                except ValidationError as error:
                    return JSONResponse(
                        {"error": {"code": "bad_request", "message": "invalid JSON body", "errors": error.errors(include_url=False, include_context=False)}},
                        status_code=400,
                    )
                output = output or body.output  # None falls through to the Accept header
                result = await service.convert_url(str(body.url), format_hint=body.format, ocr=body.ocr)
            else:
                raise ConversionError(
                    415, "bad_content_type", "send multipart/form-data with a `file`, or application/json with a `url`"
                )
        except ConversionError as error:
            return _error(error)
        return _respond(result, _wants_markdown(request, output))

    # MCP streamable HTTP at /mcp. Mounted at "/" (after the routes above, so
    # they match first) with the SDK's default /mcp path: a Mount("/mcp")
    # would 307 /mcp -> /mcp/, which breaks clients behind TLS proxies.
    app.mount("/", mcp_app)
    app.add_middleware(BodyLimit, limit=settings.max_upload_bytes * 4 // 3 + 1024 * 1024)
    app.add_middleware(BearerAuth, api_key=settings.api_key)  # added last = runs first
    return app
