import type { RoadmapBlock } from "../../types";

const STATUS_LABEL: Record<string, string> = {
  locked: "Not started",
  available: "Up next",
  in_progress: "In progress",
  done: "Done",
};

/** Every item opens, regardless of status -- order is a recommendation (shown via status/dimming),
 * never a gate. A learner can jump ahead; the agent decides how to handle that when it gets the
 * open_action (per PAGE_SYSTEM_PROMPT, normally just open it, with a brief heads-up if it genuinely
 * depends on an earlier unfinished exercise). */
export function Roadmap({
  block,
  onAction,
}: {
  block: RoadmapBlock;
  onAction: (action: string, payload?: Record<string, unknown>) => void;
}) {
  return (
    <ol className="block block-roadmap">
      {block.items.map((item) => (
        <li
          key={item.id}
          className={`roadmap-item status-${item.status} clickable`}
          onClick={() => onAction(item.open_action)}
          role="button"
          tabIndex={0}
        >
          <span className="roadmap-status">{STATUS_LABEL[item.status] ?? item.status}</span>
          <span className="roadmap-title">{item.title}</span>
          <span className="roadmap-summary">{item.summary}</span>
        </li>
      ))}
    </ol>
  );
}
