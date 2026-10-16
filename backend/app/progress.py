"""Live progress of a running turn, so the browser can show what the agent is doing.

A turn takes 20-60 seconds when the agent grounds its roadmap in a real repository (a search, a git
clone, then dozens of file reads), and until now the learner saw nothing at all for that whole time --
just a disabled button. The work was already visible to the server: CaptureUI receives every tool
call (see its docstring), but it wrote them to a log nobody reads. This keeps the last few of them
somewhere the frontend can poll.

Deliberately in memory, and deliberately per turn. The workspace on disk (store.py) is the session's
*result*; this is scratch state that is worthless the moment the turn returns, so it must not
outlive the process, must not need cleaning up, and must not be written to disk on every tool call.

Threading: run_turn executes in a threadpool worker (routes.py uses run_in_threadpool), so writes
come from a worker thread and reads from the event loop. A lock around a list append and a slice copy
is enough -- there is no need for anything cleverer, and every operation here is at most a few
microseconds long.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# How many steps to keep per turn. The frontend shows the most recent handful and counts the rest, so
# there is no reason to hold every one of a 47-tool-call turn: a handful is enough for a live feed,
# and capping it stops a runaway turn from growing a list without bound.
MAX_STEPS = 40

# A turn's progress is dropped this long after its last update. Generous on purpose: the frontend
# polls while the turn runs and reads once more after it returns, and a turn that stalls should still
# be explainable. This is the only cleanup there is, so it must not be so short that a slow turn's
# feed disappears while the learner is watching it.
_TTL_SECONDS = 15 * 60

_LOCK = threading.Lock()
_TURNS: dict[str, dict[str, Any]] = {}


def _prune_locked(now: float) -> None:
    """Drop expired turns. Called on write, which is the only time the dict grows."""
    for turn_id in [k for k, v in _TURNS.items() if now - v["updated"] > _TTL_SECONDS]:
        del _TURNS[turn_id]


def start(turn_id: str, owner_id: int | None = None, label: str = "Thinking") -> None:
    """Begin (or restart) the feed for a turn.

    `owner_id` records which learner asked for this turn, so /progress/{turn_id} can refuse to show
    one learner's activity (file paths, repository names) to another. In-memory and per process, like
    everything else here: a turn's feed is worthless the moment it returns."""
    now = time.time()
    with _LOCK:
        _prune_locked(now)
        _TURNS[turn_id] = {"label": label, "steps": [], "updated": now, "owner": owner_id}


def step(turn_id: str, text: str, *, kind: str = "work", ok: bool | None = None) -> None:
    """Append one line to a turn's feed.

    `kind` separates the phases the UI colours differently: "work" (searching, cloning, reading),
    "think" (the model composing), and "done". `ok` carries a tool's outcome, so a failed clone can
    be shown as failed rather than silently vanishing -- a failure is something the agent works
    around, and the learner seeing it explains why the turn is taking longer.
    """
    if not turn_id:
        return
    now = time.time()
    with _LOCK:
        entry = _TURNS.get(turn_id)
        if entry is None:
            # A step before start() shouldn't happen (routes.py calls start() before run_turn), but
            # losing progress entirely would be worse than creating the feed late.
            entry = _TURNS[turn_id] = {"label": "Thinking", "steps": [], "updated": now}
        entry["steps"].append({"text": text, "kind": kind, "ok": ok, "at": now})
        if len(entry["steps"]) > MAX_STEPS:
            del entry["steps"][: len(entry["steps"]) - MAX_STEPS]
        entry["updated"] = now


def annotate(turn_id: str, detail: str) -> None:
    """Attach a tool call's arguments to the step it just opened.

    seedagent reports a call in three pieces -- tool_start(name), tool_detail(args),
    tool_result(...) -- and one call is one line in the feed, not three. Without the arguments the
    step is too vague to be worth showing ("Cloning a repository..."), and with them it names the
    thing being worked on ("Cloning guillevc/yubal..."), which is what makes the wait legible.
    """
    if not turn_id or not detail:
        return
    with _LOCK:
        entry = _TURNS.get(turn_id)
        if entry and entry["steps"]:
            entry["steps"][-1]["detail"] = detail
            entry["updated"] = time.time()


def resolve(turn_id: str, *, ok: bool, summary: str = "") -> None:
    """Close the step opened by the matching tool_start, recording whether it worked."""
    if not turn_id:
        return
    with _LOCK:
        entry = _TURNS.get(turn_id)
        if entry and entry["steps"]:
            last = entry["steps"][-1]
            last["ok"] = ok
            last["done"] = True
            if not ok and summary:
                # Only kept when something went wrong: on success the summary is redundant with the
                # step's own text, and the feed is meant to be read at a glance.
                last["error"] = summary[:200]
            entry["updated"] = time.time()


def finish(turn_id: str) -> None:
    """Mark a turn's feed complete, so a poll that arrives after the response can tell the difference
    between "working" and "finished, this was the last thing it did"."""
    if not turn_id:
        return
    with _LOCK:
        entry = _TURNS.get(turn_id)
        if entry is None:
            return
        entry["done"] = True
        entry["updated"] = time.time()


def owns(turn_id: str, user_id: int | None) -> bool:
    """Whether this learner may watch this turn's feed. A turn with no recorded owner (an older entry,
    or one started without an identity) is nobody's, so it stays private to whoever ran it."""
    with _LOCK:
        entry = _TURNS.get(turn_id)
        if entry is None:
            return False
        return entry.get("owner") is not None and entry.get("owner") == user_id


def snapshot(turn_id: str, user_id: int | None = None) -> dict[str, Any] | None:
    """What the frontend polls: the turn's steps in order, plus whether it has finished.

    Passing a `user_id` restricts the answer to that learner's own turns; the feed names repositories
    and file paths, which is not something another signed-in learner should be able to read."""
    with _LOCK:
        entry = _TURNS.get(turn_id)
        if entry is None:
            return None
        if user_id is not None and entry.get("owner") not in (None, user_id):
            return None
        return {
            "steps": list(entry["steps"]),
            "done": bool(entry.get("done", False)),
        }
