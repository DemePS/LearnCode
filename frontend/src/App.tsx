import { useEffect, useRef, useState } from "react";
import {
  AuthError,
  clarify,
  clearToken,
  getSession,
  getToken,
  saveDraft,
  sendAction,
  setToken,
  startSession,
} from "./api";
import { AskAI } from "./components/AskAI";
import { AuthForm } from "./components/AuthForm";
import { PageRenderer } from "./components/PageRenderer";
import { QuestionPrompt } from "./components/QuestionPrompt";
import type { ClarifyTurn, Page } from "./types";

const SESSION_STORAGE_KEY = "learncode.session_id";
// Reserved action id the frontend's own "Back to roadmap" button always sends, regardless of what
// action ids the agent invented for its own buttons on the current page (see PAGE_SYSTEM_PROMPT).
const SHOW_ROADMAP_ACTION = "show_roadmap";
const DRAFT_AUTOSAVE_DEBOUNCE_MS = 1200;

function App() {
  const [authed, setAuthed] = useState(() => !!getToken());
  const [sessionId, setSessionId] = useState<string | null>(() =>
    localStorage.getItem(SESSION_STORAGE_KEY),
  );
  const [page, setPage] = useState<Page | null>(null);
  const [objective, setObjective] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resuming, setResuming] = useState(sessionId !== null);
  const [currentCode, setCurrentCode] = useState<{ exerciseId: string; fileName: string; code: string } | null>(
    null,
  );

  // Pre-session clarification: a lightweight back-and-forth (no project/session exists yet) that
  // happens before the first roadmap is designed, for an objective that's genuinely ambiguous.
  const [clarifyHistory, setClarifyHistory] = useState<ClarifyTurn[]>([]);
  const [clarifyQuestion, setClarifyQuestion] = useState<string | null>(null);

  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Any API call can fail with AuthError (token missing/expired/invalid) at any point, not just at
   * login -- signs the learner out and back to the login form instead of showing a raw error. */
  function handleError(err: unknown): boolean {
    if (err instanceof AuthError) {
      clearToken();
      setAuthed(false);
      return true;
    }
    setError(err instanceof Error ? err.message : String(err));
    return false;
  }

  useEffect(() => {
    if (!resuming || !sessionId) return;
    getSession(sessionId)
      .then((res) => setPage(res.page))
      .catch((err) => {
        if (!(err instanceof AuthError)) {
          localStorage.removeItem(SESSION_STORAGE_KEY);
          setSessionId(null);
        }
        handleError(err);
      })
      .finally(() => setResuming(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function proceed(obj: string, history: ClarifyTurn[]) {
    const res = await clarify(obj, history);
    if (!res.ready) {
      setClarifyQuestion(res.question);
      return;
    }
    setClarifyQuestion(null);
    const started = await startSession(obj, history);
    localStorage.setItem(SESSION_STORAGE_KEY, started.session_id);
    setSessionId(started.session_id);
    setPage(started.page);
    setCurrentCode(null);
  }

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!objective.trim() || loading) return;
    setLoading(true);
    setError(null);
    setClarifyHistory([]);
    try {
      await proceed(objective.trim(), []);
    } catch (err) {
      handleError(err);
    } finally {
      setLoading(false);
    }
  }

  async function handleClarifyAnswer(answer: string) {
    if (!clarifyQuestion || loading) return;
    setLoading(true);
    setError(null);
    const history = [...clarifyHistory, { question: clarifyQuestion, answer }];
    setClarifyHistory(history);
    try {
      await proceed(objective.trim(), history);
    } catch (err) {
      handleError(err);
    } finally {
      setLoading(false);
    }
  }

  async function handleAction(action: string, payload: Record<string, unknown> = {}) {
    if (!sessionId || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await sendAction(sessionId, action, payload);
      setPage(res.page);
      setCurrentCode(null);
    } catch (err) {
      handleError(err);
    } finally {
      setLoading(false);
    }
  }

  /** Fires on every keystroke in a code_editor block: keeps the floating help widget's context
   * fresh immediately, and (debounced, so typing doesn't fire a request per character) autosaves
   * the draft so it survives a refresh or a detour to another exercise without being submitted. */
  function handleCodeChange(exerciseId: string, fileName: string, code: string) {
    setCurrentCode({ exerciseId, fileName, code });
    if (!sessionId) return;
    if (draftTimer.current) clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => {
      saveDraft(sessionId, exerciseId, code).catch(() => {
        // A missed autosave tick isn't worth interrupting the learner over; the next keystroke
        // retries, and nothing has been lost yet if they submit before any autosave lands.
      });
    }, DRAFT_AUTOSAVE_DEBOUNCE_MS);
  }

  function startOver() {
    if (draftTimer.current) clearTimeout(draftTimer.current);
    localStorage.removeItem(SESSION_STORAGE_KEY);
    setSessionId(null);
    setPage(null);
    setObjective("");
    setError(null);
    setCurrentCode(null);
    setClarifyHistory([]);
    setClarifyQuestion(null);
  }

  function logOut() {
    clearToken();
    setAuthed(false);
    startOver();
  }

  if (!authed) {
    return (
      <AuthForm
        onAuthenticated={(token) => {
          setToken(token);
          setAuthed(true);
        }}
      />
    );
  }

  if (resuming) {
    return <div className="shell shell-centered">Loading your session…</div>;
  }

  if (!sessionId || !page) {
    return (
      <div className="shell shell-centered">
        {clarifyQuestion ? (
          <QuestionPrompt question={clarifyQuestion} onAnswer={handleClarifyAnswer} submitting={loading} />
        ) : (
          <form className="objective-form" onSubmit={handleStart}>
            <h1>What do you want to learn to build?</h1>
            <p className="objective-hint">
              One sentence is enough -- e.g. "build a URL shortener in Python" or "learn React by
              building a Kanban board". An agent will design a real, GitHub-grounded project and
              break it into exercises you build one component at a time -- it may ask a quick
              clarifying question first if your objective leaves something important open.
            </p>
            <textarea
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              placeholder="I want to learn..."
              rows={3}
              disabled={loading}
            />
            <button className="btn btn-primary" type="submit" disabled={loading || !objective.trim()}>
              {loading ? "Thinking…" : "Start"}
            </button>
            {error && <p className="error">{error}</p>}
            <button type="button" className="btn btn-link" onClick={logOut}>
              Log out
            </button>
          </form>
        )}
      </div>
    );
  }

  // "Back to roadmap" is a frontend-owned control, not something the agent has to remember to add
  // to its own actions block -- that was fragile (the agent sometimes left it off a page entirely,
  // deterministically, not just occasionally). It's hidden only when the current page already shows
  // the roadmap itself, and always sends the same reserved action id (see PAGE_SYSTEM_PROMPT) no
  // matter what action ids the agent invented for its own buttons on this page.
  const onRoadmap = page.blocks.some((b) => b.type === "roadmap");

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">LearnCode</span>
        <div className="topbar-actions">
          {!onRoadmap && (
            <button className="btn btn-link" onClick={() => handleAction(SHOW_ROADMAP_ACTION)} disabled={loading}>
              Back to roadmap
            </button>
          )}
          <button className="btn btn-link" onClick={startOver}>
            Start a new project
          </button>
          <button className="btn btn-link" onClick={logOut}>
            Log out
          </button>
        </div>
      </header>
      <main>
        {error && <p className="error">{error}</p>}
        <PageRenderer page={page} onAction={handleAction} submitting={loading} onCodeChange={handleCodeChange} />
      </main>
      <AskAI sessionId={sessionId} currentCode={currentCode} />
    </div>
  );
}

export default App;
