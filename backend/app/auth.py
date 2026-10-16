"""Signing in with GitHub, and the per-learner identity every other route depends on.

A GitHub OAuth App (not a GitHub App: an app installation token only sees the repositories the app is
installed on, so it cannot search public projects). No scopes are requested -- identity and public
search need none, and asking for `repo` would demand far more trust than this app can justify.

The token never leaves the server: the browser holds only an opaque random cookie, and the token is
used on the learner's behalf inside a turn. That is deliberate and load-bearing -- a token handed to
the browser (or accepted from a client header) would make this server a credential passthrough
proxy, and any caller could replay a stolen token through our app.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from . import db

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

COOKIE_NAME = "learncode_session"
STATE_COOKIE_NAME = "learncode_oauth_state"
SESSION_DAYS = 30

# The fixed identity used when auth is switched off, so a local run has a stable owner for its
# sessions. Never a real GitHub id, so it cannot collide with a signed-in learner's account.
_LOCAL_GITHUB_ID = -1


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def auth_disabled() -> bool:
    return _env("LEARNCODE_AUTH").lower() in ("off", "0", "false", "no")


def _fernet() -> Fernet:
    """The key that protects stored GitHub tokens. Built on first use rather than at import, so
    importing this module (as tests do) never requires the full configuration."""
    key = _env("LEARNCODE_SECRET_KEY")
    if not key:
        raise RuntimeError(
            "LEARNCODE_SECRET_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(blob: str | None) -> str | None:
    """The plaintext token, or None if there is none or it cannot be read (a key that changed since
    the row was written). Returns None rather than raising: a missing token just means the GitHub
    tools fall back to the shared anonymous budget, which is a degradation, not a failure."""
    if not blob:
        return None
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except (InvalidToken, ValueError):
        _log.warning("Stored GitHub token could not be decrypted; treating the user as tokenless.")
        return None


def hash_session_id(raw: str) -> str:
    """SHA-256 of the cookie value. Only this is stored: a dump of auth_sessions cannot be replayed
    as a login, for the same reason passwords are hashed rather than encrypted."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _base_url(request: Request) -> str:
    return (_env("LEARNCODE_BASE_URL") or str(request.base_url)).rstrip("/")


def _secure_cookies() -> bool:
    return _base_url_configured().startswith("https://")


def _base_url_configured() -> str:
    return _env("LEARNCODE_BASE_URL")


def ensure_auth_configured() -> None:
    """Fail fast at startup with one clear message rather than a confusing 500 on the first sign-in.

    Skipped entirely when auth is off, which is what makes a local run possible without registering
    an OAuth app."""
    if auth_disabled():
        _log.warning(
            "LEARNCODE_AUTH=off: every request runs as a single local user with no GitHub identity. "
            "This is for local development only -- never deploy with it."
        )
        return
    missing = [n for n in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "LEARNCODE_BASE_URL",
                           "LEARNCODE_SECRET_KEY") if not _env(n)]
    if missing:
        raise RuntimeError(
            "GitHub sign-in is not configured. Set " + ", ".join(missing) + " (see backend/.env.example), "
            "or set LEARNCODE_AUTH=off for local development without accounts."
        )


class User:
    """The signed-in learner, as the routes see them: the database row plus the one method routes
    actually need. A thin wrapper rather than returning the ORM row itself, so the token is only ever
    obtained by explicitly asking for it (see the invariants in the module docstring)."""

    def __init__(self, row: db.User):
        self._row = row
        self.id = row.id
        self.login = row.login
        self.name = row.name
        self.avatar_url = row.avatar_url

    def token(self) -> str | None:
        return decrypt_token(self._row.token_encrypted)


def _local_user() -> db.User:
    """The single user used when auth is off, created on first sighting."""
    with db.session_scope() as s:
        row = s.scalar(select(db.User).where(db.User.github_id == _LOCAL_GITHUB_ID))
        if row is None:
            row = db.User(github_id=_LOCAL_GITHUB_ID, login="local", name="Local development")
            s.add(row)
            s.commit()
        return row


def current_user(request: Request) -> User:
    """FastAPI dependency: the caller, or 401.

    Reads the opaque cookie, looks up its hash, and checks the expiry. The raw cookie value is never
    stored anywhere, so this is the only way back to a user."""
    if auth_disabled():
        return User(_local_user())

    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(401, "Not signed in")
    with db.session_scope() as s:
        row = s.get(db.AuthSession, hash_session_id(raw))
        if row is None:
            raise HTTPException(401, "Not signed in")
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            s.delete(row)  # a stale cookie is cleaned up on use, so the table cannot grow forever
            s.commit()
            raise HTTPException(401, "Session expired")
        user = s.get(db.User, row.user_id)
        if user is None:
            raise HTTPException(401, "Not signed in")
        return User(user)


