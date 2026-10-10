"""Instagram: "Instagram API with Instagram Login" (professional accounts: Creator or Business).

Personal Instagram accounts have no API since December 2024. Connecting works with an access
token generated in the Meta app dashboard (Instagram > API setup > "Generate token" for an
account added as Instagram tester) - no HTTPS redirect needed for a local tool. Long-lived
tokens last 60 days and are refreshed here once a week.
"""

import time

import httpx

from ..config import Settings
from .base import (
    AccountInfo, AuthExpired, Credentials, PlatformError, Progress, SyncResult, UploadRequest, UploadResult,
    VideoSnapshot, as_float, as_int,
)

GRAPH = "https://graph.instagram.com"
DEFAULT_VERSION = "v24.0"
PROFESSIONAL = {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}
PROFILE_FIELDS = "user_id,username,name,account_type,profile_picture_url,followers_count,media_count"
MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,thumbnail_url,timestamp,like_count,comments_count"
FULL_METRICS = "views,reach,likes,comments,shares,saved,ig_reels_avg_watch_time,ig_reels_video_view_total_time"
BASIC_METRICS = "views,reach,likes,comments,shares,saved"
MAX_VIDEOS = 200
REFRESH_AFTER_S = 7 * 86400
PUBLISH_POLLS, PUBLISH_INTERVAL_S = 60, 5.0


def insight_values(payload: dict) -> dict[str, float]:
    """Insights response -> {metric: value}; handles both `values` and `total_value` shapes."""
    result = {}
    for item in payload.get("data", []):
        if "total_value" in item:
            result[item["name"]] = item["total_value"].get("value")
        elif item.get("values"):
            result[item["name"]] = item["values"][0].get("value")
    return result


