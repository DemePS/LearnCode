"""The database: users, projects (one per learning objective), exercises and submissions, plus each
project's raw conversation with the model. SQLite by default -- one file, zero extra infra to run
this; swap LEARNCODE_DATABASE_URL for Postgres later without touching anything else, since every
query goes through SQLAlchemy.

Storing the conversation here (Message), rather than relying on seedagent's own on-disk conversation
file the way earlier versions of this backend did, is what makes agent.py's run_turn()/ask() plain
stateless Anthropic calls with no shared process state and no lock -- any backend instance can serve
any request for any project, so multiple instances can really run at once (see agent.py's module
docstring for the contrast with the old approach)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATA_DIR = Path(os.environ.get("LEARNCODE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.environ.get("LEARNCODE_DATABASE_URL", f"sqlite:///{DATA_DIR / 'learncode.db'}")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    projects: Mapped[list[Project]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Project(Base):
    """One learning session: a stated objective and the growing roadmap/exercises for it."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    objective: Mapped[str] = mapped_column(Text)
    last_page: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    user: Mapped[User] = relationship(back_populates="projects")
    exercises: Mapped[list[Exercise]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Exercise.order_index"
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Message.seq"
    )


class Message(Base):
    """The raw conversation with the model for a project: what agent.py's run_turn()/ask() send as
    `messages` on every call, replacing the context seedagent's own session used to carry."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="messages")


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    exercise_id: Mapped[str] = mapped_column(String(128))  # the agent's own id for it, e.g. "ex1"
    title: Mapped[str] = mapped_column(String(255), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    starter_code: Mapped[str] = mapped_column(Text, default="")
    # What the learner has typed but not submitted -- autosaved from the editor (debounced) so
    # reopening the exercise (a refresh, a detour to another exercise and back) resumes from here
    # instead of the agent's starter_code again. Distinct from Submission: this is never graded.
    draft_code: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(64), default="")
    file_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="locked")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    project: Mapped[Project] = relationship(back_populates="exercises")
    submissions: Mapped[list[Submission]] = relationship(
        back_populates="exercise", cascade="all, delete-orphan", order_by="Submission.created_at"
    )


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    exercise_id: Mapped[str] = mapped_column(ForeignKey("exercises.id"), index=True)
    code: Mapped[str] = mapped_column(Text)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feedback_summary: Mapped[str] = mapped_column(Text, default="")
    feedback_details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    exercise: Mapped[Exercise] = relationship(back_populates="submissions")


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
