"""Download a `{url}` source with a size cap and no access to private networks.

A document server that fetches arbitrary URLs is an SSRF primitive: on a PaaS
it sits next to databases and metadata endpoints. Every hop of a redirect
chain is resolved and refused if any address it resolves to is private,
loopback, link-local or reserved, unless ALLOW_PRIVATE_URLS is set.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import unquote, urljoin, urlparse

import httpx

from .convert import ConversionError

MAX_REDIRECTS = 5


async def _check_host(host: str, port: int) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ConversionError(400, "fetch_failed", f"cannot resolve {host}: {error}") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ConversionError(
                400, "url_not_allowed", f"{host} resolves to a non-public address ({address}); set ALLOW_PRIVATE_URLS"
            )


def filename_from(url: str, response: httpx.Response) -> str | None:
    disposition = response.headers.get("content-disposition", "")
    for part in disposition.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "filename" and value:
            return value.strip('"')
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return name or None


async def fetch(url: str, *, max_bytes: int, timeout: float, allow_private: bool) -> tuple[bytes, str | None]:
    """Return (body, filename)."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ConversionError(400, "bad_url", f"only absolute http(s) URLs are fetched, got {url!r}")
            if not allow_private:
                await _check_host(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            try:
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code >= 400:
                        raise ConversionError(
                            400, "fetch_failed", f"GET {url} answered {response.status_code}", upstream_status=response.status_code
                        )
                    declared = int(response.headers.get("content-length") or 0)
                    if declared > max_bytes:
                        raise _too_large(max_bytes)
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise _too_large(max_bytes)
                        chunks.append(chunk)
                    return b"".join(chunks), filename_from(url, response)
            except httpx.HTTPError as error:
                raise ConversionError(400, "fetch_failed", f"GET {url} failed: {error}") from error
        raise ConversionError(400, "fetch_failed", f"more than {MAX_REDIRECTS} redirects")


def _too_large(max_bytes: int) -> ConversionError:
    return ConversionError(413, "too_large", f"document is larger than {max_bytes // (1024 * 1024)} MB (MAX_UPLOAD_MB)")
