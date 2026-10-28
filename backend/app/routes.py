"""The whole API: a pre-session clarification pass, starting a session, acting on its current page,
and a page refetch. The backend does not know what a "roadmap" or an "exercise" is -- it only ever
forwards a message to the agent and hands back whatever page JSON it returns; see agent.py's
PAGE_SYSTEM_PROMPT for the page shape and action semantics.

Every route here belongs to one learner: a session row records its owner, and a request for someone
else's session answers 404 rather than 403 so the API never confirms that an id exists."""

from __future__ import annotations

import json
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import agent, progress, ratelimit, store
from .auth import current_user, optional_user
from .db import LearnSession, User
from .page_schema import RESERVED_ACTION as SHOW_ROADMAP_ACTION
from .schemas import (
    ActionRequest,
    AskRequest,
    AskResponse,
    ClarifyRequest,
    ClarifyResponse,
    ExerciseListResponse,
    ExerciseSummary,
    PageResponse,
    ProgressResponse,
    SessionListResponse,
    SessionSummary,
    StartRequest,
)

router = APIRouter(prefix="/api/learn")

# One turn at a time per session. seedagent keeps a session's conversation in a file inside its
# workspace, and two turns appending to it at once would interleave (or duplicate) messages, so this
# is about correctness, not throughput -- different sessions still run in parallel, the whole point of
# the keyed-sessions work. Per-process: see the multi-instance note in TODO.md.
_turn_locks: dict[str, threading.Lock] = {}
_turn_locks_guard = threading.Lock()


def _turn_lock(session_id: str) -> threading.Lock:
    with _turn_locks_guard:
        return _turn_locks.setdefault(session_id, threading.Lock())


def _own_session(session_id: str, user: User) -> LearnSession:
    """The caller's session, or 404. A malformed id and a session belonging to someone else are
    deliberately the same answer (see the module docstring)."""
    row = store.get_session(session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "No such session")
    return row


def _public(page: dict) -> dict:
    """Drop the internal "_error" marker (db.classify_page reads it) so the client always receives
    exactly the page shape it always has."""
    if "_error" in page:
        page = {k: v for k, v in page.items() if k != "_error"}
    return page


@router.get("/progress/{turn_id}", response_model=ProgressResponse)
async def turn_progress(turn_id: str, user: User | None = Depends(optional_user)) -> ProgressResponse:
    """What the browser polls while /start or /action is still in flight, so the learner sees the
    agent searching, cloning and reading instead of a frozen button (see app/progress.py).

    Returns an empty, finished feed rather than a 404 for an unknown turn: a poll can legitimately
    arrive just after the turn's entry was pruned (or after a server restart), and that is not an
    error the frontend should have to handle -- it only ever means "nothing left to show".

    Deliberately not 401 for a signed-out caller: the feed is a cosmetic detail of a request that is
    already authenticated, and a poll must never be the thing that fails a turn. A turn belonging to
    someone else still reports an empty feed, so one learner cannot watch another's activity."""
    snap = progress.snapshot(turn_id, user.id if user is not None else None)
    if snap is None:
        return ProgressResponse(steps=[], done=True)
    return ProgressResponse(steps=snap["steps"], done=snap["done"])


@router.post("/clarify", response_model=ClarifyResponse)
async def clarify(body: ClarifyRequest, user: User = Depends(current_user)) -> ClarifyResponse:
    """Called before any session exists (no workspace, no seedagent project -- see agent.clarify's
    docstring): decides whether the agent needs to ask one more question before a roadmap could be
    designed well. The frontend keeps asking this, growing `history` itself, until ready is true,
    then calls /start with the same objective and history.

    Needs a signed-in learner even though it creates nothing: it costs a model call, so leaving it
    open would let anyone spend the operator's budget anonymously.

    Rate-limited like the turn routes: the frontend calls this once per clarifying question, but nothing
    stopped a client from looping it (see app/ratelimit.py)."""
    objective = body.objective.strip()
    if not objective:
        raise HTTPException(400, "objective must not be empty")
    slot = ratelimit.acquire_turn_slot(user.id)
    try:
        history = [{"question": t.question, "answer": t.answer} for t in body.history]
        result = await run_in_threadpool(agent.clarify, objective, history)
    finally:
        slot.release()
    if result["ready"]:
        return ClarifyResponse(ready=True)
    return ClarifyResponse(ready=False, question=result["question"])


@router.get("", response_model=SessionListResponse)
async def list_sessions(user: User = Depends(current_user)) -> SessionListResponse:
    """The learner's own sessions, newest first -- what the frontend uses to resume after a reload
    (localStorage can be cleared, or hold an id belonging to a session that no longer exists)."""
    rows = await run_in_threadpool(store.sessions_for, user.id)
    return SessionListResponse(
        sessions=[
            SessionSummary(id=r.id, objective=r.objective, updated_at=r.updated_at)
            for r in rows
        ]
    )


