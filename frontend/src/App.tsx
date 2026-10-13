import { useEffect, useState } from "react";
import { clarify, getSession, sendAction, startSession } from "./api";
import { AskAI } from "./components/AskAI";
import { PageRenderer } from "./components/PageRenderer";
import { QuestionPrompt } from "./components/QuestionPrompt";
import type { ClarifyTurn, Page } from "./types";

const SESSION_STORAGE_KEY = "learncode.session_id";

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
      setError(err instanceof Error ? err.message : String(err));
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
      setError(err instanceof Error ? err.message : String(err));
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

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">LearnCode</span>
        <button className="btn btn-link" onClick={startOver}>
          Start a new project
        </button>
      </header>
      <main>
        {error && <p className="error">{error}</p>}
        <PageRenderer
          page={page}
          onAction={handleAction}
          submitting={loading}
          onCodeChange={(exerciseId, fileName, code) => setCurrentCode({ exerciseId, fileName, code })}
        />
      </main>
      <AskAI sessionId={sessionId} currentCode={currentCode} />
    </div>
  );
}

export default App;
