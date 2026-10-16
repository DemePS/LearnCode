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
import { EMPTY_INDEX, type ExerciseIndex } from "./actionLabel";

// Monaco (every language it ships, several MB) is the single biggest thing this app loads; code-
// splitting it here means the objective form, roadmap and feedback pages stay fast, and the cost is
// only paid once an exercise with a code_editor block actually renders.
const CodeEditor = lazy(() => import("./blocks/CodeEditor"));

type OnAction = (action: string, payload?: Record<string, unknown>) => void;
type OnCodeChange = (exerciseId: string, fileName: string, code: string) => void;

/** The entire UI, past the objective form, is this one switch: whatever block types the agent's
 * page JSON contains get rendered, in order. There is no per-screen React component -- a "roadmap
 * page" and an "exercise page" are the same component rendering different blocks. An unrecognized
 * block type (a future addition the agent starts using before this file knows about it) is skipped
 * rather than crashing the page. The casts below are the loose/strict boundary: `block.type`
 * decided the shape at runtime (this is JSON from the model, not a statically-checked value), so
 * each case trusts it rather than re-deriving a narrowing TypeScript can't express for a mixed
 * literal/non-literal discriminated union. */
function renderBlock(
  block: Block,
  key: number,
  onAction: OnAction,
  submitting: boolean,
  onCodeChange: OnCodeChange,
  index: ExerciseIndex,
  draft?: string | null,
) {
  switch (block.type) {
    case "markdown":
      return <Markdown key={key} block={block as MarkdownBlock} />;
    case "stat_row":
      return <StatRow key={key} block={block as StatRowBlock} />;
    case "roadmap":
      return <Roadmap key={key} block={block as RoadmapBlock} onAction={onAction} />;
    case "code_editor": {
      const codeBlock = block as CodeEditorBlock;
      return (
        // Keyed on exercise_id (not just the block's array index) so opening a different exercise
        // remounts the editor and resets to its own starter_code, instead of keeping whatever the
        // learner was typing for the previous one at the same position in the page.
        <Suspense
          key={`code-${codeBlock.exercise_id}`}
          fallback={<div className="block block-code-editor-loading">Loading editor…</div>}
        >
          <CodeEditor block={codeBlock} draft={draft} onAction={onAction} submitting={submitting}
                      onCodeChange={onCodeChange} />
        </Suspense>
      );
    }
    case "feedback":
      return <Feedback key={key} block={block as FeedbackBlock} />;
    case "actions":
      return <Actions key={key} block={block as ActionsBlock} index={index} onAction={onAction}
                      submitting={submitting} />;
    default:
      return null;
  }
}

/** PAGE_SYSTEM_PROMPT tells the agent to send at most one code_editor block per page, but that's a
 * request to a model, not a guarantee -- it can still get it wrong. Drop every code_editor after
 * the first so the UI can never show two, regardless of what the agent actually sent. */
function dedupeCodeEditors(blocks: Block[]): Block[] {
  let seenCodeEditor = false;
  return blocks.filter((block) => {
    if (block.type !== "code_editor") return true;
    if (seenCodeEditor) {
      console.warn("PageRenderer: dropping an extra code_editor block (the page had more than one)", block);
      return false;
    }
    seenCodeEditor = true;
    return true;
  });
}

export function PageRenderer({
  page,
  onAction,
  submitting,
  onCodeChange = () => {},
  index = EMPTY_INDEX,
}: {
  page: Page;
  onAction: OnAction;
  submitting: boolean;
  /** Fires on every keystroke in a code_editor block, so a parent (the floating help widget) can
   * give hints grounded in what the learner is actually typing right now -- not just the starter
   * code from the last page the agent sent. Optional: a caller that doesn't need live code (e.g. a
   * future embed of this renderer) can omit it. */
  onCodeChange?: OnCodeChange;
  /** The roadmap's exercises, so a button on a feedback page (which carries no roadmap of its own)
   * can still be labelled "Retry exercise 6". Defaults to empty, which falls back to wording derived
   * from the action id alone. */
  index?: ExerciseIndex;
}) {
  return (
    <div className="page">
      <h1>{page.title}</h1>
      {page.subtitle && <p className="page-subtitle">{page.subtitle}</p>}
      {dedupeCodeEditors(page.blocks).map((block, i) =>
        renderBlock(block, i, onAction, submitting, onCodeChange, index, page.draft))}
    </div>
  );
}
