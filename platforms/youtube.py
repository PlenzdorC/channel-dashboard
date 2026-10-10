"""YouTube: Data API (channel, videos, upload) + Analytics API (watch time, retention, per-day numbers).

One connected account = one channel. Login is the Google desktop OAuth flow (loopback redirect
to the dashboard itself); a Google login with several brand channels picks the channel there.
"""

import re
import time
from datetime import date, timedelta
from urllib.parse import urlencode

import httpx

from ..config import Settings
from .base import (
    AccountInfo, AuthExpired, Credentials, DailyPoint, PlatformError, Progress, SyncResult,
    UploadRequest, UploadResult, VideoSnapshot, as_float, as_int, challenge_b64, expires_at, is_expiring,
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
)
ENV_KEYS = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET")
MAX_VIDEOS = 200
DAILY_DAYS = 730  # two years: the 365-day view also needs its comparison year
RETENTION_VIDEOS = 10
UPLOAD_CHUNK = 8 * 1024 * 1024  # resumable uploads need multiples of 256 KiB
UPLOAD_RETRIES = 4
VIDEO_METRICS = ("views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
                 "shares", "subscribersGained")
DURATION_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")


def parse_duration(iso: str) -> float | None:
    """ISO 8601 duration (PT1M5S) -> seconds."""
    match = DURATION_RE.fullmatch(iso or "")
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def report_rows(payload: dict) -> list[dict]:
    """Analytics report -> list of {column: value} dicts."""
    names = [col["name"] for col in payload.get("columnHeaders", [])]
    return [dict(zip(names, row)) for row in payload.get("rows", []) or []]