@router.post("/start", response_model=PageResponse)
async def start(body: StartRequest, user: User = Depends(current_user)) -> PageResponse:
    objective = body.objective.strip()
    if not objective:
        raise HTTPException(400, "objective must not be empty")
    # Before any row or directory is created: a refused request must leave nothing behind.
    slot = ratelimit.acquire_turn_slot(user.id)
    session_id = store.new_session_id()
    # The row is created before the turn runs, so the session is owned from the moment it exists --
    # an interrupted /start leaves an owned, resumable session rather than an orphan directory.
    await run_in_threadpool(store.create_session, session_id, user.id, objective)
    workspace = store.workspace_for(session_id)
    parts = [f"Learning objective: {objective}"]
    for turn in body.history:
        parts.append(f"Clarifying Q: {turn.question}\nLearner's answer: {turn.answer}")
    parts.append(f"Propose the project roadmap page. {agent.variety_hint()}")
    message = "\n\n".join(parts)
    if body.turn_id:
        # Registered before the worker starts, so a poll arriving immediately finds the feed rather
        # than an unknown turn id.
        progress.start(body.turn_id, user.id)
    lock = _turn_lock(session_id)
    if not lock.acquire(blocking=False):
        slot.release()
        raise HTTPException(409, "A turn is already running for this session")
    try:
        result = await run_in_threadpool(agent.run_turn, workspace, message, False, body.turn_id,
                                         user.token(), True, "Thinking about your objective")
    finally:
        lock.release()
        slot.release()
    page = result.page
    # The exercises the roadmap turn invented are stored with it: each is answerable later by its
    # open_action, which is what makes clicking a roadmap item a database read instead of a turn.
    if result.exercises:
        await run_in_threadpool(
            store.save_pages,
            session_id,
            list(result.exercises.values()),
            list(result.exercises),
        )
    await run_in_threadpool(store.save_page, session_id, page, body.turn_id or None)
    return PageResponse(session_id=session_id, page=_public(page))


@router.get("/{session_id}", response_model=PageResponse)
async def get_session(session_id: str, user: User = Depends(current_user)) -> PageResponse:
    """The newest page for this session -- what a reload asks for. Served from the database, so
    redrawing a screen the agent already produced never costs a model call."""
    _own_session(session_id, user)
    page = await run_in_threadpool(store.latest_page, session_id)
    if page is None:
        raise HTTPException(404, "This session has no page yet")
    return PageResponse(session_id=session_id, page=_public(page))


@router.get("/{session_id}/roadmap", response_model=PageResponse)
async def get_roadmap(session_id: str, user: User = Depends(current_user)) -> PageResponse:
    """The stored roadmap page, whatever screen the learner is on now.

    A GET because this can only ever read: the roadmap was designed and saved by the /start turn, and
    asking for it again is a lookup, not a new generation."""
    _own_session(session_id, user)
    page = await run_in_threadpool(store.roadmap_page, session_id)
    if page is None:
        raise HTTPException(404, "This session has no roadmap yet")
    return PageResponse(session_id=session_id, page=_public(page))


@router.get("/{session_id}/exercises", response_model=ExerciseListResponse)
async def list_exercises(session_id: str, user: User = Depends(current_user)) -> ExerciseListResponse:
    """The project's exercises, in roadmap order, read from stored pages."""
    _own_session(session_id, user)
    rows = await run_in_threadpool(store.exercises, session_id)
    return ExerciseListResponse(exercises=[ExerciseSummary(**row) for row in rows])


@router.get("/{session_id}/exercises/{exercise_id}", response_model=PageResponse)
async def get_exercise(
    session_id: str, exercise_id: str, user: User = Depends(current_user)
) -> PageResponse:
    """One stored exercise, draft included -- opening an exercise from the roadmap.

    A bare GET is the point: opening an exercise the agent already wrote must never be able to reach
    the agent, so there is no action id and no turn on this path at all."""
    _own_session(session_id, user)
    page = await run_in_threadpool(store.exercise_page, session_id, exercise_id)
    if page is None:
        raise HTTPException(404, "No such exercise in this session")
    return PageResponse(session_id=session_id, page=_public(page))


