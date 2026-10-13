import type { RoadmapBlock } from "../../types";

const STATUS_LABEL: Record<string, string> = {
  locked: "Locked",
  available: "Up next",
  in_progress: "In progress",
  done: "Done",
};

export function Roadmap({
  block,
  onAction,
}: {
  block: RoadmapBlock;
  onAction: (action: string, payload?: Record<string, unknown>) => void;
}) {
  return (
    <ol className="block block-roadmap">
      {block.items.map((item) => {
        const clickable = item.status !== "locked";
        return (
          <li
            key={item.id}
            className={`roadmap-item status-${item.status}${clickable ? " clickable" : ""}`}
            onClick={clickable ? () => onAction(item.open_action) : undefined}
            role={clickable ? "button" : undefined}
            tabIndex={clickable ? 0 : undefined}
          >
            <span className="roadmap-status">{STATUS_LABEL[item.status] ?? item.status}</span>
            <span className="roadmap-title">{item.title}</span>
            <span className="roadmap-summary">{item.summary}</span>
          </li>
        );
      })}
    </ol>
  );
}
