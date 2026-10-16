"""Dev-only helper: monkeypatches seedagent.session so the FastAPI app can run (and be browser-tested)
without a live API key. Not part of the shipped app.

The keyed-sessions branch is what makes concurrent turns possible: open_project takes a
state.Session and binds it to the calling thread, and state.close_session drops that binding. This fake
keeps none of that state -- it accepts the session and ignores it -- but the argument has to be
accepted here, or every test that runs a turn would fail the moment the real signature changed."""

import json

from app import agent


class FakeSession:
    def open_project(self, workspace, ui, tools=None, system_prompt=None, resume=False, session=None):
        self.ui = ui
        self.session = session
        return workspace

    def send(self, message):
        # One of app.agent's per-exercise follow-ups (see _write_exercises) -- matched on its fixed
        # prefix before the generic "open_ex1"/"open_ex2" checks below, because the follow-up message
        # itself contains "open_action='open_ex1'" and would otherwise be mistaken for an action turn.
        if message.startswith("Now write the full exercise page"):
            if "open_action='open_ex1'" in message:
                page = {
                    "title": "Exercise 1: Data model",
                    "blocks": [
                        {
                            "type": "code_editor",
                            "exercise_id": "ex1",
                            "file_name": "models.py",
                            "language": "python",
                            "instructions": "Define a `Link` dataclass with `original_url: str` and "
                            "`code: str`.",
                            "starter_code": "from dataclasses import dataclass\n\n\nclass Link:\n    pass\n",
                            "submit_action": "submit_ex1",
                        },
                    ],
                }
            elif "open_action='open_ex2'" in message:
                page = {
                    "title": "Exercise 2: Base62 encoder",
                    "blocks": [
                        {
                            "type": "code_editor",
                            "exercise_id": "ex2",
                            "file_name": "encoding.py",
                            "language": "python",
                            "instructions": "Write `encode(n: int) -> str`, turning an integer id into a "
                            "base62 short code.",
                            "starter_code": "def encode(n: int) -> str:\n    pass\n",
                            "submit_action": "submit_ex2",
                        },
                    ],
                }
            else:
                page = {"title": "Unknown exercise", "blocks": []}
        elif "roadmap" in message.lower():
            # A plain page, no {"roadmap": ..., "exercises": [...]} wrapper: the roadmap and its
            # exercises are now two separate conversation turns (see PAGE_SYSTEM_PROMPT), and
            # app.agent._write_exercises is what asks for each exercise next, one at a time.
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

# What this file replaces, kept so it can be put back. Importing it patches seedagent for the whole
# process, which is intended for the dev server (one fake, from start to finish) but leaks into any test
# running later in the same process -- tests that want the REAL loop would silently keep getting the fake.
# `uninstall()` restores the originals so those tests can run the library as it really is.
_PATCHED = {
    "open_project": agent.session.open_project,
    "send": agent.session.send,
    "close_session": agent.state.close_session,
}


def _apply(open_project, send, close_session) -> None:
    agent.session.open_project = open_project
    agent.session.send = send
    agent.state.close_session = close_session


_apply(_fake.open_project, _fake.send, lambda sess: None)


def uninstall() -> None:
    """Put seedagent's real functions back. Called by tests that need the genuine session."""
    _apply(_PATCHED["open_project"], _PATCHED["send"], _PATCHED["close_session"])


def install() -> None:
    """Put the fake back, undoing uninstall()."""
    _apply(_fake.open_project, _fake.send, lambda sess: None)
