"""Per-user limits on the endpoints that spend money.

Every turn here costs real API credit: a single instruction runs up to AGENT_MAX_STEPS model calls, and
the agent's own retry makes it three attempts. Sign-in gates *who* may ask, but nothing limited *how
often* -- one account could start turns back-to-back and burn the whole budget, and a retry loop in the
browser (the UI retries a failed page load) could do it by accident. See TODO.md.

The limits are deliberately coarse and in-process: a fixed window per (user, bucket), held in memory.
That is the right shape for one box, which is all this app can run (the progress feed and the session
locks are in-process too -- see the multi-instance note in TODO.md). When the app is scaled out, this
module's `allow()` must be backed by a shared store (Redis INCR+EXPIRE, or a Postgres row) or the limit
becomes per-replica and therefore N times looser.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# Model-spending requests per user per window. A learner needs a handful of turns in a 5-minute stretch
# (start, answer, submit, retry); this is generous for a person and far below what an abuser wants.
TURNS_PER_WINDOW = _int_env("LEARNCODE_TURNS_PER_WINDOW", 20)
# How many full turns may be *running* at once for one user. Separate from the rate limit: the rate
# limit is about total spend, this is about one account occupying every worker slot (the global cap is
# LEARNCODE_MAX_CONCURRENT_TURNS, default 4) and starving everybody else.
CONCURRENT_TURNS_PER_USER = _int_env("LEARNCODE_CONCURRENT_TURNS_PER_USER", 2)
WINDOW_SECONDS = _int_env("LEARNCODE_RATE_WINDOW_SECONDS", 300)


@dataclass
class _Window:
    started: float
    count: int = 0


_windows: dict[tuple[str, str], _Window] = {}
_running: dict[str, int] = {}
_guard = threading.Lock()


@dataclass
class _Slot:
    """A held concurrency slot. Released in a finally block, never by the caller remembering to."""
    user_id: str
    _released: bool = field(default=False, init=False)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        with _guard:
            n = _running.get(self.user_id, 0) - 1
            if n > 0:
                _running[self.user_id] = n
            else:
                _running.pop(self.user_id, None)


def _check_window(user_id: str, bucket: str, limit: int) -> None:
    now = time.monotonic()
    key = (user_id, bucket)
    with _guard:
        # Opportunistic sweep: a dict of one small entry per active user is fine, but a long-running
        # process would keep an entry for every user who ever signed in. Cheap because it only runs
        # when a window has expired, and the dict is tiny.
        if len(_windows) > 4096:
            for k in [k for k, w in _windows.items() if now - w.started >= WINDOW_SECONDS]:
                _windows.pop(k, None)
        w = _windows.get(key)
        if w is None or now - w.started >= WINDOW_SECONDS:
            _windows[key] = _Window(started=now, count=1)
            return
        if w.count >= limit:
            retry = max(1, int(WINDOW_SECONDS - (now - w.started)))
            raise HTTPException(
                429,
                f"Too many requests -- try again in {retry} seconds.",
                headers={"Retry-After": str(retry)},
            )
        w.count += 1


def acquire_turn_slot(user_id: str) -> _Slot:
    """Count one model-spending request against the user's window and take a running slot for it.

    Raises 429 if either limit is hit. The caller MUST release the returned slot in a finally block,
    or that user's next turns will be refused 429 forever (nothing else can free it)."""
    _check_window(user_id, "turns", TURNS_PER_WINDOW)
    with _guard:
        now_running = _running.get(user_id, 0)
        if now_running >= CONCURRENT_TURNS_PER_USER:
            raise HTTPException(
                429,
                "You already have the maximum number of turns running -- wait for one to finish.",
                headers={"Retry-After": "30"},
            )
        _running[user_id] = now_running + 1
    return _Slot(user_id)


def reset() -> None:
    """Forget every window and running slot. Test hook."""
    with _guard:
        _windows.clear()
        _running.clear()
