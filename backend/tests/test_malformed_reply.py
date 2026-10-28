"""A model reply the SDK cannot read: the streamed tool call is unparseable.

The failure this guards against is not hypothetical -- it is what a real turn died of, with the SDK
raising `Unable to parse tool parameter JSON from model` out of `accumulate_event` while the model was
streaming a long exercises payload (a raw newline inside a JSON string is enough). Because the raise
happens while the tool call is still being assembled, there is no message and no page at all: the turn
simply ends. These tests pin the two halves of the fix -- one retry, and a page saying so if the retry
does not help -- and drive the real seedagent loop and the real SDK streaming path, faking only the
HTTP transport.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import agent

# The page a *good* retry returns. _split_exercises passes a plain page through unchanged.
PAGE = {"title": "Recovered", "blocks": [{"type": "markdown", "content": "Second try worked."}]}

# What the SDK raises, with the shape the app matches on. The SDK's own wording is checked against it
# in `test_the_sdk_message_still_matches_what_we_look_for`, so a reworded SDK shows up as a failure
# here rather than as a silently broken retry in production.
MALFORMED = (
    "Unable to parse tool parameter JSON from model. Please retry your request or adjust your prompt. "
    'Error: control character (\\u0000-\\u001F) found while parsing a string at line 2 column 0. '
    'JSON: {"json": [{"title": "E6", "blocks": ['
)


def _events(blocks: list[tuple[str, dict]]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(body)}\n\n" for name, body in blocks).encode()


def _text_page(text: str) -> bytes:
    """One complete streamed message whose only content is `text`."""
    return _events([
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-test",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 20}}),
        ("message_stop", {"type": "message_stop"}),
    ])


def _truncated_tool_call() -> bytes:
    """A tool_use block whose arguments are announced and never completed.

    This is the transport-level shape of it: the SDK accumulates the partial JSON in `input_json_delta`
    and only parses at `content_block_stop`, so stopping mid-way is exactly what a cut-off reply looks
    like, and the SDK raises the control-character error (or a plain JSONDecodeError) on the way into
    `get_final_message`.
    """
    return _events([
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-test",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "id": "toolu_test", "name": "validate_json_exercises", "input": {} }}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": '{"json": [{"title": "E6"'}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                           "usage": {"output_tokens": 20}}),
        ("message_stop", {"type": "message_stop"}),
    ])


@pytest.fixture()
def claude(monkeypatch):
    """A real Anthropic client on a fake transport, plus the real seedagent session."""
    import anthropic
    from seedagent import config, session as session_module

    holder: dict = {}

    def fake_client():
        transport = httpx.MockTransport(lambda request: holder["handler"](request))
        return anthropic.Anthropic(api_key="test-key", base_url="https://api.test",
                                   http_client=httpx.Client(transport=transport))

    monkeypatch.setattr(config, "_get_client", fake_client)
    monkeypatch.setattr(session_module, "_get_client", fake_client)

    def install(handler):
        holder["handler"] = handler

    install(lambda request: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=_text_page(json.dumps(PAGE))))
    return install


@pytest.fixture(autouse=True)
def real_seedagent_session(tmp_path, monkeypatch):
    """Undo the process-wide mock the route tests install, and keep memory writes in tmp_path."""
    from seedagent import config, memory, session as session_module

    import _mock_agent_server

    home = tmp_path / "memory"
    home.mkdir()
    monkeypatch.setattr(config, "MEMORY_HOME", home)
    monkeypatch.setattr(session_module, "MEMORY_HOME", home)
    monkeypatch.setattr(config, "MEMORY_UPDATES", False)
    monkeypatch.setattr(memory, "MEMORY_UPDATES", False)
    monkeypatch.setattr(agent, "MEMORY_UPDATES", False, raising=False)

    _mock_agent_server.uninstall()
    yield
    _mock_agent_server.install()


def test_the_sdk_message_still_matches_what_we_look_for():
    """The app retries on a substring of the SDK's message, so the substring is the contract.

    Asserted against the real SDK *parser* rather than a copy of its string: the stream below carries a
    tool call whose JSON cannot be parsed, `accumulate_event` is what raises, and the message it raises
    is checked for the substring the retry matches on. A previous version of this test asserted on the
    module docstring, which could never fail for a reworded error -- it proved nothing."""
    from anthropic.lib.streaming._messages import accumulate_event
    from anthropic.types import (
        ContentBlockDeltaEvent, RawContentBlockStartEvent, RawMessageStartEvent,
    )

    snapshot = accumulate_event(
        event=RawMessageStartEvent(
            type="message_start",
            message={"id": "msg_t", "type": "message", "role": "assistant", "model": "claude-test",
                     "content": [], "stop_reason": None, "stop_sequence": None,
                     "usage": {"input_tokens": 1, "output_tokens": 0}},
        ),
        current_snapshot=None,
    )
    snapshot = accumulate_event(
        event=RawContentBlockStartEvent(
            type="content_block_start", index=0,
            content_block={"type": "tool_use", "id": "toolu_1", "name": "validate_json_exercises",
                           "input": {}},
        ),
        current_snapshot=snapshot,
    )
    # A literal newline inside a JSON string: the real-world cause, since a model writing a long exercises
    # payload sometimes emits a raw control character instead of \n, and jiter refuses it in partial mode.
    bad_json = '{"json": [{"title": "E6\nline two"}]}'
    with pytest.raises(ValueError) as caught:
        accumulate_event(
            event=ContentBlockDeltaEvent(
                type="content_block_delta", index=0,
                delta={"type": "input_json_delta", "partial_json": bad_json},
            ),
            current_snapshot=snapshot,
        )
    # This is the exact substring _streamed_send matches on; a reworded SDK fails here, loudly.
    assert "parse tool parameter JSON" in str(caught.value), (
        f"the SDK no longer says 'parse tool parameter JSON': {caught.value!r}"
    )


def test_a_cut_off_reply_is_retried_once_and_recovers(store, claude, monkeypatch):
    """The whole point: one bad stream must not cost the learner the turn.

    The first request is a truncated tool call (the real failure), the second is a good page. Without
    the retry this turn ends with no page -- which is what the SDK's ValueError did in production."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=_truncated_tool_call())
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_text_page(json.dumps(PAGE)))

    claude(handler)
    workspace = store.workspaces_dir() / "retry"
    workspace.mkdir(parents=True, exist_ok=True)

    result = agent.run_turn(workspace, "propose the roadmap", resume=False)

    assert len(calls) == 2, "the malformed reply was not retried"
    assert result.page["title"] == "Recovered", result.page


