"""The whole API: two actions (start a session, act on its current page) plus a page refetch. The
backend does not know what a "roadmap" or an "exercise" is -- it only ever forwards a message to
the agent and hands back whatever page JSON it returns; see agent.py's PAGE_SYSTEM_PROMPT for the
page shape and action semantics."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from . import agent, store
from .schemas import ActionRequest, PageResponse, StartRequest

router = APIRouter(prefix="/api/learn")


@router.post("/start", response_model=PageResponse)
async def start(body: StartRequest) -> PageResponse:
    objective = body.objective.strip()
    if not objective:
        raise HTTPException(400, "objective must not be empty")
    session_id = store.new_session_id()
    workspace = store.workspace_for(session_id)
    message = f"Learning objective: {objective}\n\nPropose the project roadmap page."
    page = await run_in_threadpool(agent.run_turn, workspace, message, False)
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
    page = await run_in_threadpool(agent.run_turn, workspace, message, True)
    store.save_last_page(session_id, page)
    return PageResponse(session_id=session_id, page=page)
