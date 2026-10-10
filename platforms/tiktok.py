"""TikTok: Login Kit (desktop PKCE flow), Display API (profile + video numbers), Content Posting API (drafts).

Works with personal accounts. The Display API reports views, likes, comments and shares per
video - no watch time. Uploads go to the creator's TikTok inbox as a draft: the user opens the
notification in the app, adds caption and sound, and posts it there. That path does not need
the audit that direct public posting needs.
"""

import time
from urllib.parse import urlencode

import httpx

from ..config import Settings
from .base import (
    AccountInfo, AuthExpired, Credentials, PlatformError, Progress, SyncResult, UploadRequest, UploadResult,
    VideoSnapshot, as_int, challenge_hex, expires_at, is_expiring, read_chunks,
)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_URL = "https://open.tiktokapis.com/v2/user/info/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"
INBOX_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
ENV_KEYS = ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")
DEFAULT_SCOPES = "user.info.basic,user.info.profile,user.info.stats,video.list,video.upload"
USER_FIELDS = "open_id,avatar_url,display_name,username,follower_count,likes_count,video_count"
VIDEO_FIELDS = ("id,title,video_description,create_time,cover_image_url,share_url,duration,"
                "view_count,like_count,comment_count,share_count")
MAX_VIDEOS = 200
MB = 1_000_000  # decimal, so the 64 MB single-chunk limit holds whichever way TikTok counts
MIN_CHUNK, MAX_CHUNK, CHUNK = 5 * MB, 64 * MB, 16 * MB
STATUS_POLLS, STATUS_INTERVAL_S = 40, 3.0
DONE_STATES = {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}


def plan_chunks(size: int) -> list[int]:
    """Chunk sizes per TikTok's media transfer rules: one chunk up to 64 MB, otherwise 16 MB chunks
    with the trailing bytes merged into the last one."""
    if size <= 0:
        raise PlatformError("Die Videodatei ist leer.")
    if size <= MAX_CHUNK:
        return [size]
    count = size // CHUNK
    return [CHUNK] * (count - 1) + [size - CHUNK * (count - 1)]


def iso_from_unix(seconds) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(seconds or 0)))


