# TODO

## Persistence (commercial readiness)

Today: no real data model. A session is a seedagent "project" folder (`backend/data/workspaces/<id>/`);
seedagent writes the raw conversation to `~/.coding-agent/memory/...` (subject to its own cleanup --
`AGENT_CONVERSATION_DAYS`, default 30 days), and `backend/app/store.py` additionally snapshots only the
*last rendered page* (`last_page.json`), for a cheap refresh. Nothing about exercises, submissions or
feedback is stored as structured data; identity is a `session_id` in `localStorage`, no accounts. Fine
for a prototype (survives a server restart), not for shipping.

- [ ] A real database as the system of record: Users, Projects, Exercises (id, title, instructions,
  starter_code, order, status), Submissions (versioned, per exercise, per user), Feedback. Parse the
  agent's page JSON server-side into these rows the moment it's generated -- the page-spec JSON becomes
  a *rendering* of stored data, not the data itself.
- [ ] Real auth (accounts), not a localStorage UUID -- needed for cross-device access and ownership.
- [ ] Stop relying on seedagent's own file-based conversation for context: assemble what we send the
  model ourselves, from the database, on each call. Sidesteps seedagent's single-process global lock
  (today's biggest scaling constraint -- one call at a time, across *all* users) and lets the backend
  run as multiple stateless instances instead of being tied to one process's local disk/in-memory state.
- [ ] Decouple generation from serving: call the LLM to *create* an exercise once, store the result,
  serve it from the database afterward -- cheaper, faster, and still works if the model API has a bad
  moment.
- [ ] Store submitted code and feedback history (not just passed transiently in a message) -- needed for
  progress tracking, re-grading, abuse prevention, and basic product features ("show me what I tried").
- [ ] Move persistent storage off local disk entirely (a database, + object storage for real files if
  ever needed) -- `backend/data/workspaces/` won't survive a redeploy or scale past one instance.

## Other known gaps
- `agent.py`'s `_LOCK` still serializes every `run_turn`/`ask` call into seedagent process-wide (see its
  module docstring) -- acceptable for a demo, not for concurrent users. `agent.clarify()` already shows
  the way out: it bypasses seedagent's session/project/lock entirely (a plain, stateless Anthropic call),
  which is exactly the pattern the persistence rework above should extend to `run_turn` and `ask` too.
- No live end-to-end test against a real model yet (everything so far has been verified with
  `seedagent.session` mocked, plus Playwright against those mocks) -- worth a real run with a live
  `ANTHROPIC_API_KEY` to confirm the model reliably follows the page-spec format, grounds roadmaps in
  real GitHub projects as instructed, and asks sensible clarifying questions.
