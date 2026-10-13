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
import random
import re
import threading
from pathlib import Path

from seedagent import session
from seedagent.config import _get_client, get_model
from seedagent.ui import UI

PAGE_SYSTEM_PROMPT = """\
You are the engine behind a project-based coding-practice app. There is no hand-authored content:
every screen the learner sees is a JSON "page" that you invent and return, right now, for their
current learning objective and progress. Nothing you output is shown as chat -- it is rendered by a
generic UI, so your entire reply must be exactly one JSON object and nothing else: no markdown
fences, no commentary before or after it -- EXCEPT for a side question from the floating help
widget (the message will say so explicitly): that is not a page navigation, so reply to it in plain
markdown prose only, no JSON, nothing about page structure. See "Side questions" under Rules below.

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
       "open_action": str}, ...]}
  The project broken into real, buildable components, in the order they should be built (each one
  depends on what came before, like a real codebase: data model, then core logic, then the API layer
  that uses it, then edge cases...). `status` is informational only -- progress and the recommended
  order, shown so the learner knows where they are and what's suggested next -- it is never a gate:
  every item's `open_action` must work regardless of status, "locked" included, because the learner
  is free to explore or jump ahead even though doing things in order is encouraged. `open_action` is
  an action id of your own choosing that, when the learner clicks that item, is sent back to you (see
  Actions below) -- use it to know which exercise to open. When they open one out of the recommended
  order, just open it normally (the full `code_editor`); only add a brief, friendly heads-up in the
  markdown above it if it genuinely depends on something from an earlier exercise they haven't
  finished yet -- never refuse or redirect them back to the roadmap instead.

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
  learning a web framework installs and imports the real package and builds a real small app with it
  (routes, templates, a database); someone learning a database runs the real thing and writes a real
  client against it; someone learning a VCS's internals writes code that shells out to or calls the
  real CLI. Pick a real, well-known open-source *application* (or a realistic one modeled closely on
  one) that is itself a genuine, idiomatic user of the tool the objective names -- name both the tool
  and, where it helps, a real project that uses it well -- and base the roadmap's components on how a
  real application built with that tool is actually structured (its real layering: models,
  routes/handlers, services, config), not on the tool's own source tree. Only build a scaled-down
  clone of the tool's own internals (a mini web framework, a mini key-value store, a mini git) when
  the learner's stated objective is itself about that -- "learn how web frameworks work by building
  one", "understand how a database works internally", and the like -- and say so explicitly when
  that's why you chose that shape.
- Vary what you build. The same or a similar objective, asked by different learners (or asked again),
  should not keep landing on the same single "obvious" project -- genuinely consider a few different
  real applications that would exercise the objective well (different domains, different scales,
  different angles on the same tool) before picking one, and actively avoid defaulting to whichever
  example is most clichéd or most likely to be the very first thing that comes to mind for that
  theme. Let this session's own variety seed (given in the learner's message, if any) nudge which of
  several good candidates you land on -- it's there to help you pick differently, not to be mentioned
  to the learner or to override fit with their actual objective.
- Exercises must compose one real, coherent project toward the learner's stated objective -- not a
  grab-bag of unrelated LeetCode-style puzzles. Think "build this product, one component at a time."
  Each exercise should be completable in 10-40 minutes of focused coding.
- The very first reply (the learner just stated their objective) must be a roadmap page: a short
  markdown intro to the project you propose, then a `roadmap` block with 5-10 items, "available" for
  the first item and "locked" for the rest, plus an `actions` block to open the first exercise.
- When asked to open an exercise, reply with a page with exactly one `code_editor` block for that
  exercise (plus a short markdown block of context above it if useful) -- never two `code_editor`
  blocks on the same page, even if the exercise touches more than one file; if it genuinely needs
  more than one file, say so in the markdown and pick the single file that is this exercise's real
  focus, or split it into two roadmap items instead.
- When given a submission, reply with a `feedback` block first, then an `actions` block: if passed,
  offer to continue to the next exercise or revisit the roadmap; if not, offer to retry (re-show the
  same `code_editor`, generous with hints in `instructions` this time) or ask the agent something.
  Never silently mark something passed that genuinely isn't -- be a fair, specific grader.
- Keep going until every roadmap item is "done"; the last feedback page should congratulate the
  learner on the finished project and summarize what they built.

Side questions (floating help widget): the learner can ask a question or request a hint at any
time, from wherever they currently are, without navigating away or losing their in-progress code --
the message will tell you this is happening and give you their current exercise and code as context
when there is one. For this one case only, do not return page JSON: answer directly in plain
markdown prose, a few sentences to a short paragraph, referencing their actual code/situation rather
than generic advice. Give a hint, a nudge in the right direction, or an explanation of a concept --
never the full solution code, unless they explicitly ask you to just give them the answer outright.
"""


