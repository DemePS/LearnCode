"""Where a learning session lives: one project folder per session on disk (seedagent needs a real
directory, and keeps that project's conversation there for `resume=True`), and every page we have
rendered for it in the database.

The database is the authority on what a session is and who owns it. The folder is just a working
directory the agent reads and writes while it works -- which is exactly why the id is validated
before it is ever joined onto a path (see ensure_session_id)."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from . import db

# The only shape a session id may have: 32 lowercase hex characters (uuid4().hex). Anchored, so
# ".." -- or "../../etc", or an absolute path, or a Windows drive letter, or anything with a
# separator in it -- cannot match. This is a whitelist rather than a blacklist of "..": a session id
# arrives straight from the URL, and workspace_for() joins it onto the workspaces directory, so
# without this a request for /api/learn/../action would hand the agent a workspace of backend/data/ --
# where read_file, grep, list_directory and delete_clone would expose every other learner's project.
SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _data_dir() -> Path:
    """The data directory, read from the environment on every call.

    Not a module constant: the value is configurable (LEARNCODE_DATA_DIR), and resolving it once at
    import would silently ignore any later change -- which is exactly the kind of bug that makes a
    test pass against one directory while the app writes to another."""
    return Path(os.environ.get("LEARNCODE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def workspaces_dir() -> Path:
    return _data_dir() / "workspaces"


def new_session_id() -> str:
    return uuid.uuid4().hex


def ensure_session_id(session_id: str) -> str:
    """Return the id if it is well formed, else raise ValueError. Called before any filesystem or
    database access, so a malformed id can never reach either."""
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError(f"Invalid session id: {session_id!r}")
    return session_id


def workspace_for(session_id: str) -> Path:
    """The session's working directory, created if needed.

    Raises ValueError for a malformed id rather than merely returning a odd path: callers turn that
    into a 404, and there is no legitimate reason for one to reach here."""
    ensure_session_id(session_id)
    root = workspaces_dir()
    path = root / session_id
    # Belt and braces: even with the id validated, prove where this resolved to. Path objects with an
    # absolute right-hand side discard the left, and a symlinked data directory could redirect outside
    # it, so the check is on the resolved target rather than on the string.
    resolved = path.resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError(f"Session path escapes the workspace directory: {resolved}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_for_check(session_id: str) -> Path:
    """Like workspace_for, but never creates the folder -- used to answer "does this session exist"
    without side effects."""
    ensure_session_id(session_id)
    return workspaces_dir() / session_id


def create_session(session_id: str, user_id: int, objective: str) -> db.LearnSession:
    """Register a new session, owned by `user_id`. Ownership lives here rather than in the folder name
    so that "is this session mine?" is a row lookup instead of a guessable directory."""
    ensure_session_id(session_id)
    with db.session_scope() as s:
        row = db.LearnSession(id=session_id, user_id=user_id, objective=objective)
        s.add(row)
        s.commit()
        return row


def get_session(session_id: str) -> db.LearnSession | None:
    """The session row, or None for a malformed id or an unknown one -- callers answer 404 either way,
    so a bad id and a missing session are indistinguishable from outside."""
    try:
        ensure_session_id(session_id)
    except ValueError:
        return None
    with db.session_scope() as s:
        return s.get(db.LearnSession, session_id)


def owned_by(session_id: str, user_id: int) -> bool:
    row = get_session(session_id)
    return row is not None and row.user_id == user_id


def sessions_for(user_id: int) -> list[db.LearnSession]:
    """The learner's sessions, most recently touched first."""
    with db.session_scope() as s:
        return (
            s.query(db.LearnSession)
            .filter(db.LearnSession.user_id == user_id)
            .order_by(db.LearnSession.updated_at.desc())
            .all()
        )


def delete_session(session_id: str) -> bool:
    """Forget a session: its pages, its row, and the workspace folder with the clones in it.

    The caller must have established ownership first -- this erases whatever it is given, deliberately:
    deleting a project is the learner saying "I am done with this", so keeping the pages would leave
    rows no screen can reach, and keeping the folder would leave multi-megabyte clones nobody will
    open again. Returns False for an id that is malformed or already gone, which the caller answers
    with a 404 rather than pretending it deleted something."""
    try:
        ensure_session_id(session_id)
    except ValueError:
        return False
    with db.session_scope() as s:
        row = s.get(db.LearnSession, session_id)
        if row is None:
            return False
        # Pages first: they are the rows that point at the session (db.Page.session_id, an enforced
        # foreign key), so the reverse order would be refused by the database.
        s.query(db.Page).filter(db.Page.session_id == session_id).delete()
        s.delete(row)
        s.commit()
    shutil.rmtree(workspace_for_check(session_id), ignore_errors=True)
    return True


