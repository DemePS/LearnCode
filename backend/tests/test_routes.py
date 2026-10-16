"""routes.py: ownership, the one-turn-per-session lock, and the page endpoints.

The app is built with the mock agent (see _mock_agent_server), so these exercise the HTTP layer,
persistence and ownership without a model call or an API key. The concurrency of the real agent loop
is covered separately in test_concurrency.py."""

from __future__ import annotations

import importlib
import inspect
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import agent, routes, store


@pytest.fixture()
def client(database, data_dir, monkeypatch):
    """A TestClient driving the app with the mock agent.

    LEARNCODE_AUTH=off gives every request a fixed local user, which is what lets the learning routes
    be tested without a GitHub round trip. Tests that need real authentication (tests/test_auth.py)
    turn it back on and override the dependency instead.

    The app modules are imported once per process (see the guard below) rather than per test: importing
    app.agent registers its tools in seedagent's process-global registry, and re-registering the same
    name raises -- so a per-test import would fail on the second test. The database is still per test,
    because app.db reads its URL when configure() is called, not when it is imported.

    No lifespan runs here -- exactly the case db._ready() exists for, since a production startup hook
    would never fire and the first request would otherwise hit a missing table."""
    monkeypatch.setenv("LEARNCODE_AUTH", "off")
    if "app.main" not in sys.modules:
        importlib.import_module("_mock_agent_server")  # patches seedagent.session for the mock agent
    main = importlib.import_module("app.main")
    return TestClient(main.app)


@pytest.fixture()
def as_user(client, database):
    """Sign in as a real user row for subsequent requests, by overriding the dependency.

    The rows are real (not stand-ins) because learn_sessions.user_id is a foreign key: a fake id would
    make every insert fail on the constraint. Creating them here rather than in each test also keeps the
    id stable, which is how a test switches between two learners mid-flight."""
    from app import auth, db

    def sign_in(login: str = "alice", github_id: int = 1) -> int:
        with db.session_scope() as s:
            row = s.scalar(select(db.User).where(db.User.github_id == github_id))
            if row is None:
                row = db.User(github_id=github_id, login=login)
                s.add(row)
                s.commit()
            user_id = row.id

        def override():
            # The real wrapper, around the row: the wrapper is what hides the token from the routes,
            # so overriding with anything else would test a different contract.
            return auth.User(SimpleNamespace(id=user_id, login=login, name=None,
                                             avatar_url=None, token_encrypted=None))

        client.app.dependency_overrides[auth.current_user] = override
        # /progress/{turn_id} takes optional_user, not current_user (it must not 401 a casual poll), so a
        # sign-in that overrides only the latter leaves that route reading the LEARNCODE_AUTH=off local
        # user -- a different id, which makes the feed look like somebody else's turn and come back empty.
        if hasattr(auth, "optional_user"):
            client.app.dependency_overrides[auth.optional_user] = override
        return user_id

    return sign_in


def test_start_then_get_and_action(client, as_user, monkeypatch):
    as_user()
    # The banner is about a turn that never consulted GitHub, which the fake session by definition never
    # does. It is asserted on its own further down; suppressed here so this test is about page routing.
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    response = client.post("/api/learn/start",
                           json={"objective": "learn FastAPI", "history": [], "turn_id": ""})
    assert response.status_code == 200
    body = response.json()
    sid = body["session_id"]
    assert body["page"]["title"] == "Build a tiny URL shortener"
    # The turn's reply carried both the roadmap and its exercises, but only the roadmap is shown: the
    # exercises are stored, not rendered, which is what keeps the page a page.
    assert "exercises" not in body["page"] and "roadmap" not in body["page"]
    # The internal marker must never reach the client.
    assert "_error" not in body["page"]

    fetched = client.get(f"/api/learn/{sid}")
    assert fetched.status_code == 200
    assert fetched.json()["page"]["title"] == body["page"]["title"]

    # Opening exercise 1 is served from the stored exercise the roadmap turn generated: no model call, and
    # the learner's code editor arrives with the starter_code the clone was still in context to write.
    acted = client.post(f"/api/learn/{sid}/action",
                        json={"action": "open_ex1", "payload": {}, "turn_id": ""})
    assert acted.status_code == 200
    page = acted.json()["page"]
    assert page["title"] == "Exercise 1: Data model"
    editor = next(b for b in page["blocks"] if b["type"] == "code_editor")
    assert editor["file_name"] == "models.py"
    assert "class Link" in editor["starter_code"]
    assert "draft" not in page  # nothing typed yet

    # Serving from storage deliberately writes no new page: the exercise row already exists, and saving a
    # copy on every open would grow the history for no gain. A plain reload therefore still shows the
    # newest saved page (the roadmap); the exercise comes back by clicking it again, served from its row.
    assert fetched.json()["page"]["title"] == body["page"]["title"]
    reopened = client.post(f"/api/learn/{sid}/action",
                           json={"action": "open_ex1", "payload": {}})
    assert reopened.json()["page"]["title"] == "Exercise 1: Data model"


