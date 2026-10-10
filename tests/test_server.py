from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from dashboard.platforms import build_registry
from dashboard.platforms.base import AccountInfo
from dashboard.publish import Publisher
from dashboard.server import create_app
from dashboard.sync import SyncManager


@pytest.fixture
def client(settings, db, renders, http):
    settings = replace(settings, base=renders)
    registry = build_registry(settings, http)
    sync = SyncManager(db, registry, settings)
    app = create_app(settings, db, registry, sync, Publisher(db, registry, renders, workers=1))
    return TestClient(app)


def test_state_lists_platforms_and_niches(client):
    state = client.get("/api/state").json()
    assert [p["id"] for p in state["platforms"]] == ["youtube", "tiktok", "instagram"]
    assert state["niches"] == ["mindmatrix"]
    assert state["platforms"][1]["redirect_uri"].endswith("/oauth/tiktok/callback/")


def test_writes_require_json(client, db):
    db.save_account(AccountInfo("tiktok", "acc", "Konto"), {}, None)
    assert client.post("/api/sync", content="x", headers={"content-type": "text/plain"}).status_code == 415
    assert client.delete("/api/accounts/tiktok:acc").status_code == 415
    assert client.delete("/api/accounts/tiktok:acc", headers={"content-type": "application/json"}).status_code == 200


def test_foreign_host_header_is_rejected(client):
    assert client.get("/api/state", headers={"host": "evil.example"}).status_code == 400


def test_render_preview_cannot_escape(client):
    ok = client.get("/media/render", params={"path": "mindmatrix/2. macht-blick/renders/macht-blick.mp4"})
    assert ok.status_code == 200 and ok.headers["content-type"] == "video/mp4"
    assert client.get("/media/render", params={"path": "../.env"}).status_code == 404


def test_connect_redirects_to_tiktok_login_with_state(client):
    response = client.get("/connect/tiktok", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("https://www.tiktok.com/v2/auth/authorize/")


def test_callback_with_unknown_state_is_refused(client, db):
    response = client.get("/oauth/tiktok/callback/", params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert "fehler=" in response.headers["location"] and db.accounts() == []
    spoofed = client.get("/oauth/tiktok/callback/", params={"error": "x", "error_description": "Ruf+0800+an"},
                         follow_redirects=False)
    assert "0800" not in spoofed.headers["location"]  # foreign text only shows for a login we started


def test_niche_must_exist(client, db):
    db.save_account(AccountInfo("tiktok", "acc", "Konto"), {}, None)
    assert client.post("/api/accounts/tiktok:acc", json={"niche": "gibtsnicht"}).status_code == 400
    assert client.post("/api/accounts/tiktok:acc", json={"niche": "mindmatrix"}).status_code == 200


def test_overview_and_shorts_work_without_data(client):
    assert client.get("/api/overview?days=28").json()["kpis"]["views"] == 0
    assert client.get("/api/overview?days=5").status_code == 400
    assert sorted(r["slug"] for r in client.get("/api/shorts").json()) == ["hoer-auf-zu-nicken", "macht-blick"]


def test_security_headers(client):
    headers = client.get("/").headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
