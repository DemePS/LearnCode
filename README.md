# LearnCode

A project-based coding-practice app: like LeetCode, but the user builds one real software product
component by component, and nothing is hand-authored. The user states a learning objective; an AI
agent designs a real project (grounded in an actual open-source GitHub repo that matches the
objective, scaled down) and breaks it into exercises; the user writes code in a browser editor and
submits it back for review.

## The core idea: the UI is generated at runtime, not hardcoded

There is no fixed set of screens. On every turn, the agent (seedagent, see below) returns one JSON
**page spec** -- a title plus a list of typed blocks (`markdown`, `roadmap`, `code_editor`,
`feedback`, `actions`, `stat_row`). The frontend has exactly one generic renderer
(`frontend/src/components/PageRenderer.tsx`) that draws whatever blocks come back; a "roadmap page"
and an "exercise page" are the same React component rendering different data. Buttons and roadmap
items carry action ids the agent invents itself (`open_ex2`, `submit_ex1`, ...); the frontend only
ever echoes the clicked id back to the backend, and the agent re-derives what it meant from its own
conversation history. See `backend/app/agent.py`'s `PAGE_SYSTEM_PROMPT` for the exact contract.

## Stack

- Backend: Python + FastAPI, under `backend/`.
- Frontend: React + TypeScript + Vite (SPA), under `frontend/`. The code editor is Monaco
  (`@monaco-editor/react`), bundled locally rather than loaded from Monaco's CDN (see
  `frontend/src/monacoSetup.ts`) so the app works offline / behind a restrictive proxy.
- AI client: [`seedagent`](https://github.com/DemePS/CodeAgent) (`seedagent[browser,excel]`), the
  engine from the sibling `CodeAgent` repository. Used here purely as a text generator: each
  learning session is a seedagent "project" (its own folder under `backend/data/workspaces/`), given
  `tools=[]` and the page-spec system prompt, so it never edits files -- it only ever replies with
  page JSON. seedagent persists that project's conversation to disk, and the backend calls
  `session.open_project(..., resume=True)` on every turn, so a stateless FastAPI process doesn't need
  to keep chat history in memory itself.

## Running it

```bash
cd backend
uv sync
export ANTHROPIC_API_KEY=sk-ant-...        # or ANTHROPIC_FOUNDRY_*, or DEEPSEEK_API_KEY -- see CodeAgent's README
uv run uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
npm run dev
```

Open the Vite dev URL (prints on `npm run dev`, typically http://localhost:5173). The Vite dev
server proxies `/api` to the FastAPI backend (see `frontend/vite.config.ts`); in production, build
the frontend (`npm run build`) and the backend serves `frontend/dist` itself at `/` (see
`backend/app/main.py`) -- no separate frontend server needed.

### Testing without a live API key

`backend/_mock_agent_server.py` monkeypatches `seedagent.session` with canned responses, so the
whole app (API + real browser UI) can be exercised with no model calls at all:

```bash
cd backend
PYTHONPATH=. python3 -c "import _mock_agent_server, uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port=8123)"
```

then point the frontend (or a browser) at `http://127.0.0.1:8123/`.

## API

- `POST /api/learn/start {objective}` -> `{session_id, page}` -- starts a new session and asks the
  agent for the initial roadmap page.
- `POST /api/learn/{session_id}/action {action, payload}` -> `{session_id, page}` -- sends an
  action (a button click, or an exercise submission) back to the agent and returns the next page.
- `GET /api/learn/{session_id}` -> `{session_id, page}` -- refetches the last rendered page (for a
  browser refresh) without calling the agent again.

## Limitations

- **Concurrency.** seedagent keeps its state (`seedagent.state`, `seedagent.session.messages`) in
  module-level globals, by design for a single interactive CLI session (see CodeAgent's `CLAUDE.md`,
  "concurrency" section). `backend/app/agent.py` serializes every call with a lock, so two learners'
  requests never interleave -- correct, but requests queue up one at a time. Fine for a practice app;
  would need seedagent's own concurrency work (or running one seedagent process per session) to scale.
- **No live end-to-end test yet.** Everything above was tested with `seedagent.session` mocked (unit
  tests) and with the mock server driving a real browser (Playwright) -- confirming the wiring, the
  page-spec contract, and the UI all work. Nobody has yet confirmed that a real model call reliably
  follows the page-spec format and grounds its roadmap in an actual GitHub repo as instructed; that
  needs a real `ANTHROPIC_API_KEY` run.
- Monaco's web worker (used for background tokenization) failed to start in at least one sandboxed
  headless-browser environment this was tested in, and Monaco fell back to main-thread tokenization;
  editing still worked. Worth rechecking in a normal browser.
