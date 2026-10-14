"""GitHub repository lookup for the instructor agent.

The instructor's job needs exactly one fact from the outside world: does the open-source project I am
about to build a roadmap on actually exist, and is it still worth a learner's time? GitHub's own API
answers that as structured JSON, so nothing here scrapes a page and nothing can be anti-botted into a
204/202 (the failure mode of the DuckDuckGo HTML search this replaces).

Two calls, used in that order:

  search_repos -- find candidate repositories for a plain-language description ("self-hosted
                  bookmark manager in Python"), newest and most starred first.
  get_repo     -- confirm one repository exists and report its facts, or fail cleanly with 404.

Both are read-only. Unauthenticated GitHub allows 60 requests per hour per IP, which the search
endpoint shares; set GITHUB_TOKEN (read-only, public repos, no scopes) for 5,000/hour.
"""

import json
import os
import time
from pathlib import Path

import httpx

from seedagent.common import ToolError

API = "https://api.github.com"
TIMEOUT = 20
MAX_PER_PAGE = 20
DESCRIPTION_CHARS = 200

# One cache for the whole deployment, in the data folder next to the session workspaces: popular
# repositories are looked up over and over (every learner asking to "learn FastAPI" hits the same
# handful), and unauthenticated GitHub allows only 60 requests an hour per IP -- shared by every
# learner behind one server. A hit costs no request at all, which is also what keeps the app working
# when the rate limit is spent.
CACHE_FILE = Path(os.environ.get("LEARNCODE_DATA_DIR",
                                 Path(__file__).resolve().parent.parent / "data")) / "github_cache.json"
SEARCH_TTL = 24 * 3600  # the results of a query: a day is plenty, rankings barely move
REPO_TTL = 24 * 3600    # one repository's facts: stars and the last push date
MAX_CACHE_ENTRIES = 500  # keep the file small; least-recently-used entries are dropped

# In-process copy so a burst of lookups inside one turn does not even re-read the file.
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            _cache = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            _cache = {}
    return _cache


def _cache_get(key: str, ttl: int):
    entry = _load().get(key)
    if not isinstance(entry, dict):
        return None
    if time.time() - entry.get("at", 0) > ttl:
        return None
    return entry.get("value")


def _cache_put(key: str, value) -> None:
    """Store a value and persist the cache, dropping the oldest entries when it grows too large."""
    cache = _load()
    cache[key] = {"at": time.time(), "value": value}
    if len(cache) > MAX_CACHE_ENTRIES:
        for oldest, _ in sorted(cache.items(), key=lambda kv: kv[1].get("at", 0))[
                : len(cache) - MAX_CACHE_ENTRIES]:
            cache.pop(oldest, None)
    try:  # a cache that cannot be written is not worth failing a learner's turn over
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def cache_stats() -> dict:
    """How many entries are cached, and how many requests they have saved since the server started."""
    return {"entries": len(_load()), "hits": _hits, "misses": _misses}


_hits = 0
_misses = 0

# A ceiling per turn on live searches. The agent used to explore many topics before settling -- one
# turn in testing made 15 searches, and every learner shares one IP's 60 requests an hour (60 without
# a token; see _headers), so an unbounded turn starves everyone else for the rest of the hour. Two
# searches is enough to find a project: search_repos orders by stars, so the top three are already
# the candidates, and the clone that follows is the real confirmation.
SEARCHES_PER_TURN = int(os.environ.get("LEARNCODE_GITHUB_SEARCHES_PER_TURN", "2"))
_searches_this_turn = 0


def reset_turn_budget() -> None:
    """Called at the start of each agent turn so the cap is accounted per turn, not per process."""
    global _searches_this_turn
    _searches_this_turn = 0


def _spend() -> None:
    """Charge one live search (a cache hit is free and does not count)."""
    global _searches_this_turn
    if _searches_this_turn >= SEARCHES_PER_TURN:
        raise ToolError(
            f"You have used your {SEARCHES_PER_TURN} GitHub searches for this turn. Stop searching, "
            f"clone one of the repositories you have already found, and write the roadmap."
        )
    _searches_this_turn += 1


def searches_left() -> int:
    """How many searches this turn still has, for tests and diagnostics."""
    return SEARCHES_PER_TURN - _searches_this_turn

# GitHub asks every client to send a User-Agent; requests without one are refused.
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "LearnCode-instructor",
}


def _headers() -> dict:
    """The request headers, with a token if one is configured.

    A token raises the limit from 60 to 5,000 requests an hour, which matters as soon as more than a
    couple of learners share an IP. Read-only access to public repositories needs no scopes at all."""
    headers = dict(_HEADERS)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch(path: str, params: dict | None, ttl: int) -> dict:
    """GET a GitHub endpoint, through the cache. Returns the parsed JSON body.

    Caching matters more than it looks: the rate limit is per IP and shared by every learner on the
    deployment, and the same few repositories come up again and again."""
    global _hits, _misses
    key = path + "?" + json.dumps(params or {}, sort_keys=True)
    cached = _cache_get(key, ttl)
    if cached is not None:
        _hits += 1
        return cached

    _misses += 1
    _spend()  # only a real request counts against the turn's budget; a cache hit is free
    response = _get(path, params)
    _check(response)
    body = response.json()
    if isinstance(body, dict):
        _cache_put(key, body)
    return body


