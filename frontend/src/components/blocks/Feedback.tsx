import ReactMarkdown from "react-markdown";
import type { FeedbackBlock } from "../../types";

export function Feedback({ block }: { block: FeedbackBlock }) {
  return (
    <div className={`block block-feedback ${block.passed ? "passed" : "failed"}`}>
      <div className="feedback-summary">
        <span className="feedback-icon">{block.passed ? "✓" : "✗"}</span>
        {block.summary}
      </div>
      <div className="feedback-details">
        <ReactMarkdown>{block.details}</ReactMarkdown>
      </div>
    </div>
  );
}
