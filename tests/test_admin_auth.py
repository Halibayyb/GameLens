"""Admin sign-in: first sign-in with ADMIN_TOKEN, forced password change, sessions, lockout, the old token retired."""
import pytest
from fastapi.testclient import TestClient

from api import auth

BOOT = "first-time-token-123"
NEW = "a much better pass 42"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_VIDEO", "1")
    monkeypatch.setenv("ADMIN_TOKEN", BOOT)
    monkeypatch.setenv("ADMIN_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("HIGHLIGHTS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("PUBLISHED_DIR", str(tmp_path / "pub"))
    auth._FAILS.clear()
    from api.main import app
    return TestClient(app)


def _login(client, password, username="admin"):
    return client.post("/api/admin/login", json={"username": username, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_first_sign_in_must_change_the_password_before_using_the_workshop(client):
    first = _login(client, BOOT)
    assert first.status_code == 200 and first.json()["must_change"]
    token = first.json()["token"]
    assert client.get("/api/admin/video", headers=bearer(token)).status_code == 403
    assert client.get("/api/admin/me", headers=bearer(token)).json() == {"username": "admin", "must_change": True}
    changed = client.post("/api/admin/password", json={"current": BOOT, "new": NEW}, headers=bearer(token))
    assert changed.status_code == 200 and not changed.json()["must_change"]
    fresh = changed.json()["token"]
    assert client.get("/api/admin/video", headers=bearer(fresh)).status_code == 200
    assert client.get("/api/admin/video", headers=bearer(token)).status_code == 401   # the old session is over


def test_after_the_change_the_shared_token_no_longer_works(client):
    token = _login(client, BOOT).json()["token"]
    client.post("/api/admin/password", json={"current": BOOT, "new": NEW}, headers=bearer(token))
    assert _login(client, BOOT).status_code == 401
    assert client.get("/api/admin/video", headers={"X-Admin-Token": BOOT}).status_code == 401
    assert _login(client, NEW).json()["must_change"] is False


def test_the_shared_token_still_works_for_scripts_until_a_password_is_set(client):
    assert client.get("/api/admin/video", headers={"X-Admin-Token": BOOT}).status_code == 200


@pytest.mark.parametrize("new,msg", [("short1", "at least"), (BOOT, "different"), ("admin password 1", "username"),
                                     ("aaaaaaaaaaaaaa", "repetitive")])
def test_weak_new_passwords_are_refused_with_a_reason(client, new, msg):
    token = _login(client, BOOT).json()["token"]
    r = client.post("/api/admin/password", json={"current": BOOT, "new": new}, headers=bearer(token))
    assert r.status_code == 400 and msg in r.json()["detail"]


def test_wrong_current_password_and_lockout(client):
    token = _login(client, BOOT).json()["token"]
    assert client.post("/api/admin/password", json={"current": "nope", "new": NEW}, headers=bearer(token)).status_code == 401
    for _ in range(auth.MAX_FAILS):
        assert _login(client, "wrong").status_code == 401
    assert _login(client, BOOT).status_code == 429   # even the right password waits out the pause


def test_tampered_or_expired_sessions_are_refused(client, monkeypatch):
    token = _login(client, BOOT).json()["token"]
    kind, body, mac = token.split(".")
    assert client.get("/api/admin/me", headers=bearer(f"{kind}.{body}.{'0' * len(mac)}")).status_code == 401
    monkeypatch.setattr(auth, "SESSION_TTL_S", -1)
    old = _login(client, BOOT).json()["token"]
    assert client.get("/api/admin/me", headers=bearer(old)).status_code == 401


def test_passwords_are_stored_hashed(client, tmp_path):
    token = _login(client, BOOT).json()["token"]
    client.post("/api/admin/password", json={"current": BOOT, "new": NEW}, headers=bearer(token))
    stored = (tmp_path / "users.json").read_text()
    assert NEW not in stored and BOOT not in stored and '"hash"' in stored
