"""Validate the page JSON a turn returns, before it is stored and shown.

The prompt describes the page contract in prose and nothing enforced it: a malformed block reached the
frontend, which silently skipped it, so a broken exercise and a good one looked the same from the
server side. These models are that contract as data, so the model can be told what is wrong while it
still has the turn in hand, and re-emit before the learner ever sees it.

Two tools wrap them (see agent.py): `validate_roadmap` checks the roadmap page, `validate_exercises`
checks the exercises generated with it. Both are offered to the model as tools so validation happens
inside the turn -- a validation error is a tool result it reads and fixes, not a failed turn.

Only the structures the prompt actually specifies are required. Unknown block types are rejected
(the frontend's renderer has no branch for them), unknown *fields* are ignored rather than rejected,
so a model that adds a helpful extra key is not punished for it.
"""

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Monaco ids only, because the editor highlights what it knows and renders anything else as uncoloured
# plain text. "bash"/"zsh"/"sh" are the common mistake here: they are not ids.
MONACO_LANGUAGES = {
    "python", "javascript", "typescript", "shell", "dockerfile", "json", "yaml", "markdown",
    "sql", "go", "rust", "java", "kotlin", "csharp", "cpp", "c", "php", "ruby", "html", "css",
    "scss", "graphql", "plaintext",
}

# The UI's own persistent button, outside any page. Reserved because the learner's "Back to roadmap"
# always sends exactly this, whether or not the page has a button carrying it.
RESERVED_ACTION = "show_roadmap"

# Action ids that look like asking for help rather than moving through the project. The learner has a
# dedicated "Ask AI" widget on screen at all times, so a page button offering a hint or a chat duplicates
# it, and the prompt already forbids it -- this is what makes the rule enforceable rather than advisory.
CHAT_ACTION_WORDS = ("hint", "help", "ask", "chat", "question", "explain", "clarify", "talk",
                     "solution", "answer")


class _Strict(BaseModel):
    """Reject unknown fields in the small leaf models only, where an added key is always a mistake."""

    model_config = ConfigDict(extra="forbid")


class Markdown(_Strict):
    type: Literal["markdown"]
    content: str


class Stat(_Strict):
    label: str
    value: str


class StatRow(_Strict):
    type: Literal["stat_row"]
    stats: list[Stat] = Field(min_length=1)


class RoadmapItem(_Strict):
    id: str
    title: str
    summary: str
    status: Literal["locked", "available", "in_progress", "done"]
    open_action: str


class Roadmap(_Strict):
    type: Literal["roadmap"]
    items: list[RoadmapItem] = Field(min_length=1)


class CodeEditor(_Strict):
    type: Literal["code_editor"]
    exercise_id: str
    file_name: str
    language: str
    instructions: str
    starter_code: str = Field(min_length=1)
    submit_action: str


class Feedback(_Strict):
    type: Literal["feedback"]
    passed: bool
    summary: str
    details: str


class ActionItem(_Strict):
    """A button the learner can press.

    `action` is the whole contract: the agent invents the id and gets it back verbatim, and the UI decides
    what to write on the button from what that id turns out to mean. `label` is accepted for pages stored
    before the split (and ignored when rendering) -- the agent is no longer asked for one, because the
    button's wording is interface text: letting the model write it is how a retry button came back as
    "Retry exercise 6 with the worked shape", promising the solution the learner should be finding."""

    action: str
    label: str | None = None
    style: Literal["primary", "secondary"] = "secondary"


class Actions(_Strict):
    type: Literal["actions"]
    items: list[ActionItem] = Field(min_length=1)


Block = Annotated[
    Union[Markdown, StatRow, Roadmap, CodeEditor, Feedback, Actions],
    Field(discriminator="type"),
]


class Page(BaseModel):
    """A page as the frontend renders it. Extra keys are allowed here: "open_action" rides alongside an
    exercise's own page dict so the roadmap can link to it, and a model may add other harmless ones."""

    model_config = ConfigDict(extra="ignore")

    title: str
    subtitle: str | None = None
    blocks: list[Block] = Field(min_length=1)
    open_action: str | None = None