def save_pages(session_id: str, pages: list[dict], actions: list[str | None]) -> list[db.Page]:
    """Append several pages produced by one turn, in order, in a single transaction.

    The roadmap turn returns the roadmap plus every exercise it invented, so they must all be committed
    together: a learner clicking an exercise must never find the roadmap stored and its exercises
    missing. seq is allocated once and incremented here, and the unique constraint still guarantees it.
    """
    ensure_session_id(session_id)
    for attempt in (0, 1):
        with db.session_scope() as s:
            highest = (
                s.query(db.Page.seq)
                .filter(db.Page.session_id == session_id)
                .order_by(db.Page.seq.desc())
                .limit(1)
                .scalar()
            )
            rows = []
            for offset, page in enumerate(pages, 1):
                row = db.Page(
                    session_id=session_id,
                    seq=(highest or 0) + offset,
                    kind=db.classify_page(page),
                    action=actions[offset - 1] if offset - 1 < len(actions) else None,
                    payload=page,
                )
                s.add(row)
                rows.append(row)
            row_session = s.get(db.LearnSession, session_id)
            if row_session is not None:
                row_session.updated_at = datetime.now(timezone.utc)
            try:
                s.commit()
            except IntegrityError:
                if attempt:
                    raise
                continue  # someone took one of these seq values first; recompute and try once more
            return rows
    raise RuntimeError("unreachable")


def page_for_action(session_id: str, action: str) -> dict | None:
    """The stored page for one action id, newest first, or None if no turn ever produced one.

    What makes a roadmap item free to open: the exercise was generated with the roadmap, so this is a
    database read rather than a model call. None means "no stored page" and the caller runs a turn."""
    try:
        ensure_session_id(session_id)
    except ValueError:
        return None
    with db.session_scope() as s:
        row = (
            s.query(db.Page)
            .filter(db.Page.session_id == session_id, db.Page.action == action)
            .order_by(db.Page.seq.desc())
            .first()
        )
        return row.payload if row is not None else None


def set_exercise_draft(session_id: str, exercise_id: str, draft: str) -> dict | None:
    """Record the learner's editor contents in the stored exercise for `exercise_id`, and return it.

    Keyed on the exercise's own id, not on the action that was clicked: an exercise is stored under its
    open_action ("open_ex1") while the submit button sends a different id ("submit_ex1"), so looking up by
    the clicked action found no row and silently dropped the code -- the learner's work vanished on
    submit. The exercise_id is what both sides carry (the stored code_editor block, and the submit
    payload), so it is the one reliable link.

    The draft replaces only what the learner typed in the code editor; the exercise's own instructions,
    starter_code and language are untouched, so re-opening the exercise shows what they wrote. Returns
    None when no stored exercise has that id, which the caller answers with a turn (the exercise predates
    this, or the id is not one of the roadmap's)."""
    try:
        ensure_session_id(session_id)
    except ValueError:
        return None
    with db.session_scope() as s:
        rows = (
            s.query(db.Page)
            .filter(db.Page.session_id == session_id, db.Page.kind != "error")
            .order_by(db.Page.seq.desc())
            .all()
        )
        for row in rows:
            if _is_exercise(row.payload, exercise_id):
                payload = dict(row.payload)
                blocks = [dict(b) for b in payload.get("blocks") or []]
                for block in blocks:
                    if block.get("type") == "code_editor" and block.get("exercise_id") == exercise_id:
                        block["draft"] = draft
                payload["blocks"] = blocks
                row.payload = payload  # reassigned rather than mutated: SQLAlchemy tracks the attribute
                s.commit()
                return payload
        return None


def _is_exercise(payload, exercise_id: str) -> bool:
    """Whether a stored page is the exercise whose code editor carries this exercise_id."""
    blocks = payload.get("blocks") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") == "code_editor" and b.get("exercise_id") == exercise_id
        for b in blocks
    )


def exercise_page(session_id: str, exercise_id: str) -> dict | None:
    """The stored exercise whose code editor is `exercise_id`, or None."""
    try:
        ensure_session_id(session_id)
    except ValueError:
        return None
    with db.session_scope() as s:
        rows = (
            s.query(db.Page)
            .filter(db.Page.session_id == session_id, db.Page.kind != "error")
            .order_by(db.Page.seq.desc())
            .all()
        )
        for row in rows:
            if _is_exercise(row.payload, exercise_id):
                return row.payload
        return None


