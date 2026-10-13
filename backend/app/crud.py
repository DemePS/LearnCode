"""Database operations: users, projects, the raw conversation (Message) each turn is built from, and
parsing the agent's page JSON into structured Exercise/Submission rows the moment it's generated --
the page JSON itself is never the system of record, just a rendering of what's stored here."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth, db


def create_user(session: Session, email: str, password: str) -> db.User:
    user = db.User(email=email.strip().lower(), password_hash=auth.hash_password(password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def get_user_by_email(session: Session, email: str) -> db.User | None:
    return session.scalar(select(db.User).where(db.User.email == email.strip().lower()))


def authenticate(session: Session, email: str, password: str) -> db.User | None:
    user = get_user_by_email(session, email)
    if user is None or not auth.verify_password(password, user.password_hash):
        return None
    return user


def create_project(session: Session, user: db.User, objective: str) -> db.Project:
    project = db.Project(user_id=user.id, objective=objective)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def get_project(session: Session, project_id: str, user: db.User) -> db.Project | None:
    """None both when the project doesn't exist and when it belongs to someone else -- the caller
    turns either into the same 404, so a learner can't probe for other users' project ids."""
    project = session.get(db.Project, project_id)
    if project is None or project.user_id != user.id:
        return None
    return project


def load_messages(session: Session, project: db.Project) -> list[dict]:
    """The conversation so far, in the shape agent.run_turn()/ask() send to the Anthropic API."""
    rows = session.scalars(
        select(db.Message).where(db.Message.project_id == project.id).order_by(db.Message.seq)
    )
    return [{"role": m.role, "content": m.content} for m in rows]


def append_turn(session: Session, project: db.Project, user_text: str, assistant_text: str) -> None:
    """Record one full turn (what was sent, what came back) so the next call's load_messages()
    includes it -- this is what replaces seedagent's own conversation file."""
    next_seq = (session.scalar(select(func.max(db.Message.seq)).where(db.Message.project_id == project.id)) or 0) + 1
    session.add(db.Message(project_id=project.id, seq=next_seq, role="user", content=user_text))
    session.add(db.Message(project_id=project.id, seq=next_seq + 1, role="assistant", content=assistant_text))
    session.commit()


def save_last_page(session: Session, project: db.Project, page: dict) -> None:
    project.last_page = page
    session.commit()


def _get_or_create_exercise(session: Session, project: db.Project, exercise_id: str) -> db.Exercise:
    exercise = session.scalar(
        select(db.Exercise).where(db.Exercise.project_id == project.id, db.Exercise.exercise_id == exercise_id)
    )
    if exercise is None:
        exercise = db.Exercise(project_id=project.id, exercise_id=exercise_id)
        session.add(exercise)
    return exercise


def sync_exercises_from_page(session: Session, project: db.Project, page: dict) -> None:
    """Upsert Exercise rows from whatever the current page happens to show -- a roadmap block gives
    title/summary/status/order for every exercise at once; a code_editor block fills in the one
    exercise's instructions/starter_code/language/file_name. Called after every turn, so the
    database stays in sync with whatever the agent most recently said, without needing the agent to
    report structured data itself."""
    for block in page.get("blocks", []):
        if block.get("type") == "roadmap":
            for i, item in enumerate(block.get("items", [])):
                ex_id = item.get("id")
                if not ex_id:
                    continue
                exercise = _get_or_create_exercise(session, project, ex_id)
                exercise.title = item.get("title", exercise.title)
                exercise.summary = item.get("summary", exercise.summary)
                exercise.status = item.get("status", exercise.status)
                exercise.order_index = i
        elif block.get("type") == "code_editor":
            ex_id = block.get("exercise_id")
            if not ex_id:
                continue
            exercise = _get_or_create_exercise(session, project, ex_id)
            exercise.instructions = block.get("instructions", exercise.instructions)
            exercise.starter_code = block.get("starter_code", exercise.starter_code)
            exercise.language = block.get("language", exercise.language)
            exercise.file_name = block.get("file_name", exercise.file_name)
            if exercise.status in ("locked", "available"):
                exercise.status = "in_progress"
    session.commit()


def record_submission(
    session: Session, project: db.Project, exercise_id: str, code: str, passed: bool, summary: str, details: str
) -> None:
    """Called when an /action payload carrying exercise_id+code (a submission) gets back a page
    whose first block is feedback -- the exercise_id comes from the request, not the response: the
    page spec's feedback block doesn't carry one, but it's unambiguous within a single HTTP call."""
    exercise = _get_or_create_exercise(session, project, exercise_id)
    session.add(
        db.Submission(exercise_id=exercise.id, code=code, passed=passed, feedback_summary=summary, feedback_details=details)
    )
    exercise.draft_code = code  # keep the draft in sync with the latest known state
    if passed:
        exercise.status = "done"
    session.commit()


def save_draft(session: Session, project: db.Project, exercise_id: str, code: str) -> None:
    exercise = _get_or_create_exercise(session, project, exercise_id)
    exercise.draft_code = code
    session.commit()


def apply_saved_draft(session: Session, project: db.Project, page: dict) -> dict:
    """If the page being shown has a code_editor block for an exercise with a saved draft, swap in
    the draft in place of the agent's freshly proposed starter_code -- so reopening an exercise (a
    refresh, a detour to another one and back) resumes exactly where the learner left off, not from
    scratch, even though the agent itself has no memory of what they'd typed but never submitted."""
    for block in page.get("blocks", []):
        if block.get("type") != "code_editor":
            continue
        ex_id = block.get("exercise_id")
        if not ex_id:
            continue
        exercise = session.scalar(
            select(db.Exercise).where(db.Exercise.project_id == project.id, db.Exercise.exercise_id == ex_id)
        )
        if exercise and exercise.draft_code:
            block["starter_code"] = exercise.draft_code
    return page