def test_the_roadmap_and_exercises_have_their_own_read_only_paths(client, as_user, monkeypatch):
    """GET /roadmap, /exercises and /exercises/{id} answer from stored pages, and can never run a turn.

    These exist so that asking for a screen the agent already produced is a plain read: opening an exercise
    stops going through /action, so there is no action id to interpret and no way for a click to reach the
    model. That is what makes reopening an exercise free and deterministic."""
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    # Installed only now: /start is supposed to run a turn, the three routes below are not.
    monkeypatch.setattr(agent, "run_turn", lambda *a, **k: pytest.fail("a read route ran a turn"))

    roadmap = client.get(f"/api/learn/{sid}/roadmap")
    assert roadmap.status_code == 200
    assert any(b["type"] == "roadmap" for b in roadmap.json()["page"]["blocks"])

    listed = client.get(f"/api/learn/{sid}/exercises")
    assert listed.status_code == 200
    exercises = listed.json()["exercises"]
    assert [e["exercise_id"] for e in exercises] == ["ex1", "ex2"]
    assert exercises[0]["file_name"] == "models.py"
    assert exercises[0]["language"] == "python"
    # The list is what the roadmap shows, in roadmap order, and carries no starter code.
    assert "starter_code" not in exercises[0]

    one = client.get(f"/api/learn/{sid}/exercises/ex1")
    assert one.status_code == 200
    assert one.json()["page"]["title"] == "Exercise 1: Data model"
    editor = next(b for b in one.json()["page"]["blocks"] if b["type"] == "code_editor")
    assert "class Link" in editor["starter_code"]


def test_submit_then_read_the_exercise_path_returns_the_draft(client, as_user, monkeypatch):
    """The draft is readable through the exercise's own path, which is how reopening shows the learner's
    code rather than the skeleton."""
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    client.post(f"/api/learn/{sid}/action", json={
        "action": "submit_ex1",
        "payload": {"exercise_id": "ex1", "file_name": "models.py", "code": "class Link:\n    pass\n"}})

    reopened = client.get(f"/api/learn/{sid}/exercises/ex1")
    assert reopened.status_code == 200
    editor = next(b for b in reopened.json()["page"]["blocks"] if b["type"] == "code_editor")
    assert editor["draft"] == "class Link:\n    pass\n"


def test_the_read_paths_are_private_and_404_on_nothing(client, as_user, monkeypatch):
    """A project's roadmap and exercises belong to its owner, and an unknown exercise is a 404 -- not an
    empty page, which the UI would render as a blank screen with no explanation."""
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]

    assert client.get(f"/api/learn/{sid}/exercises/nope").status_code == 404

    as_user(login="bob", github_id=2)
    assert client.get(f"/api/learn/{sid}/roadmap").status_code == 404
    assert client.get(f"/api/learn/{sid}/exercises").status_code == 404
    assert client.get(f"/api/learn/{sid}/exercises/ex1").status_code == 404


def test_submitting_code_is_recorded_in_the_stored_exercise(client, as_user, monkeypatch):
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    client.post(f"/api/learn/{sid}/action", json={"action": "open_ex1", "payload": {}})

    # The submit button carries the editor with its own action id, the one the exercise's code_editor block
    # was generated with, so it is told apart from a plain open of the same exercise.
    submitted = client.post(f"/api/learn/{sid}/action", json={
        "action": "submit_ex1",
        "payload": {"exercise_id": "ex1", "file_name": "models.py", "code": "class Link:\n    pass\n"},
    })
    assert submitted.status_code == 200
    # ...and it is graded rather than echoed back: the feedback page is what the learner asked for.
    assert submitted.json()["page"]["blocks"][0]["type"] == "feedback"

    # The draft lands in the exercise itself, so re-opening shows what they wrote...
    reopened = client.post(f"/api/learn/{sid}/action",
                           json={"action": "open_ex1", "payload": {}}).json()["page"]
    assert reopened["blocks"][0]["draft"] == "class Link:\n    pass\n"
    # ...and only the editor's contents: the exercise's own fields are untouched.
    editor = next(b for b in reopened["blocks"] if b["type"] == "code_editor")
    assert "class Link" in editor["starter_code"]