def exercises(session_id: str) -> list[dict]:
    """Every stored exercise for this session, as the small row shape a list needs.

    The exercises were all written in one turn (the roadmap turn, while the clone was in context), so
    they are the rows carrying an `open_action`; a page produced later by an action never is one. Read
    newest-first and keep the first sighting of each exercise_id, so a re-generated exercise wins over
    the one it replaced instead of both being listed."""
    try:
        ensure_session_id(session_id)
    except ValueError:
        return []
    with db.session_scope() as s:
        rows = (
            s.query(db.Page)
            .filter(db.Page.session_id == session_id, db.Page.kind == "exercise")
            .order_by(db.Page.seq.desc())
            .all()
        )
    seen: dict[str, dict] = {}
    for row in rows:
        payload = row.payload
        for block in payload.get("blocks") or []:
            if not isinstance(block, dict) or block.get("type") != "code_editor":
                continue
            exercise_id = block.get("exercise_id")
            if not isinstance(exercise_id, str) or not exercise_id or exercise_id in seen:
                continue
            seen[exercise_id] = {
                "exercise_id": exercise_id,
                "title": payload.get("title") or "",
                "file_name": block.get("file_name") or "",
                "language": block.get("language") or "",
                "open_action": payload.get("open_action") or row.action or "",
            }
    # The roadmap's order, not the newest-first read order: this list is shown as the project's
    # exercises, and reversing a reverse would put a re-generated exercise first.
    return list(reversed(list(seen.values())))


def save_page(session_id: str, page: dict, action: str | None = None) -> db.Page:
    """Append the page the agent just produced, as the next seq for this session.

    seq is allocated inside the write rather than from a counter kept in memory, so two turns can
    never hand out the same number. The unique constraint on (session_id, seq) is the real guarantee;
    the retry covers the narrow window where two writers read the same maximum."""
    ensure_session_id(session_id)
    for attempt in (0, 1):
        with db.session_scope() as s:
            highest = (
                s.query(db.Page.seq)
                .filter(db.Page.session_id == session_id)
                .order_by(db.Page.seq.desc())
                .limit(1)
                .scalar()
            )
            row = db.Page(
                session_id=session_id,
                seq=(highest or 0) + 1,
                kind=db.classify_page(page),
                action=action,
                payload=page,
            )
            s.add(row)
            row_session = s.get(db.LearnSession, session_id)
            if row_session is not None:
                row_session.updated_at = datetime.now(timezone.utc)
            try:
                s.commit()
            except IntegrityError:
                if attempt:
                    raise
                continue  # someone took this seq first; recompute and try once more
            return row
    raise RuntimeError("unreachable")


def roadmap_page(session_id: str) -> dict | None:
    """The session's roadmap page, or None if no turn has produced one.

    "Back to roadmap" is navigation, not a request: the page is already stored, so it is read back
    instead of being regenerated. Without this the reserved action fell through to a full turn -- half a
    minute to redraw a screen that was in the database, and the regenerated copy was saved as a new page,
    pushing the learner's real history aside."""
    try:
        ensure_session_id(session_id)
    except ValueError:
        return None
    with db.session_scope() as s:
        rows = (
            s.query(db.Page)
            .filter(db.Page.session_id == session_id, db.Page.kind != "error")
            .order_by(db.Page.seq.desc())
            .all()
        )
    for row in rows:
        blocks = row.payload.get("blocks") if isinstance(row.payload, dict) else None
        if isinstance(blocks, list) and any(
            isinstance(b, dict) and b.get("type") == "roadmap" for b in blocks
        ):
            return row.payload
    return None


def latest_page(session_id: str) -> dict | None:
    """The newest page worth showing, or None if this session has none.

    Error pages are stored (they are the only trace of a failed turn) but skipped here, so a browser
    refresh after a model failure redraws the learner's last good screen instead of replacing it with
    "Something went wrong"."""
    try:
        ensure_session_id(session_id)
    except ValueError:
        return None
    with db.session_scope() as s:
        row = (
            s.query(db.Page)
            .filter(db.Page.session_id == session_id, db.Page.kind != "error")
            .order_by(db.Page.seq.desc())
            .first()
        )
        return row.payload if row is not None else None
