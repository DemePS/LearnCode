import { Suspense, lazy } from "react";
import type {
  ActionsBlock,
  Block,
  CodeEditorBlock,
  FeedbackBlock,
  MarkdownBlock,
  Page,
  RoadmapBlock,
  StatRowBlock,
} from "../types";
import { Actions } from "./blocks/Actions";
import { Feedback } from "./blocks/Feedback";
import { Markdown } from "./blocks/Markdown";
import { Roadmap } from "./blocks/Roadmap";
import { StatRow } from "./blocks/StatRow";

// Monaco (every language it ships, several MB) is the single biggest thing this app loads; code-
// splitting it here means the objective form, roadmap and feedback pages stay fast, and the cost is
// only paid once an exercise with a code_editor block actually renders.
const CodeEditor = lazy(() => import("./blocks/CodeEditor"));

type OnAction = (action: string, payload?: Record<string, unknown>) => void;

/** The entire UI, past the objective form, is this one switch: whatever block types the agent's
 * page JSON contains get rendered, in order. There is no per-screen React component -- a "roadmap
 * page" and an "exercise page" are the same component rendering different blocks. An unrecognized
 * block type (a future addition the agent starts using before this file knows about it) is skipped
 * rather than crashing the page. The casts below are the loose/strict boundary: `block.type`
 * decided the shape at runtime (this is JSON from the model, not a statically-checked value), so
 * each case trusts it rather than re-deriving a narrowing TypeScript can't express for a mixed
 * literal/non-literal discriminated union. */
function renderBlock(block: Block, key: number, onAction: OnAction, submitting: boolean) {
  switch (block.type) {
    case "markdown":
      return <Markdown key={key} block={block as MarkdownBlock} />;
    case "stat_row":
      return <StatRow key={key} block={block as StatRowBlock} />;
    case "roadmap":
      return <Roadmap key={key} block={block as RoadmapBlock} onAction={onAction} />;
    case "code_editor":
      return (
        <Suspense key={key} fallback={<div className="block block-code-editor-loading">Loading editor…</div>}>
          <CodeEditor block={block as CodeEditorBlock} onAction={onAction} submitting={submitting} />
        </Suspense>
      );
    case "feedback":
      return <Feedback key={key} block={block as FeedbackBlock} />;
    case "actions":
      return <Actions key={key} block={block as ActionsBlock} onAction={onAction} submitting={submitting} />;
    default:
      return null;
  }
}

export function PageRenderer({
  page,
  onAction,
  submitting,
}: {
  page: Page;
  onAction: OnAction;
  submitting: boolean;
}) {
  return (
    <div className="page">
      <h1>{page.title}</h1>
      {page.subtitle && <p className="page-subtitle">{page.subtitle}</p>}
      {page.blocks.map((block, i) => renderBlock(block, i, onAction, submitting))}
    </div>
  );
}