def chat_like_actions(page: Page) -> list[str]:
    """Action ids that hand the learner help instead of moving them onward.

    Only the id is judged, because the id is the whole of what the agent is responsible for: the button's
    wording belongs to the frontend, which derives it from the action (see ActionItem). A hint button
    written as "Show me the solution" is caught by its id -- a label can no longer smuggle an offer past
    this check, because the agent no longer writes labels."""
    found = []
    for block in page.blocks:
        if isinstance(block, Actions):
            for item in block.items:
                if _has_chat_word(item.action):
                    found.append(item.action)
    return found


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def _has_chat_word(text: str) -> bool:
    return any(w in CHAT_ACTION_WORDS for w in _words(text))


def _chat_problem(actions: list[str]) -> list[str]:
    if not actions:
        return []
    return [f"  actions contain {', '.join(repr(a) for a in actions)}, which offer help rather than moving "
            f"the learner through the project. There is already an \"Ask AI\" button on screen at all "
            f"times, and the explanation belongs in the feedback details: a button that promises the "
            f"answer tells the learner to click instead of think."]


def _describe_blocks(e: ValidationError, page_index: int | None = None) -> list[str]:
    """Turn pydantic's errors into lines that name the path, which is what makes them fixable."""
    where = f"page[{page_index}]" if page_index is not None else "page"
    out = []
    for err in e.errors():
        # loc is like ("blocks", 2, "code_editor", "language") or ("blocks", 1) when the block's own
        # "type" tag is unknown. Both are reported against the field path as the model wrote it.
        loc = [str(part) for part in err["loc"]]
        if err["type"] == "union_tag_invalid":
            tag = err.get("input", {}).get("type") if isinstance(err.get("input"), dict) else None
            out.append(f"  {where}.blocks[{loc[1]}] has type {tag!r}, which is not a block type. "
                       f"Valid types: markdown, stat_row, roadmap, code_editor, feedback, actions.")
            continue
        out.append(f"  {where}.{'.'.join(loc)}: {err['msg']}")
    return out


def validate_roadmap_page(payload: dict) -> list[str]:
    """Problems with the roadmap page, as short lines. Empty means it is valid.

    `payload` may be the raw {"roadmap": ..., "exercises": [...]} reply as well as the page alone, so a
    model that passes the whole turn's reply gets it judged rather than told the shape is wrong."""
    candidate = payload.get("roadmap") if isinstance(payload, dict) else None
    if not isinstance(candidate, dict):
        candidate = payload
    problems: list[str] = []
    try:
        page = Page.model_validate(candidate)
    except ValidationError as e:
        problems.extend(_describe_blocks(e))
        # A block that failed to parse cannot be inspected below (no typed object exists for it), but
        # its siblings can, so fall back to the parts of the payload that are shaped as expected and
        # report every problem in one pass rather than one problem per turn.
        page = None
        for index, block in enumerate(candidate.get("blocks", [])
                                      if isinstance(candidate.get("blocks"), list) else []):
            if isinstance(block, dict) and block.get("type") == "code_editor":
                language = block.get("language")
                if isinstance(language, str) and language not in MONACO_LANGUAGES:
                    problems.append(_language_problem(language, f"page.blocks[{index}]"))
        for block in candidate.get("blocks", []) if isinstance(candidate.get("blocks"), list) else []:
            if isinstance(block, dict) and block.get("type") == "roadmap":
                problems.extend(_roadmap_item_problems(block.get("items")))
        return problems

    roadmaps = [b for b in page.blocks if isinstance(b, Roadmap)]
    if not roadmaps:
        problems.append('  The roadmap page has no block of type "roadmap": the learner has nothing '
                        "to click, so no exercise can ever be opened.")
    for block in roadmaps:
        problems.extend(_roadmap_item_problems(block.items))
    for index, block in enumerate(page.blocks):
        if isinstance(block, CodeEditor) and block.language not in MONACO_LANGUAGES:
            problems.append(_language_problem(block.language, f"page.blocks[{index}]"))
    problems.extend(_chat_problem(chat_like_actions(page)))
    return problems


def _language_problem(language: str, where: str = "") -> str:
    prefix = f"{where} " if where else ""
    return (f"  {prefix}language {language!r} is not a Monaco language id, so it will render "
            f"uncoloured. Use one of: {', '.join(sorted(MONACO_LANGUAGES))}.")


