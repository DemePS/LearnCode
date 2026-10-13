"""API request/response shapes. A `Page` is left as a free-form dict on purpose: its blocks are
produced by seedagent at runtime (see app/agent.py's PAGE_SYSTEM_PROMPT for the schema the model
follows), and the frontend renders whatever block types come back. The backend only needs to pass
it through, not know its shape."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ClarifyTurn(BaseModel):
    question: str
    answer: str


class ClarifyRequest(BaseModel):
    objective: str
    history: list[ClarifyTurn] = []


class ClarifyResponse(BaseModel):
    ready: bool
    question: str | None = None


class StartRequest(BaseModel):
    objective: str
    history: list[ClarifyTurn] = []


class ActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = {}


class PageResponse(BaseModel):
    session_id: str
    page: dict[str, Any]


class AskRequest(BaseModel):
    question: str
    context: dict[str, Any] = {}


class AskResponse(BaseModel):
    answer: str


class DraftRequest(BaseModel):
    """Autosaved in-progress code for one exercise, not yet submitted -- see db.Exercise.draft_code."""

    exercise_id: str
    code: str


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    token: str
    email: str
