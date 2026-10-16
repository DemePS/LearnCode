import type { ActionsBlock } from "../../types";
import { actionLabel, type ExerciseIndex } from "../actionLabel";

/** The buttons on a page. Their text is derived from the action, never taken from the page: the agent
 * owns the ids, this frontend owns the interface (see actionLabel.ts). */
export function Actions({
  block,
  index,
  onAction,
  submitting,
}: {
  block: ActionsBlock;
  index: ExerciseIndex;
  onAction: (action: string, payload?: Record<string, unknown>) => void;
  submitting: boolean;
}) {
  return (
    <div className="block block-actions">
      {block.items.map((item) => (
        <button
          key={item.action}
          className={`btn btn-${item.style ?? "secondary"}`}
          disabled={submitting}
          onClick={() => onAction(item.action)}
        >
          {actionLabel(item.action, index)}
        </button>
      ))}
    </div>
  );
}
