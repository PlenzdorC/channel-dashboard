import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from dashboard.platforms.base import AuthExpired, PlatformError, UploadRequest, challenge_b64, challenge_hex
from dashboard.platforms.instagram import Instagram, insight_values
from dashboard.platforms.tiktok import MAX_CHUNK, MB, TikTok, plan_chunks
from dashboard.platforms.youtube import YouTube, parse_duration

FRESH = {"access_token": "tok", "refresh_token": "ref", "expires_at": time.time() + 3600}


# --- shared helpers -----------------------------------------------------------------------------
def test_pkce_challenges_follow_each_platforms_encoding():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # RFC 7636 appendix B
    assert challenge_b64(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert challenge_hex(verifier) == "13d31e961a1ad8ec2f16b10c4c982e0876a878ad6df144566ee1894acb70f9c3"


# --- YouTube ------------------------------------------------------------------------------------
@pytest.mark.parametrize("iso, seconds", [("PT45S", 45), ("PT1M5S", 65), ("PT1H", 3600), ("P0D", 0), ("", None), ("bogus", None)])
def test_parse_duration(iso, seconds):
    assert parse_duration(iso) == seconds


def test_youtube_authorize_url_requests_offline_access_with_pkce(settings, http):
    url = YouTube(settings, http).authorize_url("state1", "http://127.0.0.1:8765/oauth/youtube/callback", "v" * 64)
    query = parse_qs(urlparse(url).query)
    assert query["access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]
    assert "yt-analytics.readonly" in query["scope"][0]
    assert query["client_id"] == ["yt-id"]


def test_youtube_refresh_invalid_grant_means_reconnect(settings, http, router):
    router.add("POST", "https://oauth2.googleapis.com/token", httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(AuthExpired):
        YouTube(settings, http).fresh({"access_token": "x", "refresh_token": "r", "expires_at": 0})


def youtube_routes(router):
    router.add("GET", "https://www.googleapis.com/youtube/v3/channels", {"items": [{
        "id": "UC1", "snippet": {"title": "MindMatrix", "customUrl": "@mindmatrix", "publishedAt": "2026-09-01T00:00:00Z",
                                 "thumbnails": {"default": {"url": "https://yt3/avatar.jpg"}}},
        "statistics": {"subscriberCount": "12", "viewCount": "900", "videoCount": "2"},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
    router.add("GET", "https://www.googleapis.com/youtube/v3/playlistItems", {"items": [
        {"contentDetails": {"videoId": "v1"}}, {"contentDetails": {"videoId": "v2"}}]})
    router.add("GET", "https://www.googleapis.com/youtube/v3/videos", {"items": [
        {"id": "v1", "snippet": {"title": "Hör auf zu nicken", "publishedAt": "2026-10-08T16:00:00Z", "thumbnails": {}},
         "statistics": {"viewCount": "256", "likeCount": "1", "commentCount": "0"}, "contentDetails": {"duration": "PT45S"}},
        {"id": "v2", "snippet": {"title": "Langes Video", "publishedAt": "2026-09-08T16:00:00Z", "thumbnails": {}},
         "statistics": {"viewCount": "10"}, "contentDetails": {"duration": "PT10M"}}]})

    def analytics(request):
        params = dict(request.url.params)
        if params["dimensions"] == "video":
            return {"columnHeaders": [{"name": n} for n in ("video", "views", "estimatedMinutesWatched", "averageViewDuration",
                                                            "averageViewPercentage", "shares", "subscribersGained")],
                    "rows": [["v1", 250, 92.5, 22, 49.2, 3, 1]]}
        if params["dimensions"] == "day":
            return {"columnHeaders": [{"name": n} for n in ("day", "views", "estimatedMinutesWatched", "subscribersGained", "subscribersLost")],
                    "rows": [["2026-10-07", 100, 40.0, 1, 0]]}
        if params.get("filters") == "video==v1":
            return {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}],
                    "rows": [[0.01, 1.2], [0.5, 0.6], [1.0, 0.3]]}
        return httpx.Response(400, json={"error": {"message": "no data"}})

    router.add("GET", "https://youtubeanalytics.googleapis.com/v2/reports", analytics)


def test_youtube_sync_merges_data_api_and_analytics(settings, http, router):
    youtube_routes(router)
    result = YouTube(settings, http).sync(FRESH)
    assert result.account.followers == 12 and result.account.handle == "@mindmatrix"
    short = next(v for v in result.videos if v.external_id == "v1")
    assert short.views == 256  # fresh Data API count, not the lagging analytics number
    assert short.avg_view_pct == 49.2 and short.watch_time_min == 92.5 and short.subs_gained == 1
    assert short.url == "https://youtube.com/shorts/v1"
    assert next(v for v in result.videos if v.external_id == "v2").url == "https://youtu.be/v2"
    assert result.daily[0].views == 100
    assert result.retention["v1"][0] == [0.01, 1.2]
    assert "v2" not in result.retention  # its retention request failed and is skipped


def test_youtube_sync_survives_missing_analytics(settings, http, router):
    youtube_routes(router)
    router.routes = [r for r in router.routes if "youtubeanalytics" not in r[1]]
    router.add("GET", "https://youtubeanalytics.googleapis.com", httpx.Response(403, json={"error": {"message": "forbidden"}}))
    result = YouTube(settings, http).sync(FRESH)
    assert len(result.videos) == 2 and result.videos[0].avg_view_pct is None
    assert any("Analytics" in w for w in result.warnings)


def test_youtube_upload_is_resumable_and_reports_progress(settings, http, router, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 1000)
    router.add("POST", "https://www.googleapis.com/upload/youtube/v3/videos",
               httpx.Response(200, headers={"location": "https://upload.example/session"}))
    router.add("PUT", "https://upload.example/session", {"id": "new1"})
    seen = []
    result = YouTube(settings, http).upload(FRESH, UploadRequest(video, "Titel", "Text", "unlisted"), lambda f, m: seen.append(f))
    assert result.external_id == "new1" and result.url.endswith("/new1")
    assert seen[-1] == 1.0
    put = router.requests[-1]
    assert put.headers["Content-Range"] == "bytes 0-999/1000"


def test_youtube_upload_resumes_from_range_and_retries_server_errors(settings, http, router, tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.platforms.youtube.UPLOAD_CHUNK", 400)
    video = tmp_path / "clip.mp4"
    video.write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    router.add("POST", "https://www.googleapis.com/upload/youtube/v3/videos",
               httpx.Response(200, headers={"location": "https://upload.example/s"}))
    answers = iter([
        httpx.Response(308, headers={"range": "bytes=0-299"}),  # Google kept only 300 of the first 400
        httpx.Response(503),                                     # server hiccup
        httpx.Response(308, headers={"range": "bytes=0-699"}),  # status query after the error
        httpx.Response(308, headers={"range": "bytes=0-1023"}),
        httpx.Response(200, json={"id": "done1"}),
    ])
    router.add("PUT", "https://upload.example/s", lambda request: next(answers))
    youtube = YouTube(settings, http, sleep=lambda s: None)
    result = youtube.upload(FRESH, UploadRequest(video, "T", "C"), lambda f, m: None)
    assert result.external_id == "done1"
    ranges = [r.headers["Content-Range"] for r in router.requests if r.method == "PUT"]
    assert ranges == ["bytes 0-399/1024", "bytes 300-699/1024", "bytes */1024", "bytes 700-1023/1024", "bytes */1024"]


# --- TikTok -------------------------------------------------------------------------------------
@pytest.mark.parametrize("size, expected", [
    (3 * MB, [3 * MB]),
    (MAX_CHUNK, [MAX_CHUNK]),
    (100 * MB, [16 * MB] * 5 + [20 * MB]),
])
def test_plan_chunks_follows_media_transfer_rules(size, expected):
    chunks = plan_chunks(size)
    assert chunks == expected and sum(chunks) == size
    assert all(5 * MB <= c <= 64 * MB for c in chunks[:-1]) and chunks[-1] <= 128 * MB


def test_plan_chunks_rejects_empty_file():
    with pytest.raises(PlatformError):
        plan_chunks(0)


def test_tiktok_authorize_url_uses_hex_challenge(settings, http):
    url = TikTok(settings, http).authorize_url("s", "http://127.0.0.1:8765/oauth/tiktok/callback/", "v" * 64)
    query = parse_qs(urlparse(url).query)
    assert query["client_key"] == ["tt-key"]
    assert query["code_challenge"] == [challenge_hex("v" * 64)]
    assert "video.list" in query["scope"][0]


def test_tiktok_sync_paginates_video_list(settings, http, router):
    router.add("GET", "https://open.tiktokapis.com/v2/user/info/", {"data": {"user": {
        "open_id": "oid", "display_name": "MindMatrix", "username": "mindmatrix", "follower_count": 40, "video_count": 3}},
        "error": {"code": "ok"}})
    pages = iter([
        {"data": {"videos": [{"id": 1, "title": "Hör auf zu nicken", "create_time": 1760000000, "view_count": 500,
                              "like_count": 20, "comment_count": 2, "share_count": 5, "duration": 45}],
                  "cursor": 123, "has_more": True}, "error": {"code": "ok"}},
        {"data": {"videos": [{"id": 2, "video_description": "Macht-Blick #psychologie", "create_time": 1760100000,
                              "view_count": 80}], "cursor": 0, "has_more": False}, "error": {"code": "ok"}},
    ])
    router.add("POST", "https://open.tiktokapis.com/v2/video/list/", lambda request: next(pages))
    result = TikTok(settings, http).sync({**FRESH, "open_id": "oid"})
    assert [v.external_id for v in result.videos] == ["1", "2"]
    assert result.videos[1].title == "Macht-Blick #psychologie"
    assert result.account.total_views == 580 and result.account.handle == "@mindmatrix"
    second_body = router.requests[-1].content
    assert b'"cursor": 123' in second_body or b'"cursor":123' in second_body


def test_tiktok_missing_scope_is_explained(settings, http, router):
    router.add("GET", "https://open.tiktokapis.com/v2/user/info/",
               httpx.Response(401, json={"error": {"code": "scope_not_authorized", "message": "x"}}))
    with pytest.raises(AuthExpired):  # 401 always means: log in again
        TikTok(settings, http).fetch_account(FRESH)
    router.routes.clear()
    router.add("GET", "https://open.tiktokapis.com/v2/user/info/",
               httpx.Response(200, json={"error": {"code": "scope_not_authorized", "message": "x"}}))
    with pytest.raises(PlatformError, match="Berechtigung"):
        TikTok(settings, http).fetch_account(FRESH)


def test_tiktok_upload_goes_to_inbox(settings, http, router, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 4096)
    router.add("POST", "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
               {"data": {"publish_id": "p1", "upload_url": "https://upload.tiktok/up"}, "error": {"code": "ok"}})
    router.add("PUT", "https://upload.tiktok/up", httpx.Response(201))
    states = iter(["PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"])
    router.add("POST", "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
               lambda request: {"data": {"status": next(states)}, "error": {"code": "ok"}})
    result = TikTok(settings, http, sleep=lambda s: None).upload(FRESH, UploadRequest(video, "T", "C"), lambda f, m: None)
    assert result.external_id is None and "Benachrichtigungen" in result.message
    put = next(r for r in router.requests if r.method == "PUT")
    assert put.headers["Content-Range"] == "bytes 0-4095/4096"


# --- Instagram ----------------------------------------------------------------------------------
def test_insight_values_reads_both_shapes():
    payload = {"data": [{"name": "views", "values": [{"value": 300}]}, {"name": "shares", "total_value": {"value": 4}}]}
    assert insight_values(payload) == {"views": 300, "shares": 4}


def test_instagram_rejects_personal_account(settings, http, router):
    router.add("GET", "https://graph.instagram.com/v24.0/me", {"id": "1", "username": "privat", "account_type": "PERSONAL"})
    with pytest.raises(PlatformError, match="Creator"):
        Instagram(settings, http).connect_with_token("x" * 40)


def test_instagram_sync_reads_reels_insights(settings, http, router):
    router.add("GET", "https://graph.instagram.com/v24.0/me/media", {"data": [
        {"id": "m1", "caption": "Hör auf zu nicken\n#psychologie", "media_type": "VIDEO", "media_product_type": "REELS",
         "permalink": "https://instagram.com/reel/m1", "timestamp": "2026-10-08T16:00:00+0000", "like_count": 9},
        {"id": "m2", "caption": "Ein Foto", "media_type": "IMAGE", "media_product_type": "FEED"}]})
    router.add("GET", "https://graph.instagram.com/v24.0/me", {"user_id": "ig1", "username": "mindmatrix",
                                                               "followers_count": 77, "media_count": 2})
    router.add("GET", "https://graph.instagram.com/v24.0/m1/insights", {"data": [
        {"name": "views", "values": [{"value": 1200}]}, {"name": "saved", "values": [{"value": 15}]},
        {"name": "ig_reels_avg_watch_time", "values": [{"value": 21500}]},
        {"name": "ig_reels_video_view_total_time", "values": [{"value": 600000}]}]})
    result = Instagram(settings, http).sync({"access_token": "t", "refreshed_at": time.time()})
    assert len(result.videos) == 1
    reel = result.videos[0]
    assert reel.title == "Hör auf zu nicken" and reel.views == 1200 and reel.saves == 15
    assert reel.avg_view_s == 21.5 and reel.watch_time_min == 10.0
    assert reel.published_at == "2026-10-08T16:00:00Z"
    assert result.account.followers == 77


def test_instagram_expired_token(settings, http, router):
    router.add("GET", "https://graph.instagram.com/v24.0/me",
               httpx.Response(400, json={"error": {"code": 190, "message": "expired"}}))
    with pytest.raises(AuthExpired):
        Instagram(settings, http).fetch_account({"access_token": "t"})
