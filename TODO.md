# TODO

## Persistence (commercial readiness)

Now: a real database (SQLite via SQLAlchemy, `backend/app/db.py`) is the system of record -- Users,
Projects, Message (the conversation history, replacing seedagent's own on-disk conversation file),
Exercise (including `draft_code` autosave) and Submission rows. `backend/app/crud.py` parses each
agent response into these rows (`sync_exercises_from_page`, `record_submission`) the moment it's
generated; the page JSON cached on `Project.last_page` is only a rendering of that data, for a cheap
reload, never the source of truth. Auth is real (email + password, bcrypt, JWT) instead of a bare
`session_id` in `localStorage`, and every `/api/learn/*` route is ownership-checked per user.
`backend/app/store.py` and its filesystem-workspace model are gone.

- [x] A real database as the system of record: Users, Projects, Exercises, Submissions.
- [x] Real auth (accounts), not a localStorage UUID.
- [x] Stop relying on seedagent's own file-based conversation / global session state: `agent.py` now
  makes stateless Anthropic calls with the conversation assembled from the database on each call, no
  shared process state, no lock -- removes the single-process bottleneck entirely, not just the lock
  around it, since the backend no longer touches `seedagent.session`/`seedagent.state` at all.
- [ ] Decouple generation from serving: call the LLM to *create* an exercise once, store the result,
  serve it from the database afterward -- cheaper, faster, and still works if the model API has a bad
  moment.
- [x] Store submitted code and feedback history (not just passed transiently in a message) -- the
  `Submission` table, plus `Exercise.draft_code` for unsubmitted in-progress code.
- [ ] Move persistent storage off local disk entirely (a database, + object storage for real files if
  ever needed) -- SQLite is still a local file (`LEARNCODE_DATABASE_URL` can point elsewhere, e.g.
  Postgres, but nothing does yet); won't survive a redeploy or scale past one instance as configured.

## Other known gaps
- `LEARNCODE_SECRET_KEY` falls back to a random key generated at process start if unset -- fine for a
  single long-running dev process, but every restart invalidates existing tokens. Set it explicitly
  before anything resembling production use.
- No live end-to-end test against a real model yet (everything so far has been verified with a mocked
  Anthropic client, plus Playwright against those mocks) -- worth a real run with a live
  `ANTHROPIC_API_KEY` to confirm the model reliably follows the page-spec format, grounds roadmaps in
  real GitHub projects as instructed, and asks sensible clarifying questions.
