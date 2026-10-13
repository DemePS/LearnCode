import { useEffect, useState } from "react";
import { getSession, sendAction, startSession } from "./api";
import { PageRenderer } from "./components/PageRenderer";
import type { Page } from "./types";

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

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!objective.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await startSession(objective.trim());
      localStorage.setItem(SESSION_STORAGE_KEY, res.session_id);
      setSessionId(res.session_id);
      setPage(res.page);
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
  }

  if (resuming) {
    return <div className="shell shell-centered">Loading your session…</div>;
  }

  if (!sessionId || !page) {
    return (
      <div className="shell shell-centered">
        <form className="objective-form" onSubmit={handleStart}>
          <h1>What do you want to learn to build?</h1>
          <p className="objective-hint">
            One sentence is enough -- e.g. "build a URL shortener in Python" or "learn React by
            building a Kanban board". An agent will design a real, GitHub-grounded project and break
            it into exercises you build one component at a time.
          </p>
          <textarea
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            placeholder="I want to learn..."
            rows={3}
            disabled={loading}
          />
          <button className="btn btn-primary" type="submit" disabled={loading || !objective.trim()}>
            {loading ? "Designing your project…" : "Start"}
          </button>
          {error && <p className="error">{error}</p>}
        </form>
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
        <PageRenderer page={page} onAction={handleAction} submitting={loading} />
      </main>
    </div>
  );
}

export default App;
