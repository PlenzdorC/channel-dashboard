import time
from datetime import date, timedelta

import pytest

from dashboard import metrics
from dashboard.pipeline import find_renders, match_slug, normalize, resolve_render
from dashboard.platforms.base import (
    AccountInfo, AuthExpired, DailyPoint, PlatformError, SyncResult, UploadResult, VideoSnapshot,
)
from dashboard.publish import Publisher, Target
from dashboard.sync import sync_account


# --- config -------------------------------------------------------------------------------------
def test_read_env_merges_both_files_and_later_wins(tmp_path, monkeypatch):
    from dashboard.config import read_env
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    root, own = tmp_path / "root.env", tmp_path / "own.env"
    root.write_text("ELEVENLABS_API_KEY=a\nYOUTUBE_CLIENT_ID=alt\n", encoding="utf-8")
    own.write_text("﻿# Kommentar\nYOUTUBE_CLIENT_ID = \"neu\"\nTIKTOK_CLIENT_KEY=k\n", encoding="utf-8")
    env = read_env((root, own, tmp_path / "fehlt.env"))
    assert env["YOUTUBE_CLIENT_ID"] == "neu" and env["TIKTOK_CLIENT_KEY"] == "k" and env["ELEVENLABS_API_KEY"] == "a"


def test_settings_serve_this_pc_unless_home_network_names_are_set(settings):
    from dataclasses import replace
    assert (settings.listen_host, settings.allowed_hosts, settings.url) == (
        "127.0.0.1", ("127.0.0.1", "localhost"), "http://127.0.0.1:8765")
    server = replace(settings, env={"DASHBOARD_HOSTS": " 192.168.178.138 ,homeserver.fritz.box,", "DASHBOARD_PORT": "9000"})
    assert server.listen_host == "0.0.0.0"
    assert server.allowed_hosts == ("127.0.0.1", "localhost", "192.168.178.138", "homeserver.fritz.box")
    assert server.url == "http://192.168.178.138:9000"
    assert server.redirect_base == "http://127.0.0.1:9000"


# --- pipeline -----------------------------------------------------------------------------------
def test_normalize_handles_umlauts_and_quotes():
    assert normalize("„Hör auf zu nicken – Das zerstört deine Verhandlung!“") == "hoer auf zu nicken das zerstoert deine verhandlung"


def test_find_renders_reads_titles(renders):
    found = {r.slug: r for r in find_renders(renders)}
    assert set(found) == {"hoer-auf-zu-nicken", "macht-blick"}
    assert found["macht-blick"].title == "Der Macht-Blick"
    assert found["macht-blick"].path == "mindmatrix/2. macht-blick/renders/macht-blick.mp4"


def test_match_slug_by_slug_words_or_title(renders):
    found = find_renders(renders)
    assert match_slug("„Hör auf zu nicken – Das zerstört deine Verhandlung!“", found) == "hoer-auf-zu-nicken"
    assert match_slug("Der Macht-Blick: so wirkst du souverän", found) == "macht-blick"
    assert match_slug("Etwas ganz anderes", found) is None
    assert match_slug("Hör auf zu nicken", found, niche="neurofakt") is None


def test_resolve_render_only_allows_listed_files(renders):
    assert resolve_render(renders, "mindmatrix/2. macht-blick/renders/macht-blick.mp4").is_file()
    for bad in ("../secret.mp4", "mindmatrix/2. macht-blick/renders/macht-blick_hf.mp4", ".env"):
        with pytest.raises(ValueError):
            resolve_render(renders, bad)


