"""The smoke test, end to end through the HTTP layer: Swagger/OpenAPI documents the API, and every
endpoint answers the shape the frontend renders -- with the agent mocked, so no model call is made.

Ported from backend/test_backend_swagger.py (which is gone), where it ran as a bare script: it now has
to override current_user, because every /api/learn route is behind sign-in and the script would get a
401 on its first request. It overlaps tests/test_routes.py on purpose -- this is the one place that
checks the documented schema and Swagger UI as well as the request/response shapes, and it reads like
the manual smoke test it replaces."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def client(database, data_dir, monkeypatch):
    """The app with the mock agent, signed in as a real user row.

    The same two tricks as tests/test_routes.py: app.agent registers its tools in seedagent's
    process-global registry (registering the same name twice raises), so app.main is imported once per
    process; and the signed-in user is a real row, because learn_sessions.user_id is a foreign key."""
    monkeypatch.setenv("LEARNCODE_AUTH", "on")
    if "app.main" not in sys.modules:
        importlib.import_module("_mock_agent_server")
    main = importlib.import_module("app.main")

    from app import auth, db

    with db.session_scope() as s:
        row = s.scalar(select(db.User).where(db.User.github_id == 1))
        if row is None:
            row = db.User(github_id=1, login="alice")
            s.add(row)
            s.commit()
        user_id = row.id

    def override():
        return auth.User(SimpleNamespace(id=user_id, login="alice", name=None,
                                         avatar_url=None, token_encrypted=None))

    app = main.app
    app.dependency_overrides[auth.current_user] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_openapi_documents_the_api_and_serves_swagger(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in ["/api/learn/clarify", "/api/learn/start", "/api/learn/{session_id}",
                 "/api/learn/{session_id}/action", "/api/learn/{session_id}/ask",
                 "/api/learn/progress/{turn_id}", "/api/learn"]:
        assert path in paths, path
        assert "post" in paths[path] or "get" in paths[path]

    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()


def test_transcript_from_objective_to_feedback(client, monkeypatch):
    """One pass through the whole product, as a learner would: start, refetch, open an exercise,
    submit it, ask a side question."""
    from app import agent

    # The mock session never calls GitHub, so run_turn would correctly prefix every page with the
    # "Not verified" banner. That behaviour has its own test; here it would only obscure the transcript.
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    started = client.post("/api/learn/start", json={"objective": "learn FastAPI", "history": []})
    assert started.status_code == 200
    body = started.json()
    session_id = body["session_id"]
    page = body["page"]
    assert session_id and "title" in page and isinstance(page["blocks"], list)
    assert [b["type"] for b in page["blocks"]] == ["markdown", "stat_row", "roadmap", "actions"]

    refetched = client.get(f"/api/learn/{session_id}")
    assert refetched.status_code == 200
    assert refetched.json()["page"]["title"] == page["title"]
    assert client.get("/api/learn/deadbeef").status_code == 404

    opened = client.post(f"/api/learn/{session_id}/action", json={"action": "open_ex1", "payload": {}})
    assert opened.status_code == 200
    editor = next(b for b in opened.json()["page"]["blocks"] if b["type"] == "code_editor")
    assert editor["starter_code"] and editor["submit_action"]

    graded = client.post(f"/api/learn/{session_id}/action",
                         json={"action": "submit_ex1",
                               "payload": {"exercise_id": "ex1", "file_name": "models.py",
                                           "code": "class Link: pass\n"}})
    assert graded.status_code == 200
    assert "feedback" in [b["type"] for b in graded.json()["page"]["blocks"]]

    answered = client.post(f"/api/learn/{session_id}/ask", json={"question": "why?", "context": {}})
    assert answered.status_code == 200
    assert answered.json()["answer"]

    # /ask is a side channel to the screen already on screen, so it must not move the page on.
    assert client.get(f"/api/learn/{session_id}").json()["page"]["title"] == \
        graded.json()["page"]["title"]


def test_validation_answers_400_or_422(client, monkeypatch):
    from app import agent

    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    assert client.post("/api/learn/start", json={"objective": "   ", "history": []}).status_code == 400
    assert client.post("/api/learn/start", json={}).status_code == 422  # pydantic, not ours
    session_id = client.post("/api/learn/start",
                             json={"objective": "x", "history": []}).json()["session_id"]
    assert client.post(f"/api/learn/{session_id}/action", json={"action": " "}).status_code == 400
    # An empty question is the app's own 400, not a 404 from the literal path {session_id}.
    assert client.post(f"/api/learn/{session_id}/ask", json={"question": ""}).status_code == 400
    assert client.post("/api/learn/deadbeef/action", json={"action": "x"}).status_code == 404