def test_a_retry_button_reopens_the_exercise_even_with_an_invented_id(client, as_user, monkeypatch):
    """A failed submission ends on a feedback page whose retry button reopens the exercise.

    The model invents that button's id (the prompt asks it to reuse the roadmap's open_action, but nothing
    forces it), so the click can carry an action the store has never seen. It still carries the exercise's
    own exercise_id in its payload, and that is what decides: an id that is not the stored editor's
    submit_action is an open, not a grade. Otherwise a retry would cost a whole turn -- and the learner
    would be sent back to the model instead of to the code they had just written."""
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    client.post(f"/api/learn/{sid}/action", json={"action": "open_ex1", "payload": {}})
    code = "class Link:\n    pass\n"
    client.post(f"/api/learn/{sid}/action", json={
        "action": "submit_ex1", "payload": {"exercise_id": "ex1", "file_name": "x.py", "code": code}})

    def fail_if_called(*args, **kwargs):
        raise AssertionError("a retry button ran a turn instead of reopening the exercise")

    monkeypatch.setattr(agent, "run_turn", fail_if_called)
    retried = client.post(f"/api/learn/{sid}/action", json={
        "action": "open_ex1_again", "payload": {"exercise_id": "ex1", "code": code}})
    assert retried.status_code == 200
    assert retried.json()["page"]["blocks"][0]["draft"] == code


def test_the_feeds_first_line_names_the_turn(client, as_user, monkeypatch):
    """The feed's opening line must describe the turn it belongs to.

    It was hardcoded to "Thinking about your objective", which only fits the turn that writes a roadmap
    from a fresh objective -- so a submission, where the learner has just handed in code and is waiting
    for a verdict, was accompanied by a line about an objective. It reads as the agent having lost the
    thread of what it is doing, right at the moment the learner most wants to know it hasn't."""
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    openings = []
    # Captured before patching: inside the spy agent.run_turn *is* the spy, so its own signature would
    # bind nothing. Both call sites pass the flag positionally through run_in_threadpool.
    params = inspect.signature(agent.run_turn).parameters
    signature = inspect.Signature(list(params.values()))

    def spy(*args, **kwargs):
        openings.append(signature.bind(*args, **kwargs).arguments["opening"])
        return agent.TurnResult({"title": "T", "blocks": [{"type": "markdown", "content": "x"}]})

    monkeypatch.setattr(agent, "run_turn", spy)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    client.post(f"/api/learn/{sid}/action", json={"action": "open_ex1", "payload": {}})
    client.post(f"/api/learn/{sid}/action", json={
        "action": "submit_ex1", "payload": {"exercise_id": "ex1", "file_name": "x.py", "code": "x"}})
    # An action that is neither an open nor a submit (the model invented it) still gets a line that is true
    # of the turn, rather than one borrowed from the roadmap turn.
    client.post(f"/api/learn/{sid}/action", json={"action": "explain_project", "payload": {}})

    assert openings[0] == "Thinking about your objective"
    assert all("objective" not in line.lower() for line in openings[1:]), openings
    assert len(set(openings[1:])) >= 1 and openings[1] != openings[0]


def test_a_submission_is_still_graded_when_it_carries_no_code(client, as_user, monkeypatch):
    """A submit with nothing typed yet must still grade, not bounce back as a "reopen".

    The store is asked which action submits, not whether a draft is present: an empty editor is a real
    submission, and answering it from storage would show the learner their own blank code back."""
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    client.post(f"/api/learn/{sid}/action", json={"action": "open_ex1", "payload": {}})

    submitted = client.post(f"/api/learn/{sid}/action",
                            json={"action": "submit_ex1", "payload": {"exercise_id": "ex1", "code": ""}})
    assert submitted.json()["page"]["blocks"][0]["type"] == "feedback"
    # An empty draft is recorded as an empty draft, so reopening shows the blank editor the learner left.
    assert client.post(f"/api/learn/{sid}/action",
                       json={"action": "open_ex1", "payload": {}}).json()["page"]["blocks"][0]["draft"] == ""


