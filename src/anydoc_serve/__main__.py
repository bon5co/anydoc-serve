"""`anydoc-serve` / `python -m anydoc_serve`: run the server with uvicorn."""

from __future__ import annotations

import logging

import uvicorn

from .settings import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        "anydoc_serve.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )


if __name__ == "__main__":
    main()
