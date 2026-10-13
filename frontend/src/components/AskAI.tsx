import { useState } from "react";
import { askQuestion } from "../api";
import { Md } from "./Md";

interface Exchange {
  question: string;
  answer?: string;
  error?: string;
}

const HINT_QUESTION = "Can you give me a hint for what I'm currently working on, without giving away the full solution?";

/** A floating help widget, present on every page once a session exists. Unlike the normal
 * action/page flow, asking a question (or a hint) never replaces the page on screen -- it answers
 * in its own panel, so an in-progress code editor's unsaved edits are never at risk. Sends along
 * whatever the learner is currently typing (via `currentCode`, lifted in App.tsx from CodeEditor's
 * onChange) so a hint can be grounded in their actual code, not just the exercise's starter code. */
export function AskAI({
  sessionId,
  currentCode,
}: {
  sessionId: string;
  currentCode: { exerciseId: string; fileName: string; code: string } | null;
}) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [history, setHistory] = useState<Exchange[]>([]);

  function context() {
    return currentCode
      ? { exercise_id: currentCode.exerciseId, file_name: currentCode.fileName, code: currentCode.code }
      : {};
  }

  async function send(q: string) {
    const text = q.trim();
    if (!text || asking) return;
    setAsking(true);
    setQuestion("");
    const exchange: Exchange = { question: text };
    setHistory((h) => [...h, exchange]);
    try {
      const res = await askQuestion(sessionId, text, context());
      setHistory((h) => h.map((e) => (e === exchange ? { ...e, answer: res.answer } : e)));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setHistory((h) => h.map((e) => (e === exchange ? { ...e, error: message } : e)));
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="ask-ai">
      {open && (
        <div className="ask-ai-panel">
          <div className="ask-ai-header">
            <span>Ask AI</span>
            <button className="ask-ai-close" onClick={() => setOpen(false)} aria-label="Close">
              ×
            </button>
          </div>
          <div className="ask-ai-history">
            {history.length === 0 && (
              <p className="ask-ai-empty">
                Ask a question about what you're building, or request a hint -- this won't change
                what's on your screen or touch your code.
              </p>
            )}
            {history.map((e, i) => (
              <div className="ask-ai-exchange" key={i}>
                <div className="ask-ai-question">{e.question}</div>
                {e.answer && (
                  <div className="ask-ai-answer">
                    <Md>{e.answer}</Md>
                  </div>
                )}
                {e.error && <div className="ask-ai-error">{e.error}</div>}
                {!e.answer && !e.error && <div className="ask-ai-thinking">Thinking…</div>}
              </div>
            ))}
          </div>
          <div className="ask-ai-input-row">
            <button
              className="btn btn-secondary ask-ai-hint"
              disabled={asking}
              onClick={() => send(HINT_QUESTION)}
            >
              Give me a hint
            </button>
          </div>
          <form
            className="ask-ai-input-row"
            onSubmit={(e) => {
              e.preventDefault();
              send(question);
            }}
          >
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask a question…"
              disabled={asking}
            />
            <button className="btn btn-primary" type="submit" disabled={asking || !question.trim()}>
              Ask
            </button>
          </form>
        </div>
      )}
      <button className="ask-ai-fab" onClick={() => setOpen((v) => !v)} aria-label="Ask AI">
        {open ? "×" : "?"}
      </button>
    </div>
  );
}
