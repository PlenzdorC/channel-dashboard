import json
from pathlib import Path

import httpx
import pytest

from dashboard.config import Settings
from dashboard.db import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    env = {"YOUTUBE_CLIENT_ID": "yt-id", "YOUTUBE_CLIENT_SECRET": "yt-secret",
           "TIKTOK_CLIENT_KEY": "tt-key", "TIKTOK_CLIENT_SECRET": "tt-secret"}
    return Settings(db_path=tmp_path / "test.db", env=env, base=tmp_path)


@pytest.fixture
def db(settings: Settings) -> Database:
    return Database(settings.db_path)


@pytest.fixture
def renders(tmp_path: Path) -> Path:
    """A minimal short-content tree with one niche and two finished shorts."""
    kit = tmp_path / "mindmatrix" / "_kit"
    kit.mkdir(parents=True)
    (kit / "kit.json").write_text("{}", encoding="utf-8")
    for number, slug, title in ((1, "hoer-auf-zu-nicken", "Hör auf zu nicken"), (2, "macht-blick", "Der Macht-Blick")):
        project = tmp_path / "mindmatrix" / f"{number}. {slug}"
        (project / "renders").mkdir(parents=True)
        (project / "kit.json").write_text(json.dumps({"title": title}), encoding="utf-8")
        (project / "renders" / f"{slug}.mp4").write_bytes(b"\x00" * 2048)
    return tmp_path


class Router:
    """httpx MockTransport that answers by (method, url prefix) and records every request."""

    def __init__(self):
        self.routes: list[tuple[str, str, object]] = []
        self.requests: list[httpx.Request] = []

    def add(self, method: str, prefix: str, response) -> None:
        self.routes.append((method, prefix, response))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for method, prefix, response in self.routes:
            if request.method == method and url.startswith(prefix):
                result = response(request) if callable(response) else response
                if isinstance(result, httpx.Response):
                    return result
                return httpx.Response(200, json=result)
        return httpx.Response(404, json={"error": {"message": f"no route for {request.method} {url}"}})


@pytest.fixture
def router() -> Router:
    return Router()


@pytest.fixture
def http(router: Router) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(router))
