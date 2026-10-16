"""page_schema.py: the page contract the prompt describes in prose, as checks the model can act on.

These matter because the model calls the validators itself, inside the turn, and rewrites the JSON from
what they report -- a check that stays silent on a broken page is a check that never fixes anything."""

from __future__ import annotations

from app import page_schema as ps


def _actions(items):
    return [{"type": "actions", "items": list(items)}] if items else []


def roadmap_page(*, actions=()):
    return {
        "title": "Build a tiny URL shortener",
        "blocks": [
            {"type": "markdown", "content": "intro"},
            {"type": "roadmap", "items": [
                {"id": "ex1", "title": "One", "summary": "s", "status": "available",
                 "open_action": "open_ex1"},
            ]},
            *_actions(actions),
        ],
    }


def exercise_page(*, title="One", open_action="open_ex1", submit_action="submit_ex1", actions=()):
    return {
        "title": title,
        "open_action": open_action,
        "blocks": [
            {"type": "code_editor", "exercise_id": "ex1", "file_name": "a.py", "language": "python",
             "instructions": "i", "starter_code": "print(1)", "submit_action": submit_action},
            *_actions(actions),
        ],
    }


def test_a_well_formed_roadmap_and_its_exercises_pass():
    roadmap = roadmap_page()
    assert ps.validate_roadmap_page(roadmap) == []
    assert ps.validate_exercises({"exercises": [exercise_page()]}, roadmap) == []


def test_the_whole_reply_may_be_passed_instead_of_the_page_alone():
    # The model has one object in hand (routes.py stores both halves from it), so passing that object
    # must be judged rather than reported as the wrong shape.
    assert ps.validate_roadmap_page(
        {"roadmap": roadmap_page(), "exercises": [exercise_page()]}) == []


def test_an_action_that_asks_for_help_is_reported():
    # The learner has a dedicated "Ask AI" widget, so a page button for a hint duplicates it; the
    # prompt forbids it, and this is what makes the rule enforceable rather than advisory.
    # Judged on the id alone: the label is the frontend's to write (see ActionItem), so a hint button
    # is caught by what it *is*, not by how it happens to be worded.
    problems = ps.validate_roadmap_page(roadmap_page(actions=[{"action": "get_a_hint"}]))
    assert len(problems) == 1 and "get_a_hint" in problems[0]


def test_an_exercise_offering_a_chat_is_reported_too():
    """An exercise entry has to stay linked to the roadmap, so it keeps its code_editor -- and now
    also trips the buttons rule. The chat rule firing is what this asserts."""
    exercise = exercise_page(actions=[{"action": "ask_a_question"}])
    problems = ps.validate_exercises({"exercises": [exercise]}, roadmap_page())
    assert any("ask_a_question" in p for p in problems)


def test_a_label_alone_cannot_carry_an_offer_any_more():
    """A bland id with a helpful label used to slip through; now the label is not the agent's to write.

    It is accepted for pages stored before the split (and ignored when rendering), so this asserts that
    such a page still validates on its id -- the wording is not the agent's responsibility, so it is not
    the agent's mistake."""
    problems = ps.validate_roadmap_page(roadmap_page(
        actions=[{"action": "retry_ex1", "label": "Retry exercise 1 with the worked shape"}]))
    assert problems == []


def test_a_plain_retry_is_left_alone():
    """Failing an exercise and trying it again is exactly what the feedback page should offer.

    Rejecting every retry would make the check useless, so only the hint wording in an *id* is caught."""
    problems = ps.validate_roadmap_page(roadmap_page(actions=[
        {"action": "open_ex1", "label": "Retry exercise 1"},
        {"action": "show_roadmap", "label": "Back to the roadmap"},
        {"action": "run_tests", "label": "Run the tests"},
        {"action": "open_ex2", "label": "Try again"},
    ]))
    assert problems == []


def test_a_hint_behind_a_bland_action_id_is_still_reported():
    # "explain_more" is a hint request however innocently it is labelled.
    problems = ps.validate_roadmap_page(roadmap_page(actions=[{"action": "explain_more"}]))
    assert len(problems) == 1 and "explain_more" in problems[0]


def test_ordinary_action_ids_are_left_alone():
    # Only whole words count: "checkout" contains "check", "run_tests" contains nothing of the sort --
    # rejecting ids like these would make the check fire on perfectly good pages.
    problems = ps.validate_roadmap_page(roadmap_page(actions=[
        {"action": "checkout_branch", "label": "Check out the branch"},
        {"action": "run_tests", "label": "Run the tests"},
        {"action": "open_ex1", "label": "Back to this exercise"},
    ]))
    assert problems == []


def test_an_action_needs_no_label():
    """The agent stopped being asked for one: the button's text is interface, not content.

    `label` stays optional so pages stored before the split still parse -- they must not start failing
    validation over a field nobody reads any more."""
    page = ps.Page.model_validate({"title": "T", "blocks": [
        {"type": "actions", "items": [{"action": "open_ex1"}]}]})
    actions = page.blocks[0]
    assert actions.type == "actions" and actions.items[0].label is None
    assert ps.validate_roadmap_page({"title": "T", "blocks": [
        {"type": "roadmap", "items": [{"id": "ex1", "title": "One", "summary": "s",
                                       "status": "available", "open_action": "open_ex1"}]},
        {"type": "actions", "items": [{"action": "open_ex1"}]}]}) == []


def test_an_unknown_language_is_reported_because_the_editor_cannot_colour_it():
    exercise = exercise_page()
    exercise["blocks"][0]["language"] = "bash"
    problems = ps.validate_exercises({"exercises": [exercise]}, roadmap_page())
    assert len(problems) == 1 and "'bash'" in problems[0] and "shell" in problems[0]


def test_an_exercise_no_roadmap_item_opens_is_reported():
    problems = ps.validate_exercises({"exercises": [exercise_page(open_action="open_ex9")]},
                                     roadmap_page())
    assert any("open_ex1" in p and "no exercise" in p for p in problems)
    assert any("open_ex9" in p and "matches no roadmap item" in p for p in problems)


def test_buttons_on_an_exercise_page_are_reported():
    """The learner presses the editor's own Submit button; a retry cannot exist before a submission.

    What this prevents: the model attached a "retry" button to the exercise page, the UI labelled it
    from the action id, and the learner was offered a retry on an exercise they had not submitted."""
    problems = ps.validate_exercises(
        {"exercises": [exercise_page(actions=[{"action": "open_ex1", "style": "primary"}])]},
        roadmap_page())
    assert len(problems) == 1
    assert "actions" in problems[0] and "exercise page" in problems[0]


def test_the_roadmap_page_keeps_its_buttons():
    """The one page that legitimately carries an `actions` block must not start failing: without its
    button there is no way to open the first exercise at all."""
    assert ps.validate_roadmap_page(roadmap_page(actions=[{"action": "open_ex1"}])) == []
