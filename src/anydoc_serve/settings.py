"""Configuration, read once from the environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = Field("0.0.0.0", description="Interface to bind.")
    port: int = Field(8080, description="Port to bind. Railway and most PaaS set $PORT.")
    api_key: str = Field(
        "",
        description="When set, every endpoint except /health requires `Authorization: Bearer <API_KEY>`.",
    )

    max_upload_mb: float = Field(50, description="Largest accepted document, uploaded or fetched by URL.")
    convert_timeout_s: float = Field(120, description="Wall-clock limit for one conversion, OCR included.")
    max_concurrent_conversions: int = Field(
        2,
        ge=1, description="Conversions running at once; the rest wait. OCR is CPU-bound, so keep this near the core count."
    )

    ocr_enabled: bool = Field(True, description="Set false to never OCR: scanned input then fails like plain anydoc.")
    ocr_langs: Annotated[list[str], NoDecode] = Field(
        ["eng", "jpn", "tha"],
        description="Comma-separated OCR languages, from: eng, jpn, tha.",
    )
    ocr_dpi: int = Field(200, ge=50, le=600, description="Resolution scanned PDF pages are rendered at before OCR.")
    ocr_max_pages: int = Field(200, description="A document needing OCR on more pages than this is refused.")

    fetch_timeout_s: float = Field(30, description="Limit for downloading a `{url}` source, redirects included.")
    allow_private_urls: bool = Field(
        False,
        description="Allow `{url}` sources that resolve to private, loopback or link-local addresses.",
    )

    @field_validator("ocr_langs", mode="before")
    @classmethod
    def _split_langs(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip().lower() for part in value.replace("+", ",").split(",") if part.strip()]
        return value

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)


@lru_cache
def get_settings() -> Settings:
    return Settings()
