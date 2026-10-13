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
  engine from the sibling `CodeAgent` repository. Used here purely for its low-level Anthropic
  client/model config (`seedagent.config._get_client()`/`get_model()`) -- `backend/app/agent.py`
  makes its own stateless calls with the page-spec system prompt and never touches
  `seedagent.session`/`seedagent.state`, so there's no shared process state to lock around and
  requests for different (or the same) projects can run concurrently.
- Persistence: SQLite via SQLAlchemy (`backend/app/db.py`) -- Users, Projects, the conversation
  history (`Message`, replacing any on-disk conversation file), Exercises (including an autosaved
  `draft_code` for unsubmitted work) and Submissions. The agent's page JSON is parsed into these rows
  on every turn (`backend/app/crud.py`); the raw page is cached only for a fast reload, never treated
  as the source of truth.
- Auth: email + password (bcrypt + JWT bearer tokens, `backend/app/auth.py`). Every `/api/learn/*`
  route requires a valid token and is scoped to the signed-in user's own projects.

## Running it

```bash
cd backend
uv sync
export ANTHROPIC_API_KEY=sk-ant-...        # or ANTHROPIC_FOUNDRY_*, or DEEPSEEK_API_KEY -- see CodeAgent's README
export LEARNCODE_SECRET_KEY=$(openssl rand -hex 32)   # signs auth tokens; set a fixed value or every restart invalidates existing logins
# optional: LEARNCODE_DATABASE_URL (defaults to a local sqlite file under LEARNCODE_DATA_DIR/learncode.db)
# optional: LEARNCODE_DATA_DIR (defaults to backend/data)
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

`backend/_mock_agent_server.py` monkeypatches `app.agent`'s Anthropic client with canned responses,
so the whole app (API + real browser UI) can be exercised with no model calls at all:

```bash
cd backend
PYTHONPATH=. python3 -c "import _mock_agent_server, uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port=8123)"
```

then point the frontend (or a browser) at `http://127.0.0.1:8123/`.

## API

- `POST /api/auth/register {email, password}` / `POST /api/auth/login {email, password}` ->
  `{token, email}` -- every other route below requires `Authorization: Bearer <token>`.
- `POST /api/learn/clarify {objective, history}` -> `{ready, question?}` -- asks whether one more
  clarifying question is needed before a roadmap can be designed.
- `POST /api/learn/start {objective, history}` -> `{session_id, page}` -- creates a new project and
  asks the agent for the initial roadmap page. (`session_id` is the project's id -- the field name is
  kept for frontend continuity.)
- `POST /api/learn/{session_id}/action {action, payload}` -> `{session_id, page}` -- sends an
  action (a button click, or an exercise submission) back to the agent, persists the turn and any
  resulting exercise/submission rows, and returns the next page.
- `GET /api/learn/{session_id}` -> `{session_id, page}` -- refetches the last rendered page (for a
  browser refresh) without calling the agent again.
- `POST /api/learn/{session_id}/draft {exercise_id, code}` -> `{ok}` -- autosaves in-progress,
  unsubmitted code (debounced on the frontend); no agent call.
- `POST /api/learn/{session_id}/ask {question, context}` -> `{answer}` -- the floating help widget's
  side channel; answered with full project context but not persisted into the conversation.

## Limitations

- **No live end-to-end test yet.** Everything above was tested with a mocked Anthropic client (unit
  tests) and with the mock server driving a real browser (Playwright) -- confirming the wiring, the
  page-spec contract, auth, persistence, and the UI all work. Nobody has yet confirmed that a real
  model call reliably follows the page-spec format and grounds its roadmap in an actual GitHub repo
  as instructed; that needs a real `ANTHROPIC_API_KEY` run.
- **`LEARNCODE_SECRET_KEY`.** If unset, falls back to a key generated at process start, so every
  restart invalidates existing login tokens. Set it explicitly for anything beyond local dev.
- SQLite is a local file (`LEARNCODE_DATABASE_URL` can point elsewhere, e.g. Postgres, but nothing
  does by default) -- won't survive a redeploy or scale past one instance as configured.
- Monaco's web worker (used for background tokenization) failed to start in at least one sandboxed
  headless-browser environment this was tested in, and Monaco fell back to main-thread tokenization;
  editing still worked. Worth rechecking in a normal browser.
