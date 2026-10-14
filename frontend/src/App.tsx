import { useEffect, useState } from "react";
import { clarify, getSession, sendAction, startSession } from "./api";
import { ActivityFeed } from "./components/ActivityFeed";
import { AskAI } from "./components/AskAI";
import { PageRenderer } from "./components/PageRenderer";
import { QuestionPrompt } from "./components/QuestionPrompt";
import type { ClarifyTurn, Page } from "./types";

const SESSION_STORAGE_KEY = "learncode.session_id";
// Reserved action id the frontend's own "Back to roadmap" button always sends, regardless of what
// action ids the agent invented for its own buttons on the current page (see PAGE_SYSTEM_PROMPT).
const SHOW_ROADMAP_ACTION = "show_roadmap";

/** A fresh id per turn, so the activity feed polls the turn it is actually watching. `crypto` is
 * absent on an insecure origin, hence the fallback -- this id only has to be unique per turn. */
function newTurnId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function App() {
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
  // The turn the activity feed watches. Set when a request starts and cleared when the page it was
  // waiting for arrives, so the feed covers exactly the "something is happening" window. `loading`
  // already means "a turn is running", so it is the feed's active flag rather than a second state.
  const [turnId, setTurnId] = useState("");

  // Pre-session clarification: a lightweight back-and-forth (no project/session exists yet) that
  // happens before the first roadmap is designed, for an objective that's genuinely ambiguous.
  const [clarifyHistory, setClarifyHistory] = useState<ClarifyTurn[]>([]);
  const [clarifyQuestion, setClarifyQuestion] = useState<string | null>(null);

  useEffect(() => {
    if (!resuming || !sessionId) return;
    getSession(sessionId)
      .then((res) => setPage(res.page))
      .catch(() => {
        localStorage.removeItem(SESSION_STORAGE_KEY);
        setSessionId(null);
      })
      .finally(() => setResuming(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function proceed(obj: string, history: ClarifyTurn[], turn: string) {
    const res = await clarify(obj, history);
    if (!res.ready) {
      setClarifyQuestion(res.question);
      return;
    }
    setClarifyQuestion(null);
    const started = await startSession(obj, history, turn);
    localStorage.setItem(SESSION_STORAGE_KEY, started.session_id);
    setSessionId(started.session_id);
    setPage(started.page);
    setCurrentCode(null);
  }

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!objective.trim() || loading) return;
    const turn = newTurnId();
    setTurnId(turn);
    setLoading(true);
    setError(null);
    setClarifyHistory([]);
    try {
      await proceed(objective.trim(), [], turn);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleClarifyAnswer(answer: string) {
    if (!clarifyQuestion || loading) return;
    const turn = newTurnId();
    setTurnId(turn);
    setLoading(true);
    setError(null);
    const history = [...clarifyHistory, { question: clarifyQuestion, answer }];
    setClarifyHistory(history);
    try {
      await proceed(objective.trim(), history, turn);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleAction(action: string, payload: Record<string, unknown> = {}) {
    if (!sessionId || loading) return;
    const turn = newTurnId();
    setTurnId(turn);
    setLoading(true);
    setError(null);
    try {
      const res = await sendAction(sessionId, action, payload, turn);
      setPage(res.page);
      setCurrentCode(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  function startOver() {
    localStorage.removeItem(SESSION_STORAGE_KEY);
    setSessionId(null);
    setPage(null);
    setObjective("");
    setError(null);
    setCurrentCode(null);
    setClarifyHistory([]);
    setClarifyQuestion(null);
  }

  if (resuming) {
    return <div className="shell shell-centered">Loading your session…</div>;
  }

  if (!sessionId || !page) {
    // The first turn is two model calls (clarify, then the roadmap), so it is the longest wait in the
    // app -- the feed belongs here too, not only once a session exists. It starts reporting as soon
    // as /start is called; /clarify emits no steps, so the line simply reads "Getting started…".
    if (loading) {
      return (
        <div className="shell shell-centered">
          <ActivityFeed turnId={turnId} active={loading} />
        </div>
      );
    }
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
        </div>
      </header>
      <main>
        {error && <p className="error">{error}</p>}
        {/* While a turn runs the feed takes the page's place rather than sitting above it: the turn is
            going to replace this whole screen with a new page in under a minute, so there is nothing
            worth reading underneath, and the one thing the learner needs is to see it is still going. */}
        {loading ? (
          <ActivityFeed turnId={turnId} active={loading} />
        ) : (
          <PageRenderer
            page={page}
            onAction={handleAction}
            submitting={loading}
            onCodeChange={(exerciseId, fileName, code) => setCurrentCode({ exerciseId, fileName, code })}
          />
        )}
      </main>
      {!loading && <AskAI sessionId={sessionId} currentCode={currentCode} />}
    </div>
  );
}

export default App;
