"""store.py: session ids, page persistence, and the ordering guarantees the app relies on.

The id tests are the important ones. A session id arrives straight from a URL and is joined onto the
workspaces directory, so an id that escapes that directory would let a request run the agent against
backend/data/ and read every other learner's project."""

from __future__ import annotations

import pytest

from app import db

GOOD = "0" * 32


def test_new_id_is_well_formed(store):
    for _ in range(20):
        sid = store.new_session_id()
        assert store.SESSION_ID_RE.match(sid)
        assert len(sid) == 32


def test_round_trip_and_seq_increments(store, user_factory):
    uid = user_factory("alice", 1)
    sid = store.new_session_id()
    store.create_session(sid, uid, "learn fastapi")

    first = store.save_page(sid, {"title": "Roadmap", "blocks": [{"type": "roadmap"}]})
    second = store.save_page(sid, {"title": "Ex 1", "blocks": [{"type": "code_editor"}]},
                             action="open_ex1")

    assert (first.seq, second.seq) == (1, 2)
    assert first.kind == "roadmap"
    assert second.kind == "exercise"
    assert second.action == "open_ex1"
    assert store.latest_page(sid)["title"] == "Ex 1"


def test_latest_page_skips_errors_but_stores_them(store, user_factory):
    uid = user_factory("alice", 1)
    sid = store.new_session_id()
    store.create_session(sid, uid, "learn fastapi")
    store.save_page(sid, {"title": "Roadmap", "blocks": [{"type": "roadmap"}]})

    store.save_page(sid, {"_error": True, "title": "Something went wrong",
                          "blocks": [{"type": "markdown"}]}, action="whatever")

    # The learner's last good screen survives a failure, which is the whole point: a refresh after a
    # dead turn must not replace the roadmap with an error.
    assert store.latest_page(sid)["title"] == "Roadmap"
    with db.session_scope() as s:
        assert s.query(db.Page).filter_by(kind="error").count() == 1


def test_latest_page_of_unknown_session_is_none(store):
    assert store.latest_page("f" * 32) is None


def test_error_page_alone_yields_none(store, user_factory):
    """A session whose only page is an error has nothing to show -- and must not answer with the
    error page, or a learner whose very first turn failed would see it on every reload."""
    uid = user_factory("alice", 1)
    sid = store.new_session_id()
    store.create_session(sid, uid, "learn fastapi")
    store.save_page(sid, {"_error": True, "blocks": [{"type": "markdown"}]})
    assert store.latest_page(sid) is None


@pytest.mark.parametrize("bad", [
    "..",
    "../x",
    "../../etc/passwd",
    "/etc/passwd",
    "C:\\windows",
    "A" * 32,          # uppercase: the whitelist is lowercase hex only
    "a" * 31,          # too short
    "a" * 33,          # too long
    "a" * 64,
    "a" * 32 + "/x",   # a separator
    "a" * 31 + "G",    # not hex
    "a" * 31 + "-",
    "",
    ".",
    "a" * 32 + "\x00",
])
def test_bad_session_ids_are_rejected_and_touch_nothing(store, data_dir, bad):
    with pytest.raises(ValueError):
        store.workspace_for(bad)
    with pytest.raises(ValueError):
        store.workspace_for_check(bad)
    # get_session answers None instead of raising: the route turns either into a 404.
    assert store.get_session(bad) is None
    assert store.latest_page(bad) is None
    # Nothing was created on disk for any of these, which is what proves the traversal is closed.
    assert not (data_dir / "workspaces").exists()


def test_workspace_stays_inside_the_workspaces_directory(store, user_factory, data_dir):
    uid = user_factory("alice", 1)
    sid = store.new_session_id()
    store.create_session(sid, uid, "learn fastapi")
    path = store.workspace_for(sid)
    assert path.resolve().parent == store.workspaces_dir().resolve()
    assert path.parent.name == "workspaces"
    assert path.name == sid


def test_ownership(store, user_factory):
    alice = user_factory("alice", 1)
    bob = user_factory("bob", 2)
    sid = store.new_session_id()
    store.create_session(sid, alice, "learn fastapi")

    assert store.owned_by(sid, alice)
    assert not store.owned_by(sid, bob)
    assert not store.owned_by("f" * 32, alice)
    assert store.get_session(sid).user_id == alice


def test_sessions_for_is_newest_first(store, user_factory):
    uid = user_factory("alice", 1)
    first = store.new_session_id()
    second = store.new_session_id()
    store.create_session(first, uid, "older")
    store.create_session(second, uid, "newer")
    # updated_at is set at creation and moved by each page, so touching the older one reorders them.
    store.save_page(first, {"blocks": []})

    assert [r.objective for r in store.sessions_for(uid)] == ["older", "newer"]
    assert store.sessions_for(999) == []


def test_delete_session_removes_its_pages_and_workspace(store, user_factory):
    alice = user_factory("alice", 1)
    keep = store.new_session_id()
    gone = store.new_session_id()
    store.create_session(keep, alice, "keep me")
    store.create_session(gone, alice, "delete me")
    for sid in (keep, gone):
        store.save_page(sid, {"title": "Roadmap", "blocks": [{"type": "roadmap"}]})
        (store.workspace_for(sid) / "clone.txt").write_text("x")

    assert store.delete_session(gone)
    # The pages go with the session, not just the row: they are unreachable without it, and leaving them
    # behind would keep a deleted project's exercises (and drafts) on disk.
    assert store.get_session(gone) is None
    assert store.latest_page(gone) is None
    assert not store.workspace_for_check(gone).exists()
    # The neighbour is untouched -- the point of the test is that this is scoped to one session.
    assert store.latest_page(keep)["title"] == "Roadmap"
    assert (store.workspace_for_check(keep) / "clone.txt").exists()
    assert [r.id for r in store.sessions_for(alice)] == [keep]
    # Deleting what is already gone is False rather than an error, and a malformed id never reaches the
    # filesystem.
    assert not store.delete_session(gone)
    assert not store.delete_session("../..")


def test_data_survives_a_new_engine(store, user_factory, tmp_path):
    """A restart: the same file, a brand new engine. Nothing may live only in memory."""
    uid = user_factory("alice", 1)
    sid = store.new_session_id()
    store.create_session(sid, uid, "learn rust")
    store.save_page(sid, {"title": "Roadmap v1", "blocks": [{"type": "roadmap"}]})

    db.configure(f"sqlite:///{tmp_path / 'test.db'}")

    assert store.latest_page(sid)["title"] == "Roadmap v1"
    assert store.owned_by(sid, uid)


def test_classify_page_covers_every_block_shape():
    cases = [
        ({"blocks": [{"type": "roadmap"}]}, "roadmap"),
        ({"blocks": [{"type": "code_editor"}]}, "exercise"),
        ({"blocks": [{"type": "feedback"}]}, "feedback"),
        ({"blocks": [{"type": "markdown"}]}, "page"),
        ({"_error": True, "blocks": [{"type": "roadmap"}]}, "error"),
        ({}, "page"),
        ({"blocks": None}, "page"),
        ({"blocks": [None, {"type": "code_editor"}]}, "exercise"),
    ]
    for page, expected in cases:
        assert db.classify_page(page) == expected
