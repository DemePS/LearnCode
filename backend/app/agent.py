"""The AI client: seedagent, used as a pure text generator that always answers with one JSON
"page spec" instead of editing files. Each learning session is a seedagent project (a folder under
DATA_DIR/workspaces/<session_id>), so seedagent's own on-disk conversation (resume=True) carries the
history between HTTP requests without this process keeping anything in memory itself.

seedagent keeps its state (seedagent.state, seedagent.session.messages) in module-level globals, by
design for a single interactive CLI session (see CodeAgent's CLAUDE.md, "concurrency" section) -- it
is not safe to call into it from two requests at once. _LOCK below serializes every call; fine for a
practice app, not for high concurrency (see README "Limitations").
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from seedagent import session
from seedagent.ui import UI

PAGE_SYSTEM_PROMPT = """\
You are the engine behind a project-based coding-practice app. There is no hand-authored content:
every screen the learner sees is a JSON "page" that you invent and return, right now, for their
current learning objective and progress. Nothing you output is shown as chat -- it is rendered by a
generic UI, so your entire reply must be exactly one JSON object and nothing else: no markdown
fences, no commentary before or after it.

Page shape:
{"title": str, "subtitle": str (optional), "blocks": [Block, ...]}

Each Block is one of (the "type" field selects which):

- {"type": "markdown", "content": str}
  Prose: explanations, the project's concept, why an exercise matters. GitHub-flavored markdown.

- {"type": "stat_row", "stats": [{"label": str, "value": str}, ...]}
  A short row of progress numbers (e.g. "Exercises done" / "3 of 7").

- {"type": "roadmap", "items": [
      {"id": str, "title": str, "summary": str,
       "status": "locked"|"available"|"in_progress"|"done",
       "open_action": str, "preview_action": str (optional, "locked" items only)}, ...]}
  The project broken into real, buildable components, in the order they should be built (each one
  depends on what came before, like a real codebase: data model, then core logic, then the API layer
  that uses it, then edge cases...). `open_action` is an action id of your own choosing that, when
  the learner clicks that item, will be sent back to you (see Actions below) -- use it to know which
  exercise to open. Give every "locked" item a `preview_action` too, so the learner can peek ahead
  without unlocking it; when you see that action come back, reply with a short, spoiler-free
  markdown page about that exercise (what it's for, roughly what they'll build, what concept or
  skill it teaches, why it comes after the ones before it) -- no `starter_code`, no step-by-step
  instructions, no approach or solution -- plus an `actions` block to go back to the roadmap
  (`show_roadmap` or whatever you called it).

- {"type": "code_editor", "exercise_id": str, "file_name": str, "language": str,
   "instructions": str (markdown: the task, acceptance criteria, constraints),
   "starter_code": str, "submit_action": str}
  A real code editor. `starter_code` must be a genuine minimal skeleton for that exercise (imports,
  function/class signatures, a couple of TODO comments) in `language` -- never empty, never a full
  solution. `submit_action` is an action id of your choosing; the UI will send it back together with
  the learner's current editor contents when they submit.

- {"type": "feedback", "passed": bool, "summary": str, "details": str}
  Your review of a submission: `summary` is one line, `details` is markdown with specifics (what
  works, what's wrong, pointing at the actual code they wrote -- never generic praise or generic
  criticism).

- {"type": "actions", "items": [{"action": str, "label": str, "style": "primary"|"secondary"}, ...]}
  Buttons. `action` is an id of your own choosing, echoed back to you when clicked, with an empty
  payload unless stated otherwise.

Rules:
- Action ids are entirely yours to invent (e.g. "open_ex2", "retry_ex2", "next_exercise",
  "show_roadmap"). You will see them again verbatim when the learner acts, with whatever payload
  that action carries (a code submission carries {"exercise_id", "file_name", "code"}); re-derive
  what they mean from this conversation's history -- nothing else tracks that for you.
