"""The two validation tools: check a page's JSON before finishing the turn.

Registering these as tools (rather than validating silently in run_turn) is what lets the model fix its
own output: a validation failure comes back as a tool result it reads while the turn is still live, so
it can re-emit corrected JSON. Validating only at the end would turn a fixable mistake into a page the
learner sees broken -- or, worse, into exercises stored malformed and served broken forever after.

The model passes the JSON as its argument. Accepting it as a string (as well as an object) matters:
models routinely hand a tool the serialised blob, and refusing that would make the tool useless exactly
when it is needed.
"""

import json

from seedagent.common import ToolError

from .page_schema import _report, validate_exercises, validate_roadmap_page


def _as_dict(payload, tool: str) -> dict:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            raise ToolError(
                f"That is not valid JSON ({e}), so it cannot be checked. Pass the object itself, or a "
                f"JSON string that parses."
            )
    if not isinstance(payload, dict):
        raise ToolError(f"{tool} takes the page as an object (or a JSON string of one).")
    return payload


def tool_validate_json_roadmap(json: dict | str) -> str:
    """Check the roadmap page JSON against the page contract before finishing the turn."""
    payload = _as_dict(json, "validate_json_roadmap")
    return _report(validate_roadmap_page(payload), "The roadmap")

def tool_validate_json_exercises(json: dict | list | str, roadmap: dict | str | None = None) -> str:
    """Check the exercises JSON (and its links to the roadmap) before finishing the turn."""
    if isinstance(json, list):
        payload: dict = {"exercises": json}
    else:
        payload = _as_dict(json, "validate_json_exercises")
        if isinstance(payload.get("exercises"), list):
            pass
        elif "blocks" in payload:
            # A single exercise page passed on its own, which is the obvious thing to try.
            payload = {"exercises": [payload]}
        else:
            raise ToolError(
                "Pass the exercises as a list, or the whole {\"roadmap\": ..., \"exercises\": [...]} "
                "reply."
            )
    road = _as_dict(roadmap, "validate_json_exercises") if isinstance(roadmap, (str, dict)) else None
    # If the caller passed the whole reply as `json`, its roadmap is right there: use it rather than
    # demanding the same object a second time.
    if road is None and isinstance(payload.get("roadmap"), dict):
        road = payload["roadmap"]
    return _report(validate_exercises(payload, road), "The exercises", plural=True)


ROADMAP_SCHEMA = {
    "name": "validate_json_roadmap",
    "description": (
        "Check the roadmap page you are about to return, before you finish the turn. It reports any "
        "block that does not match the page contract (a missing field, a wrong type, an unknown block "
        "type, a language id the editor cannot highlight) and any roadmap whose item ids or "
        "open_actions are not unique. Call this on every roadmap you write, then fix what it reports "
        "and call it again until it says the roadmap is valid."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "json": {
                "description": "The roadmap page object, e.g. {\"title\": ..., \"blocks\": [...]}.",
                "type": ["object", "string"],
            },
        },
        "required": ["json"],
    },
}

EXERCISES_SCHEMA = {
    "name": "validate_json_exercises",
    "description": (
        "Check the exercises you generated alongside the roadmap, before you finish the turn. It "
        "reports any exercise that does not match the page contract, any exercise with no code_editor "
        "or an empty starter_code, any exercise with no open_action, and any mismatch between the "
        "roadmap's items and the exercises: an item with no exercise can never be opened, and an "
        "exercise no item points at can never be reached. Call this on every exercise set you write, "
        "then fix what it reports and call it again until it says the exercises are valid."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "json": {
                "description": "The exercises as a list, or the whole {\"roadmap\": ..., \"exercises\": "
                               "[...]} reply. The roadmap is read from it when present, so the links "
                               "can be checked too.",
                "type": ["object", "array", "string"],
            },
            "roadmap": {
                "description": "Optional: the roadmap page, when the exercises were passed on their own.",
                "type": ["object", "string"],
            },
        },
        "required": ["json"],
    },
}
