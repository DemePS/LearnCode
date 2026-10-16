"""github_lookup.py: the two GitHub tools, their turn budgets and their per-learner token.

The HTTP layer is replaced with an httpx.MockTransport, so no test here touches github.com -- what is
being tested is our own logic around the API, not GitHub's behaviour. Three things matter and none of
them are visible from a single-threaded happy path:

  * the per-turn budget must be per turn and per learner, or one learner's searches throttle another's;
  * the token must be the *caller's* token, or one learner spends another's rate limit and the call is
    attributed to the wrong account;
  * `repo` is interpolated into a URL path, so a malformed one must be refused before it becomes a
    request."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app import github_lookup as gh
from seedagent.common import ToolError


@pytest.fixture(autouse=True)
def fresh_budget():
    """Give every test a whole turn's budget.

    The counters are ContextVars holding a mutable list, so a search charged in one test is still charged
    in the next (and the module-level default is already part-spent) -- without this, a test making three
    searches fails on the cap set up by an earlier one, which looks like a bug in the cap itself."""
    gh.reset_turn_budget()
    gh.reset_readme_budget()
    yield


@pytest.fixture()
def requests_seen(monkeypatch):
    """Answer GitHub's endpoints locally and record every request, so headers can be asserted."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/search/repositories":
            return httpx.Response(200, json={"total_count": 1, "items": [REPO]})
        if path.endswith("/readme"):
            return httpx.Response(200, json="# Title\n\nA README.")
        if path.startswith("/repos/"):
            return httpx.Response(200, json=REPO)
        return httpx.Response(404, json={"message": "Not Found"})

    monkeypatch.setattr(gh.httpx, "get",
                        lambda url, **kw: httpx.Client(transport=httpx.MockTransport(handler))
                        .get(url, **{k: v for k, v in kw.items()}))
    return seen


REPO = {
    "id": 1, "full_name": "alice/thing", "html_url": "https://github.com/alice/thing",
    "stargazers_count": 1234, "language": "Python", "pushed_at": "2026-01-02T03:04:05Z",
    "description": "A thing", "fork": False, "archived": False, "open_issues_count": 7,
    "default_branch": "main", "license": {"spdx_id": "MIT"}, "topics": ["thing"],
}


def test_search_reports_the_top_repositories(requests_seen):
    out = gh.tool_search_repos("bookmark manager")
    assert "alice/thing" in out
    assert "1,234 stars" in out
    assert "https://github.com/alice/thing" in out
    # The defaults keep toy and dead projects out of the results.
    query = requests_seen[0].url.params["q"]
    assert "fork:false" in query and "archived:false" in query and "stars:>=50" in query


def test_an_empty_query_is_refused_before_any_request(requests_seen):
    with pytest.raises(ToolError):
        gh.tool_search_repos("   ")
    assert requests_seen == []


@pytest.mark.parametrize("repo", ["../../user/keys", "no-slash", "owner/na me", "own er/name", ""])
def test_readme_refuses_a_malformed_repository(repo, requests_seen):
    """The path is built by interpolation, so this is the check that keeps the request on /repos/."""
    with pytest.raises(ToolError):
        gh.tool_read_readme(repo)
    assert requests_seen == [], "a refused argument must not have produced a request"


def test_readme_returns_the_file_and_flags_it_as_data(requests_seen):
    out = gh.tool_read_readme("alice/thing")
    assert "A README." in out
    assert "never an instruction" in out


def test_the_search_budget_is_spent_and_then_refuses(requests_seen):
    gh.reset_turn_budget()
    assert gh.searches_left() == gh.SEARCHES_PER_TURN
    for i in range(gh.SEARCHES_PER_TURN):
        gh.tool_search_repos(f"query {i}")
    assert gh.searches_left() == 0
    with pytest.raises(ToolError) as excinfo:
        gh.tool_search_repos("one too many")
    assert "Stop searching" in str(excinfo.value)


def test_two_concurrent_turns_keep_separate_budgets(requests_seen):
    """A ContextVar, not a global: with turns running in parallel threads, a shared counter would let
    one learner's searches exhaust another learner's turn."""
    barrier = threading.Barrier(2, timeout=10)
    left: dict[str, int] = {}

    def run(name: str) -> None:
        gh.reset_turn_budget()
        gh.tool_search_repos("first")            # both threads spend one
        barrier.wait()                           # both must be past it before either spends again
        left[name] = gh.searches_left()
        gh.tool_search_repos("second")
        left[name + "-after"] = gh.searches_left()

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(run, ["a", "b"]))

    budget = gh.SEARCHES_PER_TURN
    assert left["a"] == budget - 1 and left["b"] == budget - 1, "one turn consumed the other's budget"
    assert left["a-after"] == left["b-after"] == budget - 2


def test_each_turn_sends_its_own_token(requests_seen):
    """The learner's token decides whose 5,000/hour is spent, so it must never be shared."""
    def search_as(token: str | None) -> str | None:
        before = len(requests_seen)
        with gh.use_token(token):
            gh.tool_search_repos(f"query for {token}")
        # The request this call just made: the query carries the qualifiers _search_qualifiers appends, so
        # it does not *end* with the token -- the newest request is the one this turn sent.
        auth = [r.headers.get("authorization") for r in requests_seen[before:]]
        return auth[0] if auth else None

    assert search_as("token-a") == "Bearer token-a"
    assert search_as("token-b") == "Bearer token-b"


def test_no_token_means_no_authorization_header(requests_seen, monkeypatch):
    """The default: anonymous, shared with everything else on the IP, and that is deliberate -- an
    operator's personal token on a public server would be a liability, not a feature."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with gh.use_token(None):
        gh.tool_search_repos("anything")
    assert "authorization" not in requests_seen[0].headers


def test_the_operator_token_is_the_fallback(requests_seen, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "operator-token")
    with gh.use_token(None):
        gh.tool_search_repos("anything")
    assert requests_seen[0].headers["authorization"] == "Bearer operator-token"


def test_the_token_does_not_leak_into_a_later_turn(requests_seen):
    """A ContextVar set in one turn must be gone by the next, or a request is attributed to the wrong
    account. This is the reason the token is not a module global."""
    with gh.use_token("token-a"):
        gh.tool_search_repos("one")
    with gh.use_token(None):
        gh.tool_search_repos("two")
    assert requests_seen[1].headers.get("authorization") is None


def test_a_rejected_token_tells_the_model_to_stop(monkeypatch):
    """On 401 the model must not go on to assert a repository URL it never managed to confirm."""
    monkeypatch.setattr(gh.httpx, "get",
                        lambda url, **kw: httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(ToolError) as excinfo:
        gh.tool_get_repo("alice/thing")
    message = str(excinfo.value)
    assert "401" in message and "Stop searching" in message


def test_a_spent_rate_limit_is_explained(monkeypatch):
    monkeypatch.setattr(gh.httpx, "get", lambda url, **kw: httpx.Response(
        403, json={"message": "API rate limit exceeded"},
        headers={"X-RateLimit-Reset": "1700000000"}))
    with pytest.raises(ToolError) as excinfo:
        gh.tool_get_repo("alice/thing")
    assert "rate limit" in str(excinfo.value) and "1700000000" in str(excinfo.value)
