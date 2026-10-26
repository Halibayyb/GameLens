"""Admin sign-in for the upload workshop: username and password, a forced change on first sign-in, sessions.

The first sign-in uses ``admin`` (``ADMIN_USER``) with the deployment's ``ADMIN_TOKEN`` as a one-time password;
the page then makes the person choose their own. Passwords are stored as salted scrypt hashes, in Blob Storage
when ``AZURE_STORAGE_ACCOUNT`` is set (the API container's disk does not survive a scale to zero), else in a
file under the highlights folder. Sessions are signed tokens that expire and stop working when the password
changes. Once a password is set, ``ADMIN_TOKEN`` no longer signs anyone in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

SESSION_TTL_S = 12 * 3600
MIN_PASSWORD = 12
MAX_FAILS, FAIL_WINDOW_S = 5, 300   # sign-in attempts per username and client before a pause
BLOB_NAME = "_admin/users.json"
_LOCK = threading.Lock()
_FAILS: dict[str, list[float]] = {}


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ---------- the user store ----------

def _local_path() -> Path:
    custom = os.getenv("ADMIN_USERS_FILE")
    if custom:
        return Path(custom)
    from highlights.store import highlights_dir
    return highlights_dir() / "_admin" / "users.json"


def _blob():
    account, connection = os.getenv("AZURE_STORAGE_ACCOUNT"), os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not (account or connection) or os.getenv("ADMIN_USERS_FILE"):
        return None
    from azure.storage.blob import BlobClient
    container = os.getenv("HIGHLIGHTS_CONTAINER", "highlights")
    if connection:
        return BlobClient.from_connection_string(connection, container_name=container, blob_name=BLOB_NAME)
    from azure.identity import DefaultAzureCredential
    return BlobClient(f"https://{account}.blob.core.windows.net", container, BLOB_NAME,
                      credential=DefaultAzureCredential())


def load_users() -> dict:
    blob = _blob()
    if blob is not None:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return json.loads(blob.download_blob().readall())
        except ResourceNotFoundError:
            return {}
    path = _local_path()
    return json.loads(path.read_text()) if path.exists() else {}


def save_users(users: dict) -> None:
    data = json.dumps(users, indent=1)
    blob = _blob()
    if blob is not None:
        blob.upload_blob(data.encode(), overwrite=True)
        return
    path = _local_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data)
    tmp.replace(path)


def _username() -> str:
    return (os.getenv("ADMIN_USER") or "admin").strip().lower()


def _bootstrap_secret() -> str:
    return os.getenv("ADMIN_TOKEN", "").strip()


# ---------- passwords ----------

def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def _check(user: dict, password: str) -> bool:
    return hmac.compare_digest(_hash(password, bytes.fromhex(user["salt"])), user["hash"])


def _version(user: dict | None) -> str:
    """Changes whenever the password does; a session carries it, so a new password ends old sessions."""
    return "bootstrap" if user is None else user["hash"][:16]


# ---------- sessions ----------

def _key() -> bytes:
    explicit = os.getenv("ADMIN_SESSION_SECRET")
    return (explicit or hashlib.sha256(("session:" + _bootstrap_secret()).encode()).hexdigest()).encode()


def _sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"s1.{body}.{mac}"


def _unsign(token: str) -> dict | None:
    try:
        kind, body, mac = token.split(".")
    except ValueError:
        return None
    good = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    if kind != "s1" or not hmac.compare_digest(mac, good):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None


def _session(username: str, user: dict | None) -> dict:
    must = user is None or bool(user.get("must_change"))
    token = _sign({"u": username, "v": _version(user), "exp": int(time.time()) + SESSION_TTL_S, "n": secrets.token_hex(4)})
    return {"token": token, "username": username, "must_change": must, "expires_in": SESSION_TTL_S}


# ---------- what the routes call ----------

def _throttle(key: str) -> None:
    now = time.time()
    recent = [t for t in _FAILS.get(key, []) if now - t < FAIL_WINDOW_S]
    _FAILS[key] = recent
    if len(recent) >= MAX_FAILS:
        raise AuthError(429, "Too many attempts. Wait a few minutes and try again.")


def login(username: str, password: str, client: str = "") -> dict:
    username = (username or "").strip().lower()
    key = f"{username}|{client}"
    _throttle(key)
    users = load_users()
    user = users.get(username)
    ok = False
    if user is not None:
        ok = _check(user, password or "")
    elif username == _username() and not users and _bootstrap_secret():
        ok = hmac.compare_digest((password or "").encode(), _bootstrap_secret().encode())   # first sign-in only
    if not ok:
        _FAILS.setdefault(key, []).append(time.time())
        raise AuthError(401, "Wrong username or password.")
    _FAILS.pop(key, None)
    return _session(username, user)


def session_user(token: str) -> tuple[str, dict | None, bool]:
    """(username, stored user or None while bootstrapping, must_change) for a valid session, else AuthError 401."""
    data = _unsign(token or "")
    if not data or data.get("exp", 0) < time.time():
        raise AuthError(401, "Your session has ended. Sign in again.")
    users = load_users()
    user = users.get(data["u"])
    if user is None and (users or data["u"] != _username()):
        raise AuthError(401, "Your session has ended. Sign in again.")
    if data.get("v") != _version(user):
        raise AuthError(401, "The password has changed. Sign in again.")
    return data["u"], user, user is None or bool(user.get("must_change"))


def change_password(token: str, current: str, new: str) -> dict:
    username, user, _ = session_user(token)
    if user is None:
        if not hmac.compare_digest((current or "").encode(), _bootstrap_secret().encode()):
            raise AuthError(401, "The current password is wrong.")
    elif not _check(user, current or ""):
        raise AuthError(401, "The current password is wrong.")
    problem = password_problem(new, username, current)
    if problem:
        raise AuthError(400, problem)
    salt = secrets.token_bytes(16)
    with _LOCK:
        users = load_users()
        users[username] = {"salt": salt.hex(), "hash": _hash(new, salt), "must_change": False,
                           "changed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        save_users(users)
    return _session(username, users[username])


def password_problem(new: str, username: str, current: str = "") -> str | None:
    new = new or ""
    if len(new) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if new == current or new == _bootstrap_secret():
        return "Choose a password different from the current one."
    if username and username in new.lower():
        return "The password must not contain the username."
    if len(set(new)) < 6:
        return "Use a less repetitive password."
    return None


def legacy_token_ok(provided: str) -> bool:
    """The shared ADMIN_TOKEN as an API key (scripts, the first sign-in) until someone has set a password."""
    secret = _bootstrap_secret()
    if not provided or not secret or not hmac.compare_digest(provided.encode(), secret.encode()):
        return False
    return not load_users()
