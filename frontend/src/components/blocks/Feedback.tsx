import type { FeedbackBlock } from "../../types";
import { Md } from "../Md";

export function Feedback({ block }: { block: FeedbackBlock }) {
  return (
    <div className={`block block-feedback ${block.passed ? "passed" : "failed"}`}>
      <div className="feedback-summary">
        <span className="feedback-icon">{block.passed ? "✓" : "✗"}</span>
        {block.summary}
      </div>
      <div className="feedback-details">
        <Md>{block.details}</Md>
      </div>
    </div>
  );
}
