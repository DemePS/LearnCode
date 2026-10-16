"""auth.py: the GitHub sign-in flow and the cookie it hands out.

The GitHub calls are faked with an httpx MockTransport rather than a live request, because what is worth
testing here is not that GitHub works but that *we* handle the answers correctly: that a mismatched
`state` is refused, that the token we store is not the token in the clear, that the cookie is not
readable from JavaScript, and that no response ever contains the token.

Auth is ON in this module (the routes tests run with it off), which is what makes the 401 paths and the
session lookup reachable at all."""

from __future__ import annotations

import importlib
import logging
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth, db

TOKEN = "gho_notarealtoken_at_all_0123456789"
PROFILE = {"id": 4242, "login": "alice", "name": "Alice", "avatar_url": "https://example.test/a.png"}


@pytest.fixture()
def app_module(database, data_dir, monkeypatch):
    """app.main with sign-in switched on and a throwaway Fernet key.

    Imported once per process for the same reason as tests/test_routes.py: app.agent registers tools in
    seedagent's process-global registry, and a second registration of the same name raises."""
    monkeypatch.setenv("LEARNCODE_AUTH", "on")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("LEARNCODE_BASE_URL", "http://testserver")
    monkeypatch.setenv("LEARNCODE_SECRET_KEY", Fernet.generate_key().decode())
    if "app.main" not in sys.modules:
        importlib.import_module("_mock_agent_server")
    return importlib.import_module("app.main")