# --- storage + metrics --------------------------------------------------------------------------
def store(db, platform, views_by_day: dict[str, int], followers_by_day: dict[str, int] | None = None, daily=()):
    info = AccountInfo(platform, "acc", "Konto", "@konto", "")
    key = db.save_account(info, {"access_token": "t"}, "mindmatrix")
    for day, views in views_by_day.items():
        followers = (followers_by_day or {}).get(day)
        snap = VideoSnapshot("v1", "Hör auf zu nicken", "", f"{min(views_by_day)}T10:00:00Z", duration_s=45,
                             views=views, likes=views // 10, avg_view_s=20)
        db.store_sync(key, AccountInfo(platform, "acc", "Konto", "@konto", "", followers=followers),
                      (snap,), daily if day == max(views_by_day) else (), {}, day=day)
    return key


def iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_daily_series_from_snapshot_deltas_spreads_gaps(db):
    store(db, "tiktok", {iso(4): 100, iso(3): 160, iso(1): 260})
    series = metrics.daily_series(db, db.accounts()[0], date.today() - timedelta(days=10), date.today())
    assert series[iso(3)]["views"] == 60
    assert series[iso(2)]["views"] == 50 and series[iso(1)]["views"] == 50  # two-day gap split evenly


def test_daily_series_prefers_platform_reported_days(db):
    store(db, "youtube", {iso(1): 999}, daily=(DailyPoint(iso(2), views=40, watch_time_min=12.0),))
    series = metrics.daily_series(db, db.accounts()[0], date.today() - timedelta(days=10), date.today())
    assert series == {iso(2): {"views": 40, "watch_time_min": 12.0}}


def test_overview_kpis_and_follower_change(db):
    store(db, "tiktok", {iso(40): 0, iso(30): 100, iso(1): 400}, followers_by_day={iso(40): 5, iso(30): 10, iso(1): 30})
    data = metrics.overview(db, {}, 28)
    # +300 between day -30 and day -1 is ~10 per day; 27 of those 29 days fall inside the last 28 days
    assert data["kpis"]["views"] == 270
    assert data["kpis"]["followers"] == 30 and data["kpis"]["followers_delta"] == 20
    card = data["accounts"][0]
    assert card["latest"]["rank"] == 1 and card["latest"]["rank_of"] == 1


def test_avg_view_pct_falls_back_to_duration():
    assert metrics.avg_view_pct({"avg_view_s": 22.5, "duration_s": 45}) == 50
    assert metrics.avg_view_pct({"avg_view_pct": 61.0, "avg_view_s": 1, "duration_s": 45}) == 61.0
    assert metrics.engagement({"views": 200, "likes": 10, "comments": 4, "shares": 4, "saves": 2}) == 10


def test_delete_account_removes_its_data(db):
    key = store(db, "tiktok", {iso(1): 10})
    db.delete_account(key)
    assert db.accounts() == [] and db.query("SELECT * FROM video_stats") == []


# --- sync ---------------------------------------------------------------------------------------
class FakeConnector:
    platform, label, connect_mode = "tiktok", "TikTok", "oauth"

    def __init__(self, result=None, error=None, refreshed=None):
        self.result, self.error, self.refreshed, self.uploads = result, error, refreshed, []

    def missing_config(self):
        return []

    def fresh(self, creds):
        return self.refreshed or creds

    def sync(self, creds):
        if self.error:
            raise self.error
        return self.result

    def upload(self, creds, request, progress):
        self.uploads.append(request)
        progress(1.0, "fertig")
        return UploadResult("x1", "https://example/x1", "ok")


def test_sync_stores_refreshed_credentials_and_links_slugs(db, renders):
    key = db.save_account(AccountInfo("tiktok", "acc", "Konto"), {"access_token": "old"}, "mindmatrix")
    result = SyncResult(AccountInfo("tiktok", "acc", "Konto", followers=3), {"access_token": "new"},
                        (VideoSnapshot("v1", "Der Macht-Blick #psychologie", "", "2026-10-01T00:00:00Z", views=50),))
    outcome = sync_account(db, {"tiktok": FakeConnector(result, refreshed={"access_token": "new"})}, key, renders)
    assert outcome.ok
    assert db.credentials(key) == {"access_token": "new"}
    assert db.one("SELECT slug, slug_source FROM videos") == {"slug": "macht-blick", "slug_source": "auto"}


def test_refreshed_login_is_kept_even_when_the_sync_fails(db, renders):
    key = db.save_account(AccountInfo("tiktok", "acc", "Konto"), {"access_token": "old"}, None)
    connector = FakeConnector(error=PlatformError("down"), refreshed={"access_token": "rotated"})
    assert not sync_account(db, {"tiktok": connector}, key, renders).ok
    assert db.credentials(key) == {"access_token": "rotated"}


def test_failed_metric_on_second_sync_keeps_earlier_value(db):
    key = db.save_account(AccountInfo("instagram", "acc", "Konto"), {}, None)
    info = AccountInfo("instagram", "acc", "Konto")
    db.store_sync(key, info, (VideoSnapshot("m1", "Reel", "", "2026-10-01", views=100, likes=5),), (), {}, day="2026-10-09")
    db.store_sync(key, info, (VideoSnapshot("m1", "Reel", "", "2026-10-01", views=None, likes=6),), (), {}, day="2026-10-09")
    assert db.one("SELECT views, likes FROM video_stats") == {"views": 100, "likes": 6}


def test_video_missing_from_one_snapshot_does_not_inflate_growth(db):
    key = db.save_account(AccountInfo("tiktok", "acc", "Konto"), {}, None)
    info = AccountInfo("tiktok", "acc", "Konto")
    old = "2026-01-01T00:00:00Z"
    db.store_sync(key, info, (VideoSnapshot("a", "A", "", old, views=1000), VideoSnapshot("b", "B", "", old, views=50)), (), {}, day=iso(3))
    db.store_sync(key, info, (VideoSnapshot("b", "B", "", old, views=60),), (), {}, day=iso(2))  # "a" missing
    db.store_sync(key, info, (VideoSnapshot("a", "A", "", old, views=1010), VideoSnapshot("b", "B", "", old, views=70)), (), {}, day=iso(1))
    series = metrics.daily_series(db, db.accounts()[0], date.today() - timedelta(days=10), date.today())
    assert series[iso(2)]["views"] == 10 and series[iso(1)]["views"] == 10  # not +1010 when "a" reappears


def test_sync_marks_expired_login(db, renders):
    key = db.save_account(AccountInfo("tiktok", "acc", "Konto"), {}, None)
    outcome = sync_account(db, {"tiktok": FakeConnector(error=AuthExpired("neu verbinden"))}, key, renders)
    assert not outcome.ok and db.account(key)["status"] == "reconnect"


def test_manual_slug_is_not_overwritten(db, renders):
    key = db.save_account(AccountInfo("tiktok", "acc", "Konto"), {}, "mindmatrix")
    result = SyncResult(AccountInfo("tiktok", "acc", "Konto"), {}, (VideoSnapshot("v1", "Der Macht-Blick", "", "2026-10-01"),))
    sync_account(db, {"tiktok": FakeConnector(result)}, key, renders)
    db.set_slug("tiktok:v1", "hoer-auf-zu-nicken", "manual")
    sync_account(db, {"tiktok": FakeConnector(result)}, key, renders)
    assert db.one("SELECT slug FROM videos")["slug"] == "hoer-auf-zu-nicken"
    db.set_slug("tiktok:v1", None, "manual")  # "nicht zugeordnet" must stick as well
    sync_account(db, {"tiktok": FakeConnector(result)}, key, renders)
    assert db.one("SELECT slug FROM videos")["slug"] is None


def test_sync_manager_queues_requests_while_running(db, renders, settings):
    import threading
    from dataclasses import replace

    from dashboard.sync import SyncManager
    gate = threading.Event()
    results = {k: SyncResult(AccountInfo("tiktok", k, k), {}) for k in ("a1", "a2")}

    class SlowConnector(FakeConnector):
        def sync(self, creds):
            gate.wait(5)
            return self.result

    keys = [db.save_account(AccountInfo("tiktok", k, k), {}, None) for k in ("a1", "a2")]
    manager = SyncManager(db, {"tiktok": SlowConnector(results["a1"])}, replace(settings, base=renders))
    assert manager.start(keys[0]) and manager.start(keys[1])
    gate.set()
    for _ in range(100):
        if not manager.running:
            break
        time.sleep(0.05)
    assert [o.account_id for o in manager.last_outcomes] == keys


# --- publishing ---------------------------------------------------------------------------------
def test_publisher_uploads_and_registers_video(db, renders):
    key = db.save_account(AccountInfo("tiktok", "acc", "Konto"), {}, "mindmatrix")
    connector = FakeConnector()
    publisher = Publisher(db, {"tiktok": connector}, renders, workers=1)
    job = publisher.submit("mindmatrix/2. macht-blick/renders/macht-blick.mp4", "Der Macht-Blick", "Text", [Target(key)])
    publisher.pool.shutdown(wait=True)
    target = db.jobs()[0]["targets"][0]
    assert job == 1 and target["status"] == "done" and target["url"] == "https://example/x1"
    assert db.one("SELECT slug, slug_source FROM videos") == {"slug": "macht-blick", "slug_source": "posted"}


def test_publisher_rejects_unknown_files_and_accounts(db, renders):
    key = db.save_account(AccountInfo("tiktok", "acc", "Konto"), {}, None)
    publisher = Publisher(db, {"tiktok": FakeConnector()}, renders, workers=1)
    with pytest.raises(ValueError):
        publisher.submit("../../.env", "T", "C", [Target(key)])
    with pytest.raises(ValueError):
        publisher.submit("mindmatrix/2. macht-blick/renders/macht-blick.mp4", "T", "C", [Target("youtube:nope")])
    with pytest.raises(ValueError):
        publisher.submit("mindmatrix/2. macht-blick/renders/macht-blick.mp4", "T", "C", [Target(key, privacy="geheim")])