def _roadmap_item_problems(items) -> list[str]:
    """Duplicate ids and open_actions, from either parsed items or raw dicts."""
    out = []
    for field_name in ("id", "open_action"):
        values = [(getattr(i, field_name, None) if not isinstance(i, dict) else i.get(field_name))
                  for i in items]
        values = [v for v in values if isinstance(v, str)]
        dupes = sorted({v for v in values if values.count(v) > 1})
        if dupes:
            out.append(f"  Roadmap item {field_name}s are not unique: {', '.join(dupes)}. Each exercise "
                       f"is looked up by its open_action, so these cannot be told apart.")
    return out


def validate_exercises(payload: dict, roadmap: dict | None = None) -> list[str]:
    """Problems with the exercises generated alongside a roadmap, or with how they link to it.

    When `roadmap` is given (the parsed roadmap page), the link is checked in both directions: every
    roadmap item must have an exercise that opens it, and every exercise must be reachable from an item.
    An exercise that no item points at can never be opened, and an item with no exercise becomes a
    button that runs a needless turn."""
    problems: list[str] = []
    listed = payload.get("exercises") if isinstance(payload, dict) else None
    if not isinstance(listed, list) or not listed:
        return ["  The reply has no \"exercises\" list. The roadmap's items would then have no exercise "
                "to open, and clicking one would have to run a whole new turn."]

    seen: dict[str, int] = {}
    for index, exercise in enumerate(listed):
        if not isinstance(exercise, dict):
            problems.append(f"  page[{index}] is not an object.")
            continue
        open_action = exercise.get("open_action")
        if not isinstance(open_action, str) or not open_action.strip():
            problems.append(f"  page[{index}] ({exercise.get('title', 'untitled')!r}) has no "
                            f"\"open_action\", so the roadmap cannot link to it and it can never be "
                            f"opened. Set it to the matching roadmap item's open_action.")
        else:
            seen.setdefault(open_action.strip(), index)
        try:
            page = Page.model_validate(exercise)
        except ValidationError as e:
            problems.extend(_describe_blocks(e, index))
            continue
        editors = [b for b in page.blocks if isinstance(b, CodeEditor)]
        if not editors:
            problems.append(f"  page[{index}] ({page.title!r}) has no \"code_editor\" block, so there "
                            f"is nothing for the learner to write code in.")
        for block in editors:
            if not block.starter_code.strip():
                problems.append(f"  page[{index}] ({page.title!r}) has an empty starter_code; it must "
                                f"be a real minimal skeleton, never empty.")
            if block.language not in MONACO_LANGUAGES:
                problems.append(_language_problem(block.language, f"page[{index}]"))
        if editors and any(isinstance(b, Actions) for b in page.blocks):
            problems.append(f"  page[{index}] ({page.title!r}) has an \"actions\" block on an exercise "
                            f"page. The learner presses the editor's own Submit button; the roadmap is "
                            f"already reachable from the persistent button outside the page, and nothing "
                            f"can be retried before they have submitted. Remove it -- buttons belong on "
                            f"the roadmap page and on the feedback page.")
        problems.extend(_chat_problem(chat_like_actions(page)))

    if isinstance(roadmap, dict):
        items = [
            item
            for block in roadmap.get("blocks", [])
            if isinstance(block, dict) and block.get("type") == "roadmap"
            for item in block.get("items", [])
            if isinstance(item, dict)
        ]
        wanted = [item.get("open_action") for item in items if isinstance(item.get("open_action"), str)]
        for action in wanted:
            if action not in seen:
                problems.append(f"  Roadmap item with open_action {action!r} has no exercise in the "
                                f"list, so clicking it would have to run a whole new turn.")
        titles = {item.get("open_action"): item.get("title") for item in items}
        for action in seen:
            if wanted and action not in wanted:
                problems.append(f"  The exercise for open_action {action!r} matches no roadmap item. "
                                f"Nothing can open it. Roadmap open_actions are: "
                                f"{', '.join(repr(w) for w in wanted)}.")
    return problems


def _report(problems: list[str], what: str, *, plural: bool = False) -> str:
    verb = "are" if plural else "is"
    if not problems:
        return f"{what} {verb} valid."
    return (f"{what} {verb} not valid. Fix these and return the corrected JSON:\n"
            + "\n".join(problems))
