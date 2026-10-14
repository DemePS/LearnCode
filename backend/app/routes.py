"""The whole API: a pre-session clarification pass, starting a session, acting on its current page,
and a page refetch. The backend does not know what a "roadmap" or an "exercise" is -- it only ever
forwards a message to the agent and hands back whatever page JSON it returns; see agent.py's
PAGE_SYSTEM_PROMPT for the page shape and action semantics."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from . import agent, progress, store
from .schemas import (
    ActionRequest,
    AskRequest,
    AskResponse,
    ClarifyRequest,
    ClarifyResponse,
    PageResponse,
    ProgressResponse,
    StartRequest,
)

router = APIRouter(prefix="/api/learn")


@router.get("/progress/{turn_id}", response_model=ProgressResponse)
async def turn_progress(turn_id: str) -> ProgressResponse:
    """What the browser polls while /start or /action is still in flight, so the learner sees the
    agent searching, cloning and reading instead of a frozen button (see app/progress.py).

    Returns an empty, finished feed rather than a 404 for an unknown turn: a poll can legitimately
    arrive just after the turn's entry was pruned (or after a server restart), and that is not an
    error the frontend should have to handle -- it only ever means "nothing left to show"."""
    snap = progress.snapshot(turn_id)
    if snap is None:
        return ProgressResponse(steps=[], done=True)
    return ProgressResponse(steps=snap["steps"], done=snap["done"])


@router.post("/clarify", response_model=ClarifyResponse)
async def clarify(body: ClarifyRequest) -> ClarifyResponse:
    """Called before any session exists (no workspace, no seedagent project -- see agent.clarify's
    docstring): decides whether the agent needs to ask one more question before a roadmap could be
    designed well. The frontend keeps asking this, growing `history` itself, until ready is true,
    then calls /start with the same objective and history."""
    objective = body.objective.strip()
    if not objective:
        raise HTTPException(400, "objective must not be empty")
    history = [{"question": t.question, "answer": t.answer} for t in body.history]
    result = await run_in_threadpool(agent.clarify, objective, history)
    if result["ready"]:
        return ClarifyResponse(ready=True)
    return ClarifyResponse(ready=False, question=result["question"])


@router.post("/start", response_model=PageResponse)
async def start(body: StartRequest) -> PageResponse:
    objective = body.objective.strip()
    if not objective:
        raise HTTPException(400, "objective must not be empty")
    session_id = store.new_session_id()
    workspace = store.workspace_for(session_id)
    parts = [f"Learning objective: {objective}"]
    for turn in body.history:
        parts.append(f"Clarifying Q: {turn.question}\nLearner's answer: {turn.answer}")
    parts.append(f"Propose the project roadmap page. {agent.variety_hint()}")
    message = "\n\n".join(parts)
    if body.turn_id:
        # Registered before the worker starts, so a poll arriving immediately finds the feed rather
        # than an unknown turn id.
        progress.start(body.turn_id)
    page = await run_in_threadpool(agent.run_turn, workspace, message, False, body.turn_id)
    store.save_last_page(session_id, page)
    return PageResponse(session_id=session_id, page=page)


@router.get("/{session_id}", response_model=PageResponse)
async def get_session(session_id: str) -> PageResponse:
    if not store.session_exists(session_id):
        raise HTTPException(404, "No such session")
    page = store.load_last_page(session_id)
    if page is None:
        raise HTTPException(404, "This session has no page yet")
    return PageResponse(session_id=session_id, page=page)


@router.post("/{session_id}/action", response_model=PageResponse)
async def act(session_id: str, body: ActionRequest) -> PageResponse:
    if not store.session_exists(session_id):
        raise HTTPException(404, "No such session")
    action = body.action.strip()
    if not action:
        raise HTTPException(400, "action must not be empty")
    message = (
        f"The learner triggered action '{action}' with payload:\n"
        f"{json.dumps(body.payload)}\n\n"
        "Continue the experience and return the next page JSON."
    )
    workspace = store.workspace_for(session_id)
    if body.turn_id:
        progress.start(body.turn_id)
    page = await run_in_threadpool(agent.run_turn, workspace, message, True, body.turn_id)
    store.save_last_page(session_id, page)
    return PageResponse(session_id=session_id, page=page)


@router.post("/{session_id}/ask", response_model=AskResponse)
async def ask(session_id: str, body: AskRequest) -> AskResponse:
    """The floating help widget: unlike /action, this never touches the session's last_page --
    the answer is shown alongside whatever page is already on screen, not in place of it."""
    if not store.session_exists(session_id):
        raise HTTPException(404, "No such session")
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question must not be empty")
    workspace = store.workspace_for(session_id)
    answer = await run_in_threadpool(agent.ask, workspace, question, body.context)
    return AskResponse(answer=answer)
