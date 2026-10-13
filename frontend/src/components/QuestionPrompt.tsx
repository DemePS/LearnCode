import { useState } from "react";

/** Shown during the pre-session clarification pass (see App.tsx's clarify loop): a lightweight,
 * stateless back-and-forth that happens before any project/session exists, so an ambiguous
 * objective ("build a chat app") doesn't get a guessed-at roadmap. */
export function QuestionPrompt({
  question,
  onAnswer,
  submitting,
}: {
  question: string;
  onAnswer: (text: string) => void;
  submitting: boolean;
}) {
  const [text, setText] = useState("");

  return (
    <div className="question-prompt">
      <div className="question-prompt-label">Before I plan this out, one thing:</div>
      <p className="question-prompt-text">{question}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim() && !submitting) onAnswer(text.trim());
        }}
      >
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Your answer…"
          rows={3}
          disabled={submitting}
          autoFocus
        />
        <button className="btn btn-primary" type="submit" disabled={submitting || !text.trim()}>
          {submitting ? "Thinking…" : "Answer"}
        </button>
      </form>
    </div>
  );
}