def test_reopening_a_submitted_exercise_does_not_grade_it_again(client, as_user, monkeypatch):
    """Opening an exercise that was already submitted must not run a turn.

    Reopening sends the editor's contents back with it (that is how the draft survives), so "the payload
    carries code" cannot mean "this is a submission": treating it as one ran a whole graded turn every
    time a learner looked at work they had already handed in."""
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    client.post(f"/api/learn/{sid}/action", json={"action": "open_ex1", "payload": {}})
    code = "class Link:\n    pass\n"
    client.post(f"/api/learn/{sid}/action", json={
        "action": "submit_ex1",
        "payload": {"exercise_id": "ex1", "file_name": "models.py", "code": code}})

    def fail_if_called(*args, **kwargs):
        raise AssertionError("reopening a submitted exercise ran a turn")

    monkeypatch.setattr(agent, "run_turn", fail_if_called)
    reopened = client.post(f"/api/learn/{sid}/action", json={
        "action": "open_ex1",
        "payload": {"exercise_id": "ex1", "file_name": "models.py", "code": code}})
    assert reopened.status_code == 200
    assert reopened.json()["page"]["blocks"][0]["draft"] == code


def test_back_to_roadmap_is_navigation_not_a_turn(client, as_user, monkeypatch):
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    # Open an exercise, so the current page is no longer the roadmap.
    opened = client.post(f"/api/learn/{sid}/action", json={"action": "open_ex1", "payload": {}})
    assert opened.json()["page"]["title"] == "Exercise 1: Data model"

    # The UI's own persistent button sends this reserved id. It must be answered from the stored roadmap:
    # a turn here would cost ~30s to redraw a page already in the database.
    def fail_if_called(*args, **kwargs):
        raise AssertionError("Back to roadmap ran a model call instead of navigating")

    monkeypatch.setattr(agent, "run_turn", fail_if_called)
    back = client.post(f"/api/learn/{sid}/action", json={"action": "show_roadmap", "payload": {}})
    assert back.status_code == 200
    assert back.json()["page"]["title"] == "Build a tiny URL shortener"
    assert any(b["type"] == "roadmap" for b in back.json()["page"]["blocks"])

    # A session whose roadmap was never produced still answers, rather than 500ing.
    other = store.new_session_id()
    store.create_session(other, user_id=as_user(), objective="x")
    assert client.post(f"/api/learn/{other}/action",
                       json={"action": "show_roadmap", "payload": {}}).status_code == 404


def test_a_failed_turn_still_answers_with_a_page(client, as_user, monkeypatch):
    """A model failure is the learner's to see, not a 500.

    run_turn's early exits (no answer, empty reply, unparseable JSON) once returned a bare page dict while
    the success path returned a TurnResult, so /start raised AttributeError on `result.page` for exactly
    the turns that were trying to report an error."""
    as_user()
    from app import agent as agent_module

    def failing_turn(*args, **kwargs):
        return agent_module.TurnResult(agent_module._error_page("the model returned nothing"))

    monkeypatch.setattr(agent_module, "run_turn", failing_turn)
    response = client.post("/api/learn/start", json={"objective": "learn FastAPI", "history": []})
    assert response.status_code == 200  # not 500
    page = response.json()["page"]
    assert page["title"] == "Something went wrong"
    assert "the model returned nothing" in page["blocks"][0]["content"]
    assert "_error" not in page  # the internal marker never reaches the client

    # The error page IS stored (it is the only trace of a failed turn), but GET skips error pages by
    # design: a refresh after a model failure redraws the last good screen instead of the error. So a
    # session whose only page is the error has nothing readable back, and 404 is the honest answer.
    sid = response.json()["session_id"]
    assert client.get(f"/api/learn/{sid}").status_code == 404


def test_an_unknown_action_still_runs_a_turn(client, as_user, monkeypatch):
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    # open_ex2 was never generated (the fake only returns one exercise), so this must not be answered from
    # storage -- it has to reach the fake session and come back with whatever it produces.
    acted = client.post(f"/api/learn/{sid}/action", json={"action": "open_ex2", "payload": {}})
    assert acted.status_code == 200
    assert "Feedback on exercise 1" in acted.json()["page"]["title"]


