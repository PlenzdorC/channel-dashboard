"""Local web server: JSON API for the dashboard page, OAuth callbacks, render previews.

Bound to 127.0.0.1 only. Host header pinned (no DNS rebinding) and every state-changing request
must be JSON, which a foreign web page cannot send without a CORS preflight this server never grants.
"""

import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import metrics
from .config import STATIC, Settings, find_niches
from .db import Database
from .pipeline import find_renders, resolve_render
from .platforms import PLATFORMS
from .platforms.base import Connector, PlatformError, new_verifier
from .publish import Publisher, Target
from .sync import SyncManager, link_slugs

STATE_TTL_S = 600
SECURITY_HEADERS = {
    # thumbnails and avatars come from the platforms' CDNs; everything else is served from here
    "Content-Security-Policy": "default-src 'self'; img-src 'self' https: data:; media-src 'self'; "
                               "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
CALLBACK_PATHS = {"youtube": "/oauth/youtube/callback", "tiktok": "/oauth/tiktok/callback/"}


@dataclass(frozen=True)
class PendingLogin:
    platform: str
    verifier: str
    created: float


class PendingLogins:
    """OAuth `state` -> PKCE verifier, kept in memory for ten minutes."""

    def __init__(self):
        self._items: dict[str, PendingLogin] = {}
        self._lock = threading.Lock()

    def add(self, platform: str) -> tuple[str, str]:
        state, verifier = secrets.token_urlsafe(24), new_verifier()
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if time.time() - v.created < STATE_TTL_S}
            self._items[state] = PendingLogin(platform, verifier, time.time())
        return state, verifier

    def take(self, state: str, platform: str) -> PendingLogin | None:
        with self._lock:
            item = self._items.pop(state, None)
        if item and item.platform == platform and time.time() - item.created < STATE_TTL_S:
            return item
        return None


class NicheBody(BaseModel):
    niche: str | None = None


class SlugBody(BaseModel):
    slug: str | None = None


class TokenBody(BaseModel):
    token: str = Field(min_length=20, max_length=1000)
    niche: str | None = None


class SyncBody(BaseModel):
    account_id: str | None = None


class TargetBody(BaseModel):
    account_id: str
    privacy: str = "private"
    publish_at: str | None = None


class PostBody(BaseModel):
    render: str
    title: str = Field(min_length=1, max_length=100)
    caption: str = Field(default="", max_length=2200)
    targets: list[TargetBody] = Field(min_length=1)


