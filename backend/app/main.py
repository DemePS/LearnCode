"""The FastAPI app: the JSON API under /api, and (once built) the React SPA's static files for
everything else.

Run it with `uv run python -m app` from backend/ (see the __main__ block at the bottom, which is the
supported way in: it passes the --reload-exclude flags the app needs), or with
`uv run uvicorn app.main:app --reload --reload-exclude 'data/*' --reload-exclude 'data/**'`."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes import router

app = FastAPI(title="LearnCode")

# The Vite dev server runs on its own port and calls this API directly; harmless in production
# where the backend serves the built SPA itself (same origin, so CORS is not even exercised).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

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
