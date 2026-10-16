"""Two turns, at the same time.

The whole point of this branch is that learners no longer queue behind one another, and that is not
something a single-threaded test can show: a test that awaits one turn, then the next, passes just as
well against a global lock. So both turns are started on real threads and each one's *model request*
waits on a `threading.Barrier(2)`. The barrier only releases once both requests are in flight, which
means the test hangs and fails on timeout if anything upstream still serialises them -- that failure is
the assertion. Nothing else here is faked beyond the model: the real seedagent loop, the real
per-session `state.Session`, the real ContextVar-carried GitHub token and budgets.

Claude is faked at the HTTP layer (an httpx MockTransport on the Anthropic client seedagent builds)
rather than by stubbing seedagent's own functions, so the loop's streaming path -- `client.messages.
stream(...)`, the event handling, `get_final_message()` -- really runs. The brief's `AnthropicFoundry`
also goes through httpx, so this is the same seam it uses, reached through the client seedagent
actually constructs (`config._get_client`).
"""

from __future__ import annotations

import json
import threading

import httpx
import pytest

# A page the model would return: one markdown block, so run_turn's _extract_json has something real to
# parse and the GitHub-free path is exercised end to end.
PAGE = {
    "title": "Concurrent page",
    "blocks": [{"type": "markdown", "content": "Two turns ran side by side."}],
}


def _sse(text: str, model: str = "claude-test") -> bytes:
    """One complete streamed assistant message, as the Messages API streams it.

    The events and their order are what the SDK's `_accumulate_event` requires: `message_start` with a
    message skeleton, one text block start/delta/stop, then `message_delta` (which carries the
    `stop_reason` the loop reads to decide the turn is finished) and `message_stop`."""
    payload = json.dumps(PAGE)
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text + payload}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 20}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(body)}\n\n" for name, body in events).encode()


@pytest.fixture()
def claude(monkeypatch):
    """Replace the Anthropic client seedagent builds with one whose transport runs `handler`.

    Returns an `install(handler)` function so a test can set what the model answers (and, here, make
    the request wait). The client is a real `anthropic.Anthropic`; only its transport is fake, so
    request building and streamed-response parsing (the parts most likely to break on an SDK upgrade)
    still run. Without install(), every call gets a plain canned page."""
    import anthropic
    from seedagent import config, session as session_module

    handler_holder: dict = {}

    def fake_client():
        transport = httpx.MockTransport(lambda request: handler_holder["handler"](request))
        # No /v1 here: the SDK appends it, and a base_url ending in /v1 would make every request
        # /v1/v1/messages -- which the path assertions below would then have to expect.
        return anthropic.Anthropic(api_key="test-key", base_url="https://api.test",
                                   http_client=httpx.Client(transport=transport))

    def install(handler):
        handler_holder["handler"] = handler

    install(lambda request: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=_sse("")))
    monkeypatch.setattr(config, "_get_client", fake_client)
    # session.send calls the client through the name imported into seedagent.session, so that binding
    # is the one that counts (a bare `from x import _get_client` copies the reference).
    monkeypatch.setattr(session_module, "_get_client", fake_client)
    return install


@pytest.fixture(autouse=True)
def real_seedagent_session(tmp_path, monkeypatch):
    """Run the library's real session, even though the route tests patched it for the whole process.

    _mock_agent_server replaces open_project/send/close_session at import time, and the route tests
    import it once for the process -- so without this, the turns below would never reach the fake HTTP
    transport and nothing would be concurrency-tested at all. Its uninstall() puts the originals back.

    AGENT_MEMORY=off stops the post-turn memory update, a second model call per turn that the call
    counts below would otherwise include; MEMORY_HOME is also redirected because the real loop saves the
    conversation after every model call and must not litter the developer's home directory. The flag is
    patched rather than set in the environment because config.MEMORY_UPDATES is read at import time,
    which has long since happened by the time a fixture runs -- and memory.py imported it by value too."""
    from app import agent as agent_module
    from seedagent import config, memory, session as session_module

    import _mock_agent_server

    memory_home = tmp_path / "memory"
    memory_home.mkdir()
    monkeypatch.setattr(config, "MEMORY_HOME", memory_home)
    monkeypatch.setattr(session_module, "MEMORY_HOME", memory_home)
    monkeypatch.setattr(config, "MEMORY_UPDATES", False)
    monkeypatch.setattr(memory, "MEMORY_UPDATES", False)
    monkeypatch.setattr(agent_module, "MEMORY_UPDATES", False, raising=False)

    _mock_agent_server.uninstall()
    yield
    _mock_agent_server.install()


def test_two_turns_stream_at_the_same_time(store, claude):
    """The assertion is the barrier: each turn's request blocks until the other turn's request has
    arrived, so both being in flight at once is the only way either can finish."""
    from app import agent

    install = claude
    barrier = threading.Barrier(2, timeout=15)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Any request reaching here is a model call from a turn that has already claimed its slot and
        # opened its own session -- so waiting here is exactly "wait until both turns are running".
        calls.append(request.url.path)
        barrier.wait()
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_sse(""))

    install(handler)

    results: dict[str, dict] = {}
    errors: dict[str, BaseException] = {}

    def run(name: str) -> None:
        workspace = store.workspaces_dir() / name
        workspace.mkdir(parents=True, exist_ok=True)
        try:
            results[name] = agent.run_turn(workspace, f"objective for {name}", resume=False).page
        except BaseException as exc:  # recorded, re-raised in the test thread below
            errors[name] = exc

    threads = [threading.Thread(target=run, args=(n,), name=n) for n in ("turn-a", "turn-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), (
        "a turn never finished: the two model calls were serialised instead of overlapping "
        "(a barrier timeout surfaces as BrokenBarrierError below)")
    assert not errors, f"a concurrent turn raised: {errors}"
    assert sorted(results) == ["turn-a", "turn-b"]
    for page in results.values():
        assert page.get("title") == "Concurrent page", page
    # Both turns really did reach the model: two calls are in the list because neither could have
    # returned before the second one arrived (the first would still be waiting at the barrier).
    assert calls == ["/v1/messages", "/v1/messages"], calls


def test_one_turns_github_token_does_not_leak_into_the_other(store, claude):
    """The reason the token travels in an argument and a ContextVar rather than a module global.

    Each turn's token is read from inside its own model call, and both calls wait on a barrier before
    reading -- so the two reads happen while both turns are genuinely in flight. A module global would
    be overwritten by whichever turn started last, and this is where that shows up."""
    import httpx as _httpx

    from app import agent, github_lookup

    install = claude
    barrier = threading.Barrier(2, timeout=15)
    observed: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        barrier.wait()  # both turns are inside their model call, each with its own ContextVar set
        # github_lookup reads its token with _token.get(), so reading the same way here is what makes
        # this a test of the ContextVar the app actually uses, not of a variable local to the test.
        observed.append(github_lookup._token.get())
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(""))

    install(handler)

    def run(name: str, token: str) -> None:
        workspace = store.workspaces_dir() / name
        workspace.mkdir(parents=True, exist_ok=True)
        agent.run_turn(workspace, f"objective for {name}", resume=False, github_token=token)

    threads = [
        threading.Thread(target=run, args=("turn-x", "token-for-x")),
        threading.Thread(target=run, args=("turn-y", "token-for-y")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "the two turns did not overlap"
    # Each call saw its own turn's token: neither saw the other's (which a module global would have
    # produced, whichever turn started last winning) and neither saw None (a stray fallback).
    assert sorted(observed) == ["token-for-x", "token-for-y"], observed
