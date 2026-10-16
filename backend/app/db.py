"""The database: accounts, learning sessions, and every page the agent has produced.

Deliberately synchronous. The agent's turns are blocking anyway (seedagent's loop is sync and the
routes already push it onto a worker thread), so an async driver would only add a second concurrency
model to reason about without making anything faster. Routes call these helpers through
run_in_threadpool for the same reason.

SQLite is the default because a single server process needs no more than that, but nothing here is
SQLite-specific -- LEARNCODE_DATABASE_URL swaps in Postgres (see configure())."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATA_DIR = Path(os.environ.get("LEARNCODE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

_DEFAULT_PATH = DATA_DIR / "learncode.db"


def _default_url() -> str:
    _DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_DEFAULT_PATH}"


class Base(DeclarativeBase):
    pass


class User(Base):
    """One GitHub account. `token_encrypted` holds the Fernet ciphertext of their OAuth token: the
    plaintext exists only for the duration of a turn, because several turns run at once and a shared
    token would leak one learner's rate limit (and identity) into another's requests."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    login: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    token_encrypted: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuthSession(Base):
    """A signed-in browser. The cookie carries a random value; only its SHA-256 is stored, so a dump
    of this table cannot be replayed as a login (the same reason passwords are hashed, not encrypted:
    nothing ever needs to read the original back)."""

    __tablename__ = "auth_sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class LearnSession(Base):
    """A learner's project. The id doubles as the seedagent workspace folder name on disk; the row is
    the authority on who owns it, so a request can no longer reach someone else's workspace just by
    guessing a directory name."""

    __tablename__ = "learn_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    objective: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Page(Base):
    """Every page the agent has produced, in order. Keeping the whole history rather than only the
    last one is what lets a learner go back to an exercise they already reached without paying for
    another model call. `kind` is derived from the blocks (see classify_page) so error pages can be
    skipped when answering "what is on screen" but kept for diagnostics."""

    __tablename__ = "pages"
    # One exercise per open_action: the roadmap turn generates them all ahead of time, and a click on a
    # roadmap item is answered by reading the row back instead of running a turn, so that lookup is by
    # (session_id, action) and needs the index rather than a scan of the session's whole history.
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_pages_session_seq"),
        Index("ix_pages_session_action", "session_id", "action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("learn_sessions.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


def classify_page(page: dict) -> str:
    """Which shape of page this is, from the block types alone.

    The backend deliberately never interprets what a roadmap or an exercise *means* (the agent invents
    both at runtime -- see PAGE_SYSTEM_PROMPT). The one exception is this label, which exists only so
    "show me the newest non-broken page" can tell a failure apart from a real screen. Order matters:
    a feedback page carries an actions block and an exercise page may carry markdown, so the specific
    block types are tested before the fallback."""
    if page.get("_error"):
        return "error"
    types = {b.get("type") for b in page.get("blocks") or [] if isinstance(b, dict)}
    if "roadmap" in types:
        return "roadmap"
    if "code_editor" in types:
        return "exercise"
    if "feedback" in types:
        return "feedback"
    return "page"


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_initialised = False


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # check_same_thread=False because each turn runs on a worker thread, and the app may answer
        # several at once; SQLAlchemy's pool is what actually keeps that safe.
        engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver callback
            # WAL lets a reader (a page refetch) run while a turn is writing a page, instead of
            # blocking on SQLite's default whole-file lock. foreign_keys is off by default in SQLite,
            # so the FKs declared above would otherwise not be enforced. busy_timeout absorbs the
            # brief overlap of two concurrent writes rather than raising "database is locked".
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return engine
    return create_engine(url, future=True)


def configure(url: str | None = None) -> None:
    """Point the module at `url` (or the environment's default) and dispose the previous engine.

    Tests call this to get an isolated database, and init() calls it lazily so importing this module
    never touches the filesystem -- which matters because tests build a TestClient without a
    lifespan, so no startup hook would ever run."""
    global _engine, _SessionLocal, _initialised
    if _engine is not None:
        _engine.dispose()
    _engine = _make_engine(url or os.environ.get("LEARNCODE_DATABASE_URL") or _default_url())
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    _initialised = False


def _ready() -> sessionmaker[Session]:
    """The session factory, creating the schema first if need be. Idempotent: create_all() checks
    what already exists, so calling this on every request costs one cheap query, and calling it twice
    in a process is harmless."""
    global _initialised
    if _SessionLocal is None:
        configure()
    assert _SessionLocal is not None
    if not _initialised:
        Base.metadata.create_all(_engine)
        _initialised = True
    return _SessionLocal


def init_db() -> None:
    """Create any missing tables. Called lazily by _ready(); exposed for tests and startup."""
    _ready()


def session_scope() -> Session:
    """A new Session. Callers own commit/close (see the `with db.session_scope() as s:` idiom)."""
    return _ready()()
