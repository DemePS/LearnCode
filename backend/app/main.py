"""The FastAPI app: the JSON API under /api, and (once built) the React SPA's static files for
everything else.

Run it with `uv run python -m app` from backend/ (see the __main__ block at the bottom, which is the
supported way in: it passes the --reload-exclude flags the app needs), or with
`uv run uvicorn app.main:app --reload --reload-exclude 'data/*' --reload-exclude 'data/**'`."""

from __future__ import annotations

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

app = FastAPI(title="LearnCode")

# The Vite dev server runs on its own port and calls this API directly; harmless in production
# where the backend serves the built SPA itself (same origin, so CORS is not even exercised).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(router)


@app.on_event("startup")
async def _startup() -> None:
    """Create the schema, and refuse to start with a half-configured sign-in.

    The database is also created lazily on first use (see db._ready), because the tests build a
    TestClient without running these hooks -- so this is a fast fail for a misconfigured deployment
    rather than the only thing standing between the app and a missing table."""
    init_db()
    ensure_auth_configured()

_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

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
    import sys

    import uvicorn

    if "--reload" in sys.argv:
        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=8000,
            reload=True,
            reload_excludes=["data/*", "data/**", "*/data/*", "*/data/**"],
        )
    else:
        uvicorn.run(app, host="127.0.0.1", port=8000)