def test_only_the_designing_turn_asks_for_grounding(client, as_user, monkeypatch):
    """The "not verified" banner belongs to the turn that designs the project, and to no other.

    Grounding was checked on every turn, so the banner appeared on grading and retry pages too -- turns
    answered from the stored exercise and the learner's own code that never touch GitHub. The learner saw
    it on top of every page, which is how a warning meaning "this project is not based on real code"
    became permanent noise. The flag records who asked; assert on that rather than on the wording."""
    as_user()
    asked = []
    # Captured before patching: inside the spy, agent.run_turn is the spy, so its signature would bind
    # nothing and every call would look like a default.
    params = inspect.signature(agent.run_turn).parameters

    def spy(*args, **kwargs):
        # /start hands the flag positionally through run_in_threadpool, so read it by name.
        bound = inspect.Signature(list(params.values())).bind(*args, **kwargs)
        asked.append(bound.arguments.get("require_grounding", False))
        return agent.TurnResult({"title": "T", "blocks": [{"type": "markdown", "content": "x"}]})

    monkeypatch.setattr(agent, "run_turn", spy)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    client.post(f"/api/learn/{sid}/action", json={"action": "submit_ex1",
                                                  "payload": {"exercise_id": "ex1", "code": "x"}})
    client.post(f"/api/learn/{sid}/ask", json={"question": "why?"})

    assert asked[0] is True, "/start designs the project from real code, so it must ask for grounding"
    assert all(a is not True for a in asked[1:]), asked


def test_session_list_is_newest_first(client, as_user):
    as_user()
    first = client.post("/api/learn/start", json={"objective": "older", "history": []}).json()["session_id"]
    second = client.post("/api/learn/start", json={"objective": "newer", "history": []}).json()["session_id"]
    client.post(f"/api/learn/{first}/action", json={"action": "open_ex1", "payload": {}})

    listing = client.get("/api/learn").json()["sessions"]
    assert [s["id"] for s in listing] == [first, second]
    assert listing[0]["objective"] == "older"


def test_another_user_cannot_read_act_or_ask(client, as_user):
    as_user()
    sid = client.post("/api/learn/start", json={"objective": "mine", "history": []}).json()["session_id"]

    as_user(login="bob", github_id=2)
    # 404 rather than 403 for all three: answering 403 would confirm the session exists.
    assert client.get(f"/api/learn/{sid}").status_code == 404
    assert client.post(f"/api/learn/{sid}/action", json={"action": "open_ex1", "payload": {}}).status_code == 404
    assert client.post(f"/api/learn/{sid}/ask", json={"question": "hi", "context": {}}).status_code == 404
    # And the other user's list stays empty, so the id cannot be discovered by browsing.
    assert client.get("/api/learn").json()["sessions"] == []


def test_unknown_and_malformed_ids_are_404(client, as_user):
    as_user()
    for bad in ["..", "not-a-uuid", "A" * 32, "a" * 31, "a" * 33, "0" * 31 + "z"]:
        assert client.get(f"/api/learn/{bad}").status_code == 404, bad
        assert client.post(f"/api/learn/{bad}/action",
                           json={"action": "x", "payload": {}}).status_code == 404, bad
        assert client.delete(f"/api/learn/{bad}").status_code == 404, bad


def test_deleting_a_project_removes_its_pages_row_and_workspace(client, as_user, monkeypatch):
    as_user()
    monkeypatch.setattr(agent, "_grounding_note", lambda ui: None)
    sid = client.post("/api/learn/start",
                      json={"objective": "learn FastAPI", "history": []}).json()["session_id"]
    workspace = store.workspace_for(sid)
    (workspace / "clone.txt").write_text("pretend this is a clone")
    assert client.get(f"/api/learn/{sid}").status_code == 200

    assert client.delete(f"/api/learn/{sid}").json() == {"ok": True}
    # The project is gone from every angle: no pages to read, nothing to act on, and no workspace left
    # holding the clones (which are the bulk of the disk a project uses).
    assert client.get(f"/api/learn/{sid}").status_code == 404
    assert client.post(f"/api/learn/{sid}/action", json={"action": "open_ex1", "payload": {}}).status_code == 404
    assert not workspace.exists()
    assert client.get("/api/learn").json()["sessions"] == []
    # Deleting twice is a 404, not a second "ok": the second call deleted nothing.
    assert client.delete(f"/api/learn/{sid}").status_code == 404