@router.post("/{session_id}/action", response_model=PageResponse)
async def act(session_id: str, body: ActionRequest, user: User = Depends(current_user)) -> PageResponse:
    _own_session(session_id, user)
    action = body.action.strip()
    if not action:
        raise HTTPException(400, "action must not be empty")

    # "Back to roadmap" is the frontend's own persistent button, and it sends this reserved id whatever
    # actions the page carries. It is pure navigation: the roadmap is already stored, so answer it from
    # there. Left to fall through, it ran a whole turn to redraw a stored screen and saved the redraw as
    # a new page, which is how a navigation click ended up costing half a minute and a model call.
    if action == SHOW_ROADMAP_ACTION:
        page = await run_in_threadpool(store.roadmap_page, session_id)
        if page is None:
            raise HTTPException(404, "This session has no roadmap yet")
        return PageResponse(session_id=session_id, page=_public(page))

    # Opening an exercise the roadmap turn already wrote costs nothing: the page was generated while the
    # cloned code was in context, so it is read back rather than regenerated. The draft is whatever the
    # learner has typed so far -- absent until they submit -- and it is written into the stored exercise so
    # coming back to it shows what they wrote, which is also what makes a retry land on their own code.
    #
    # Keyed on the exercise's own id, not on the clicked action: an exercise is stored under its open_action
    # while its submit button sends a different id, so keying on the action dropped the code.
    #
    # Submitting is told apart by the *stored code editor's* submit_action, not by whether the payload
    # carries code -- reopening an exercise sends its contents back too, and grading that again would burn
    # a turn each time a learner looked at their own work. Matching on the editor's own id means a retry
    # button works whichever action id the model invented for it: an unknown id that carries an editor the
    # store already knows is an open, never a grade.
    payload = body.payload if isinstance(body.payload, dict) else {}
    exercise_id = payload.get("exercise_id")
    submitting = False
    if isinstance(exercise_id, str) and exercise_id:
        stored = await run_in_threadpool(store.exercise_page, session_id, exercise_id)
        if stored is not None:
            editors = [b for b in stored.get("blocks", [])
                       if isinstance(b, dict) and b.get("type") == "code_editor"]
            submitting = any(b.get("submit_action") == action for b in editors)
            if not submitting:
                return PageResponse(session_id=session_id, page=_public(stored))
            draft = payload.get("code")
            if isinstance(draft, str):
                await run_in_threadpool(store.set_exercise_draft, session_id, exercise_id, draft)
    else:
        stored = await run_in_threadpool(store.page_for_action, session_id, action)
        if stored is not None:
            return PageResponse(session_id=session_id, page=_public(stored))

    # The feed's first line, before any tool has run. A submission is the one action with something
    # specific to say -- the agent is reading the code the learner just wrote -- and the generic wording
    # is wrong for every other case too, since these turns continue an existing project rather than
    # starting from an objective.
    opening = "Reviewing your code" if submitting else "Working out the next step"

    message = (
        f"The learner triggered action '{action}' with payload:\n"
        f"{json.dumps(body.payload)}\n\n"
        "Continue the experience and return the next page JSON."
    )
    workspace = store.workspace_for(session_id)
    # Everything above is a database read or a stored-page hit, so it stays free and unthrottled; only a
    # request that reached this point is about to spend a model call.
    slot = ratelimit.acquire_turn_slot(user.id)
    if body.turn_id:
        progress.start(body.turn_id, user.id)
    lock = _turn_lock(session_id)
    if not lock.acquire(blocking=False):
        slot.release()
        raise HTTPException(409, "A turn is already running for this session")
    try:
        result = await run_in_threadpool(agent.run_turn, workspace, message, True, body.turn_id,
                                         user.token(), False, opening)
    finally:
        lock.release()
        slot.release()
    page = result.page
    # The action id is stored with the page: it is what lets a later "show me this screen again" be a
    # database read rather than a re-run of the turn that produced it.
    await run_in_threadpool(store.save_page, session_id, page, action)
    return PageResponse(session_id=session_id, page=_public(page))


@router.delete("/{session_id}")
async def delete_session(session_id: str, user: User = Depends(current_user)) -> dict:
    """Forget one of the caller's projects, at their request: its pages, its session row and its
    workspace folder (see store.delete_session).

    Ownership is checked first, so a wrong id or someone else's is a 404 -- the same answer /get gives,
    and never a hint that the session exists. A turn in flight makes it a 409 rather than a race:
    deleting the folder under a running clone would leave the turn writing into a path that is gone,
    and the learner with a page for a project that no longer exists."""
    _own_session(session_id, user)
    lock = _turn_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "A step is already running for this project -- wait for it to finish, then delete it.")
    lock.release()
    if not await run_in_threadpool(store.delete_session, session_id):
        raise HTTPException(404, "No such session")
    return {"ok": True}


@router.post("/{session_id}/ask", response_model=AskResponse)
async def ask(session_id: str, body: AskRequest, user: User = Depends(current_user)) -> AskResponse:
    """The floating help widget: unlike /action, this never touches the session's page history --
    the answer is shown alongside whatever page is already on screen, not in place of it."""
    _own_session(session_id, user)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question must not be empty")
    workspace = store.workspace_for(session_id)
    slot = ratelimit.acquire_turn_slot(user.id)
    lock = _turn_lock(session_id)
    if not lock.acquire(blocking=False):
        slot.release()
        raise HTTPException(409, "A turn is already running for this session")
    try:
        answer = await run_in_threadpool(agent.ask, workspace, question, body.context, user.token())
    finally:
        lock.release()
        slot.release()
    return AskResponse(answer=answer)
