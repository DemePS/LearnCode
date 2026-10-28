"""The FastAPI app: the JSON API under /api, and (once built) the React SPA's static files for
everything else.

Run it with `uv run python -m app` from backend/ (see the __main__ block at the bottom, which is the
supported way in: it passes the --reload-exclude flags the app needs), or with
`uv run uvicorn app.main:app --reload --reload-exclude 'data/*' --reload-exclude 'data/**'`."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/.env, loaded before anything reads the environment: seedagent and app.auth/db all take their
# settings from os.environ, and a local run should need no exported variables. Read from an absolute path
# next to this package rather than dotenv's default ".env", because the app is started from the repository
# root as often as from backend/ and the default lookup would then find nothing -- which looks exactly like
# "sign-in is not configured" even with a correct .env. override=False keeps a real environment variable
# winning over the file, so a deployment's settings are never silently clobbered.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import ensure_auth_configured, router as auth_router
from .db import init_db
from .routes import router


class _DropProgressPolls(logging.Filter):
    """Hide the activity-feed polls from uvicorn's access log.

    The feed polls GET /api/learn/progress/<turn_id> every 600ms for the length of a turn (20-60s), so
    one turn writes ~50-100 identical 200 lines. They buried the lines that matter -- the agent's own
    `-> tool: ...` records and any real error -- and made a working turn look like a runaway loop. The
    endpoint itself is unchanged; only its access-log line goes.

    A filter on the access logger rather than a uvicorn flag, because uvicorn has no "exclude this path"
    option and the app is started both through `python -m app` and directly with `uvicorn app.main:app`.
    Attached at import, so it applies however the server is launched."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn passes (client, method, path, http_version, status) as the record's args; a plain
        # string record (a startup banner, say) has none, and is always kept.
        if isinstance(args, tuple) and len(args) >= 3:
            return "/api/learn/progress/" not in str(args[2])
        return True


logging.getLogger("uvicorn.access").addFilter(_DropProgressPolls())


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Create the schema, and refuse to start half-configured.

    The database is also created lazily on first use (see db._ready), because the tests build a
    TestClient without running these hooks -- so this is a fast fail for a misconfigured deployment
    rather than the only thing standing between the app and a missing table."""
    init_db()
    ensure_auth_configured()
    _ensure_model_configured()
    yield


app = FastAPI(title="LearnCode", lifespan=_lifespan)

# The Vite dev server runs on its own port and calls this API directly; harmless in production
# where the backend serves the built SPA itself (same origin, so CORS is not even exercised). A
# deployment that serves the API on its own domain and the SPA elsewhere sets LEARNCODE_CORS_ORIGINS to
# a comma-separated list; unset keeps the dev defaults so a local run needs no configuration.
_cors_origins = [
    o.strip()
    for o in (os.environ.get("LEARNCODE_CORS_ORIGINS") or "").split(",")
    if o.strip()
] or ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(router)


@app.get("/health", include_in_schema=False)
def health() -> dict:
    """Liveness for the platform's health check (and for a container orchestrator's readiness probe).

    Deliberately touches the database: the app is useless without it, and a process that has started but
    cannot open its SQLite file would otherwise report healthy forever. It does NOT check the model API
    -- an outage there should not take the whole app out of rotation, because browsing stored pages still
    works and the learner only loses the ability to run new turns."""
    from sqlalchemy import text

    from . import db

    try:
        # `_ready()` is the app's own lazy initialiser: it creates the schema on first use and returns
        # the session factory, so this checks exactly what a real request would depend on.
        with db._ready()() as session:
            session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001 - any failure here means "not ready"
        from fastapi import HTTPException

        raise HTTPException(503, f"database unavailable: {e}") from e
    return {"status": "ok"}


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Create the schema, and refuse to start half-configured.

    The database is also created lazily on first use (see db._ready), because the tests build a
    TestClient without running these hooks -- so this is a fast fail for a misconfigured deployment
    rather than the only thing standing between the app and a missing table."""
    init_db()
    ensure_auth_configured()
    _ensure_model_configured()
    yield


def _ensure_model_configured() -> None:
    """Fail at boot, not on the learner's first turn, when no model credential is reachable.

    seedagent can reach a model three ways: an API key (Anthropic, or DeepSeek's compatible endpoint),
    a Foundry key, or Azure AD. Only the first two are readable from the environment, so a missing key
    here is reported only when *nothing* else could work -- an Azure AD deployment has no key by design
    and must not be refused. Checking it this way turns the most common deploy mistake (a .env without
    the key) into a startup failure instead of a turn that fails for every visitor."""
    from seedagent import config

    if not config.uses_anthropic_api():
        # Foundry path: either a key, or Azure AD, which needs no static check from here.
        return
    if config.current_api_key():
        return
    raise RuntimeError(
        "No model API key is configured. Set ANTHROPIC_API_KEY, or the provider key seedagent reads "
        "for CODEAGENT_PROVIDER (see backend/.env.example); without one every turn fails."
    )

_FRONTEND_DIST = Path(
    os.environ.get("LEARNCODE_FRONTEND_DIST")
    or Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
)

if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        """Every non-API path serves the SPA shell; React Router (if any is added later) handles
        the rest client-side. full_path is unused on purpose: there is only one page to serve."""
        return FileResponse(_FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    # A reloader that watches data/ fights the agent: every session clones repositories into
    # data/workspaces/<session_id>/ (hundreds of files each), and a running turn writes its own
    # conversation and last_page.json. WatchFiles sees all of it and restarts the server *during the
    # turn* -- which on this machine has ended with the reloader dying outright ("memory allocation
    # of 34832 bytes failed") while Uvicorn spawned a replacement. It also means the model's turn is
    # killed halfway and the learner's roadmap never arrives.
    #
    # So the default watch must exclude the data folder. Uvicorn's --reload-exclude matches on the
    # path relative to the reload directory, and a recursive pattern (**) is needed as well as the
    # plain one: on Windows the first alone did not cover nested paths. Passing them here rather than
    # relying on the command line means the supported way to start the server cannot forget them.
    #
    # Reload is opt-in: `python -m app` serves without it, and `python -m app --reload` turns it on.
    #
    # Host and port come from the environment so the same entrypoint works in a container: the default
    # stays 127.0.0.1:8000 for a local run, and LEARNCODE_HOST=0.0.0.0 makes it reachable from outside
    # (a container that binds only to loopback is unreachable from the platform's router).
    import sys

    import uvicorn

    host = os.environ.get("LEARNCODE_HOST", "127.0.0.1")
    port = int(os.environ.get("LEARNCODE_PORT", "8000"))

    if "--reload" in sys.argv:
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            reload=True,
            reload_excludes=["data/*", "data/**", "*/data/*", "*/data/**"],
        )
    else:
        uvicorn.run(app, host=host, port=port, proxy_headers=True, forwarded_allow_ips="*")