@pytest.fixture()
def github(monkeypatch):
    """Replace the two GitHub endpoints the callback talks to, recording what we sent them.

    Returns a small object so a test can inspect the request (the code exchange carries the client
    secret, so what we send matters) and change the canned answer (a rejection, a profile with no id)."""
    calls: list[httpx.Request] = []
    answers = {
        "oauth": httpx.Response(200, json={"access_token": TOKEN}),
        "user": httpx.Response(200, json=PROFILE),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "github.com":
            return answers["oauth"]
        return answers["user"]

    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(auth.httpx, "AsyncClient", factory)
    return type("GitHub", (), {"calls": calls, "answers": answers})


def sign_in(client: TestClient, github) -> httpx.Response:
    """Drive the real two-step flow: ask for the redirect, then come back with the code."""
    started = client.get("/api/auth/github/login", follow_redirects=False)
    assert started.status_code == 302
    state = client.cookies[auth.STATE_COOKIE_NAME]
    return client.get(f"/api/auth/github/callback?code=the-code&state={state}", follow_redirects=False)


def test_anonymous_gets_401(app_module):
    """The whole point of Stage 4: the learning API is not open to the world any more.

    Lives here rather than in test_routes.py because it can only pass with auth switched on."""
    with TestClient(app_module.app) as client:
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/learn").status_code == 401
        assert client.get("/api/learn/" + "a" * 32).status_code == 401
        assert client.post("/api/learn/start",
                           json={"objective": "x", "history": []}).status_code == 401
        # /clarify creates nothing but costs a model call, so it is gated for the same reason.
        assert client.post("/api/learn/clarify",
                           json={"objective": "x", "history": []}).status_code == 401


def test_login_redirects_with_state_cookie(app_module):
    with TestClient(app_module.app) as client:
        response = client.get("/api/auth/github/login", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(auth.GITHUB_AUTHORIZE_URL)
    assert "client_id=client-id" in location
    assert "scope=" not in location, "no scope at all: identity and public search need none"
    state = location.split("state=")[1].split("&")[0]
    assert state, "the state must be a real random value, or the CSRF check means nothing"
    cookie = response.headers["set-cookie"]
    assert auth.STATE_COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower(), "Lax, so the cookie survives GitHub's redirect back"


def test_callback_refuses_a_mismatched_state(app_module, github):
    """The CSRF guard. Without it, an attacker could feed us a code from their own account."""
    with TestClient(app_module.app) as client:
        client.get("/api/auth/github/login", follow_redirects=False)
        response = client.get("/api/auth/github/callback?code=the-code&state=not-the-state",
                              follow_redirects=False)

    assert response.status_code == 400
    assert github.calls == [], "a bad state must be refused before anything is sent to GitHub"


def test_callback_refuses_without_the_state_cookie(app_module, github):
    with TestClient(app_module.app) as client:
        response = client.get("/api/auth/github/callback?code=the-code&state=whatever",
                              follow_redirects=False)
    assert response.status_code == 400


def test_successful_login_stores_an_unreadable_token(app_module, github):
    with TestClient(app_module.app) as client:
        response = sign_in(client, github)
        assert response.status_code == 302
        assert response.headers["location"] == "/"

        cookie = response.headers["set-cookie"]
        assert auth.COOKIE_NAME in cookie
        assert "HttpOnly" in cookie, "the session cookie must not be readable from JavaScript"
        assert "samesite=lax" in cookie.lower()
        assert "Secure" not in cookie, "http://testserver is not https, so Secure would break the cookie"

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json() == {"login": "alice", "name": "Alice",
                             "avatar_url": "https://example.test/a.png"}

    # The exchange is a POST carrying the code, and it uses the client secret -- server-side, which is
    # why the token can stay server-side too.
    oauth_call = github.calls[0]
    assert oauth_call.url == auth.GITHUB_TOKEN_URL
    assert b"code=the-code" in oauth_call.content
    assert oauth_call.headers["accept"] == "application/json"
    assert github.calls[1].headers["authorization"] == f"Bearer {TOKEN}"

    with db.session_scope() as s:
        user = s.scalar(select(db.User).where(db.User.github_id == PROFILE["id"]))
        assert user is not None
        assert user.token_encrypted
        assert TOKEN not in user.token_encrypted, "the token must not be stored in the clear"
        assert auth.decrypt_token(user.token_encrypted) == TOKEN
        assert s.query(db.AuthSession).filter_by(user_id=user.id).count() == 1


def test_the_token_never_reaches_the_client(app_module, github):
    """The invariant that matters most: the browser holds an opaque cookie and nothing else."""
    with TestClient(app_module.app) as client:
        response = sign_in(client, github)
        bodies = [response.text, client.get("/api/auth/me").text, client.get("/api/learn").text]
        cookie = response.headers["set-cookie"]

    assert all(TOKEN not in body for body in bodies), "a response body leaked the GitHub token"
    assert TOKEN not in cookie, "the cookie leaked the GitHub token"


def test_logout_removes_the_server_side_session(app_module, github):
    """Deleting the row, not just the cookie: a copied cookie must stop working."""
    with TestClient(app_module.app) as client:
        sign_in(client, github)
        with db.session_scope() as s:
            assert s.query(db.AuthSession).count() == 1

        assert client.post("/api/auth/logout").json() == {"ok": True}
        with db.session_scope() as s:
            assert s.query(db.AuthSession).count() == 0
        assert client.get("/api/auth/me").status_code == 401


def test_an_expired_session_is_401_and_is_cleaned_up(app_module, github):
    with TestClient(app_module.app) as client:
        sign_in(client, github)
        with db.session_scope() as s:
            row = s.scalar(select(db.AuthSession))
            # A minute in the past rather than a fixed date, so the test does not depend on the clock.
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            s.commit()

        assert client.get("/api/auth/me").status_code == 401

    with db.session_scope() as s:
        assert s.query(db.AuthSession).count() == 0, "a stale row is dropped when it is presented"


def test_a_forged_cookie_is_401(app_module):
    """Only the hash is stored, so an invented value cannot be turned into a session."""
    with TestClient(app_module.app) as client:
        client.cookies.set(auth.COOKIE_NAME, "not-a-real-cookie")
        assert client.get("/api/auth/me").status_code == 401


def test_a_second_login_reuses_the_same_user_row(app_module, github):
    """Signing in twice must not create two accounts: github_id is the identity, not the cookie."""
    with TestClient(app_module.app) as client:
        sign_in(client, github)
        sign_in(client, github)

    with db.session_scope() as s:
        assert s.query(db.User).filter_by(github_id=PROFILE["id"]).count() == 1
        assert s.query(db.AuthSession).count() == 2, "each sign-in is its own browser session"


def test_github_rejection_is_a_400(app_module, github):
    github.answers["oauth"] = httpx.Response(200, json={"error": "bad_verification_code"})
    with TestClient(app_module.app) as client:
        response = sign_in(client, github)
    assert response.status_code == 400
    assert "access token" in response.json()["detail"]


def test_a_profile_without_an_id_is_refused(app_module, github):
    """A token that cannot be tied to a GitHub account must not create a user row."""
    github.answers["user"] = httpx.Response(200, json={"login": "alice"})
    with TestClient(app_module.app) as client:
        response = sign_in(client, github)
    assert response.status_code == 400
    with db.session_scope() as s:
        assert s.query(db.User).count() == 0


def test_auth_off_serves_one_local_user_and_warns(monkeypatch, database, data_dir):
    """LEARNCODE_AUTH=off is how a local run works without registering an OAuth app. It must warn
    loudly, because running a deployment with it means every learner is the same person."""
    monkeypatch.setenv("LEARNCODE_AUTH", "off")
    # Deliberately half-configured: with auth off this must NOT raise, unlike the test below.
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("LEARNCODE_SECRET_KEY", raising=False)

    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Collect()
    logger = logging.getLogger("app.auth")
    logger.addHandler(handler)
    try:
        auth.ensure_auth_configured()
    finally:
        logger.removeHandler(handler)
    assert any("LEARNCODE_AUTH=off" in r.getMessage() for r in records)

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        assert client.get("/api/learn").status_code == 200, "auth off must not require a cookie"

    with db.session_scope() as s:
        row = s.scalar(select(db.User).where(db.User.github_id == -1))
        assert row is not None, "the fixed local user is created on first use"
        assert row.login == "local"
        assert row.token_encrypted is None, "the local user has no GitHub token to store"


def test_a_missing_configuration_fails_fast(monkeypatch):
    """A clear message at startup beats a confusing 500 on the first sign-in."""
    monkeypatch.setenv("LEARNCODE_AUTH", "on")
    for name in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "LEARNCODE_BASE_URL", "LEARNCODE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        auth.ensure_auth_configured()
    message = str(excinfo.value)
    for name in ("GITHUB_CLIENT_ID", "LEARNCODE_SECRET_KEY"):
        assert name in message
    assert "LEARNCODE_AUTH=off" in message, "the message must say how to run without accounts"


def test_an_undecryptable_token_degrades_instead_of_raising(app_module, github, monkeypatch):
    """A key change must not break every request: the learner just loses their personal rate limit."""
    with TestClient(app_module.app) as client:
        sign_in(client, github)
    monkeypatch.setenv("LEARNCODE_SECRET_KEY", Fernet.generate_key().decode())
    with db.session_scope() as s:
        user = s.scalar(select(db.User).where(db.User.github_id == PROFILE["id"]))
        assert auth.decrypt_token(user.token_encrypted) is None
        assert auth.User(user).token() is None
