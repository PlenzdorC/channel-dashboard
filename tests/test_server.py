from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from dashboard.platforms import build_registry
from dashboard.platforms.base import AccountInfo
from dashboard.publish import Publisher
from dashboard.server import create_app
from dashboard.sync import SyncManager


@pytest.fixture
def make_client(settings, db, renders, http):
    def make(**env) -> TestClient:
        configured = replace(settings, base=renders, env={**settings.env, **env})
        registry = build_registry(configured, http)
        sync = SyncManager(db, registry, configured)
        return TestClient(create_app(configured, db, registry, sync, Publisher(db, registry, renders, workers=1)))
    return make


@pytest.fixture
def client(make_client):
    return make_client()


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


def test_home_network_address_is_accepted_when_configured(client, make_client):
    lan = {"host": "192.168.178.138:8765"}
    assert client.get("/api/state", headers=lan).status_code == 400
    server = make_client(DASHBOARD_HOSTS="192.168.178.138, homeserver.fritz.box")
    assert server.get("/api/state", headers=lan).status_code == 200
    assert server.get("/api/state", headers={"host": "homeserver.fritz.box"}).status_code == 200
    assert server.get("/api/state", headers={"host": "evil.example"}).status_code == 400
    # logins still come back to the loopback address the OAuth apps know
    assert server.get("/api/state").json()["platforms"][1]["redirect_uri"] == "http://127.0.0.1:8765/oauth/tiktok/callback/"


def test_password_protects_every_page(make_client):
    server = make_client(DASHBOARD_PASSWORD="geheim-123")
    for path in ("/", "/api/state", "/static/app.js"):
        response = server.get(path)
        assert response.status_code == 401 and response.headers["www-authenticate"].startswith("Basic")
    assert server.get("/api/state", auth=("", "falsch")).status_code == 401
    for broken in (b"Basic !!!", "Basic ä".encode()):
        assert server.get("/api/state", headers={"authorization": broken}).status_code == 401
    assert server.get("/api/state", auth=("egal", "geheim-123")).status_code == 200


def test_pasted_login_address_connects_the_account(client, db, router, monkeypatch):
    monkeypatch.setattr(SyncManager, "start", lambda self, key=None: True)
    router.add("POST", "https://open.tiktokapis.com/v2/oauth/token/", {
        "access_token": "at", "refresh_token": "rt", "open_id": "oid", "expires_in": 86400})
    router.add("GET", "https://open.tiktokapis.com/v2/user/info/", {
        "data": {"user": {"open_id": "oid", "display_name": "MindMatrix"}}, "error": {"code": "ok"}})
    login = client.get("/connect/tiktok", follow_redirects=False).headers["location"]
    state = parse_qs(urlsplit(login).query)["state"][0]
    pasted = f"http://127.0.0.1:8765/oauth/tiktok/callback/?code=abc&scopes=video.list&state={state}"
    assert client.post("/api/connect/finish", json={"url": pasted}).json() == {"ok": True, "name": "MindMatrix"}
    assert [a["id"] for a in db.accounts()] == ["tiktok:oid"]
    token_request = next(r for r in router.requests if r.url.path == "/v2/oauth/token/")
    assert b"code=abc" in token_request.content
    again = client.post("/api/connect/finish", json={"url": pasted})  # a state is only good once
    assert again.status_code == 400 and "abgelaufen" in again.json()["detail"]
    other = client.post("/api/connect/finish", json={"url": "http://127.0.0.1:8765/api/state?code=x"})
    assert other.status_code == 400


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