class YouTube:
    platform = "youtube"
    label = "YouTube"
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
            "client_id": self.settings.get("YOUTUBE_CLIENT_ID"),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent select_account",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": challenge_b64(verifier),
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URL}?{urlencode(query)}"

    def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> Credentials:
        payload = self._token_request({
            "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code_verifier": verifier,
        })
        if not payload.get("refresh_token"):
            raise PlatformError("Google hat keinen Refresh-Token geliefert. Bitte erneut verbinden.")
        return self._credentials(payload, payload["refresh_token"])

    def _token_request(self, data: dict) -> dict:
        data = {**data, "client_id": self.settings.get("YOUTUBE_CLIENT_ID"),
                "client_secret": self.settings.get("YOUTUBE_CLIENT_SECRET")}
        response = self.http.post(TOKEN_URL, data=data)
        payload = response.json() if response.content else {}
        if response.status_code == 400 and payload.get("error") == "invalid_grant":
            raise AuthExpired("YouTube-Login abgelaufen oder widerrufen. Bitte Konto neu verbinden.")
        if response.status_code >= 400:
            raise PlatformError(f"Google-Login fehlgeschlagen: {payload.get('error_description') or payload.get('error') or response.status_code}")
        return payload

    @staticmethod
    def _credentials(payload: dict, refresh_token: str) -> Credentials:
        return {"access_token": payload["access_token"], "refresh_token": refresh_token,
                "expires_at": expires_at(payload.get("expires_in"))}

    def fresh(self, creds: Credentials) -> Credentials:
        if not is_expiring(creds):
            return creds
        payload = self._token_request({"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]})
        return self._credentials(payload, payload.get("refresh_token") or creds["refresh_token"])

    # --- reading -----------------------------------------------------------------------------
    def _get(self, creds: Credentials, url: str, params: dict) -> dict:
        response = self.http.get(url, params=params, headers={"Authorization": f"Bearer {creds['access_token']}"})
        if response.status_code == 401:
            raise AuthExpired("YouTube-Zugriff abgelaufen. Bitte Konto neu verbinden.")
        if response.status_code >= 400:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = f"HTTP {response.status_code}"
            raise PlatformError(f"YouTube: {message}")
        return response.json()

    def fetch_account(self, creds: Credentials) -> AccountInfo:
        return self._channel(self.fresh(creds))[0]

    def _channel(self, creds: Credentials) -> tuple[AccountInfo, dict]:
        items =self._get(creds, f"{API}/channels", {"part": "snippet,statistics,contentDetails", "mine": "true"}).get("items", [])
        if not items:
            raise PlatformError("Zu diesem Google-Login gibt es keinen YouTube-Kanal.")
        channel = items[0]
        snippet, stats = channel["snippet"], channel.get("statistics", {})
        info = AccountInfo(
            platform=self.platform,
            external_id=channel["id"],
            name=snippet.get("title", ""),
            handle=snippet.get("customUrl", ""),
            avatar_url=snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
            followers=None if stats.get("hiddenSubscriberCount") else as_int(stats.get("subscriberCount")),
            total_views=as_int(stats.get("viewCount")),
            video_count=as_int(stats.get("videoCount")),
        )
        return info, channel

    def sync(self, creds: Credentials) -> SyncResult:
        creds = self.fresh(creds)
        info, channel = self._channel(creds)
        uploads = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        video_ids = self._upload_ids(creds, uploads) if uploads else []
        warnings: list[str] = []
        start = channel["snippet"].get("publishedAt", "2005-01-01")[:10]
        try:
            analytics = self._video_analytics(creds, start)
        except PlatformError as exc:
            analytics, warnings = {}, [*warnings, f"Analytics nicht verfügbar: {exc}"]
        videos = tuple(self._videos(creds, video_ids, analytics))
        try:
            daily = self._daily(creds, max(start, (date.today() - timedelta(days=DAILY_DAYS)).isoformat()))
        except PlatformError as exc:
            daily, warnings = (), [*warnings, f"Tageswerte nicht verfügbar: {exc}"]
        retention = {}
        for video in sorted(videos, key=lambda v: v.published_at, reverse=True)[:RETENTION_VIDEOS]:
            try:
                points = self._retention(creds, video.external_id, start)
            except PlatformError:
                continue
            if points:
                retention[video.external_id] = points
        return SyncResult(account=info, credentials=creds, videos=videos, daily=daily,
                          retention=retention, warnings=tuple(warnings))

    def _upload_ids(self, creds: Credentials, playlist: str) -> list[str]:
        ids: list[str] = []
        token = None
        while len(ids) < MAX_VIDEOS:
            params = {"part": "contentDetails", "playlistId": playlist, "maxResults": 50}
            if token:
                params["pageToken"] = token
            page = self._get(creds, f"{API}/playlistItems", params)
            ids += [item["contentDetails"]["videoId"] for item in page.get("items", [])]
            token = page.get("nextPageToken")
            if not token:
                break
        return ids[:MAX_VIDEOS]

    def _video_analytics(self, creds: Credentials, start: str) -> dict[str, dict]:
        payload = self._get(creds, ANALYTICS_URL, {
            "ids": "channel==MINE", "startDate": start, "endDate": date.today().isoformat(),
            "dimensions": "video", "metrics": ",".join(VIDEO_METRICS), "sort": "-views", "maxResults": 200,
        })
        return {row["video"]: row for row in report_rows(payload)}

    def _videos(self, creds: Credentials, ids: list[str], analytics: dict[str, dict]):
        for i in range(0, len(ids), 50):
            page = self._get(creds, f"{API}/videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50])})
            for item in page.get("items", []):
                snippet, stats = item["snippet"], item.get("statistics", {})
                row = analytics.get(item["id"], {})
                thumbs = snippet.get("thumbnails", {})
                thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
                duration = parse_duration(item.get("contentDetails", {}).get("duration", ""))
                yield VideoSnapshot(
                    external_id=item["id"],
                    title=snippet.get("title", ""),
                    url=f"https://youtube.com/shorts/{item['id']}" if duration and duration <= 180 else f"https://youtu.be/{item['id']}",
                    published_at=snippet.get("publishedAt", ""),
                    thumbnail_url=thumb,
                    duration_s=duration,
                    views=as_int(stats.get("viewCount")),
                    likes=as_int(stats.get("likeCount")),
                    comments=as_int(stats.get("commentCount")),
                    shares=as_int(row.get("shares")),
                    watch_time_min=as_float(row.get("estimatedMinutesWatched")),
                    avg_view_s=as_float(row.get("averageViewDuration")),
                    avg_view_pct=as_float(row.get("averageViewPercentage")),
                    subs_gained=as_int(row.get("subscribersGained")),
                )

    def _daily(self, creds: Credentials, start: str) -> tuple[DailyPoint, ...]:
        payload = self._get(creds, ANALYTICS_URL, {
            "ids": "channel==MINE", "startDate": start, "endDate": date.today().isoformat(), "dimensions": "day",
            "metrics": "views,estimatedMinutesWatched,subscribersGained,subscribersLost", "sort": "day",
        })
        return tuple(
            DailyPoint(day=row["day"], views=as_int(row.get("views")), watch_time_min=as_float(row.get("estimatedMinutesWatched")),
                       subs_gained=as_int(row.get("subscribersGained")), subs_lost=as_int(row.get("subscribersLost")))
            for row in report_rows(payload)
        )

    def _retention(self, creds: Credentials, video_id: str, start: str) -> list[list[float]]:
        payload = self._get(creds, ANALYTICS_URL, {
            "ids": "channel==MINE", "startDate": start, "endDate": date.today().isoformat(),
            "dimensions": "elapsedVideoTimeRatio", "metrics": "audienceWatchRatio", "filters": f"video=={video_id}",
            "sort": "elapsedVideoTimeRatio",
        })
        return [[float(r["elapsedVideoTimeRatio"]), float(r["audienceWatchRatio"])] for r in report_rows(payload)]

    # --- posting -----------------------------------------------------------------------------
    def upload(self, creds: Credentials, request: UploadRequest, progress: Progress) -> UploadResult:
        creds = self.fresh(creds)
        size = request.path.stat().st_size
        status = {"privacyStatus": request.privacy, "selfDeclaredMadeForKids": False}
        if request.publish_at:
            status = {**status, "privacyStatus": "private", "publishAt": request.publish_at}
        body = {"snippet": {"title": request.title[:100], "description": request.caption[:5000], "categoryId": "22"},
                "status": status}
        auth = {"Authorization": f"Bearer {creds['access_token']}"}
        start = self.http.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, json=body,
                               headers={**auth, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
        if start.status_code >= 400 or "location" not in start.headers:
            raise PlatformError(f"YouTube-Upload abgelehnt: {start.text[:300]}")
        session = start.headers["location"]
        sent, failures = 0, 0
        with request.path.open("rb") as fh:
            while True:
                fh.seek(sent)
                chunk = fh.read(UPLOAD_CHUNK)
                span = f"{sent}-{sent + len(chunk) - 1}" if chunk else "*"  # everything sent: just ask for the result
                headers = {**auth, "Content-Length": str(len(chunk)), "Content-Range": f"bytes {span}/{size}"}
                try:
                    response = self.http.put(session, content=chunk, headers=headers, timeout=300)
                except httpx.TransportError:
                    response = None
                if response is not None and response.status_code in (200, 201):
                    progress(1.0, "Hochgeladen")
                    video_id = response.json()["id"]
                    note = f"Geplant für {request.publish_at}." if request.publish_at else f"Hochgeladen ({request.privacy})."
                    return UploadResult(video_id, f"https://youtube.com/shorts/{video_id}", note)
                if response is not None and response.status_code == 308:
                    sent, failures = received_bytes(response), 0  # resume where Google says it stands
                    progress(sent / size, "Lade zu YouTube hoch")
                    continue
                failures += 1
                if failures > UPLOAD_RETRIES or (response is not None and response.status_code < 500):
                    detail = f"HTTP {response.status_code} {response.text[:200]}" if response is not None else "keine Verbindung"
                    raise PlatformError(f"YouTube-Upload fehlgeschlagen: {detail}")
                self.sleep(2 ** failures)
                try:
                    sent = self._upload_status(session, size, auth)
                except httpx.TransportError:
                    pass  # retry the same chunk

    def _upload_status(self, session: str, size: int, auth: dict) -> int:
        """Ask the resumable session how many bytes arrived (after an error)."""
        response = self.http.put(session, headers={**auth, "Content-Length": "0", "Content-Range": f"bytes */{size}"})
        return received_bytes(response) if response.status_code == 308 else 0


def received_bytes(response: httpx.Response) -> int:
    """`Range: bytes=0-N` of a 308 answer -> N + 1; no header means nothing arrived yet."""
    match = re.match(r"bytes=0-(\d+)", response.headers.get("range", ""))
    return int(match.group(1)) + 1 if match else 0