def test_a_reply_that_is_cut_off_twice_gives_a_page_not_a_traceback(store, claude):
    """Both attempts malformed: the learner gets a page that says so and offers Try again.

    The alternative was a raw traceback in the console and a screen that never changed, with nothing
    telling them the turn had even run."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_truncated_tool_call())

    claude(handler)
    workspace = store.workspaces_dir() / "twice"
    workspace.mkdir(parents=True, exist_ok=True)

    result = agent.run_turn(workspace, "propose the roadmap", resume=False)

    # Counted in attempts, not HTTP calls: one malformed attempt legitimately makes several model calls
    # (a tool round-trip each) before the attempt's step budget ends it, so a raw call count says nothing
    # about how many attempts ran. What matters is that the turn gave up after _SEND_ATTEMPTS and still
    # produced the page that explains it, instead of a traceback.
    assert len(calls) >= agent._SEND_ATTEMPTS, "the turn did not retry past the first attempt"
    assert result.page["title"] == "The reply was cut off"
    # It still needs to be a *page*: a retry button the learner can press, and the marker routes.py
    # strips before answering (db.classify_page stores it as kind="error").
    assert result.page.get("_error") is True
    actions = [b for b in result.page["blocks"] if b["type"] == "actions"]
    assert actions and actions[0]["items"][0]["action"] == "retry"
    assert "Try again" in json.dumps(result.page)


def test_a_healthy_turn_is_never_retried(store, claude):
    """The retry must not cost a second model call on a turn that worked."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_text_page(json.dumps(PAGE)))

    claude(handler)
    workspace = store.workspaces_dir() / "healthy"
    workspace.mkdir(parents=True, exist_ok=True)

    result = agent.run_turn(workspace, "propose the roadmap", resume=False)

    assert len(calls) == 1
    assert result.page["title"] == "Recovered"


def test_an_unrelated_value_error_is_not_swallowed(store, claude, monkeypatch):
    """Only the tool-call parse error is caught; anything else must keep propagating.

    A blanket `except ValueError` around session.send would turn every unrelated bug in the agent into
    "the reply was cut off", which is exactly the kind of thing that hides a real fault for months."""
    from seedagent import session as session_module

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_text_page(json.dumps(PAGE)))

    claude(handler)
    monkeypatch.setattr(session_module, "send", _raise_unrelated)
    workspace = store.workspaces_dir() / "unrelated"
    workspace.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="something else went wrong"):
        agent.run_turn(workspace, "propose the roadmap", resume=False)
    # Not retried, and not turned into a reply page: the original error reached the caller intact. (The
    # fake handler above is never used -- session.send is replaced -- so `calls` stays empty; the old
    # assertion that it contained a marker was a leftover from before the retry existed.)
    assert calls == [], calls


def _raise_unrelated(text: str) -> bool:
    raise ValueError("something else went wrong")


def test_the_malformed_error_text_is_what_run_turn_matches(store, claude):
    """run_turn decides which error page to build from ui.error_text, so the text must match what the
    retry raises. A mismatch would silently fall back to the generic page."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_truncated_tool_call())

    claude(handler)
    workspace = store.workspaces_dir() / "text"
    workspace.mkdir(parents=True, exist_ok=True)

    result = agent.run_turn(workspace, "propose the roadmap", resume=False)
    # Not the generic page: its body is the raw detail in a code fence.
    body = result.page["blocks"][0]["content"]
    assert "```" not in body
    assert "cut off" in body
