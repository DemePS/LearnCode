import type { ActionsBlock } from "../../types";

export function Actions({
  block,
  onAction,
  submitting,
}: {
  block: ActionsBlock;
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
          {item.label}
        </button>
      ))}
    </div>
  );
}
