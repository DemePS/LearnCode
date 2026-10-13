"""Dev-only helper: monkeypatches app.agent's Anthropic client so the FastAPI app can run (and be
browser-tested) without a live ANTHROPIC_API_KEY. Not part of the shipped app.

agent.py no longer goes through seedagent.session (see its module docstring), so there's no
FakeSession to patch -- instead this stubs out the low-level pieces agent.py itself calls:
_get_client() (the Anthropic client), get_model(), and clarify() (used before a project exists).
"""

import json

from app import agent


def fake_clarify(objective, history):
    if not history:
        return {"ready": False, "question": "Which language do you want to use?"}
    return {"ready": True}


class FakeMessages:
    def create(self, **kwargs):
        user_text = kwargs["messages"][-1]["content"]

        if "open_ex1" in user_text:
            page = {
                "title": "Exercise 1: Data model",
                "blocks": [
                    {
                        "type": "code_editor",
                        "exercise_id": "ex1",
                        "file_name": "models.py",
                        "language": "python",
                        "instructions": "Define a `Link` dataclass with `original_url: str` and `code: str`.",
                        "starter_code": "from dataclasses import dataclass\n\n\nclass Link:\n    pass\n",
                        "submit_action": "submit_ex1",
                    },
                ],
            }
        elif "submit_ex1" in user_text:
            page = {
                "title": "Feedback on exercise 1",
                "blocks": [
                    {
                        "type": "feedback",
                        "passed": True,
                        "summary": "Looks good!",
                        "details": "Your `Link` dataclass has the right fields.",
                    },
                    {
                        "type": "actions",
                        "items": [{"action": "open_ex2", "label": "Next exercise", "style": "primary"}],
                    },
                ],
            }
        else:
            page = {
                "title": "Build a tiny URL shortener",
                "subtitle": "A scaled-down version of shlink/shlink",
                "blocks": [
                    {
                        "type": "markdown",
                        "content": "We'll build a small clone of **shlink/shlink**, a real open-source "
                        "URL shortener, one real component at a time.",
                    },
                    {
                        "type": "stat_row",
                        "stats": [{"label": "Exercises done", "value": "0 of 2"}],
                    },
                    {
                        "type": "roadmap",
                        "items": [
                            {
                                "id": "ex1",
                                "title": "Data model",
                                "summary": "Define the Link record (original_url, short code).",
                                "status": "available",
                                "open_action": "open_ex1",
                            },
                            {
                                "id": "ex2",
                                "title": "Base62 encoder",
                                "summary": "Turn an integer id into a short code.",
                                "status": "locked",
                                "open_action": "open_ex2",
                            },
                        ],
                    },
                    {
                        "type": "actions",
                        "items": [{"action": "open_ex1", "label": "Start exercise 1", "style": "primary"}],
                    },
                ],
            }

        text = json.dumps(page)
        block = type("Block", (), {"type": "text", "text": text})()
        return type("Response", (), {"content": [block], "stop_reason": "end_turn"})()


_fake_client = type("FakeClient", (), {"messages": FakeMessages()})()

agent.clarify = fake_clarify
agent._get_client = lambda: _fake_client
agent.get_model = lambda: "claude-sonnet-5"