def _get(path: str, params: dict | None = None) -> httpx.Response:
    try:
        return httpx.get(f"{API}{path}", params=params, headers=_headers(), timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise ToolError(f"GitHub could not be reached ({type(e).__name__}: {e}).")


def _check(response: httpx.Response) -> None:
    """Turn GitHub's error replies into messages the model can act on, rather than a stack trace."""
    if response.status_code == 200:
        return
    if response.status_code == 403 and "rate limit" in response.text.lower():
        reset = response.headers.get("X-RateLimit-Reset")
        raise ToolError(
            f"GitHub's rate limit is exhausted (60 requests an hour without a token"
            f"{', resets at ' + reset if reset else ''}). Stop searching and use the repositories "
            f"you have already confirmed."
        )
    if response.status_code == 404:
        raise ToolError("No such repository (404). Check the owner and name: it may be renamed, "
                        "deleted, or private -- pick another project.")
    if response.status_code == 422:
        raise ToolError(f"GitHub rejected the search query: {response.json().get('message', '')}")
    raise ToolError(f"GitHub returned HTTP {response.status_code}: {response.text[:200]}")


def _describe(item: dict, verbose: bool) -> str:
    """One repository as a line or two the model can weigh up."""
    stars = item.get("stargazers_count", 0)
    language = item.get("language") or "?"
    pushed = (item.get("pushed_at") or "")[:10]
    flags = []
    if item.get("archived"):
        flags.append("ARCHIVED")
    if item.get("fork"):
        flags.append("fork")
    flag = ("  [" + ", ".join(flags) + "]") if flags else ""
    lines = [f"{item['full_name']} -- {stars:,} stars, {language}, last push {pushed}{flag}",
             f"   {item.get('html_url')}"]
    description = (item.get("description") or "").strip()
    if description:
        lines.append(f"   {description[:DESCRIPTION_CHARS]}")
    if verbose:
        license_id = (item.get("license") or {}).get("spdx_id")
        lines.append(f"   open issues: {item.get('open_issues_count', 0)}"
                     f" | default branch: {item.get('default_branch')}"
                     f" | license: {license_id or 'none'}"
                     f" | topics: {', '.join(item.get('topics') or []) or '-'}")
    return "\n".join(lines)


def tool_search_repos(query: str, language: str = "", min_stars: int = 50,
                      max_results: int = 5) -> str:
    """Search GitHub for repositories matching a description of what the project does."""
    if not isinstance(query, str) or not query.strip():
        raise ToolError("Give a non-empty search query describing what the project does.")

    qualifiers = []
    terms = query.strip()
    if language and language.strip():
        qualifiers.append(f"language:{language.strip()}")
    if min_stars:
        qualifiers.append(f"stars:>={int(min_stars)}")
    # Exclude forks and archived repos by default: a learner should not be pointed at a dead copy.
    qualifiers += ["fork:false", "archived:false"]
    full_query = " ".join([terms] + qualifiers)

    count = 5 if max_results is None else max(1, min(int(max_results), MAX_PER_PAGE))
    data = _fetch("/search/repositories",
                  {"q": full_query, "sort": "stars", "order": "desc", "per_page": count},
                  SEARCH_TTL)

    items = data.get("items", [])
    if not items:
        return (f'Nothing matched "{full_query}": GitHub needs every word to appear in the '
                f'repository. Retry with one or two plain words, for example "fastapi".')

    total = data.get("total_count", len(items))
    lines = [f'{len(items)} of {total:,} repositories for "{full_query}", most starred first:',
             "The top three are the candidates: pick one of them and clone it.", ""]
    for i, item in enumerate(items, 1):
        mark = " *" if i <= 3 else "  "
        lines.append(f"{mark} {i}. {_describe(item, verbose=False)}")
    return "\n".join(lines)


def tool_get_repo(repo: str) -> str:
    """Confirm one repository exists ("owner/name") and report its facts; 404 when it does not."""
    if not isinstance(repo, str) or "/" not in repo.strip():
        raise ToolError('Give the repository as "owner/name", for example "tiangolo/fastapi".')
    slug = repo.strip().strip("/")
    item = _fetch(f"/repos/{slug}", None, REPO_TTL)

    lines = [f"{item['full_name']} exists on GitHub.", _describe(item, verbose=True)]
    if item.get("archived"):
        lines.append("   WARNING: archived -- read-only and unmaintained. Choose something else.")
    return "\n".join(lines)


SEARCH_SCHEMA = {
    "name": "search_repos",
    "description": (
        "Search GitHub for open-source repositories. Pass one or two plain words -- the tool or the "
        "topic, for example 'fastapi' or 'bookmark manager' -- not a sentence: GitHub requires every "
        "word you send to appear in the repository. Results come back ordered by stars, most starred "
        "first, and the top three are marked -- choose one of those. Use this instead of recalling a "
        "project from memory. Then clone the one you chose: the clone is what proves it exists and "
        "gives you the code to write the exercises against."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "One or two plain words -- the tool or the domain, for example "
                                     "'fastapi' or 'bookmark manager'. Not a sentence."},
            "language": {"type": "string",
                         "description": "Optional language filter, e.g. 'python'."},
            "min_stars": {"type": "integer", "minimum": 0,
                          "description": "Only repositories with at least this many stars (default "
                                         "50, to keep toy and abandoned projects out; set 0 to "
                                         "see everything)."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20,
                            "description": "How many to return (default 5)."},
        },
        "required": ["query"],
    },
}