def optional_user(request: Request) -> User | None:
    """The caller if they are signed in, else None -- for the one endpoint that must answer anonymously
    (the progress poll: a turn can be watched from a page that loaded before sign-in state was known, and
    an unknown turn is not an error). Ownership is still enforced there: a signed-in learner only ever
    sees their own turn's feed (progress.owns), so this is not an opening for one learner to watch
    another's activity. Every route that does real work uses current_user and 401s instead."""
    try:
        return current_user(request)
    except HTTPException:
        return None


@router.get("/github/login")
async def github_login(request: Request) -> RedirectResponse:
    """Send the learner to GitHub. The random `state` is kept in a short-lived httpOnly cookie and
    checked on the way back, which is what stops an attacker from feeding us someone else's code."""
    if auth_disabled():
        raise HTTPException(400, "Sign-in is disabled (LEARNCODE_AUTH=off)")
    state = secrets.token_urlsafe(24)
    redirect_uri = f"{_base_url(request)}/api/auth/github/callback"
    url = (
        f"{GITHUB_AUTHORIZE_URL}?client_id={_env('GITHUB_CLIENT_ID')}"
        f"&redirect_uri={redirect_uri}&state={state}"
        # No `scope` parameter at all: identity and public repository search need no permission.
    )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        STATE_COOKIE_NAME, state, max_age=600, httponly=True, samesite="lax",
        secure=_secure_cookies(),
    )
    return response


@router.get("/github/callback")
async def github_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    """Exchange the code for a token, identify the learner, and start a session.

    The exchange happens here, server-side, because it needs the client secret -- which is also why
    the token can stay on this side of the wire."""
    if auth_disabled():
        raise HTTPException(400, "Sign-in is disabled (LEARNCODE_AUTH=off)")
    expected = request.cookies.get(STATE_COOKIE_NAME)
    if not state or not expected or not secrets.compare_digest(state, expected):
        # 400 rather than a redirect: a mismatched state means the flow did not start here, and
        # silently continuing would be the actual vulnerability.
        raise HTTPException(400, "OAuth state did not match")
    if not code:
        raise HTTPException(400, "Missing authorization code")

    redirect_uri = f"{_base_url(request)}/api/auth/github/callback"
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": _env("GITHUB_CLIENT_ID"),
                "client_secret": _env("GITHUB_CLIENT_SECRET"),
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        if token_response.status_code != 200:
            raise HTTPException(400, "GitHub rejected the authorization code")
        token = token_response.json().get("access_token")
        if not token:
            raise HTTPException(400, "GitHub did not return an access token")

        user_response = await client.get(
            GITHUB_USER_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "User-Agent": "learncode"},
        )
        if user_response.status_code != 200:
            raise HTTPException(400, "Could not read the GitHub profile")
        profile = user_response.json()

    github_id = profile.get("id")
    login = profile.get("login")
    if github_id is None or not login:
        raise HTTPException(400, "GitHub profile was missing an id or login")

    now = datetime.now(timezone.utc)
    with db.session_scope() as s:
        row = s.scalar(select(db.User).where(db.User.github_id == int(github_id)))
        if row is None:
            row = db.User(github_id=int(github_id), login=login)
            s.add(row)
        row.login = login
        row.name = profile.get("name")
        row.avatar_url = profile.get("avatar_url")
        row.token_encrypted = encrypt_token(token)
        row.last_login_at = now
        s.commit()
        user_id = row.id

        raw = secrets.token_urlsafe(32)
        s.add(
            db.AuthSession(
                id_hash=hash_session_id(raw),
                user_id=user_id,
                expires_at=now + timedelta(days=SESSION_DAYS),
            )
        )
        s.commit()

    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        COOKIE_NAME, raw, max_age=SESSION_DAYS * 24 * 3600, httponly=True, samesite="lax",
        secure=_secure_cookies(),
    )
    # The state cookie has done its job; leaving it would let a stale value sit in the browser.
    response.delete_cookie(STATE_COOKIE_NAME)
    return response


@router.post("/logout")
async def logout(request: Request) -> dict[str, bool]:
    """Drop the server-side session. Deleting the row (not just the cookie) is the part that matters:
    the cookie is useless on its own, but leaving the row would leave a working credential behind."""
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        with db.session_scope() as s:
            row = s.get(db.AuthSession, hash_session_id(raw))
            if row is not None:
                s.delete(row)
                s.commit()
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict[str, str | None]:
    """Who the browser is signed in as, or 401. Never includes the token (module docstring)."""
    return {"login": user.login, "name": user.name, "avatar_url": user.avatar_url}