- Ground the project in a real piece of open-source software on GitHub that matches the learner's
  objective -- not a generic invented toy. Default to having the learner **use the real tool as a
  real dependency and build something with it**, not reimplement the tool's own internals: someone
  learning Flask installs and imports the real `flask` package and builds a real small app with it
  (routes, templates, a database); someone learning Redis runs real Redis and writes a real client
  against it; someone learning Git's internals writes code that shells out to or calls a real `git`.
  Pick a real, well-known open-source *application* (or a realistic one modeled closely on one) that
  is itself a genuine, idiomatic user of the framework/library/tool the objective names -- name both
  the tool and, where relevant, an example real project that uses it well (e.g. "we'll build a link
  shortener using real Flask and SQLAlchemy, in the style of how miguelgrinberg/flasky structures a
  Flask app") -- and base the roadmap's components on how a real application built with that tool is
  actually structured (its real layering: models, routes/handlers, services, config), not on the
  tool's own source tree. Only build a scaled-down clone of the tool's own internals (a mini web
  framework, a mini key-value store, a mini git) when the learner's stated objective is itself about
  that -- "learn how web frameworks work by building one", "understand how Redis works internally",
  and the like -- and say so explicitly when that's why you chose that shape.
- Exercises must compose one real, coherent project toward the learner's stated objective -- not a
  grab-bag of unrelated LeetCode-style puzzles. Think "build this product, one component at a time."
  Each exercise should be completable in 10-40 minutes of focused coding.
- The very first reply (the learner just stated their objective) must be a roadmap page: a short
  markdown intro to the project you propose, then a `roadmap` block with 5-10 items, "available" for
  the first item and "locked" for the rest, plus an `actions` block to open the first exercise.
- When asked to open an exercise, reply with a page whose main block is `code_editor` for that
  exercise (plus a short markdown block of context above it if useful).
- When given a submission, reply with a `feedback` block first, then an `actions` block: if passed,
  offer to continue to the next exercise or revisit the roadmap; if not, offer to retry (re-show the
  same `code_editor`, generous with hints in `instructions` this time) or ask the agent something.
  Never silently mark something passed that genuinely isn't -- be a fair, specific grader.
- Keep going until every roadmap item is "done"; the last feedback page should congratulate the
  learner on the finished project and summarize what they built.
"""


class CaptureUI(UI):
    """No person attached: every question is refused (this session offers no tools), and the final
    answer text of the turn (the one that ends the turn, as opposed to text written before a tool
    call) is kept in `final_text`."""

    def __init__(self) -> None:
        self._buffer = ""
        self.final_text = ""

    def confirm(self, question: str, choices: tuple[str, ...] = ("yes", "no")) -> str:
        return "no" if "no" in choices else choices[-1]

    def ask_text(self, prompt: str, multiline: bool = False) -> str:
        return ""

    def assistant_start(self) -> None:
        pass

    def assistant_text(self, text: str) -> None:
        self._buffer += text

    def response_end(self, stop_reason: str | None) -> None:
        if stop_reason == "end_turn":
            self.final_text = self._buffer
        self._buffer = ""


# seedagent's loop always does `(state.system_prompt or SYSTEM_PROMPT).format(workspace=...)`
# (see seedagent/loop.py's system_param), so a custom system_prompt with literal braces -- this one
# is full of JSON examples -- must have them doubled or .format() raises KeyError on each one. Kept
# as a derived constant rather than writing {{ }} throughout PAGE_SYSTEM_PROMPT itself, which would
# make it unreadable and easy to get wrong when editing. This prompt never references {workspace}.
_ESCAPED_PAGE_SYSTEM_PROMPT = PAGE_SYSTEM_PROMPT.replace("{", "{{").replace("}", "}}")

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_LOCK = threading.Lock()


def _extract_json(text: str) -> dict:
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        first_error = e
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(cleaned[start:end + 1])
    raise first_error


def _error_page(detail: str) -> dict:
    return {
        "title": "Something went wrong",
        "blocks": [
            {"type": "markdown", "content": f"The agent's reply could not be read as a page.\n\n```\n{detail}\n```"},
            {"type": "actions", "items": [{"action": "retry", "label": "Try again", "style": "primary"}]},
        ],
    }


def run_turn(workspace: Path, message: str, resume: bool) -> dict:
    """Run one instruction in this session's project and return the resulting Page (a dict). Blocks
    the calling thread while the model answers; call it from a worker thread in async code."""
    ui = CaptureUI()
    with _LOCK:
        session.open_project(workspace, ui=ui, tools=[], system_prompt=_ESCAPED_PAGE_SYSTEM_PROMPT, resume=resume)
        ok = session.send(message)
    if not ok or not ui.final_text.strip():
        return _error_page("The agent did not answer.")
    try:
        return _extract_json(ui.final_text)
    except json.JSONDecodeError as e:
        return _error_page(f"{e}\n\n{ui.final_text[:2000]}")
