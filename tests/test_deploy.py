"""Deployment-facing behaviour: environment config and optional auth."""

import base64

import pytest
from fastapi.testclient import TestClient

from trendlab.server.app import app


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client():
    return TestClient(app)


# ---------- auth off (the default) ----------
def test_no_password_means_open_access(client, monkeypatch):
    monkeypatch.delenv("TRENDLAB_PASSWORD", raising=False)
    assert client.get("/").status_code == 200
    assert client.get("/api/search", params={"q": "reliance", "funds": False}).status_code == 200


# ---------- auth on ----------
@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setenv("TRENDLAB_PASSWORD", "s3cret-pässword")
    monkeypatch.delenv("TRENDLAB_USER", raising=False)


def test_password_blocks_anonymous_requests(client, locked):
    res = client.get("/")
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith("Basic")


def test_password_blocks_the_api_too(client, locked):
    assert client.get("/api/search", params={"q": "reliance"}).status_code == 401


def test_correct_credentials_are_let_through(client, locked):
    assert client.get("/", headers=_basic("trendlab", "s3cret-pässword")).status_code == 200


def test_wrong_password_is_rejected(client, locked):
    assert client.get("/", headers=_basic("trendlab", "wrong")).status_code == 401


def test_wrong_user_is_rejected(client, locked):
    assert client.get("/", headers=_basic("admin", "s3cret-pässword")).status_code == 401


def test_custom_username(client, monkeypatch):
    monkeypatch.setenv("TRENDLAB_PASSWORD", "pw")
    monkeypatch.setenv("TRENDLAB_USER", "ayush")
    assert client.get("/", headers=_basic("ayush", "pw")).status_code == 200
    assert client.get("/", headers=_basic("trendlab", "pw")).status_code == 401


def test_health_stays_open_for_platform_probes(client, locked):
    """Render, Fly and Docker all probe this; locking it would fail deploys."""
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("header", [
    "Bearer abc", "Basic", "Basic !!!not-base64!!!", "Basic " + base64.b64encode(b"nocolon").decode(),
])
def test_malformed_auth_headers_are_rejected_not_crashed(client, locked, header):
    assert client.get("/", headers={"Authorization": header}).status_code == 401


# ---------- environment config ----------
def test_cache_dir_follows_the_environment(monkeypatch):
    from trendlab.config import AppConfig

    monkeypatch.setenv("TRENDLAB_CACHE_DIR", "/data/cache")
    assert AppConfig().cache_dir == "/data/cache"
    monkeypatch.delenv("TRENDLAB_CACHE_DIR")
    assert AppConfig().cache_dir == ".cache/trendlab"
