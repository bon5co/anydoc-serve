"""Download a `{url}` source with a size cap and no access to private networks.

A document server that fetches arbitrary URLs is an SSRF primitive: on a PaaS
it sits next to databases and metadata endpoints. Every hop of a redirect
chain is resolved and refused if any address it resolves to is private,
loopback, link-local or reserved, unless ALLOW_PRIVATE_URLS is set. The
address the connection actually reached is checked again before any of the
body is read, so a DNS answer that changes between the check and the connect
(DNS rebinding) is refused too.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import unquote, urljoin, urlparse

import httpx

from .convert import ConversionError

MAX_REDIRECTS = 5
NAT64 = ipaddress.ip_network("64:ff9b::/96")
SIX_TO_FOUR = ipaddress.ip_network("2002::/16")


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv6 forms that carry an IPv4 address: judge the embedded one
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip in NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip in SIX_TO_FOUR:
            ip = ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    return ip.is_global


def _refuse(host: str, address: str) -> ConversionError:
    return ConversionError(
        400, "url_not_allowed", f"{host} resolves to a non-public address ({address}); set ALLOW_PRIVATE_URLS"
    )


async def _check_host(host: str, port: int) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ConversionError(400, "fetch_failed", f"cannot resolve {host}: {error}") from error
    for info in infos:
        if not is_public(info[4][0]):
            raise _refuse(host, info[4][0])


def _check_peer(host: str, response: httpx.Response) -> None:
    stream = response.extensions.get("network_stream")
    peer = stream.get_extra_info("server_addr") if stream is not None else None
    if not peer:
        raise ConversionError(400, "fetch_failed", f"could not verify the address {host} connected to")
    if not is_public(peer[0]):
        raise _refuse(host, peer[0])


def filename_from(url: str, response: httpx.Response) -> str | None:
    disposition = response.headers.get("content-disposition", "")
    for part in disposition.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "filename" and value:
            return value.strip('"')
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return name or None


async def fetch(url: str, *, max_bytes: int, timeout: float, allow_private: bool) -> tuple[bytes, str | None]:
    """Return (body, filename). `timeout` bounds the whole download, redirects included."""
    try:
        async with asyncio.timeout(timeout):
            return await _fetch(url, max_bytes=max_bytes, timeout=timeout, allow_private=allow_private)
    except TimeoutError as error:
        raise ConversionError(400, "fetch_failed", f"fetching {url} took longer than {timeout:g}s") from error


async def _fetch(url: str, *, max_bytes: int, timeout: float, allow_private: bool) -> tuple[bytes, str | None]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ConversionError(400, "bad_url", f"only absolute http(s) URLs are fetched, got {url!r}")
            if not allow_private:
                await _check_host(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            try:
                async with client.stream("GET", url) as response:
                    if not allow_private:
                        _check_peer(parsed.hostname, response)
                    if response.has_redirect_location:
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
