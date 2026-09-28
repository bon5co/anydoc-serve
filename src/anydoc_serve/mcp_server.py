"""MCP server: one `convert_document` tool, served over streamable HTTP at /mcp."""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING, Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__
from .convert import ConversionError

if TYPE_CHECKING:
    from .app import Service


def build_mcp(service: Service) -> MCPServer:
    mcp = MCPServer(
        "anydoc-serve",
        version=__version__,
        instructions=(
            "Converts documents (Word, PowerPoint, Excel, OpenDocument, RTF, EPUB, CSV, PDF) and scanned "
            "PDFs or images to Markdown. Scanned pages are OCRed locally."
        ),
    )

    @mcp.tool()
    async def convert_document(
        url: Annotated[str | None, Field(description="http(s) URL of the document. Give this or content_base64.")] = None,
        content_base64: Annotated[
            str | None, Field(description="The document's bytes, base64-encoded. Give this or url.")
        ] = None,
        filename: Annotated[
            str | None, Field(description="Original file name; its extension helps with CSV and images.")
        ] = None,
        format: Annotated[str | None, Field(description="Force a format, e.g. `csv`. Default: detected.")] = None,
        ocr: Annotated[
            Literal["auto", "off"],
            Field(description="`auto` OCRs scanned pages and images locally; `off` refuses them."),
        ] = "auto",
    ) -> str:
        """Convert a document or scanned image to GitHub-Flavored Markdown.

        Supports .docx/.doc, .pptx/.ppt, .xlsx/.xls, .odt/.ods/.odp, .rtf, .epub, .csv, .pdf
        (text or scanned), and images (.png, .jpg, .tiff, .webp, ...). Returns the Markdown."""
        if (url is None) == (content_base64 is None):
            raise ToolError("give exactly one of `url` or `content_base64`")
        try:
            if url is not None:
                result = await service.convert_url(url, format_hint=format, ocr=ocr)
            else:
                try:
                    data = base64.b64decode(content_base64 or "", validate=True)
                except (binascii.Error, ValueError) as error:
                    raise ToolError(f"content_base64 is not valid base64: {error}") from error
                result = await service.convert(data, filename=filename, format_hint=format, ocr=ocr)
        except ConversionError as error:
            raise ToolError(f"{error.code}: {error.message}") from error
        return result.markdown

    return mcp