def test_another_user_cannot_delete_a_session(client, as_user):
    as_user()
    sid = client.post("/api/learn/start", json={"objective": "mine", "history": []}).json()["session_id"]

    as_user(login="bob", github_id=2)
    # 404, like every other route: a 403 would confirm the session exists.
    assert client.delete(f"/api/learn/{sid}").status_code == 404
    as_user()
    assert client.get(f"/api/learn/{sid}").status_code == 200


def test_a_running_turn_refuses_deletion(client, as_user):
    as_user()
    sid = client.post("/api/learn/start", json={"objective": "learn", "history": []}).json()["session_id"]
    # A turn holding the lock is the same state /action answers 409 for, and the same reason: the folder
    # must not be removed under a clone that is still writing into it.
    lock = routes._turn_lock(sid)
    assert lock.acquire(blocking=False)
    try:
        assert client.delete(f"/api/learn/{sid}").status_code == 409
    finally:
        lock.release()
    assert client.delete(f"/api/learn/{sid}").status_code == 200


def test_traversal_id_cannot_reach_the_data_directory(client, as_user, data_dir):
    """The original bug, end to end: a ".." id must not resolve to a workspace."""
    as_user()
    assert client.get("/api/learn/..").status_code in (404, 400)
    assert not (data_dir / "workspaces").exists() or not any(
        p.is_file() for p in (data_dir / "workspaces").rglob("*"))


def test_second_simultaneous_action_gets_409(client, as_user):
    as_user()
    sid = client.post("/api/learn/start", json={"objective": "x", "history": []}).json()["session_id"]

    from app import routes

    lock = routes._turn_lock(sid)
    lock.acquire()
    try:
        response = client.post(f"/api/learn/{sid}/action", json={"action": "open_ex1", "payload": {}})
        assert response.status_code == 409
        assert response.json()["detail"] == "A turn is already running for this session"
    finally:
        lock.release()
    # The lock is released, so the next turn goes through.
    assert client.post(f"/api/learn/{sid}/action",
                       json={"action": "open_ex1", "payload": {}}).status_code == 200


def test_ask_does_not_create_a_page(client, as_user, database):
    as_user()
    sid = client.post("/api/learn/start", json={"objective": "x", "history": []}).json()["session_id"]
    with database.session_scope() as s:
        from app import db
        before = s.query(db.Page).filter_by(session_id=sid).count()

    answer = client.post(f"/api/learn/{sid}/ask", json={"question": "why?", "context": {}})
    assert answer.status_code == 200
    assert answer.json()["answer"]

    with database.session_scope() as s:
        from app import db
        after = s.query(db.Page).filter_by(session_id=sid).count()
    assert after == before, "/ask must not append a page: it is a side channel to the current screen"


def test_empty_inputs_are_rejected(client, as_user):
    as_user()
    assert client.post("/api/learn/start", json={"objective": "   ", "history": []}).status_code == 400
    sid = client.post("/api/learn/start", json={"objective": "x", "history": []}).json()["session_id"]
    assert client.post(f"/api/learn/{sid}/action",
                       json={"action": "  ", "payload": {}}).status_code == 400
    assert client.post(f"/api/learn/{sid}/ask",
                       json={"question": " ", "context": {}}).status_code == 400


def test_progress_unknown_turn_is_an_empty_finished_feed(client):
    """A poll can arrive after the entry was pruned or the server restarted; that is not an error."""
    body = client.get("/api/learn/progress/does-not-exist").json()
    assert body == {"steps": [], "done": True}


def test_progress_feed_is_reported_for_a_live_turn(client, as_user):
    from app import progress

    uid = as_user()
    progress.start("turn-abc", uid)
    progress.step("turn-abc", "Cloning owner/repo")
    body = client.get("/api/learn/progress/turn-abc").json()
    assert body["done"] is False
    assert [s["text"] for s in body["steps"]] == ["Cloning owner/repo"]


def test_openapi_covers_every_endpoint(client):
    paths = client.app.openapi()["paths"]
    for path in ["/api/learn/clarify", "/api/learn/start", "/api/learn/{session_id}",
                 "/api/learn/{session_id}/action", "/api/learn/{session_id}/ask",
                 "/api/learn/progress/{turn_id}", "/api/learn",
                 "/api/auth/me", "/api/auth/logout", "/api/auth/github/login",
                 "/api/auth/github/callback"]:
        assert path in paths, path
