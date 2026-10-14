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
    # Optional id the browser invents for this turn, so it can poll /progress/{turn_id} while the
    # request is still in flight (the turn itself returns nothing until it is finished). Absent when
    # the caller does not want progress.
    turn_id: str = ""


class ActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = {}
    turn_id: str = ""


class ProgressStep(BaseModel):
    text: str
    kind: str = "work"
    detail: str = ""
    ok: bool | None = None
    done: bool = False
    error: str = ""


class ProgressResponse(BaseModel):
    """What the activity feed polls while a turn runs. `steps` may be empty for the first moments,
    and a 404 means the turn id is unknown (finished long ago, or from a previous process)."""

    steps: list[ProgressStep] = []
    done: bool = False


class PageResponse(BaseModel):
    session_id: str
    page: dict[str, Any]


class AskRequest(BaseModel):
    question: str
    context: dict[str, Any] = {}


class AskResponse(BaseModel):
    answer: str
