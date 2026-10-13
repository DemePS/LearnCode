"""The FastAPI app: the JSON API under /api, and (once built) the React SPA's static files for
everything else. Run with `uv run uvicorn app.main:app --reload` from backend/."""

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
