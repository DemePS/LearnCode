"""Dev-only helper: monkeypatches seedagent.session so the FastAPI app can run (and be
browser-tested) without a live ANTHROPIC_API_KEY. Not part of the shipped app."""

import json

from app import agent


class FakeSession:
    def open_project(self, workspace, ui, tools, system_prompt, resume):
        self.ui = ui

    def send(self, message):
        if "roadmap" in message.lower():
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
        elif "open_ex1" in message:
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
        else:
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
        self.ui.assistant_start()
        self.ui.assistant_text(json.dumps(page))
        self.ui.response_end("end_turn")
        return True


_fake = FakeSession()
agent.session.open_project = _fake.open_project
agent.session.send = _fake.send
