"""Connector registry: one connector object per platform, sharing one HTTP client."""

import httpx

from ..config import Settings
from .base import Connector
from .instagram import Instagram
from .tiktok import TikTok
from .youtube import YouTube

PLATFORMS = ("youtube", "tiktok", "instagram")


def build_registry(settings: Settings, http: httpx.Client | None = None) -> dict[str, Connector]:
    if settings.demo:
        from ..demo import DemoConnector
        return {name: DemoConnector(name) for name in PLATFORMS}
    http = http or httpx.Client(timeout=60, follow_redirects=True)
    return {"youtube": YouTube(settings, http), "tiktok": TikTok(settings, http), "instagram": Instagram(settings, http)}
