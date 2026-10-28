"""Shared fixtures.

Every test gets its own database file and its own data directory, because the app reads both from the
environment at call time and a leaked database between tests would hide exactly the bugs this suite
exists to catch (an id reaching another learner's rows, a page appended to the wrong session)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """A private LEARNCODE_DATA_DIR, so workspaces and the GitHub cache never touch the real data."""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("LEARNCODE_DATA_DIR", str(root))
    return root


@pytest.fixture()
def database(tmp_path, data_dir, monkeypatch):
    """A private SQLite file, wired into app.db for the duration of the test.

    configure() disposes the previous engine, so tests cannot leak state into one another through a
    module-level engine."""
    from app import db

    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("LEARNCODE_DATABASE_URL", url)
    db.configure(url)
    db.init_db()
    yield db
    db.configure("sqlite:///" + str(tmp_path / "unused.db"))


@pytest.fixture()
def store(database, data_dir):
    from app import store as store_module

    return store_module


@pytest.fixture()
def gh(data_dir, monkeypatch):
    """github_lookup with no ambient GitHub token and fresh per-turn budgets."""
    from app import github_lookup

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    github_lookup.reset_turn_budget()
    github_lookup.reset_readme_budget()
    yield github_lookup


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Start every test with an empty rate-limit table.

    app/ratelimit.py counts per user in process memory, so without this the suite's own turns would
    spend the budget and later tests would get 429s -- a real behaviour, but one that belongs in the
    dedicated tests, not in an unrelated one."""
    from app import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture()
def user_factory(database):
    """Create users on demand -- most tests need two, to prove one cannot reach the other's data."""
    from app import db

    def make(login: str, github_id: int):
        with db.session_scope() as s:
            row = db.User(github_id=github_id, login=login)
            s.add(row)
            s.commit()
            return row.id

    return make