class Instagram:
    platform = "instagram"
    label = "Instagram"
    connect_mode = "token"

    def __init__(self, settings: Settings, http: httpx.Client, sleep=time.sleep):
        self.settings = settings
        self.http = http
        self.sleep = sleep
        self.version = settings.get("INSTAGRAM_API_VERSION", DEFAULT_VERSION)

    def missing_config(self) -> list[str]:
        return []  # nothing in .env: each account brings its own token

    def _url(self, path: str) -> str:
        return f"{GRAPH}/{self.version}/{path.lstrip('/')}"

    def _call(self, method: str, url: str, token: str, **kwargs) -> dict:
        params = kwargs.pop("params", {})
        if "access_token=" not in url:  # paging links already carry it
            params = {**params, "access_token": token}
        response = self.http.request(method, url, params=params, **kwargs)
        payload = response.json() if response.content else {}
        if "error" in payload or response.status_code >= 400:
            error = payload.get("error", {}) if isinstance(payload.get("error"), dict) else {}
            if error.get("code") == 190 or response.status_code == 401:
                raise AuthExpired("Instagram-Token ungültig oder abgelaufen. Bitte neuen Token eintragen.")
            raise PlatformError(f"Instagram: {error.get('message') or response.status_code}")
        return payload

    # --- login -------------------------------------------------------------------------------
    def connect_with_token(self, token: str) -> Credentials:
        token = token.strip()
        if len(token) < 20:
            raise PlatformError("Das sieht nicht nach einem Instagram-Token aus.")
        profile = self._call("GET", self._url("me"), token, params={"fields": PROFILE_FIELDS})
        if profile.get("account_type") and profile["account_type"] not in PROFESSIONAL:
            raise PlatformError("Das ist ein privates Instagram-Konto. Bitte zuerst auf Creator umstellen.")
        return {"access_token": token, "refreshed_at": time.time()}

    def fresh(self, creds: Credentials) -> Credentials:
        if time.time() - float(creds.get("refreshed_at", 0)) < REFRESH_AFTER_S:
            return creds
        payload = self._call("GET", f"{GRAPH}/refresh_access_token", creds["access_token"],
                             params={"grant_type": "ig_refresh_token"})
        return {"access_token": payload["access_token"], "refreshed_at": time.time()}

    # --- reading -----------------------------------------------------------------------------
    def fetch_account(self, creds: Credentials) -> AccountInfo:
        profile = self._call("GET", self._url("me"), creds["access_token"], params={"fields": PROFILE_FIELDS})
        return AccountInfo(
            platform=self.platform,
            external_id=str(profile.get("user_id") or profile.get("id")),
            name=profile.get("name") or profile.get("username", ""),
            handle=f"@{profile['username']}" if profile.get("username") else "",
            avatar_url=profile.get("profile_picture_url", ""),
            followers=as_int(profile.get("followers_count")),
            video_count=as_int(profile.get("media_count")),
        )

    def sync(self, creds: Credentials) -> SyncResult:
        creds = self.fresh(creds)
        info = self.fetch_account(creds)
        videos, warnings = [], []
        for media in self._media(creds):
            if media.get("media_type") != "VIDEO" and media.get("media_product_type") != "REELS":
                continue
            try:
                stats = self._insights(creds, media["id"])
            except PlatformError as exc:
                stats, warnings = {}, [*warnings[:4], f"Insights für {media['id']}: {exc}"]
            avg_ms, total_ms = as_float(stats.get("ig_reels_avg_watch_time")), as_float(stats.get("ig_reels_video_view_total_time"))
            videos.append(VideoSnapshot(
                external_id=media["id"],
                title=(media.get("caption") or "").split("\n")[0][:150],
                url=media.get("permalink", ""),
                published_at=media.get("timestamp", "").replace("+0000", "Z"),
                thumbnail_url=media.get("thumbnail_url", ""),
                views=as_int(stats.get("views")),
                likes=as_int(stats.get("likes", media.get("like_count"))),
                comments=as_int(stats.get("comments", media.get("comments_count"))),
                shares=as_int(stats.get("shares")),
                saves=as_int(stats.get("saved")),
                reach=as_int(stats.get("reach")),
                avg_view_s=avg_ms / 1000 if avg_ms is not None else None,
                watch_time_min=total_ms / 60000 if total_ms is not None else None,
            ))
        total = sum(v.views or 0 for v in videos)
        return SyncResult(account=AccountInfo(**{**info.__dict__, "total_views": total}), credentials=creds,
                          videos=tuple(videos), warnings=tuple(warnings))

    def _media(self, creds: Credentials):
        url, params, seen = self._url("me/media"), {"fields": MEDIA_FIELDS, "limit": 50}, 0
        while url and seen < MAX_VIDEOS:
            page = self._call("GET", url, creds["access_token"], params=params)
            for item in page.get("data", []):
                seen += 1
                yield item
            url, params = page.get("paging", {}).get("next"), {}

    def _insights(self, creds: Credentials, media_id: str) -> dict:
        try:
            return insight_values(self._call("GET", self._url(f"{media_id}/insights"), creds["access_token"],
                                             params={"metric": FULL_METRICS}))
        except AuthExpired:
            raise
        except PlatformError:  # older media or a retired metric: retry with the basic set
            return insight_values(self._call("GET", self._url(f"{media_id}/insights"), creds["access_token"],
                                             params={"metric": BASIC_METRICS}))

    # --- posting (Reels, resumable upload of the local file) ---------------------------------
    def upload(self, creds: Credentials, request: UploadRequest, progress: Progress) -> UploadResult:
        creds = self.fresh(creds)
        token = creds["access_token"]
        container = self._call("POST", self._url("me/media"), token, data={
            "media_type": "REELS", "upload_type": "resumable", "caption": request.caption[:2200], "share_to_feed": "true"})
        container_id, upload_uri = container.get("id"), container.get("uri")
        if not container_id or not upload_uri:
            raise PlatformError("Instagram hat keinen Upload-Container angelegt.")
        size = request.path.stat().st_size
        progress(0.05, "Lade zu Instagram hoch")
        response = self.http.post(upload_uri, content=request.path.read_bytes(), timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
        if response.status_code >= 400:
            raise PlatformError(f"Instagram-Upload fehlgeschlagen: HTTP {response.status_code} {response.text[:200]}")
        progress(0.6, "Instagram verarbeitet")
        self._await_container(token, container_id)
        published = self._call("POST", self._url("me/media_publish"), token, data={"creation_id": container_id})
        media_id = published.get("id")
        if not media_id:
            raise PlatformError("Instagram hat das Reel nicht veröffentlicht. Bitte in der App nachsehen.")
        permalink = self._call("GET", self._url(media_id), token, params={"fields": "permalink"}).get("permalink")
        progress(1.0, "Veröffentlicht")
        return UploadResult(media_id, permalink, "Als Reel veröffentlicht.")

    def _await_container(self, token: str, container_id: str) -> None:
        for _ in range(PUBLISH_POLLS):
            status = self._call("GET", self._url(container_id), token, params={"fields": "status_code,status"})
            if status.get("status_code") == "FINISHED":
                return
            if status.get("status_code") in ("ERROR", "EXPIRED"):
                raise PlatformError(f"Instagram konnte das Video nicht verarbeiten: {status.get('status', '')}")
            self.sleep(PUBLISH_INTERVAL_S)
        raise PlatformError("Instagram verarbeitet das Video zu lange. Später in der App nachsehen.")