class TikTok:
    platform = "tiktok"
    label = "TikTok"
    connect_mode = "oauth"

    def __init__(self, settings: Settings, http: httpx.Client, sleep=time.sleep):
        self.settings = settings
        self.http = http
        self.sleep = sleep

    # --- login -------------------------------------------------------------------------------
    def missing_config(self) -> list[str]:
        return [key for key in ENV_KEYS if not self.settings.get(key)]

    def authorize_url(self, state: str, redirect_uri: str, verifier: str) -> str:
        query = {
            "client_key": self.settings.get("TIKTOK_CLIENT_KEY"),
            "response_type": "code",
            "scope": self.settings.get("TIKTOK_SCOPES", DEFAULT_SCOPES),
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge_hex(verifier),
            "code_challenge_method": "S256",
            "disable_auto_auth": "1",  # always show the login, so a second account can be picked
        }
        return f"{AUTH_URL}?{urlencode(query)}"

    def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> Credentials:
        return self._token_request({"code": code, "grant_type": "authorization_code",
                                    "redirect_uri": redirect_uri, "code_verifier": verifier})

    def fresh(self, creds: Credentials) -> Credentials:
        if not is_expiring(creds):
            return creds
        return self._token_request({"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]})

    def _token_request(self, data: dict) -> Credentials:
        data = {**data, "client_key": self.settings.get("TIKTOK_CLIENT_KEY"),
                "client_secret": self.settings.get("TIKTOK_CLIENT_SECRET")}
        response = self.http.post(TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        payload = response.json() if response.content else {}
        if "access_token" not in payload:
            error = payload.get("error_description") or payload.get("error") or f"HTTP {response.status_code}"
            if data["grant_type"] == "refresh_token":
                raise AuthExpired(f"TikTok-Login abgelaufen ({error}). Bitte Konto neu verbinden.")
            raise PlatformError(f"TikTok-Login fehlgeschlagen: {error}")
        return {"access_token": payload["access_token"], "refresh_token": payload["refresh_token"],
                "open_id": payload.get("open_id", ""), "scope": payload.get("scope", ""),
                "expires_at": expires_at(payload.get("expires_in"))}

    # --- reading -----------------------------------------------------------------------------
    def _call(self, creds: Credentials, method: str, url: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {creds['access_token']}", "Content-Type": "application/json; charset=UTF-8"}
        response = self.http.request(method, url, headers=headers, **kwargs)
        payload = response.json() if response.content else {}
        error = payload.get("error", {})
        code = error.get("code", "ok") if isinstance(error, dict) else "ok"
        if response.status_code == 401 or code in ("access_token_invalid", "invalid_token"):
            raise AuthExpired("TikTok-Zugriff abgelaufen. Bitte Konto neu verbinden.")
        if code == "scope_not_authorized":
            raise PlatformError("TikTok: Berechtigung fehlt. Ist das Produkt in der TikTok-App freigeschaltet? Danach neu verbinden.")
        if response.status_code >= 400 or code != "ok":
            raise PlatformError(f"TikTok: {error.get('message') or code or response.status_code}")
        return payload.get("data", {})

    def fetch_account(self, creds: Credentials) -> AccountInfo:
        user = self._call(self.fresh(creds), "GET", USER_URL, params={"fields": USER_FIELDS}).get("user", {})
        return AccountInfo(
            platform=self.platform,
            external_id=user.get("open_id") or creds.get("open_id", ""),
            name=user.get("display_name", ""),
            handle=f"@{user['username']}" if user.get("username") else "",
            avatar_url=user.get("avatar_url", ""),
            followers=as_int(user.get("follower_count")),
            video_count=as_int(user.get("video_count")),
        )

    def sync(self, creds: Credentials) -> SyncResult:
        creds = self.fresh(creds)
        info = self.fetch_account(creds)
        videos = tuple(self._videos(creds))
        total = sum(v.views or 0 for v in videos)
        return SyncResult(account=AccountInfo(**{**info.__dict__, "total_views": total}), credentials=creds, videos=videos)

    def _videos(self, creds: Credentials):
        cursor, seen = None, 0
        while seen < MAX_VIDEOS:
            body = {"max_count": 20, **({"cursor": cursor} if cursor else {})}
            data = self._call(creds, "POST", VIDEO_LIST_URL, params={"fields": VIDEO_FIELDS}, json=body)
            for item in data.get("videos", []):
                seen += 1
                yield VideoSnapshot(
                    external_id=str(item["id"]),
                    title=(item.get("title") or item.get("video_description") or "").strip()[:150],
                    url=item.get("share_url", ""),
                    published_at=iso_from_unix(item.get("create_time")),
                    thumbnail_url=item.get("cover_image_url", ""),
                    duration_s=as_int(item.get("duration")),
                    views=as_int(item.get("view_count")),
                    likes=as_int(item.get("like_count")),
                    comments=as_int(item.get("comment_count")),
                    shares=as_int(item.get("share_count")),
                )
            if not data.get("has_more"):
                break
            cursor = data.get("cursor")

    # --- posting -----------------------------------------------------------------------------
    def upload(self, creds: Credentials, request: UploadRequest, progress: Progress) -> UploadResult:
        creds = self.fresh(creds)
        size = request.path.stat().st_size
        sizes = plan_chunks(size)
        init = self._call(creds, "POST", INBOX_INIT_URL, json={"source_info": {
            "source": "FILE_UPLOAD", "video_size": size, "chunk_size": sizes[0], "total_chunk_count": len(sizes)}})
        upload_url, publish_id = init.get("upload_url"), init.get("publish_id")
        if not upload_url:
            raise PlatformError("TikTok hat keine Upload-Adresse geliefert.")
        for offset, chunk in read_chunks(request.path, sizes):
            response = self.http.put(upload_url, content=chunk, timeout=300, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{size}"})
            if response.status_code not in (200, 201, 206):
                raise PlatformError(f"TikTok-Upload fehlgeschlagen: HTTP {response.status_code}")
            progress((offset + len(chunk)) / size * 0.9, "Lade zu TikTok hoch")
        return UploadResult(None, None, self._await_inbox(creds, publish_id, progress))

    def _await_inbox(self, creds: Credentials, publish_id: str, progress: Progress) -> str:
        for _ in range(STATUS_POLLS):
            status = self._call(creds, "POST", STATUS_URL, json={"publish_id": publish_id})
            state = status.get("status", "")
            if state in DONE_STATES:
                progress(1.0, "Entwurf zugestellt")
                return "Entwurf liegt in deinen TikTok-Benachrichtigungen: öffnen, Text einfügen, posten."
            if state == "FAILED":
                raise PlatformError(f"TikTok hat das Video abgelehnt: {status.get('fail_reason', 'unbekannt')}")
            progress(0.95, "TikTok verarbeitet")
            self.sleep(STATUS_INTERVAL_S)
        return "Hochgeladen, TikTok verarbeitet noch. Der Entwurf erscheint gleich in deinen Benachrichtigungen."
