"""API request/response shapes. A `Page` is left as a free-form dict on purpose: its blocks are
produced by seedagent at runtime (see app/agent.py's PAGE_SYSTEM_PROMPT for the schema the model
follows), and the frontend renders whatever block types come back. The backend only needs to pass
it through, not know its shape."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StartRequest(BaseModel):
    objective: str


class ActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = {}


class PageResponse(BaseModel):
    session_id: str
    page: dict[str, Any]