CLARIFY_SYSTEM_PROMPT = """\
You help plan a project-based coding-practice exercise, before any exercises exist yet. Given a
learner's stated objective, and any clarifying questions already asked and answered, decide: do you
need to ask ONE more short, concrete question before you could design a genuinely good, focused
project roadmap? Ask only when the objective is ambiguous in a way that would change what gets built
-- no language or stack given and it matters, "a chat app" with very different reasonable readings,
a scope so broad you'd otherwise have to guess at what matters most to them. Most objectives don't
need this: when in doubt, don't ask -- you can make a reasonable assumption instead and mention it
later when you design the roadmap.

Reply with exactly one JSON object, nothing else:
{"ready": true} -- you have enough to design a good roadmap now
{"ready": false, "question": "..."} -- one short question, the next (and only the next) one to ask
"""


def clarify(objective: str, history: list[dict]) -> dict:
    """A lightweight, stateless call -- seedagent's own Anthropic client and model (the same
    get_model()/_get_client() its own session.check_connection() uses for a plain ping), but no
    agent loop, no tools, no project workspace -- used before any seedagent "project" exists, to
    decide whether to ask the learner one more clarifying question or hand off to run_turn to design
    the roadmap. `history` is the growing list of {"question": str, "answer": str} already asked;
    the caller (routes.py) keeps it (nothing this short-lived needs its own persisted conversation).
    Returns {"ready": True} or {"ready": False, "question": str}; fails open to {"ready": True} if
    the model's reply can't be read, so a hiccup here never blocks a learner from starting."""
    parts = [f"Learning objective: {objective}"]
    for turn in history:
        parts.append(f"Q: {turn['question']}\nA: {turn['answer']}")
    try:
        response = _get_client().messages.create(
            model=get_model(),
            max_tokens=300,
            system=CLARIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
        )
    except Exception:
        return {"ready": True}
    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        result = _extract_json(text)
    except json.JSONDecodeError:
        return {"ready": True}
    if result.get("ready") is True:
        return {"ready": True}
    question = result.get("question")
    if not isinstance(question, str) or not question.strip():
        return {"ready": True}
    return {"ready": False, "question": question.strip()}


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

# Wording alone ("vary what you build") doesn't reliably stop a model from converging on the same
# single "obvious" project for a given theme -- a learner reported exactly that (same theme, same
# roadmap, every time). Pairing the instruction with an actual per-call random seed and a concrete
# random angle gives the sampling something real to condition on, which is much more effective than
# an instruction alone. Used only when designing the initial roadmap (variety_hint(), called from
# routes.py's /start) -- later turns (opening an exercise, grading a submission) don't need it.
_VARIETY_ANGLES = [
    "a personal/indie tool a solo developer would build for themselves",
    "a small internal tool a team would build for their own workflow",
    "an unusual or less typical domain for this theme, not the first example that comes to mind",
    "a creative or playful take rather than the most businesslike one",
    "a tool for a specific niche audience rather than a generic one",
    "something data- or content-heavy rather than CRUD-heavy",
    "a tool that automates a tedious manual task",
    "something aimed at hobbyists or a specific community",
]


def variety_hint() -> str:
    """A line to append to the message that kicks off a new roadmap, so repeated or similar
    objectives don't keep landing on the same project (see the "Vary what you build" rule in
    PAGE_SYSTEM_PROMPT, which tells the model how to use this)."""
    seed = random.randint(1000, 9999)
    angle = random.choice(_VARIETY_ANGLES)
    return f"(Variety seed: {seed}. Lean toward: {angle}.)"


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


def ask(workspace: Path, question: str, context: dict) -> str:
    """The floating help-widget side channel: answered in the same session (so it has the full
    conversation -- what project, what exercise, what's already been submitted) but, per the "Side
    questions" rule in PAGE_SYSTEM_PROMPT, as plain prose rather than a page -- so the answer can be
    shown alongside whatever is currently on screen (an in-progress code_editor, say) without
    replacing it or losing unsaved edits, unlike run_turn's page swap."""
    message = (
        "The learner asked a question via the floating help widget. This is a side question, not a "
        "page navigation: answer it in plain markdown prose only, per the \"Side questions\" rule -- "
        "do not return page JSON, and nothing about their current screen should need to change.\n\n"
        f"Question: {question}\n\n"
        f"Context (their current exercise/code, if any): {json.dumps(context)}"
    )
    ui = CaptureUI()
    with _LOCK:
        session.open_project(workspace, ui=ui, tools=[], system_prompt=_ESCAPED_PAGE_SYSTEM_PROMPT, resume=True)
        ok = session.send(message)
    if not ok or not ui.final_text.strip():
        return "Sorry, I couldn't get an answer just now -- try again in a moment."
    return ui.final_text.strip()
