"""Where a learning session lives on disk: one project folder per session (seedagent needs a real
directory, and keeps that project's conversation there for `resume=True`), plus the last page we
rendered, so a browser refresh doesn't need a fresh model call just to redraw the current screen."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

DATA_DIR = Path(os.environ.get("LEARNCODE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
WORKSPACES_DIR = DATA_DIR / "workspaces"

_LAST_PAGE_FILE = "last_page.json"


def new_session_id() -> str:
    return uuid.uuid4().hex


def workspace_for(session_id: str) -> Path:
    path = WORKSPACES_DIR / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_exists(session_id: str) -> bool:
    return workspace_for_check(session_id).is_dir()


def workspace_for_check(session_id: str) -> Path:
    """Like workspace_for, but never creates the folder -- used to answer "does this session
    exist" without side effects."""
    return WORKSPACES_DIR / session_id


def save_last_page(session_id: str, page: dict) -> None:
    (workspace_for(session_id) / _LAST_PAGE_FILE).write_text(json.dumps(page), encoding="utf-8")


def load_last_page(session_id: str) -> dict | None:
    path = workspace_for_check(session_id) / _LAST_PAGE_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
