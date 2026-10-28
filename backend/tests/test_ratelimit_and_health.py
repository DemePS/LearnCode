"""The money guards: per-user rate limits, the per-attempt step budget, and the health endpoint.

These are the pieces that stop one account (or one looping model) from spending the operator's API
budget or starving everyone else, so each limit gets a test that proves it actually refuses rather
than merely being configured."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(database, data_dir, monkeypatch):
    """A TestClient on the real app, signed in as a local user (LEARNCODE_AUTH=off), like test_routes.

    The app modules are imported once per process: app.agent registers its tools in seedagent's global
    registry and re-registering a name raises, so a per-test import would fail on the second test."""
    import importlib
    import sys

    monkeypatch.setenv("LEARNCODE_AUTH", "off")
    if "app.main" not in sys.modules:
        importlib.import_module("_mock_agent_server")
    main = importlib.import_module("app.main")
    return TestClient(main.app)


# ---------------------------------------------------------------- the rate limit


def test_a_user_is_refused_after_the_window_limit(monkeypatch):
    """The turn routes share one budget per user. Exhausting it answers 429 with Retry-After, which is
    what a client needs to back off instead of retrying in a loop."""
    from app import ratelimit

    monkeypatch.setattr(ratelimit, "TURNS_PER_WINDOW", 3)
    monkeypatch.setattr(ratelimit, "CONCURRENT_TURNS_PER_USER", 10)

    slots = [ratelimit.acquire_turn_slot("user-1") for _ in range(3)]
    with pytest.raises(Exception) as caught:
        ratelimit.acquire_turn_slot("user-1")
    assert getattr(caught.value, "status_code", None) == 429
    assert "Retry-After" in getattr(caught.value, "headers", {})
    for s in slots:
        s.release()


def test_the_limit_is_per_user_not_global(monkeypatch):
    """One learner hitting the ceiling must not lock everybody else out."""
    from app import ratelimit

    monkeypatch.setattr(ratelimit, "TURNS_PER_WINDOW", 1)
    monkeypatch.setattr(ratelimit, "CONCURRENT_TURNS_PER_USER", 10)

    ratelimit.acquire_turn_slot("greedy").release()
    with pytest.raises(Exception):
        ratelimit.acquire_turn_slot("greedy")
    # A different learner is unaffected.
    ratelimit.acquire_turn_slot("patient").release()


def test_concurrent_turns_are_capped_per_user(monkeypatch):
    """The rate limit governs total spend; this governs how many turns one user may have *running*,
    so a single account cannot occupy every global worker slot (LEARNCODE_MAX_CONCURRENT_TURNS)."""
    from app import ratelimit

    monkeypatch.setattr(ratelimit, "TURNS_PER_WINDOW", 100)
    monkeypatch.setattr(ratelimit, "CONCURRENT_TURNS_PER_USER", 2)

    a = ratelimit.acquire_turn_slot("u")
    b = ratelimit.acquire_turn_slot("u")
    with pytest.raises(Exception) as caught:
        ratelimit.acquire_turn_slot("u")
    assert getattr(caught.value, "status_code", None) == 429
    a.release()
    # Releasing a slot really frees it, or a crashed turn would lock the user out for good.
    c = ratelimit.acquire_turn_slot("u")
    b.release()
    c.release()


def test_releasing_twice_does_not_corrupt_the_count():
    """A slot is released in a finally block and may be released again by a confused caller; the
    counter must not go negative (which would hand out extra concurrency forever)."""
    from app import ratelimit

    slot = ratelimit.acquire_turn_slot("u")
    slot.release()
    slot.release()
    assert ratelimit._running.get("u", 0) == 0


def test_the_window_expires(monkeypatch):
    """A limit that never resets is a lockout, not a rate limit."""
    from app import ratelimit

    monkeypatch.setattr(ratelimit, "TURNS_PER_WINDOW", 1)
    monkeypatch.setattr(ratelimit, "CONCURRENT_TURNS_PER_USER", 10)
    monkeypatch.setattr(ratelimit, "WINDOW_SECONDS", 0)

    ratelimit.acquire_turn_slot("u").release()
    ratelimit.acquire_turn_slot("u").release()  # the zero-length window has already rolled over


def test_clarify_is_rate_limited(client, monkeypatch):
    """/clarify costs a model call and creates nothing, so it needed the same guard as a turn."""
    from app import ratelimit

    monkeypatch.setattr(ratelimit, "TURNS_PER_WINDOW", 1)
    monkeypatch.setattr(ratelimit, "CONCURRENT_TURNS_PER_USER", 10)
    first = client.post("/api/learn/clarify", json={"objective": "learn FastAPI", "history": []})
    assert first.status_code == 200
    second = client.post("/api/learn/clarify", json={"objective": "learn FastAPI", "history": []})
    assert second.status_code == 429
    assert second.headers.get("retry-after")


# ---------------------------------------------------------------- the step budget


def test_a_looping_model_is_cut_off_by_the_step_budget():
    """A model that keeps repeating the same tool call must not run the whole AGENT_MAX_STEPS budget.

    This is the shape of the real outage: an unparseable `validate_json_exercises` call makes the loop
    re-ask forever, so without a per-attempt cap one attempt is 100 model calls, and the retry multiplies
    that. The budget trips session.stop() at the cap instead."""
    from app import agent

    ui = agent.CaptureUI(turn_id="")
    assert ui._step_budget > 0
    # Pretend the loop has run to one short of the cap, then make the call that reaches it.
    ui._steps = ui._step_budget - 1
    ui.tool_start("validate_json_exercises")
    assert ui._step_budget_exhausted is True
    assert ui.ended_empty() is True


def test_an_answer_after_the_budget_is_still_a_yes():
    """The budget must not refuse a turn that produced a page: only an empty one counts as wasted."""
    from app import agent

    ui = agent.CaptureUI(turn_id="")
    ui.tool_start("read_file")
    ui.final_text = '{"title": "Roadmap", "blocks": []}'
    assert ui.ended_empty() is False


def test_reset_reply_clears_the_budget_for_the_next_attempt():
    """Each attempt gets its own step allowance; otherwise attempt 2 would start already exhausted."""
    from app import agent

    ui = agent.CaptureUI(turn_id="")
    ui._steps = ui._step_budget - 1
    ui.tool_start("x")
    assert ui._step_budget_exhausted
    ui.reset_reply()
    assert ui._step_budget_exhausted is False
    assert ui._steps == 0


# ---------------------------------------------------------------- health


def test_health_reports_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_does_not_require_a_signed_in_user(client):
    """A health check runs before anyone has signed in -- 401 here would make the platform think the
    app is down."""
    client.cookies.clear()
    assert client.get("/health").status_code == 200
