"""API request/response shapes. A `Page` is left as a free-form dict on purpose: its blocks are
produced by seedagent at runtime (see app/agent.py's PAGE_SYSTEM_PROMPT for the schema the model
follows), and the frontend renders whatever block types come back. The backend only needs to pass
it through, not know its shape."""

from __future__ import annotations

from datetime import datetime
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


class SessionSummary(BaseModel):
    """One row of the learner's project list -- enough to resume from a reload without naming the
    project by hand, and nothing more (the pages themselves are fetched per session)."""

    id: str
    objective: str
    updated_at: datetime


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary] = []


class ExerciseSummary(BaseModel):
    """One row of a project's exercise list -- what the learner needs to pick an exercise, without
    the starter code (that arrives with the exercise itself). Read straight from stored pages, so
    listing exercises never costs a turn."""

    exercise_id: str
    title: str
    file_name: str
    language: str
    open_action: str


class ExerciseListResponse(BaseModel):
    exercises: list[ExerciseSummary] = []


class AskRequest(BaseModel):
    question: str
    context: dict[str, Any] = {}


class AskResponse(BaseModel):
    answer: str
