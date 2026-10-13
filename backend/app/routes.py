"""The whole API. The backend does not know what a "roadmap" or an "exercise" is -- it only ever
forwards a message to the agent and hands back whatever page JSON it returns; see agent.py's
PAGE_SYSTEM_PROMPT for the page shape and action semantics.

Every /api/learn/* route requires a logged-in user (auth.get_current_user), and every project-scoped
route further checks ownership (crud.get_project returns None for a project that doesn't exist or
belongs to someone else, turned into the same 404 here either way, so a learner can't probe for
other users' project ids)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import agent, auth, crud, db
from .schemas import (
    ActionRequest,
    AskRequest,
    AskResponse,
    ClarifyRequest,
    ClarifyResponse,
    DraftRequest,
    LoginRequest,
    PageResponse,
    RegisterRequest,
    StartRequest,
    TokenResponse,
)

auth_router = APIRouter(prefix="/api/auth")
learn_router = APIRouter(prefix="/api/learn")


@auth_router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, session: Session = Depends(db.get_db)) -> TokenResponse:
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Enter a valid email address")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if crud.get_user_by_email(session, email) is not None:
        raise HTTPException(409, "An account with that email already exists")
    user = crud.create_user(session, email, body.password)
    return TokenResponse(token=auth.create_token(user.id), email=user.email)


@auth_router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: Session = Depends(db.get_db)) -> TokenResponse:
    user = crud.authenticate(session, body.email, body.password)
    if user is None:
        raise HTTPException(401, "Wrong email or password")
    return TokenResponse(token=auth.create_token(user.id), email=user.email)


def _project_or_404(session: Session, project_id: str, user: db.User) -> db.Project:
    project = crud.get_project(session, project_id, user)
    if project is None:
        raise HTTPException(404, "No such project")
    return project


@learn_router.post("/clarify", response_model=ClarifyResponse)
async def clarify(body: ClarifyRequest, user: db.User = Depends(auth.get_current_user)) -> ClarifyResponse:
    """No project exists yet at this point, so there's nothing to own-check -- being logged in is
    enough. See agent.clarify's docstring for why this doesn't touch the database at all."""
    objective = body.objective.strip()
    if not objective:
        raise HTTPException(400, "objective must not be empty")
    history = [{"question": t.question, "answer": t.answer} for t in body.history]
    result = await run_in_threadpool(agent.clarify, objective, history)
    if result["ready"]:
        return ClarifyResponse(ready=True)
    return ClarifyResponse(ready=False, question=result["question"])


@learn_router.post("/start", response_model=PageResponse)
async def start(
    body: StartRequest,
    user: db.User = Depends(auth.get_current_user),
    session: Session = Depends(db.get_db),
) -> PageResponse:
    objective = body.objective.strip()
    if not objective:
        raise HTTPException(400, "objective must not be empty")
    project = crud.create_project(session, user, objective)
    parts = [f"Learning objective: {objective}"]
    for turn in body.history:
        parts.append(f"Clarifying Q: {turn.question}\nLearner's answer: {turn.answer}")
    parts.append(f"Propose the project roadmap page. {agent.variety_hint()}")
    message = "\n\n".join(parts)
    page, assistant_text = await run_in_threadpool(agent.run_turn, [], message, project.id)
    crud.append_turn(session, project, message, assistant_text)
    crud.sync_exercises_from_page(session, project, page)
    crud.save_last_page(session, project, page)
    return PageResponse(session_id=project.id, page=page)


@learn_router.get("/{project_id}", response_model=PageResponse)
async def get_project_page(
    project_id: str,
    user: db.User = Depends(auth.get_current_user),
    session: Session = Depends(db.get_db),
) -> PageResponse:
    project = _project_or_404(session, project_id, user)
    if project.last_page is None:
        raise HTTPException(404, "This project has no page yet")
    page = crud.apply_saved_draft(session, project, dict(project.last_page))
    return PageResponse(session_id=project.id, page=page)


@learn_router.post("/{project_id}/action", response_model=PageResponse)
async def act(
    project_id: str,
    body: ActionRequest,
    user: db.User = Depends(auth.get_current_user),
    session: Session = Depends(db.get_db),
) -> PageResponse:
    project = _project_or_404(session, project_id, user)
    action = body.action.strip()
    if not action:
        raise HTTPException(400, "action must not be empty")
    message = (
        f"The learner triggered action '{action}' with payload:\n"
        f"{json.dumps(body.payload)}\n\n"
        "Continue the experience and return the next page JSON."
    )
    messages = crud.load_messages(session, project)
    page, assistant_text = await run_in_threadpool(agent.run_turn, messages, message, project.id)
    crud.append_turn(session, project, message, assistant_text)
    crud.sync_exercises_from_page(session, project, page)

    # A submission: the payload carries exercise_id + code, and the response opens with feedback --
    # record it as a graded Submission. exercise_id comes from the request (not the response: the
    # feedback block itself doesn't carry one, but it's unambiguous within a single HTTP call).
    blocks = page.get("blocks", [])
    if "exercise_id" in body.payload and "code" in body.payload and blocks and blocks[0].get("type") == "feedback":
        fb = blocks[0]
        crud.record_submission(
            session,
            project,
            body.payload["exercise_id"],
            body.payload["code"],
            bool(fb.get("passed")),
            fb.get("summary", ""),
            fb.get("details", ""),
        )

    page = crud.apply_saved_draft(session, project, page)
    crud.save_last_page(session, project, page)
    return PageResponse(session_id=project.id, page=page)


@learn_router.post("/{project_id}/draft")
async def save_draft(
    project_id: str,
    body: DraftRequest,
    user: db.User = Depends(auth.get_current_user),
    session: Session = Depends(db.get_db),
) -> dict:
    """Autosave only -- see db.Exercise.draft_code. Called often (debounced on the frontend), so it
    deliberately does nothing expensive: no agent call, just one row update."""
    project = _project_or_404(session, project_id, user)
    crud.save_draft(session, project, body.exercise_id, body.code)
    return {"ok": True}


@learn_router.post("/{project_id}/ask", response_model=AskResponse)
async def ask(
    project_id: str,
    body: AskRequest,
    user: db.User = Depends(auth.get_current_user),
    session: Session = Depends(db.get_db),
) -> AskResponse:
    """The floating help widget: unlike /action, this never touches the project's last_page or its
    exercises/submissions -- the answer is shown alongside whatever page is already on screen, not
    in place of it."""
    project = _project_or_404(session, project_id, user)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question must not be empty")
    messages = crud.load_messages(session, project)
    answer = await run_in_threadpool(agent.ask, messages, question, body.context, project.id)
    return AskResponse(answer=answer)