def create_app(settings: Settings, db: Database, registry: dict[str, Connector], sync: SyncManager,
               publisher: Publisher) -> FastAPI:
    app = FastAPI(title="Kanal-Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    logins = PendingLogins()
    niches = find_niches(settings.base)

    @app.middleware("http")
    async def json_only_writes(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and \
                not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"detail": "JSON erwartet"}, status_code=415)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # revalidate, so an updated dashboard loads at once
        return response

    def redirect_uri(platform: str) -> str:
        return settings.redirect_base + CALLBACK_PATHS[platform]

    def filters(platform: str | None, account: str | None, niche: str | None) -> dict:
        return {"platform": platform, "account": account, "niche": niche}

    # --- page --------------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/media/render")
    def render_file(path: str):
        try:
            return FileResponse(resolve_render(settings.base, path), media_type="video/mp4")
        except ValueError:
            raise HTTPException(404, "Video nicht gefunden")

    # --- state & numbers ---------------------------------------------------------------------
    @app.get("/api/state")
    def state():
        return {
            "demo": settings.demo,
            "niches": niches,
            "platforms": [{"id": p, "label": registry[p].label, "connect_mode": registry[p].connect_mode,
                           "missing": registry[p].missing_config(),
                           "redirect_uri": redirect_uri(p) if p in CALLBACK_PATHS else None} for p in PLATFORMS],
            "accounts": db.accounts(),
            "sync": sync.state(),
        }

    @app.get("/api/overview")
    def overview(days: int = 28, platform: str | None = None, account: str | None = None, niche: str | None = None):
        if days not in (7, 28, 90, 365):
            raise HTTPException(400, "Zeitraum 7, 28, 90 oder 365 Tage")
        return metrics.overview(db, filters(platform, account, niche), days)

    @app.get("/api/videos")
    def videos(platform: str | None = None, account: str | None = None, niche: str | None = None):
        return metrics.videos(db, filters(platform, account, niche))

    @app.get("/api/videos/{video_id}")
    def video(video_id: str):
        detail = metrics.video_detail(db, video_id)
        if not detail:
            raise HTTPException(404, "Video nicht gefunden")
        return detail

    @app.post("/api/videos/{video_id}/slug")
    def set_slug(video_id: str, body: SlugBody):
        known = {r.slug for r in find_renders(settings.base)}
        if body.slug and body.slug not in known:
            raise HTTPException(400, "Unbekannter Slug")
        db.set_slug(video_id, body.slug, "manual")
        return {"ok": True}

    @app.get("/api/shorts")
    def shorts():
        return metrics.by_slug(db, find_renders(settings.base))

    @app.get("/api/renders")
    def renders():
        return [r.__dict__ for r in find_renders(settings.base)]

    # --- accounts ----------------------------------------------------------------------------
    @app.post("/api/accounts/{account_id}")
    def update_account(account_id: str, body: NicheBody):
        if not db.account(account_id):
            raise HTTPException(404, "Konto nicht gefunden")
        if body.niche and body.niche not in niches:
            raise HTTPException(400, "Unbekannte Nische")
        db.set_niche(account_id, body.niche or None)
        link_slugs(db, settings.base, account_id)
        return {"ok": True}

    @app.delete("/api/accounts/{account_id}")
    def remove_account(account_id: str):
        db.delete_account(account_id)
        return {"ok": True}

    @app.get("/connect/{platform}")
    def connect(platform: str):
        connector = registry.get(platform)
        if not connector or connector.connect_mode != "oauth":
            raise HTTPException(404, "Unbekannte Plattform")
        if settings.demo:
            return back_to_accounts(fehler="Im Demo-Modus werden keine echten Konten verbunden")
        if connector.missing_config():
            return back_to_accounts(fehler=f"Zugangsdaten für {connector.label} fehlen in .env")
        state, verifier = logins.add(platform)
        return RedirectResponse(connector.authorize_url(state, redirect_uri(platform), verifier))

    def back_to_accounts(**message: str) -> RedirectResponse:
        return RedirectResponse(f"/#konten?{urlencode(message)}", status_code=303)

    def finish_login(platform: str, request: Request):
        params = request.query_params
        pending = logins.take(params.get("state", ""), platform)
        if not pending:  # only a login this dashboard started may put text on the page
            return back_to_accounts(fehler="Login abgelaufen, bitte noch einmal verbinden")
        if params.get("error") or not params.get("code"):
            reason = (params.get("error_description") or params.get("error") or "kein Code")[:200]
            return back_to_accounts(fehler=f"Login abgebrochen: {reason}")
        connector = registry[platform]
        try:
            creds = connector.exchange_code(params["code"], redirect_uri(platform), pending.verifier)
            info = connector.fetch_account(creds)
        except PlatformError as exc:
            return back_to_accounts(fehler=str(exc))
        key = db.save_account(info, creds)
        sync.start(key)
        return back_to_accounts(verbunden=info.name)

    @app.get("/oauth/youtube/callback")
    def youtube_callback(request: Request):
        return finish_login("youtube", request)

    @app.get("/oauth/tiktok/callback/")
    @app.get("/oauth/tiktok/callback")
    def tiktok_callback(request: Request):
        return finish_login("tiktok", request)

    @app.post("/api/connect/instagram")
    def connect_instagram(body: TokenBody):
        connector = registry["instagram"]
        try:
            creds = connector.connect_with_token(body.token)
            info = connector.fetch_account(creds)
        except PlatformError as exc:
            raise HTTPException(400, str(exc))
        key = db.save_account(info, creds, body.niche if body.niche in niches else None)
        sync.start(key)
        return {"ok": True, "account_id": key, "name": info.name}

    # --- sync & posting ----------------------------------------------------------------------
    @app.post("/api/sync")
    def start_sync(body: SyncBody):
        return {"started": sync.start(body.account_id), **sync.state()}

    @app.get("/api/sync")
    def sync_state():
        return sync.state()

    @app.post("/api/post")
    def post(body: PostBody):
        try:
            job = publisher.submit(body.render, body.title.strip(), body.caption.strip(),
                                   [Target(t.account_id, t.privacy, t.publish_at or None) for t in body.targets])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"job_id": job}

    @app.get("/api/posts")
    def posts():
        return db.jobs()

    return app
